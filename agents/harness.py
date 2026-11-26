"""The agent under test: one Messages API tool loop, the same in every arm.

`run_question` runs one question for one arm and returns a record. The prompt holds only the
question text and its answer shape; `expert_note`, `verify`, `traps`, gold SQL and gold results
never reach the agent. Arms differ only in their tools:

- `raw`: read-only SQL on `bronze` in the warehouse, plus the extracted CMS docs.
- any other name is a semantic-layer arm (`starter`, `factory_r1`, `reference`, ...): MetricFlow
  tools plus read-only SQL on `data/layers/<layer>.duckdb`, with bronze not attached.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agents.shapes import answer_schema, parse_shape, validate_answer
from agents.tools import DocsTool, Limits, MetricLayer, SqlTool, ToolError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "agent.yml"
PRICING_PATH = ROOT / "config" / "pricing.yml"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text())
    cfg["system_prompt"] = cfg["system_prompt"].replace("{max_turns}", str(cfg["max_turns"]))
    return cfg


def config_hash(cfg: dict[str, Any]) -> str:
    keys = ("model", "effort", "thinking_display", "max_turns", "max_tokens", "system_prompt")
    blob = json.dumps({k: cfg[k] for k in keys}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def limits_from(cfg: dict[str, Any]) -> Limits:
    tr = cfg["tool_results"]
    return Limits(tr["max_rows"], tr["max_chars"], tr["max_cell_chars"], cfg["sql_timeout_s"])


def _path(cfg: dict[str, Any], key: str) -> Path:
    p = Path(cfg["paths"][key])
    return p if p.is_absolute() else ROOT / p


# ── pricing ──────────────────────────────────────────────────────────────────

USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def load_pricing(path: Path = PRICING_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def cost_usd(usage: dict[str, int], model: str, pricing: dict[str, Any]) -> float:
    p = pricing["models"][model]
    write_1h = usage.get("cache_write_1h_tokens", 0)
    write_5m = usage.get("cache_creation_input_tokens", 0) - write_1h
    total = (
        usage.get("input_tokens", 0) * p["input"]
        + usage.get("output_tokens", 0) * p["output"]
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        + write_5m * p["cache_write_5m"]
        + write_1h * p["cache_write_1h"]
    )
    return round(total / 1e6, 6)


# ── tools shared by every arm ────────────────────────────────────────────────

FLAG_GAP = {
    "name": "flag_gap",
    "description": (
        "Record that something you needed to answer was missing, unclear or wrong in the data, "
        "documentation or definitions you were given. Call it whenever that happens, as often as "
        "needed; it does not end the task and does not change the data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question_id": {
                "type": "string",
                "description": "The id of the question you are answering.",
            },
            "what_was_missing": {
                "type": "string",
                "description": "What you needed and couldn't find or trust.",
            },
            "evidence": {
                "type": "string",
                "description": "What you saw: queries, results, doc lines.",
            },
        },
        "required": ["question_id", "what_was_missing", "evidence"],
    },
}


def submit_answer_spec(shape: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "submit_answer",
        "description": (
            "Submit your final answer. Call it once, when you are done; it ends the task. The "
            "answer must match the schema. Numbers are plain numbers (no units, commas or currency "
            "signs); "
            "percents are in 0-100 units (12.3 means 12.3%). Report names (companies, hospitals, "
            "products, specialties) exactly as they appear in the data: don't paraphrase them, fix "
            "their capitalization or expand abbreviations. Put the definitions and assumptions you "
            "used in notes."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": answer_schema(parse_shape(shape)),
                "notes": {"type": "string", "description": "Definitions and assumptions you used."},
            },
            "required": ["answer", "notes"],
            "additionalProperties": False,
        },
    }


SQL_LIMITS_NOTE = (
    "Results show at most {rows} rows and {chars} characters; queries stop after {secs} s."
)


def _sql_spec(description: str, lim: Limits) -> dict[str, Any]:
    note = SQL_LIMITS_NOTE.format(rows=lim.max_rows, chars=lim.max_chars, secs=int(lim.timeout_s))
    return {
        "name": "sql",
        "description": f"{description} {note}",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "One DuckDB SQL statement."}},
            "required": ["query"],
        },
    }


# ── arms ─────────────────────────────────────────────────────────────────────


class Arm:
    name: str

    def tool_specs(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def call(self, tool: str, args: dict[str, Any]) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


class RawArm(Arm):
    """Bronze tables and the CMS docs; nobody built anything on top."""

    name = "raw"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.limits = limits_from(cfg)
        self.db = SqlTool(
            _path(cfg, "warehouse"),
            "warehouse",
            default_schema="bronze",
            allowed_schemas={"bronze"},
            blocked_schemas=frozenset({"meta"}),
            limits=self.limits,
            duckdb_settings=_duckdb_settings(cfg),
        )
        self.docs = DocsTool(_path(cfg, "docs"), self.limits)

    def tool_specs(self) -> list[dict[str, Any]]:
        lim = self.limits
        return [
            _sql_spec(
                "Run one read-only DuckDB SQL query against the source tables in schema `bronze` "
                "(unqualified names resolve there). Every bronze column is stored as text, exactly "
                "as published. Explore with DESCRIBE bronze.<table>, SHOW TABLES or "
                "information_schema.columns.",
                lim,
            ),
            {
                "name": "list_docs",
                "description": (
                    "List the CMS documentation available (data dictionaries, methodology, "
                    "code values) with line counts."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "search_docs",
                "description": (
                    "Search the CMS documentation for lines containing all the given words "
                    "(case-insensitive). Returns doc:line: text; use read_doc to see the context."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Words to find."}},
                    "required": ["query"],
                },
            },
            {
                "name": "read_doc",
                "description": f"Read lines of one document, at most {lim.max_chars} chars.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Document name from list_docs."},
                        "offset": {
                            "type": "integer",
                            "description": "First line, 1-based. Default 1.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of lines. Default 200.",
                        },
                    },
                    "required": ["name"],
                },
            },
        ]

    def call(self, tool: str, args: dict[str, Any]) -> str:
        if tool == "sql":
            return self.db.run(args["query"])
        if tool == "list_docs":
            return self.docs.list_docs()
        if tool == "search_docs":
            return self.docs.search_docs(args["query"])
        if tool == "read_doc":
            return self.docs.read_doc(args["name"], args.get("offset", 1), args.get("limit", 200))
        raise ToolError(f"Unknown tool {tool!r}")

    def close(self) -> None:
        self.db.close()


def layer_manifest_path(cfg: dict[str, Any], layer: str) -> Path:
    """A per-layer snapshot (`make layer-manifest`) wins over the live dbt target."""
    snap = _path(cfg, "layers") / f"{layer}.semantic_manifest.json"
    return snap if snap.exists() else _path(cfg, "dbt_manifest")


class LayerArm(Arm):
    """A semantic layer: MetricFlow metrics plus SQL on its silver/gold tables, never bronze."""

    def __init__(
        self,
        cfg: dict[str, Any],
        layer: str,
        *,
        db_path: Path | None = None,
        manifest_path: Path | None = None,
    ) -> None:
        self.name = layer
        self.limits = limits_from(cfg)
        self.last_compiled_sql: str | None = None
        self.layer = MetricLayer(manifest_path or layer_manifest_path(cfg, layer), self.limits)
        dbs = self.layer.databases
        if len(dbs) > 1:
            raise ValueError(f"layer {layer!r} spans several databases: {sorted(dbs)}")
        # Attach under the database name MetricFlow compiled against, whatever the file is called.
        alias = next(iter(dbs), layer)
        self.db = SqlTool(
            db_path or _path(cfg, "layers") / f"{layer}.duckdb",
            alias,
            default_schema="gold",
            blocked_schemas=frozenset({"bronze", "meta"}),
            limits=self.limits,
            duckdb_settings=_duckdb_settings(cfg),
        )

    def tool_specs(self) -> list[dict[str, Any]]:
        names = {"type": "array", "items": {"type": "string"}}
        return [
            {
                "name": "list_metrics",
                "description": "List the semantic layer's metrics and descriptions. Start here.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "explain_metric",
                "description": (
                    "Show one metric's full definition: its measures, expressions, filters, the "
                    "table it reads, and every dimension and entity it can be grouped or "
                    "filtered by."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "query_metrics",
                "description": (
                    "Query metrics through MetricFlow. group_by takes dimension names as listed by "
                    "explain_metric (entity__dimension, or metric_time__day|month|year). where "
                    "takes MetricFlow filter expressions, e.g. "
                    "\"{{ Dimension('payment__program_year') }} = '2024'\" or "
                    "\"{{ TimeDimension('metric_time', 'month') }} >= '2024-01-01'\"; several are "
                    "ANDed. order_by takes metric or group-by names, prefixed with - for "
                    "descending. Set show_sql to see the SQL MetricFlow generated."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metrics": {**names, "description": "Metric names."},
                        "group_by": names,
                        "where": names,
                        "order_by": names,
                        "limit": {"type": "integer"},
                        "show_sql": {"type": "boolean"},
                    },
                    "required": ["metrics"],
                },
            },
            _sql_spec(
                "Run one read-only DuckDB SQL query against the semantic layer's own tables "
                f"(schemas: {', '.join(self.db.schemas)}; unqualified names resolve to gold if "
                "it exists). Explore with SHOW ALL TABLES, DESCRIBE <schema>.<table> or "
                "information_schema.columns.",
                self.limits,
            ),
        ]

    def call(self, tool: str, args: dict[str, Any]) -> str:
        if tool == "list_metrics":
            return self.layer.list_metrics()
        if tool == "explain_metric":
            return self.layer.explain_metric(args["name"])
        if tool == "query_metrics":
            sql = self.layer.compile(
                args["metrics"],
                args.get("group_by"),
                args.get("where"),
                args.get("order_by"),
                args.get("limit"),
            )
            self.last_compiled_sql = sql
            result = self.db.run(sql, checked=False)
            return f"{result}\n\n-- SQL:\n{sql}" if args.get("show_sql") else result
        if tool == "sql":
            return self.db.run(args["query"])
        raise ToolError(f"Unknown tool {tool!r}")

    def close(self) -> None:
        self.db.close()


def _duckdb_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    s = dict(cfg.get("duckdb") or {})
    if "temp_directory" in s and not Path(s["temp_directory"]).is_absolute():
        s["temp_directory"] = str(ROOT / s["temp_directory"])
    return s


def make_arm(name: str, cfg: dict[str, Any]) -> Arm:
    return RawArm(cfg) if name == "raw" else LayerArm(cfg, name)


# ── the loop ─────────────────────────────────────────────────────────────────


def user_prompt(question: dict[str, Any]) -> str:
    shape = yaml.safe_dump(question["answer"], default_flow_style=True, sort_keys=False).strip()
    return (
        f"Question {question['id']}:\n{question['question'].strip()}\n\n"
        f"Answer shape: {shape}\n"
        "Submit it with submit_answer."
    )


class Trace:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = path.open("w", encoding="utf-8")

    def write(self, kind: str, **data: Any) -> None:
        rec = {"t": dt.datetime.now(dt.UTC).isoformat(), "type": kind, **data}
        self.f.write(json.dumps(rec, default=str) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


def _jsonable(x: Any) -> Any:
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json", exclude_none=True)
    if isinstance(x, list):
        return [_jsonable(v) for v in x]
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    return x


@dataclass
class RunState:
    usage: dict[str, int] = field(default_factory=lambda: dict.fromkeys(USAGE_KEYS, 0))
    flags: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    answer: Any = None
    notes: str | None = None
    submitted: bool = False

    def add_usage(self, u: Any) -> None:
        for k in USAGE_KEYS:
            self.usage[k] += getattr(u, k, None) or 0
        cc = getattr(u, "cache_creation", None)
        if cc is not None and getattr(cc, "ephemeral_1h_input_tokens", None):
            self.usage["cache_write_1h_tokens"] = (
                self.usage.get("cache_write_1h_tokens", 0) + cc.ephemeral_1h_input_tokens
            )


def run_dir(cfg: dict[str, Any], pilot_id: str, arm: str, run: int) -> Path:
    return _path(cfg, "runs") / pilot_id / arm / f"run{run}"


def run_question(
    question: dict[str, Any],
    arm: Arm,
    run: int,
    *,
    client: Any,
    cfg: dict[str, Any],
    pricing: dict[str, Any],
    pilot_id: str,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    qid = question["id"]
    out_dir = out_dir or run_dir(cfg, pilot_id, arm.name, run)
    trace_path = out_dir / f"{qid}.jsonl"
    shape = question["answer"]
    fields = parse_shape(shape)
    tools = [*arm.tool_specs(), FLAG_GAP, submit_answer_spec(shape)]
    system = [
        {"type": "text", "text": cfg["system_prompt"], "cache_control": {"type": "ephemeral"}}
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt(question)}]
    params = {
        "model": cfg["model"],
        "max_tokens": cfg["max_tokens"],
        "system": system,
        "tools": tools,
        "thinking": {"type": "adaptive", "display": cfg["thinking_display"]},
        "output_config": {"effort": cfg["effort"]},
        "cache_control": {"type": "ephemeral"},  # automatic breakpoint on the growing tail
    }
    st = RunState()
    trace = Trace(trace_path)
    trace.write(
        "start",
        question_id=qid,
        arm=arm.name,
        run=run,
        pilot_id=pilot_id,
        config_hash=config_hash(cfg),
        **{k: v for k, v in params.items() if k != "cache_control"},
    )
    t0 = time.monotonic()
    started = dt.datetime.now(dt.UTC).isoformat()
    turns, sent = 0, 0
    stop_reason, error = "max_turns", None
    try:
        while turns < cfg["max_turns"]:
            trace.write("request", turn=turns + 1, new_messages=_jsonable(messages[sent:]))
            sent = len(messages)
            with client.messages.stream(messages=messages, **params) as stream:
                resp = stream.get_final_message()
            turns += 1
            st.add_usage(resp.usage)
            trace.write("response", turn=turns, message=_jsonable(resp))
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason in ("refusal", "max_tokens"):
                stop_reason = resp.stop_reason
                error = f"model stopped with {resp.stop_reason}"
                break
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                content: list[dict[str, Any]] = [
                    {"type": "text", "text": "Finish by calling submit_answer with your answer."}
                ]
            else:
                content = [_run_tool(tu, arm, fields, st, trace, turns) for tu in tool_uses]
            if st.submitted:
                stop_reason = "submitted"
                break
            if cfg.get("last_turn_warning") and turns == cfg["max_turns"] - 1:
                content.append(
                    {
                        "type": "text",
                        "text": "Last turn: call submit_answer now with your best answer.",
                    }
                )
            messages.append({"role": "user", "content": content})
    except Exception as e:  # API or harness failure: record it, the pilot retries these
        stop_reason, error = "error", f"{type(e).__name__}: {e}"
        trace.write("error", error=error)
    finally:
        arm.close()
    record = {
        "question_id": qid,
        "arm": arm.name,
        "run": run,
        "pilot_id": pilot_id,
        "model": cfg["model"],
        "config_hash": config_hash(cfg),
        "answer": st.answer,
        "notes": st.notes,
        "flags": st.flags,
        "turns": turns,
        "tool_calls": st.tool_calls,
        "usage": st.usage,
        "cost_usd": cost_usd(st.usage, cfg["model"], pricing),
        "wall_s": round(time.monotonic() - t0, 2),
        "started_at": started,
        "stop_reason": stop_reason,
        "error": error,
        "trace": str(
            trace_path.relative_to(ROOT) if trace_path.is_relative_to(ROOT) else trace_path
        ),
    }
    trace.write("end", record=record)
    trace.close()
    (out_dir / f"{qid}.record.json").write_text(json.dumps(record, indent=1, default=str))
    return record


def _run_tool(
    tu: Any, arm: Arm, fields: list, st: RunState, trace: Trace, turn: int
) -> dict[str, Any]:
    st.tool_calls += 1
    args = tu.input if isinstance(tu.input, dict) else {}
    is_error = False
    try:
        if tu.name == "flag_gap":
            st.flags.append(
                {**{k: args.get(k) for k in FLAG_GAP["input_schema"]["required"]}, "turn": turn}
            )
            out = "Flag recorded."
        elif tu.name == "submit_answer":
            problems = validate_answer(args.get("answer"), fields)
            if problems:
                raise ToolError("Answer rejected: " + "; ".join(problems))
            st.answer, st.notes, st.submitted = args["answer"], args.get("notes"), True
            out = "Answer received."
        else:
            out = arm.call(tu.name, args)
    except ToolError as e:
        out, is_error = str(e), True
    except (KeyError, TypeError, ValueError) as e:
        out, is_error = f"Bad arguments for {tu.name}: {e}", True
    extra = {}
    if tu.name == "query_metrics" and getattr(arm, "last_compiled_sql", None):
        extra["compiled_sql"] = arm.last_compiled_sql
        arm.last_compiled_sql = None
    trace.write("tool", turn=turn, name=tu.name, input=args, output=out, is_error=is_error, **extra)
    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": tu.id, "content": out}
    if is_error:
        result["is_error"] = True
    return result
