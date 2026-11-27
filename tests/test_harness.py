"""Harness tests with a scripted fake Anthropic client; no network, no real warehouse."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import duckdb
import pytest
import yaml
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from agents import harness, pilot
from agents.harness import LayerArm, RawArm, _jsonable, load_config, load_pricing, run_question
from agents.tools import DocsTool, Limits, MetricLayer, SqlTool, ToolError

FIXTURE = Path(__file__).parent / "fixtures" / "mf_project"
MANIFEST = FIXTURE / "semantic_manifest.json"

QUESTION = {
    "id": "q900",
    "question": "How many general payment records are there?",
    "answer": {"records": "int", "usd": "number"},
    "expert_note": "SECRET-EXPERT-NOTE",
    "verify": ["SECRET-VERIFY"],
    "traps": ["SECRETTRAP"],
    "correction": "SECRET-CORRECTION",
}


# ── fake client ──────────────────────────────────────────────────────────────

_ids = iter(range(10**6))


def tool(tool_name: str, /, **inp) -> ToolUseBlock:
    return ToolUseBlock(id=f"toolu_{next(_ids)}", type="tool_use", name=tool_name, input=inp)


def msg(*blocks, stop: str = "tool_use", usage=(100, 50, 10, 20)) -> Message:
    i, o, cr, cw = usage
    return Message(
        id=f"msg_{next(_ids)}",
        type="message",
        role="assistant",
        model="claude-opus-5-5",
        content=list(blocks),
        stop_reason=stop,
        stop_sequence=None,
        usage=Usage(
            input_tokens=i,
            output_tokens=o,
            cache_read_input_tokens=cr,
            cache_creation_input_tokens=cw,
        ),
    )


class _Stream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


class FakeClient:
    """Scripted `client.messages.stream(...)`. Each entry is a Message, an exception, or a
    callable taking the request kwargs (to answer based on what the harness sent)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(json.loads(json.dumps(_jsonable(kw), default=str)))
        nxt = self.script.pop(0) if self.script else msg(tool("sql", query="select 1"))
        return _Stream(nxt(kw) if callable(nxt) else nxt)


