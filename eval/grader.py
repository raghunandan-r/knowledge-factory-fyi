"""Gold answers from gold SQL, and grading of submitted answers against them.

Gold SQL convention (`eval/gold_sql/<qid>.sql`, run read-only against bronze):

- Scalar shapes, e.g. ``{records: int, usd: number}``: one row whose column names are the
  answer keys. Extra columns are ignored.
- ``{ranked: [{product: str, usd: number}], k: 10}``: rows in rank order, best first, with the
  item keys as columns and at least k rows.
- ``{table: [{program_year: int, usd: number}]}``: one row per answer row, item keys as columns.
  Rows are matched on their str columns (on the first column if none is str), in any order.
- ``{set: [str]}``: one column, one row per member.
- A str cell may be a DuckDB LIST of strings: any one of them is accepted (e.g. every name a
  company ID reported under). This holds for scalars, ranked items and table key columns.
- Mixed shapes, e.g. ``{usd: number, ranked: [...], k: 3}``, may split the file into parts with
  a line ``-- part: <key>`` before each statement. A part named after a list key returns that
  list's rows as above; a part named after a scalar key returns one row and fills every scalar
  key among its columns. A shape whose keys are all scalars needs no parts.

Comparison (PLAN.md §6): ints match exactly. Other numbers match to the cent (±0.01), or to
the dollar (±1) when gold is 1,000 or more, so rounding passes but a different method doesn't.
A question's optional ``tolerance: {<key or column>: <relative>}`` overrides this for answers
that are approximate by nature. Percents are numbers in 0-100 units. Strings compare after
casefolding, dropping punctuation, collapsing whitespace and stripping company suffixes (Inc,
LLC, Corp, ...). Sets
compare as sets; ranked answers need the top k names in order with their numbers in tolerance;
tables need the same rows. Every key must be right for the answer to be right.

Gold results are cached in `data/gold_results/<qid>.json` with the SQL's hash; `make gold`
recomputes stale or missing ones.
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import duckdb

from agents.shapes import SCALARS, Field, parse_shape

ROOT = Path(__file__).resolve().parent.parent
GOLD_SQL_DIR = ROOT / "eval" / "gold_sql"
GOLD_DIR = ROOT / "data" / "gold_results"
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
ABS_TOL, ABS_TOL_LARGE, LARGE = 0.01, 1.0, 1000
_EPS = 1e-9  # float noise: 40.57 - 40.56 is 0.0100000000000016

_PART = re.compile(r"^\s*--\s*part:\s*(\w+)\s*$", re.MULTILINE)


# ── gold ─────────────────────────────────────────────────────────────────────


def split_parts(sql: str) -> list[tuple[str | None, str]]:
    marks = list(_PART.finditer(sql))
    if not marks:
        return [(None, sql)]
    return [
        (m.group(1), sql[m.end() : marks[i + 1].start() if i + 1 < len(marks) else len(sql)])
        for i, m in enumerate(marks)
    ]


def _py(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.date | dt.datetime):
        return v.isoformat()
    if isinstance(v, list | tuple):
        return [_py(x) for x in v]
    return v


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[tuple]]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return cols, [tuple(_py(v) for v in r) for r in cur.fetchall()]


def _col(cols: list[str], name: str) -> int:
    for i, c in enumerate(cols):
        if c == name:
            return i
    for i, c in enumerate(cols):
        if c.lower() == name.lower():
            return i
    raise ValueError(f"gold SQL has no column {name!r} (columns: {cols})")


def _list_value(f: Field, cols: list[str], rows: list[tuple]) -> list[Any]:
    if f.kind == "set":
        return [r[0] for r in rows]
    idx = {c: _col(cols, c) for c, _ in f.item}
    out = [{c: r[i] for c, i in idx.items()} for r in rows]
    if f.kind == "ranked" and len(out) < (f.k or 0):
        raise ValueError(f"ranked gold has {len(out)} rows, needs at least k={f.k}")
    return out


def gold_from_sql(
    sql: str, shape: dict[str, Any], con: duckdb.DuckDBPyConnection
) -> dict[str, Any]:
    fields = parse_shape(shape)
    by_key = {f.key: f for f in fields}
    scalars = [f for f in fields if f.kind in SCALARS]
    lists = [f for f in fields if f.is_list]
    gold: dict[str, Any] = {}
    parts = split_parts(sql)
    if parts[0][0] is None:
        if scalars and lists or len(lists) > 1:
            raise ValueError("mixed shape: split the gold SQL with '-- part: <key>' lines")
        parts = [(lists[0].key if lists else scalars[0].key, sql)]
    for key, part_sql in parts:
        f = by_key.get(key)
        if f is None:
            raise ValueError(f"part {key!r} is not an answer key")
        cols, rows = _rows(con, part_sql)
        if f.is_list:
            gold[key] = _list_value(f, cols, rows)
            continue
        if len(rows) != 1:
            raise ValueError(f"scalar part {key!r} must return one row, got {len(rows)}")
        _col(cols, key)
        for s in scalars:
            if any(c.lower() == s.key.lower() for c in cols):
                gold[s.key] = rows[0][_col(cols, s.key)]
    missing = [f.key for f in fields if f.key not in gold]
    if missing:
        raise ValueError(f"gold SQL left keys without a value: {missing}")
    return gold


def sql_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gold_path(qid: str, gold_dir: Path | None = None) -> Path:
    return (gold_dir or GOLD_DIR) / f"{qid}.json"


def load_gold(
    qid: str, *, gold_dir: Path | None = None, sql_dir: Path | None = None
) -> dict[str, Any]:
    """The cached gold answer; raises if it's missing or older than its SQL."""
    p, sql = gold_path(qid, gold_dir), (sql_dir or GOLD_SQL_DIR) / f"{qid}.sql"
    if not p.exists():
        raise FileNotFoundError(f"no gold result for {qid}; run `make gold`")
    rec = json.loads(p.read_text())
    if sql.exists() and rec["sql_sha256"] != sql_hash(sql):
        raise ValueError(f"gold result for {qid} is stale; run `make gold`")
    return rec


