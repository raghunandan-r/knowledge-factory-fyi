"""Static checks on the eval files. No data needed."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"


def drafts() -> dict[str, dict]:
    qs = yaml.safe_load((EVAL / "questions" / "draft.yaml").read_text())["questions"]
    return {q["id"]: q for q in qs}


def test_pilot_questions_are_dev_have_gold_and_cover_every_trap():
    pilot = yaml.safe_load((EVAL / "pilot.yaml").read_text())["questions"]
    qs = drafts()
    assert len(pilot) == len(set(pilot)) == 20
    for qid in pilot:
        q = qs[qid]
        assert q["split"] == "dev", qid  # held-out drafts never run before the freeze
        assert "REPUB" not in q["traps"], qid  # one release loaded: REPUB can't fail
        assert (EVAL / "gold_sql" / f"{qid}.sql").exists(), qid
        assert not any(s.startswith("seed:") for s in q["sources"]), qid
    primary = {qs[qid]["traps"][0] for qid in pilot}
    assert primary >= {"GRAIN", "WIDE", "RTYPE", "FILES", "ENTITY", "TIME", "TAXON", "SUPPR"}


def test_gold_sql_reads_bronze_only():
    for f in (EVAL / "gold_sql").glob("*.sql"):
        sql = f.read_text().lower()
        for schema in ("silver.", "gold.", "wh."):
            assert schema not in sql, f"{f.name} reads {schema}"