def tool_results(call: dict) -> list[dict]:
    return [
        b
        for b in call["messages"][-1]["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    p = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(p))
    con.execute("CREATE SCHEMA bronze; CREATE SCHEMA meta")
    con.execute(
        "CREATE TABLE bronze.op_general AS SELECT * FROM (VALUES ('1', '100.00'), ('2', '250.50'),"
        " ('3', '49.50')) t(record_id, total_amount_of_payment_usdollars)"
    )
    con.execute("CREATE TABLE bronze.op_national_summary AS SELECT '2024' AS program_year")
    con.execute("CREATE TABLE meta.sync_runs AS SELECT 'secret' AS run_id")
    con.close()
    return p


@pytest.fixture
def layer_db(tmp_path: Path) -> Path:
    p = tmp_path / "layers" / "toy.duckdb"
    p.parent.mkdir()
    con = duckdb.connect(str(p))
    con.execute("CREATE SCHEMA gold")
    for model in ("payments", "all_days"):
        con.execute(
            f"CREATE TABLE gold.{model} AS " + (FIXTURE / "models" / f"{model}.sql").read_text()
        )
    con.close()
    return p


@pytest.fixture
def cfg(tmp_path: Path, warehouse: Path) -> dict:
    c = load_config()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "op_dictionary.md").write_text(
        "# Open Payments dictionary\n\nTotal_Amount_of_Payment_USDollars: the record's total.\n"
        "Number_of_Payments_Included_in_Total_Amount: payments bundled in one record.\n"
    )
    c["paths"] = {
        **c["paths"],
        "warehouse": str(warehouse),
        "docs": str(docs),
        "layers": str(tmp_path / "layers"),
        "runs": str(tmp_path / "runs"),
        "dbt_manifest": str(MANIFEST),
    }
    c["duckdb"] = {"threads": 1, "memory_limit": "500MB", "temp_directory": str(tmp_path / "spill")}
    return c


def run(cfg, script, arm=None, question=QUESTION):
    client = FakeClient(script)
    rec = run_question(
        question,
        arm or RawArm(cfg),
        1,
        client=client,
        cfg=cfg,
        pricing=load_pricing(),
        pilot_id="t",
    )
    return rec, client


GOOD = {"answer": {"records": 3, "usd": 400.0}, "notes": "records = rows"}


# ── the loop ─────────────────────────────────────────────────────────────────


def test_sql_flag_and_submit(cfg):
    rec, client = run(
        cfg,
        [
            msg(
                TextBlock(type="text", text="Counting."),
                tool("sql", query="select count(*) as n from op_general"),
                tool("flag_gap", question_id="q900", what_was_missing="units", evidence="none"),
            ),
            msg(tool("submit_answer", **GOOD)),
        ],
    )
    assert rec["stop_reason"] == "submitted"
    assert rec["answer"] == GOOD["answer"] and rec["notes"] == "records = rows"
    assert rec["turns"] == 2 and rec["tool_calls"] == 3
    assert rec["flags"] == [
        {"question_id": "q900", "what_was_missing": "units", "evidence": "none", "turn": 1}
    ]
    assert rec["usage"] == {
        "input_tokens": 200,
        "output_tokens": 100,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 40,
    }
    expected = (200 * 4 + 100 * 20 + 20 * 0.2 + 40 * 5) / 1e6
    assert rec["cost_usd"] == pytest.approx(expected)
    # both tool results go back in one user message
    results = tool_results(client.calls[1])
    assert [r.get("is_error", False) for r in results] == [False, False]
    assert "n\n3" in results[0]["content"]
    trace = [
        json.loads(line) for line in Path(cfg["paths"]["runs"], "t/raw/run1/q900.jsonl").open()
    ]
    assert [t["type"] for t in trace][:3] == ["start", "request", "response"]
    assert sum(t["type"] == "tool" for t in trace) == 3
    assert trace[-1]["type"] == "end"
    assert json.loads(Path(cfg["paths"]["runs"], "t/raw/run1/q900.record.json").read_text()) == rec


def test_request_settings_and_no_leak(cfg):
    rec, client = run(cfg, [msg(tool("submit_answer", **GOOD))])
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": cfg["effort"]}
    assert call["thinking"]["type"] == "adaptive"
    assert "tool_choice" not in call  # forced tool use is a 400 on Opus 5.5
    assert call["system"][0]["text"] == cfg["system_prompt"]
    names = [t["name"] for t in call["tools"]]
    assert names == ["sql", "list_docs", "search_docs", "read_doc", "flag_gap", "submit_answer"]
    submit = call["tools"][-1]
    assert submit["strict"] is True
    assert (
        submit["input_schema"]["properties"]["answer"]["properties"]["records"]["type"] == "integer"
    )
    blob = json.dumps(client.calls)
    for secret in (
        "SECRET-EXPERT-NOTE",
        "SECRET-VERIFY",
        "SECRETTRAP",
        "SECRET-CORRECTION",
        "gold",
    ):
        assert secret not in blob
    assert QUESTION["question"] in call["messages"][0]["content"]
    assert "{records: int, usd: number}" in call["messages"][0]["content"]


def test_max_turns_stop_with_last_turn_warning(cfg):
    cfg["max_turns"] = 3
    rec, client = run(cfg, [msg(tool("sql", query="select 1")) for _ in range(5)])
    assert rec["stop_reason"] == "max_turns" and rec["turns"] == 3 and rec["answer"] is None
    last_user = client.calls[-1]["messages"][-1]["content"]
    assert any(b.get("type") == "text" and "Last turn" in b["text"] for b in last_user)
    assert not any(b.get("type") == "text" for b in client.calls[1]["messages"][-1]["content"])


def test_tool_errors_are_returned_not_raised(cfg):
    rec, client = run(
        cfg,
        [
            msg(
                tool("sql", query="select * from meta.sync_runs"),
                tool("sql", query="select nope from op_general"),
                tool("read_doc", name="missing"),
                tool("no_such_tool"),
            ),
            msg(tool("submit_answer", answer={"records": "three"}, notes="")),
            msg(tool("submit_answer", **GOOD)),
        ],
    )
    assert rec["stop_reason"] == "submitted"
    r1 = tool_results(client.calls[1])
    assert all(r["is_error"] for r in r1)
    assert "Access denied" in r1[0]["content"]
    assert "nope" in r1[1]["content"]
    r2 = tool_results(client.calls[2])
    assert r2[0]["is_error"] and "Answer rejected" in r2[0]["content"]


def test_text_only_reply_is_nudged(cfg):
    rec, client = run(
        cfg,
        [
            msg(TextBlock(type="text", text="It's 3."), stop="end_turn"),
            msg(tool("submit_answer", **GOOD)),
        ],
    )
    assert rec["stop_reason"] == "submitted" and rec["turns"] == 2
    assert "submit_answer" in client.calls[1]["messages"][-1]["content"][0]["text"]


def test_refusal_and_api_error_are_recorded(cfg):
    rec, _ = run(cfg, [msg(stop="refusal")])
    assert rec["stop_reason"] == "refusal" and rec["answer"] is None
    rec, _ = run(cfg, [RuntimeError("boom")])
    assert rec["stop_reason"] == "error" and "boom" in rec["error"]


# ── raw arm: bronze only ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "select * from meta.sync_runs",
        "select * from warehouse.meta.sync_runs",
        "select * from sync_runs",
        "select * from query_table('meta.sync_runs')",
        "select * from query('select * from warehouse.meta.sync_runs')",
        "select * from read_csv('/etc/passwd')",
        "attach '/tmp/x.duckdb' as x",
        "create table bronze.x as select 1",
        "delete from bronze.op_general",
        "set enable_external_access = true",
        "select 1; select 2",
    ],
)
def test_raw_arm_blocks(cfg, sql):
    arm = RawArm(cfg)
    with pytest.raises(ToolError):
        arm.call("sql", {"query": sql})
    arm.close()