def compute_gold(
    qids: list[str],
    questions: dict[str, dict[str, Any]],
    *,
    force: bool = False,
    warehouse: Path | None = None,
    gold_dir: Path | None = None,
    sql_dir: Path | None = None,
) -> dict[str, str]:
    warehouse, gold_dir, sql_dir = (
        warehouse or WAREHOUSE,
        gold_dir or GOLD_DIR,
        sql_dir or GOLD_SQL_DIR,
    )
    gold_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    con = None
    for qid in qids:
        sql_path = sql_dir / f"{qid}.sql"
        if not sql_path.exists():
            status[qid] = "no gold SQL"
            continue
        h = sql_hash(sql_path)
        out = gold_path(qid, gold_dir)
        if not force and out.exists() and json.loads(out.read_text()).get("sql_sha256") == h:
            status[qid] = "up to date"
            continue
        if con is None:
            con = duckdb.connect(str(warehouse), read_only=True)
        t0 = time.monotonic()
        try:
            shape = questions[qid]["answer"]
            answer = gold_from_sql(sql_path.read_text(), shape, con)
        except Exception as e:
            status[qid] = f"ERROR {type(e).__name__}: {e}"
            continue
        rec = {
            "question_id": qid,
            "shape": shape,
            "sql_sha256": h,
            "computed_at": dt.datetime.now(dt.UTC).isoformat(),
            "seconds": round(time.monotonic() - t0, 1),
            "answer": answer,
        }
        out.write_text(json.dumps(rec, indent=1))
        status[qid] = f"computed in {rec['seconds']} s"
    if con is not None:
        con.close()
    return status


# ── comparison ───────────────────────────────────────────────────────────────

_SUFFIXES = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "ltd", "limited",
    "lp", "llp", "plc", "gmbh", "ag", "sa", "nv", "bv",
}  # fmt: skip


def norm_str(s: Any) -> str:
    s = str(s).casefold().replace("&", " and ")
    s = re.sub(r"[.'’]", "", s)
    tokens = re.sub(r"[^\w]+|_", " ", s).split()
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").replace("%", "").strip())
    except ValueError:
        return None


def compare_scalar(kind: str, got: Any, exp: Any, rel_tol: float | None = None) -> tuple[bool, str]:
    if kind == "str":
        alts = exp if isinstance(exp, list) else [exp]
        if got is None:
            return False, "missing"
        if norm_str(got) in {norm_str(a) for a in alts if a is not None}:
            return True, "match"
        shown = alts[0] if len(alts) == 1 else f"one of {alts[:5]}"
        return False, f"expected {shown!r}, got {got!r}"
    e, g = _num(exp), _num(got)
    if e is None:
        return False, f"gold value is {exp!r}, not a number"
    if g is None:
        return False, f"expected {exp}, got {got!r}"
    diff = abs(g - e)
    if e == 0:
        return (g == 0), ("match" if g == 0 else f"expected exactly 0, got {got}")
    if rel_tol is not None:
        ok = diff <= rel_tol * abs(e) + _EPS
    elif kind == "int":
        ok = g == e
    else:
        ok = diff <= (ABS_TOL_LARGE if abs(e) >= LARGE else ABS_TOL) + _EPS
    if ok:
        return True, "match" if diff == 0 else f"within {diff:.4g}"
    return False, f"expected {exp}, got {got} (off by {diff / abs(e):.2%})"


