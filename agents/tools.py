"""Tool backends: read-only SQL, CMS docs search, and MetricFlow compile-then-execute."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import re
import threading
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb


class ToolError(Exception):
    """Shown to the agent as an is_error tool result."""


@dataclass(frozen=True)
class Limits:
    max_rows: int = 200
    max_chars: int = 20000
    max_cell_chars: int = 500
    timeout_s: float = 120.0


def _cell(v: Any, max_chars: int) -> str:
    if v is None:
        s = "NULL"
    elif isinstance(v, decimal.Decimal | dt.date | dt.datetime):
        s = str(v)
    elif isinstance(v, list | dict | tuple):
        s = json.dumps(v, default=str)
    else:
        s = str(v)
    s = s.replace("\n", " ")
    return s if len(s) <= max_chars else s[:max_chars] + "…"


def clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated: output was {len(text)} chars, showing {max_chars}]"


def format_rows(columns: list[str], rows: list[tuple], more: bool, limits: Limits) -> str:
    lines = [" | ".join(columns)]
    lines += [" | ".join(_cell(v, limits.max_cell_chars) for v in r) for r in rows]
    if more:
        lines.append(
            f"[truncated: showing the first {limits.max_rows} rows; the query returned more]"
        )
    elif not rows:
        lines.append("(0 rows)")
    return clip("\n".join(lines), limits.max_chars)


_TABLE_FN = re.compile(r"\bquery(_table)?\s*\(", re.IGNORECASE)
_PART = re.compile(r'"([^"]*)"|([^.]+)')
_SYSTEM_SCHEMAS = {"information_schema", "pg_catalog"}


def _ident_parts(ref: str) -> list[str]:
    ref = re.split(r"\s+AS\s+", ref, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return [(a or b).strip().lower() for a, b in _PART.findall(ref)]


class SqlTool:
    """One read-only DuckDB database for one agent run.

    The file is ATTACHed READ_ONLY to a private in-memory instance, then file access is turned
    off and the configuration locked, so the agent can't attach anything else, read files or
    undo the settings. Read-only attaches take no write lock, so runs can go in parallel.
    """

    def __init__(
        self,
        db_path: str | Path,
        alias: str,
        *,
        default_schema: str | None = None,
        allowed_schemas: set[str] | None = None,
        blocked_schemas: frozenset[str] = frozenset(),
        limits: Limits = Limits(),
        duckdb_settings: dict[str, Any] | None = None,
    ) -> None:
        db_path = Path(db_path).resolve()
        if not db_path.exists():
            raise FileNotFoundError(db_path)
        if "'" in str(db_path) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
            raise ValueError("unsafe database path or alias")
        self.alias = alias
        self.allowed = {s.lower() for s in allowed_schemas} if allowed_schemas else None
        self.blocked = {s.lower() for s in blocked_schemas}
        self.limits = limits
        self.con = duckdb.connect(":memory:")
        for k, v in (duckdb_settings or {}).items():
            if k == "temp_directory":
                Path(v).mkdir(parents=True, exist_ok=True)
                v = str(Path(v).resolve() / uuid.uuid4().hex)
            self.con.execute(f"SET {k} = ?", [v])
        self.con.execute(f"ATTACH '{db_path}' AS {alias} (READ_ONLY)")
        schemas = {
            r[0]
            for r in self.con.execute(
                "SELECT schema_name FROM duckdb_schemas() WHERE database_name = ?", [alias]
            ).fetchall()
        }
        self.schemas = sorted(schemas)
        target = f"{alias}.{default_schema}" if default_schema in schemas else alias
        self.con.execute(f"USE {target}")
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")

    def check(self, sql: str) -> None:
        try:
            stmts = self.con.extract_statements(sql)
        except duckdb.Error as e:
            raise ToolError(f"SQL parse error: {e}") from None
        if len(stmts) != 1:
            raise ToolError("Send exactly one SQL statement per call.")
        if stmts[0].type not in (duckdb.StatementType.SELECT, duckdb.StatementType.EXPLAIN):
            raise ToolError(
                "Read-only access: only SELECT, DESCRIBE, SHOW and EXPLAIN are allowed."
            )
        if _TABLE_FN.search(sql):
            raise ToolError("The query() and query_table() table functions are not allowed.")
        try:
            refs = self.con.get_table_names(sql, qualified=True)
        except duckdb.Error as e:
            raise ToolError(f"{type(e).__name__}: {e}") from None
        for ref in refs:
            parts = _ident_parts(ref)
            quals = parts[:-1]
            if any(q in self.blocked for q in quals):
                raise ToolError(f"Access denied: {ref}")
            if self.allowed is not None and quals:
                schema = quals[-1]
                if schema not in self.allowed | _SYSTEM_SCHEMAS:
                    raise ToolError(
                        f"Access denied: {ref}. Allowed schemas: {', '.join(sorted(self.allowed))}."
                    )

    def run(self, sql: str, *, checked: bool = True) -> str:
        return format_rows(*self.fetch(sql, checked=checked), self.limits)

    def fetch(self, sql: str, *, checked: bool = True) -> tuple[list[str], list[tuple], bool]:
        """Columns, at most max_rows rows, and whether there were more."""
        if checked:
            self.check(sql)
        timer = threading.Timer(self.limits.timeout_s, self.con.interrupt)
        timer.start()
        try:
            cur = self.con.execute(sql)
            if cur.description is None:
                return ["result"], [("(no result set)",)], False
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(self.limits.max_rows + 1)
        except duckdb.InterruptException:
            raise ToolError(
                f"Query cancelled after {self.limits.timeout_s:.0f} s. Narrow or aggregate first."
            ) from None
        except duckdb.Error as e:
            raise ToolError(f"{type(e).__name__}: {e}") from None
        finally:
            timer.cancel()
        return cols, rows[: self.limits.max_rows], len(rows) > self.limits.max_rows

    def close(self) -> None:
        self.con.close()


_IDENT = re.compile(r"[A-Za-z_]\w*")


class TableDocs:
    """Descriptions of a layer's tables and columns, for DESCRIBE and SHOW TABLES.

    Two sources: dbt's model and column docs (`manifest.json`, optional), and the semantic YAML,
    where each dimension, entity and measure describes the column it reads. Agents mostly write
    SQL on the tables, so knowledge kept only in metric YAML would otherwise go unread.
    """

    def __init__(
        self, semantic_manifest_path: str | Path, dbt_manifest_path: str | Path | None = None
    ) -> None:
        self.tables: dict[tuple[str, str], list[str]] = {}
        self.columns: dict[tuple[str, str], dict[str, list[str]]] = {}
        if dbt_manifest_path and Path(dbt_manifest_path).exists():
            for n in json.loads(Path(dbt_manifest_path).read_text())["nodes"].values():
                if n["resource_type"] != "model":
                    continue
                key = (n["schema"].lower(), (n.get("alias") or n["name"]).lower())
                self._add(self.tables.setdefault(key, []), n.get("description"))
                for c in n.get("columns", {}).values():
                    self._col(key, c["name"], c.get("description"))
        # The raw JSON, not MetricFlow's parsed manifest: parsing rewrites count measures into
        # sum(CASE ...), which would hide the column they count.
        for sm in json.loads(Path(semantic_manifest_path).read_text())["semantic_models"]:
            rel = sm["node_relation"]
            key = (rel["schema_name"].lower(), rel["alias"].lower())
            label = f"Semantic model {sm['name']}: {sm.get('description') or ''}"
            self._add(self.tables.setdefault(key, []), label)
            for kind, group in (
                ("dimension", "dimensions"),
                ("entity", "entities"),
                ("measure", "measures"),
            ):
                for d in sm.get(group) or []:
                    agg = f" ({d['agg']})" if kind == "measure" else ""
                    desc = f"{kind} {d['name']}{agg}: {d.get('description') or ''}"
                    self._col(key, d.get("expr") or d["name"], desc)

    @staticmethod
    def _add(out: list[str], text: str | None) -> None:
        text = " ".join((text or "").split()).rstrip(": ")
        if text and text not in out:
            out.append(text)

    def _col(self, key: tuple[str, str], expr: str, text: str | None) -> None:
        if _IDENT.fullmatch(expr.strip()):  # computed expressions don't name one column
            self._add(self.columns.setdefault(key, {}).setdefault(expr.strip().lower(), []), text)

    def resolve(self, parts: list[str], schemas: list[str]) -> tuple[str, str] | None:
        """(schema, table) for a possibly unqualified name, searching gold first."""
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        for s in sorted(schemas, key=lambda s: s != "gold"):
            if (s, parts[-1]) in self.tables or (s, parts[-1]) in self.columns:
                return s, parts[-1]
        return None

    def table(self, key: tuple[str, str] | None) -> str:
        return "; ".join(self.tables.get(key, [])) if key else ""

    def column(self, key: tuple[str, str] | None, name: str) -> str:
        return "; ".join(self.columns.get(key, {}).get(str(name).lower(), [])) if key else ""


@lru_cache(maxsize=64)
def _doc_lines(path: str, mtime: float) -> tuple[str, ...]:
    return tuple(Path(path).read_text(encoding="utf-8", errors="replace").splitlines())


class DocsTool:
    """Extracted CMS documentation (`*.md`), read by name and line range."""

    def __init__(self, docs_dir: str | Path, limits: Limits = Limits()) -> None:
        self.dir = Path(docs_dir)
        self.limits = limits

    def _docs(self) -> dict[str, Path]:
        if not self.dir.is_dir():
            return {}
        return {p.stem: p for p in sorted(self.dir.glob("*.md"))}

    def _lines(self, p: Path) -> tuple[str, ...]:
        return _doc_lines(str(p), p.stat().st_mtime)

    def list_docs(self) -> str:
        docs = self._docs()
        if not docs:
            return "No documents are available."
        out = ["name | lines | title"]
        for name, p in docs.items():
            lines = self._lines(p)
            title = next((ln.lstrip("# ").strip() for ln in lines if ln.strip()), "")
            out.append(f"{name} | {len(lines)} | {title[:120]}")
        return "\n".join(out)

    def search_docs(self, query: str, max_hits: int = 40) -> str:
        terms = [t for t in query.casefold().split() if t]
        if not terms:
            raise ToolError("Empty query.")
        hits: list[tuple[int, str, int, str]] = []
        for name, p in self._docs().items():
            lines = self._lines(p)
            for i, line in enumerate(lines, start=1):
                # PDF extraction wraps long column names mid-word
                # ("Number_of_Payments_Included_in_To" / "tal_Amount..."), so a line is
                # also searched joined to the next one.
                nxt = lines[i].strip().casefold() if i < len(lines) else ""
                low = line.casefold()
                score = sum(t in low or (t not in nxt and t in low.rstrip() + nxt) for t in terms)
                if score:
                    hits.append((score, name, i, line.strip()))
        full = [h for h in hits if h[0] == len(terms)]
        note = ""
        if not full and hits:
            note = "[no line contains every term; showing lines with the most terms]\n"
        chosen = sorted(full or hits, key=lambda h: (-h[0], h[1], h[2]))[:max_hits]
        if not chosen:
            return "No matches."
        total = len(full or hits)
        body = "\n".join(f"{n}:{i}: {_cell(t, 300)}" for _, n, i, t in chosen)
        more = f"\n[{total} matching lines; showing {len(chosen)}]" if total > len(chosen) else ""
        return clip(note + body + more, self.limits.max_chars)

    def read_doc(self, name: str, offset: int = 1, limit: int = 200) -> str:
        docs = self._docs()
        key = Path(name).stem
        if key not in docs:
            raise ToolError(f"No document named {name!r}. Call list_docs.")
        lines = self._lines(docs[key])
        start = max(int(offset), 1)
        limit = max(1, min(int(limit), 1000))
        chunk = lines[start - 1 : start - 1 + limit]
        end = start - 1 + len(chunk)
        head = f"[{key}: lines {start}-{end} of {len(lines)}]\n"
        return clip(head + "\n".join(chunk), self.limits.max_chars)


class _CompileOnlyClient:
    """MetricFlow's SqlClient protocol, for DuckDB SQL rendering only; never touches a database."""

    def __init__(self) -> None:
        from metricflow.protocols.sql_client import SqlEngine
        from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer

        self.sql_engine_type = SqlEngine.DUCKDB
        self.sql_plan_renderer = DuckDbSqlPlanRenderer()

    def query(self, *a: Any, **k: Any) -> Any:
        raise RuntimeError("compile-only client")

    execute = dry_run = query

    def close(self) -> None:
        pass

    def render_bind_parameter_key(self, key: str) -> str:
        return f"${key}"