def test_raw_arm_reads_bronze(cfg):
    arm = RawArm(cfg)
    assert "3" in arm.call("sql", {"query": "select count(*) from bronze.op_general"})
    assert "op_general" in arm.call("sql", {"query": "show tables"})
    assert "total_amount" in arm.call("sql", {"query": "describe op_general"})
    assert "2024" in arm.call(
        "sql", {"query": "with x as (select * from op_national_summary) select * from x"}
    )
    arm.close()


def test_docs_tools(cfg):
    docs = DocsTool(cfg["paths"]["docs"])
    assert "op_dictionary | 4 | Open Payments dictionary" in docs.list_docs()
    hits = docs.search_docs("payments bundled")
    assert hits.startswith("op_dictionary:4:")
    assert "most terms" in docs.search_docs("bundled zebra")
    assert "lines 2-3 of 4" in docs.read_doc("op_dictionary", offset=2, limit=2)
    assert (
        DocsTool(Path(cfg["paths"]["docs"]) / "nope").list_docs() == "No documents are available."
    )


def test_search_docs_finds_names_wrapped_across_lines(tmp_path):
    (tmp_path / "d.md").write_text("Number_of_Payments_Included_in_To\ntal_Amount_of_Payment\n")
    hit = DocsTool(tmp_path).search_docs("number_of_payments_included_in_total_amount")
    assert hit.startswith("d:1:")