def _compare_item(
    f: Field, got: dict[str, Any], exp: dict[str, Any], tol: dict[str, float]
) -> list[str]:
    errs = []
    for c, t in f.item:
        ok, why = compare_scalar(t, got.get(c), exp.get(c), tol.get(c))
        if not ok:
            errs.append(f"{c}: {why}")
    return errs


def _key_match(f: Field, got: dict[str, Any], exp: dict[str, Any]) -> bool:
    """Keys match exactly, even under a per-question tolerance: 2023 never passes for 2024."""
    types = dict(f.item)
    for c in f.key_columns:
        if types[c] == "str":
            if not compare_scalar("str", got.get(c), exp.get(c))[0]:
                return False
        elif _num(got.get(c)) is None or _num(got.get(c)) != _num(exp.get(c)):
            return False
    return True


def compare_field(
    f: Field, got: Any, exp: Any, tol: dict[str, float] | None = None
) -> tuple[bool, str]:
    tol = tol or {}
    if f.kind in SCALARS:
        return compare_scalar(f.kind, got, exp, tol.get(f.key))
    if not isinstance(got, list):
        return False, f"expected a list, got {type(got).__name__}"
    if f.kind == "set":
        g, e = {norm_str(x) for x in got}, {norm_str(x) for x in exp}
        if g == e:
            return True, f"same {len(e)} members"
        return False, f"missing {sorted(e - g)[:10]}, extra {sorted(g - e)[:10]}"
    if f.kind == "ranked":
        k = f.k or len(exp)
        if len(got) < k:
            return False, f"expected {k} items, got {len(got)}"
        errs = []
        for i in range(k):
            gi = got[i] if isinstance(got[i], dict) else {}
            errs += [f"#{i + 1} {e}" for e in _compare_item(f, gi, exp[i], tol)]
        return (not errs), (f"top {k} match" if not errs else "; ".join(errs))
    # table
    rows = [r for r in got if isinstance(r, dict)]
    if len(rows) != len(exp):
        return False, f"expected {len(exp)} rows, got {len(got)}"
    errs, used = [], set()
    for e in exp:
        j = next((j for j, r in enumerate(rows) if j not in used and _key_match(f, r, e)), None)
        label = {c: e.get(c) for c in f.key_columns}
        if j is None:
            errs.append(f"no row for {label}")
            continue
        used.add(j)
        errs += [f"{label} {x}" for x in _compare_item(f, rows[j], e, tol)]
    return (not errs), (f"all {len(exp)} rows match" if not errs else "; ".join(errs))


def grade(
    answer: Any,
    gold: dict[str, Any],
    shape: dict[str, Any],
    tolerance: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Verdict with a reason per answer key; right only if every key is right."""
    fields = parse_shape(shape)
    if not isinstance(answer, dict):
        return {
            "correct": False,
            "fields": {f.key: {"correct": False, "reason": "no answer submitted"} for f in fields},
        }
    out = {}
    for f in fields:
        if f.key not in answer:
            out[f.key] = {"correct": False, "reason": "missing"}
            continue
        ok, why = compare_field(f, answer[f.key], gold[f.key], tolerance)
        out[f.key] = {"correct": ok, "reason": why}
    return {"correct": all(v["correct"] for v in out.values()), "fields": out}


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    from agents.pilot import load_questions

    ap = argparse.ArgumentParser(description="Compute gold results from eval/gold_sql.")
    ap.add_argument("qids", nargs="*", help="question ids (default: every gold SQL file)")
    ap.add_argument("--force", action="store_true", help="recompute even if up to date")
    args = ap.parse_args(argv)
    qids = args.qids or sorted(p.stem for p in GOLD_SQL_DIR.glob("*.sql"))
    status = compute_gold(qids, load_questions(), force=args.force)
    for qid, s in status.items():
        print(f"{qid}  {s}")
    return 1 if any(s.startswith("ERROR") for s in status.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