# MetricFlow resets a process-global ID generator on every query, so compiles must not overlap.
_MF_LOCK = threading.Lock()


@lru_cache(maxsize=8)
def _engine(manifest_path: str, mtime: float) -> tuple[Any, Any]:
    from metricflow.engine.metricflow_engine import MetricFlowEngine
    from metricflow_semantics.model.dbt_manifest_parser import (
        parse_manifest_from_dbt_generated_manifest,
    )
    from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

    manifest = parse_manifest_from_dbt_generated_manifest(Path(manifest_path).read_text())
    with _MF_LOCK:
        engine = MetricFlowEngine(SemanticManifestLookup(manifest), _CompileOnlyClient())
    return manifest, engine


def _dump(obj: Any) -> Any:
    """Pydantic manifest objects to plain JSON, without the nulls and empty lists."""
    data = obj.dict() if hasattr(obj, "dict") else obj

    def prune(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: prune(v) for k, v in x.items() if v not in (None, [], {}, "")}
        if isinstance(x, list):
            return [prune(v) for v in x]
        return x

    return json.loads(json.dumps(prune(data), default=str))


class MetricLayer:
    """A dbt + MetricFlow semantic layer, read from its `semantic_manifest.json`.

    Definitions come straight from the manifest. Queries are compiled to DuckDB SQL by
    MetricFlow's Python engine with a compile-only client, then run by the caller on its own
    read-only connection, so parallel runs never open the layer for writing (`mf query` does).
    """

    def __init__(self, manifest_path: str | Path, limits: Limits = Limits()) -> None:
        p = Path(manifest_path).resolve()
        if not p.exists():
            raise FileNotFoundError(p)
        self.manifest, self.engine = _engine(str(p), p.stat().st_mtime)
        self.limits = limits
        self._metrics = {m.name: m for m in self.manifest.metrics}

    @property
    def databases(self) -> set[str]:
        return {
            sm.node_relation.database
            for sm in self.manifest.semantic_models
            if sm.node_relation.database
        }

    def list_metrics(self) -> str:
        if not self._metrics:
            return "No metrics are defined."
        out = ["name | type | label | description"]
        for m in sorted(self._metrics.values(), key=lambda m: m.name):
            desc = (m.description or "").replace("\n", " ")
            out.append(f"{m.name} | {m.type.value} | {m.label or ''} | {_cell(desc, 300)}")
        return clip("\n".join(out), self.limits.max_chars)

    def explain_metric(self, name: str) -> str:
        m = self._metrics.get(name)
        if m is None:
            raise ToolError(f"No metric named {name!r}. Call list_metrics.")
        measures = {mr.name for mr in m.type_params.input_measures} | (
            {m.type_params.measure.name} if m.type_params.measure else set()
        )
        sources = []
        for sm in self.manifest.semantic_models:
            hit = [ms for ms in sm.measures if ms.name in measures]
            if hit:
                sources.append(
                    {
                        "semantic_model": sm.name,
                        "description": sm.description,
                        "table": sm.node_relation.relation_name,
                        "measures": [_dump(ms) for ms in hit],
                    }
                )
        with _MF_LOCK:
            dims = self.engine.simple_dimensions_for_metrics([name])
            ents = self.engine.entities_for_metrics([name])
        out = {
            "metric": _dump(m),
            "measures": sources,
            "group_by": [
                {"name": d.dunder_name, "type": d.type.value, "description": d.description}
                for d in dims
            ]
            + [{"name": e.name, "type": "entity", "description": e.description} for e in ents],
        }
        return clip(json.dumps(_dump(out), indent=1), self.limits.max_chars)

    def compile(
        self,
        metrics: list[str],
        group_by: list[str] | None = None,
        where: list[str] | None = None,
        order_by: list[str] | None = None,
        limit: int | None = None,
    ) -> str:
        from metricflow.engine.metricflow_engine import MetricFlowQueryRequest

        req = MetricFlowQueryRequest.create(
            metric_names=list(metrics),
            group_by_names=list(group_by or []) or None,
            where_constraints=list(where or []) or None,
            order_by_names=list(order_by or []) or None,
            limit=limit,
        )
        try:
            with _MF_LOCK:
                stmt = self.engine.explain(req).sql_statement
        except Exception as e:  # MetricFlow raises many types; all are the agent's to fix
            raise ToolError(
                f"MetricFlow could not compile the query:\n{clip(str(e), 4000)}"
            ) from None
        if stmt.bind_parameter_set.param_items:
            raise ToolError("MetricFlow produced bind parameters, which this harness can't run.")
        return stmt.sql