def test_truncation_and_timeout(warehouse):
    db = SqlTool(
        warehouse,
        "warehouse",
        default_schema="bronze",
        limits=Limits(max_rows=5, max_chars=200, timeout_s=0.3),
    )
    out = db.run("select range from range(50)")
    assert "showing the first 5 rows" in out
    assert "[truncated: output was" in db.run("select repeat('x', 100) as s from range(4)")
    with pytest.raises(ToolError, match="cancelled"):
        db.run("select sum(a.range * b.range) from range(100000) a, range(100000) b")
    assert "42" in db.run("select 42")  # connection survives the interrupt
    db.close()


# ── layer arm: MetricFlow compile, then execute on a read-only connection ────


def test_metricflow_compile_then_execute(cfg, layer_db):
    arm = LayerArm(cfg, "toy", db_path=layer_db, manifest_path=MANIFEST)
    listing = arm.call("list_metrics", {})
    assert "total_usd | simple" in listing and "usd_per_record | ratio" in listing
    explained = json.loads(arm.call("explain_metric", {"name": "records"}))
    assert explained["metric"]["description"].startswith("Number of payment records")
    assert explained["measures"][0]["table"] == '"toy"."gold"."payments"'
    assert {"payment__recipient_type", "metric_time__day"} <= {
        g["name"] for g in explained["group_by"]
    }
    out = arm.call(
        "query_metrics",
        {
            "metrics": ["total_usd", "records", "usd_per_record"],
            "group_by": ["payment__recipient_type"],
            "where": ["{{ Dimension('payment__program_year') }} = 2024"],
            "order_by": ["-total_usd"],
            "show_sql": True,
        },
    )
    lines = out.splitlines()
    assert lines[0] == "payment__recipient_type | total_usd | records | usd_per_record"
    assert lines[1].startswith("Physician | 350.00 | 2 | 175.0")
    assert lines[2].startswith("Nurse Practitioner | 50.00 | 1 | 50.0")
    assert '"toy"."gold"."payments"' in out
    with pytest.raises(ToolError, match="MetricFlow could not compile"):
        arm.call("query_metrics", {"metrics": ["nope"]})
    with pytest.raises(ToolError, match="No metric"):
        arm.call("explain_metric", {"name": "nope"})
    arm.close()


def test_layer_sql_cannot_see_bronze(cfg, layer_db, warehouse):
    arm = LayerArm(cfg, "toy", db_path=layer_db, manifest_path=MANIFEST)
    assert "4" in arm.call("sql", {"query": "select count(*) from payments"})
    for sql in (
        "select * from bronze.op_general",
        "select * from warehouse.bronze.op_general",
        f"attach '{warehouse}' as w",
        f"select * from '{warehouse}'",
    ):
        with pytest.raises(ToolError):
            arm.call("sql", {"query": sql})
    arm.close()


def test_layer_runs_in_parallel(cfg, layer_db):
    errors: list[Exception] = []

    def go():
        try:
            arm = LayerArm(cfg, "toy", db_path=layer_db, manifest_path=MANIFEST)
            for _ in range(5):
                assert "1400.00" in arm.call("query_metrics", {"metrics": ["total_usd"]})
            arm.close()
        except Exception as e:  # collected for the main thread
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_layer_arm_end_to_end(cfg, layer_db):
    q = {**QUESTION, "answer": {"usd": "number"}}
    rec, client = run(
        cfg,
        [
            msg(tool("query_metrics", metrics=["total_usd"])),
            msg(tool("submit_answer", answer={"usd": 1400.0}, notes="")),
        ],
        arm=LayerArm(cfg, "toy", db_path=layer_db, manifest_path=MANIFEST),
        question=q,
    )
    assert rec["stop_reason"] == "submitted" and rec["arm"] == "toy"
    assert [t["name"] for t in client.calls[0]["tools"]] == [
        "list_metrics", "explain_metric", "query_metrics", "sql", "flag_gap", "submit_answer",
    ]  # fmt: skip
    trace = [json.loads(line) for line in Path(rec["trace"]).open()]
    tools = [t for t in trace if t["type"] == "tool"]
    assert tools and "compiled_sql" in tools[0]


@pytest.mark.skipif(
    shutil.which("dbt") is None and not Path(".venv/bin/dbt").exists(), reason="dbt not installed"
)
def test_fixture_manifest_is_reproducible(tmp_path):
    proj = tmp_path / "proj"
    shutil.copytree(
        FIXTURE, proj, ignore=shutil.ignore_patterns("target", "logs", "semantic_manifest.json")
    )
    dbt = shutil.which("dbt") or str(Path(".venv/bin/dbt").resolve())
    subprocess.run(  # noqa: S603 - our own fixture project
        [dbt, "parse", "--project-dir", str(proj), "--profiles-dir", str(proj), "--quiet"],
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "KF_FIXTURE_DB": str(tmp_path / "toy.duckdb"),
            "HOME": str(tmp_path),
            "DO_NOT_TRACK": "1",  # no anonymous usage events: these tests stay offline
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        },
    )
    fresh = json.loads((proj / "target" / "semantic_manifest.json").read_text())
    kept = json.loads(MANIFEST.read_text())
    assert [m["name"] for m in fresh["metrics"]] == [m["name"] for m in kept["metrics"]]
    assert MetricLayer(proj / "target" / "semantic_manifest.json").databases == {"toy"}


# ── pilot ────────────────────────────────────────────────────────────────────


def test_pilot_dry_run_and_resume(cfg, tmp_path, monkeypatch, capsys):
    pilot_file = tmp_path / "pilot.yaml"
    pilot_file.write_text(yaml.safe_dump({"pilot_id": "fx", "questions": ["q900"]}))
    monkeypatch.setattr(pilot, "load_config", lambda: cfg)
    monkeypatch.setattr(pilot, "load_questions", lambda: {"q900": QUESTION})
    assert (
        pilot.main(["run", "--arm", "raw", "--runs", "2", "--pilot", str(pilot_file), "--dry-run"])
        == 0
    )
    out = capsys.readouterr().out
    assert "2 to run" in out and "estimated cost: $" in out and "would run q900 run2" in out
    # a finished record is skipped, an errored one is retried
    p = pilot.record_path(cfg, "fx", "raw", 1, "q900")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"stop_reason": "submitted"}))
    pilot.main(["run", "--arm", "raw", "--runs", "2", "--pilot", str(pilot_file), "--dry-run"])
    assert "1 to run" in capsys.readouterr().out
    p.write_text(json.dumps({"stop_reason": "error"}))
    pilot.main(["run", "--arm", "raw", "--runs", "2", "--pilot", str(pilot_file), "--dry-run"])
    assert "2 to run" in capsys.readouterr().out


def test_pilot_report(cfg, tmp_path, monkeypatch, capsys):
    import eval.grader as grader

    monkeypatch.setattr(grader, "GOLD_DIR", tmp_path / "gold")
    monkeypatch.setattr(grader, "GOLD_SQL_DIR", tmp_path / "gold_sql")
    (tmp_path / "gold").mkdir()
    (tmp_path / "gold" / "q900.json").write_text(
        json.dumps({"sql_sha256": "x", "answer": {"records": 3, "usd": 400.0}})
    )
    for k, usd in enumerate([400.0, 400.004, 500.0], start=1):
        run_question(
            QUESTION,
            RawArm(cfg),
            k,
            client=FakeClient(
                [msg(tool("submit_answer", answer={"records": 3, "usd": usd}, notes=""))]
            ),
            cfg=cfg,
            pricing=load_pricing(),
            pilot_id="fx",
        )
    rep = pilot.build_report(cfg, "fx", ["q900"], {"q900": QUESTION})
    assert rep["by_question"]["q900"]["arms"]["raw"] == {"runs": [True, True, False], "right": True}
    arm = rep["by_arm"]["raw"]
    assert arm["right_2_of_3"] == 1 and arm["runs_correct"] == 2 and arm["wrong_by_trap"] == {}
    assert arm["usage"]["input_tokens"] == 300
    pilot.print_summary(rep)
    assert "✓✓✗ R" in capsys.readouterr().out


def test_cost_estimate_is_positive(cfg):
    per_run, basis = pilot.estimate_cost_per_run(cfg, load_pricing(), [])
    assert 0.1 < per_run < 10 and "planning" in basis
    assert (
        pilot.estimate_cost_per_run(cfg, load_pricing(), [{"cost_usd": 2.0, "turns": 3}])[0] == 2.0
    )


def test_real_pilot_questions_build_tools():
    qs = pilot.load_questions()
    for qid in pilot.load_pilot()[1]:
        spec = harness.submit_answer_spec(qs[qid]["answer"])
        assert spec["input_schema"]["properties"]["answer"]["required"]


def test_smoke_model_is_kept_apart_from_the_pilot(cfg, monkeypatch):
    from agents.harness import load_config as fresh_config
    from agents.pilot import resolve_pilot_id

    monkeypatch.delenv("KF_SMOKE_MODEL", raising=False)
    assert not fresh_config().get("smoke")
    monkeypatch.setenv("KF_SMOKE_MODEL", "particle-ai/particle-1:free")
    smoke = fresh_config()
    assert smoke["model"] == "particle-ai/particle-1:free" and smoke["smoke"]
    assert resolve_pilot_id(smoke, "pilot-v1") == "smoke-particle-ai-particle-1-free-pilot-v1"

    cfg.update(model=smoke["model"], smoke=True)
    rec, client = run(cfg, [msg(tool("submit_answer", **GOOD))])
    call = client.calls[0]
    assert call["model"] == "particle-ai/particle-1:free"
    for claude_only in ("thinking", "output_config", "cache_control"):
        assert claude_only not in call
    assert "cache_control" not in call["system"][0]
    assert rec["stop_reason"] == "submitted" and rec["cost_usd"] == 0.0


def test_layer_describe_and_show_tables_carry_descriptions(cfg, layer_db, tmp_path):
    dbt_manifest = tmp_path / "manifest.json"
    dbt_manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.toy.payments": {
                        "resource_type": "model",
                        "name": "payments",
                        "schema": "gold",
                        "alias": "payments",
                        "description": "Toy payments, one row per record.",
                        "columns": {"amount_usd": {"name": "amount_usd", "description": "USD."}},
                    }
                }
            }
        )
    )
    arm = LayerArm(
        cfg, "toy", db_path=layer_db, manifest_path=MANIFEST, dbt_manifest_path=dbt_manifest
    )
    out = arm.call("sql", {"query": "DESCRIBE payments"})
    head, cols, *rows = out.splitlines()
    assert head == (
        "-- gold.payments: Toy payments, one row per record.; "
        "Semantic model payments: One row per toy payment record."
    )
    assert cols.endswith("| description")
    by_col = {r.split(" | ")[0]: r.split(" | ")[-1] for r in rows}
    assert by_col["amount_usd"] == "USD.; measure amount_usd (sum): Sum of payment amounts in USD."
    assert by_col["recipient_type"].startswith("dimension recipient_type: Physician")
    assert (
        "entity payment" in by_col["payment_id"]
        and "measure payment_records" in by_col["payment_id"]
    )
    shown = arm.call("sql", {"query": "show all tables"})
    assert "Semantic model payments" in shown and shown.splitlines()[0].endswith("description")
    assert "Semantic model payments" in arm.call("sql", {"query": "SHOW TABLES;"})
    assert "description" not in arm.call("sql", {"query": "select 1 as x"})
    arm.close()
    # without a dbt manifest the semantic YAML alone still describes the columns
    arm = LayerArm(
        cfg, "toy", db_path=layer_db, manifest_path=MANIFEST, dbt_manifest_path=tmp_path / "none"
    )
    assert "measure amount_usd" in arm.call("sql", {"query": "describe gold.payments"})
    arm.close()
