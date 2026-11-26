"""Grader and answer-shape tests: every shape, tolerance edges, gold SQL conventions."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
import yaml

from agents.shapes import answer_schema, parse_shape, validate_answer
from eval import grader
from eval.grader import compare_scalar, gold_from_sql, grade, norm_str, split_parts

RANKED = {"ranked": [{"company": "str", "usd": "number"}], "k": 3}
TABLE = {"table": [{"program_year": "int", "usd": "number"}]}
TABLE_STR = {"table": [{"group": "str", "prescribers": "int", "avg_claims": "number"}]}
MIXED = {"usd": "number", "ranked": [{"country": "str", "usd": "number"}], "k": 2}


# ── shapes ───────────────────────────────────────────────────────────────────


def test_every_draft_shape_parses_and_has_a_schema():
    root = Path(__file__).resolve().parent.parent / "eval" / "questions"
    for p in root.glob("*.yaml"):
        if p.name.startswith("leaks"):
            continue
        for q in yaml.safe_load(p.read_text())["questions"]:
            schema = answer_schema(parse_shape(q["answer"]))
            assert schema["required"] and schema["additionalProperties"] is False, q["id"]


def test_shape_kinds():
    fields = {f.key: f for f in parse_shape(MIXED)}
    assert fields["usd"].kind == "number"
    assert fields["ranked"].kind == "ranked" and fields["ranked"].k == 2
    assert "k" not in fields
    assert parse_shape({"set": ["str"]})[0].kind == "set"
    assert parse_shape(TABLE_STR)[0].key_columns == ("group",)
    assert parse_shape(TABLE)[0].key_columns == ("program_year",)
    with pytest.raises(ValueError):
        parse_shape({"ranked": [{"a": "str"}]})  # no k
    with pytest.raises(ValueError):
        parse_shape({"x": "float"})


def test_schema_details():
    s = answer_schema(parse_shape({"percent": "number", "name": "str", "n": "int"}))
    assert "0-100" in s["properties"]["percent"]["description"]
    assert "exactly as" in s["properties"]["name"]["description"].lower()
    assert s["properties"]["n"]["type"] == "integer"
    r = answer_schema(parse_shape(RANKED))["properties"]["ranked"]
    assert "Exactly 3 items" in r["description"]
    assert r["items"]["additionalProperties"] is False


def test_validate_answer():
    f = parse_shape(RANKED)
    ok = {
        "ranked": [
            {"company": "A", "usd": 1},
            {"company": "B", "usd": 2},
            {"company": "C", "usd": 3.5},
        ]
    }
    assert validate_answer(ok, f) == []
    assert "exactly 3" in validate_answer({"ranked": ok["ranked"][:2]}, f)[0]
    assert validate_answer({"ranked": ok["ranked"], "x": 1}, f) == ["unexpected keys ['x']"]
    assert validate_answer({"n": True}, parse_shape({"n": "int"}))  # bools aren't ints
    assert validate_answer({"n": 3.0}, parse_shape({"n": "int"})) == []
    assert validate_answer(None, f) == ["answer must be an object"]


# ── comparison rules ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("got", "exp", "ok"),
    [
        (100.5, 100.0, True),  # exactly 0.5%
        (99.5, 100.0, True),
        (100.51, 100.0, False),
        (99.49, 100.0, False),
        (0, 0, True),
        (0.0001, 0, False),  # a gold zero needs an exact zero
        ("1,234.5", 1234.5, True),  # stray formatting is forgiven
        ("$12", 12, True),
        (True, 1, False),
        ("abc", 1, False),
        (12.3, 12.3, True),
        (0.123, 12.3, False),  # percents are 0-100, never fractions
    ],
)
def test_number_tolerance(got, exp, ok):
    assert compare_scalar("number", got, exp)[0] is ok


def test_ints_use_the_same_tolerance():
    assert compare_scalar("int", 1004, 1000)[0]
    assert not compare_scalar("int", 1006, 1000)[0]


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Novo Nordisk Inc", "NOVO NORDISK, INC."),
        ("AbbVie Inc.", "abbvie"),
        ("Johnson & Johnson", "Johnson and Johnson"),
        ("Medtronic USA, Inc.", "Medtronic Usa"),
        ("Stryker Corporation", "Stryker Corp."),
        ("Smith  &  Nephew,  L.L.C.", "smith and nephew"),
        ("OZEMPIC", "Ozempic"),
        ("Hospital Of The Univ Of Penna", "hospital of the univ. of penna"),
    ],
)
def test_string_normalization_matches(a, b):
    assert norm_str(a) == norm_str(b)


def test_string_normalization_keeps_real_differences():
    assert norm_str("Hospital Of The Univ Of Penna") != norm_str(
        "Hospital of the University of Pennsylvania"
    )
    assert norm_str("Inc") == "inc"  # a lone suffix is kept
    assert norm_str("Allopathic & Osteopathic Physicians|Cardiology") != norm_str("Cardiology")


def test_string_alternatives_from_gold_lists():
    assert compare_scalar("str", "Pfizer Inc.", ["PFIZER LABORATORIES", "Pfizer, Inc"])[0]
    ok, why = compare_scalar("str", "Merck", ["PFIZER LABORATORIES", "Pfizer, Inc"])
    assert not ok and "one of" in why


# ── grade() per shape ────────────────────────────────────────────────────────


def test_scalar_dict_every_key_must_be_right():
    shape = {"records": "int", "usd": "number"}
    gold = {"records": 1000, "usd": 5000.0}
    assert grade({"records": 1000, "usd": 5010.0}, gold, shape)["correct"]
    v = grade({"records": 1000, "usd": 6000.0}, gold, shape)
    assert (
        not v["correct"] and v["fields"]["records"]["correct"] and not v["fields"]["usd"]["correct"]
    )
    assert "off by" in v["fields"]["usd"]["reason"]
    v = grade(None, gold, shape)
    assert not v["correct"] and v["fields"]["usd"]["reason"] == "no answer submitted"
    assert grade({"records": 1000}, gold, shape)["fields"]["usd"]["reason"] == "missing"


def test_scalar_with_name_alternatives():
    shape = {"usd_total": "number", "top_hospital": "str", "top_usd": "number"}
    gold = {
        "usd_total": 1e9,
        "top_hospital": ["HOSPITAL OF THE UNIV OF PENNA", "Penn Medicine"],
        "top_usd": 5e7,
    }
    assert grade(
        {"usd_total": 1.001e9, "top_hospital": "Hospital Of The Univ Of Penna", "top_usd": 5e7},
        gold,
        shape,
    )["correct"]
    assert not grade(
        {"usd_total": 1e9, "top_hospital": "University of Pennsylvania", "top_usd": 5e7},
        gold,
        shape,
    )["correct"]


def _ranked(*pairs):
    return [{"company": c, "usd": u} for c, u in pairs]


def test_ranked():
    gold = {
        "ranked": _ranked(
            (["ABBVIE INC.", "AbbVie"], 300.0), ("Pfizer Inc.", 200.0), ("Merck", 100.0), ("X", 50)
        )
    }
    assert grade(
        {"ranked": _ranked(("AbbVie", 300), ("Pfizer", 200.5), ("MERCK", 100))}, gold, RANKED
    )["correct"]
    v = grade({"ranked": _ranked(("Pfizer", 200), ("AbbVie", 300), ("Merck", 100))}, gold, RANKED)
    assert not v["correct"] and "#1 company" in v["fields"]["ranked"]["reason"]
    v = grade({"ranked": _ranked(("AbbVie", 300), ("Pfizer", 250), ("Merck", 100))}, gold, RANKED)
    assert not v["correct"] and "#2 usd" in v["fields"]["ranked"]["reason"]
    assert not grade({"ranked": _ranked(("AbbVie", 300), ("Pfizer", 200))}, gold, RANKED)["correct"]
    # extra items past k are ignored
    assert grade(
        {"ranked": _ranked(("AbbVie", 300), ("Pfizer", 200), ("Merck", 100), ("Q", 1))},
        gold,
        RANKED,
    )["correct"]


def test_table_matches_rows_in_any_order():
    gold = {"table": [{"program_year": 2023, "usd": 10.0}, {"program_year": 2024, "usd": 20.0}]}
    got = {"table": [{"program_year": 2024, "usd": 20.05}, {"program_year": 2023, "usd": 10.0}]}
    assert grade(got, gold, TABLE)["correct"]
    v = grade(
        {"table": [{"program_year": 2023, "usd": 10.0}, {"program_year": 2025, "usd": 20.0}]},
        gold,
        TABLE,
    )
    assert (
        not v["correct"] and "no row for {'program_year': 2024}" in v["fields"]["table"]["reason"]
    )
    v = grade({"table": [*got["table"], {"program_year": 2025, "usd": 1.0}]}, gold, TABLE)
    assert not v["correct"] and "expected 2 rows" in v["fields"]["table"]["reason"]


def test_table_with_str_keys_and_alternatives():
    gold = {
        "table": [
            {"group": ["paid", "Paid prescribers"], "prescribers": 100, "avg_claims": 50.0},
            {"group": "unpaid", "prescribers": 900, "avg_claims": 20.0},
        ]
    }
    got = {
        "table": [
            {"group": "Unpaid", "prescribers": 901, "avg_claims": 20.0},
            {"group": "Paid prescribers", "prescribers": 100, "avg_claims": 50.1},
        ]
    }
    assert grade(got, gold, TABLE_STR)["correct"]
    got["table"][0]["prescribers"] = 1000
    v = grade(got, gold, TABLE_STR)
    assert not v["correct"] and "prescribers" in v["fields"]["table"]["reason"]


def test_set():
    shape = {"set": ["str"]}
    gold = {"set": ["Pfizer Inc.", "AbbVie"]}
    assert grade({"set": ["ABBVIE INC", "pfizer"]}, gold, shape)["correct"]
    v = grade({"set": ["pfizer"]}, gold, shape)
    assert not v["correct"] and "missing ['abbvie']" in v["fields"]["set"]["reason"]
    assert not grade({"set": ["pfizer", "abbvie", "merck"]}, gold, shape)["correct"]


def test_mixed_shape():
    gold = {
        "usd": 1000.0,
        "ranked": [{"country": "Canada", "usd": 300.0}, {"country": "Mexico", "usd": 200.0}],
    }
    good = {
        "usd": 1000.0,
        "ranked": [{"country": "CANADA", "usd": 300.0}, {"country": "Mexico", "usd": 200.0}],
    }
    assert grade(good, gold, MIXED)["correct"]
    assert not grade({**good, "usd": 900.0}, gold, MIXED)["correct"]


# ── gold SQL convention ──────────────────────────────────────────────────────


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE SCHEMA bronze")
    c.execute(
        "CREATE TABLE bronze.p AS SELECT * FROM (VALUES "
        "('1', 'Pfizer Inc.', 'P1', 'Canada', '2023', '10.00'),"
        "('2', 'PFIZER INC', 'P1', 'Canada', '2024', '20.50'),"
        "('3', 'AbbVie', 'A1', 'Mexico', '2024', '5.25'),"
        "('4', 'Merck', 'M1', 'United States', '2024', '1.00')"
        ") t(id, name, company_id, country, py, amt)"
    )
    return c


def test_gold_scalar_one_row(con):
    sql = "SELECT count(*) AS records, sum(amt::DECIMAL(18,2)) AS usd, 'x' AS ignored FROM bronze.p"
    assert gold_from_sql(sql, {"records": "int", "usd": "number"}, con) == {
        "records": 4,
        "usd": 36.75,
    }
    with pytest.raises(ValueError, match="without a value"):
        gold_from_sql("SELECT 4 AS records", {"records": "int", "usd": "number"}, con)
    with pytest.raises(ValueError, match="one row"):
        gold_from_sql("SELECT amt AS usd FROM bronze.p", {"usd": "number"}, con)


def test_gold_ranked_with_name_lists(con):
    sql = """
        SELECT list(DISTINCT name ORDER BY name) AS company, sum(amt::DECIMAL(18,2)) AS usd
        FROM bronze.p GROUP BY company_id ORDER BY usd DESC LIMIT 3"""
    g = gold_from_sql(sql, RANKED, con)
    assert g["ranked"][0] == {"company": ["PFIZER INC", "Pfizer Inc."], "usd": 30.5}
    answer = {"ranked": _ranked(("Pfizer", 30.5), ("AbbVie", 5.25), ("Merck", 1))}
    assert grade(answer, g, RANKED)["correct"]
    with pytest.raises(ValueError, match="needs at least k=3"):
        gold_from_sql(sql.replace("LIMIT 3", "LIMIT 2"), RANKED, con)


def test_gold_table_and_set(con):
    g = gold_from_sql(
        "SELECT py::INT AS program_year, sum(amt::DOUBLE) AS usd FROM bronze.p GROUP BY 1",
        TABLE,
        con,
    )
    assert sorted(r["program_year"] for r in g["table"]) == [2023, 2024]
    g = gold_from_sql("SELECT DISTINCT country FROM bronze.p", {"set": ["str"]}, con)
    assert sorted(g["set"]) == ["Canada", "Mexico", "United States"]


def test_gold_parts_for_mixed_shapes(con):
    sql = """
-- Total abroad, then the top countries.
-- part: usd
SELECT sum(amt::DECIMAL(18,2)) AS usd FROM bronze.p WHERE country <> 'United States';
-- part: ranked
SELECT country, sum(amt::DECIMAL(18,2)) AS usd FROM bronze.p
WHERE country <> 'United States' GROUP BY 1 ORDER BY usd DESC LIMIT 2;
"""
    assert [k for k, _ in split_parts(sql)] == ["usd", "ranked"]
    g = gold_from_sql(sql, MIXED, con)
    assert g == {
        "usd": 35.75,
        "ranked": [{"country": "Canada", "usd": 30.5}, {"country": "Mexico", "usd": 5.25}],
    }
    with pytest.raises(ValueError, match="mixed shape"):
        gold_from_sql("SELECT 1 AS usd", MIXED, con)
    with pytest.raises(ValueError, match="without a value"):
        gold_from_sql("-- part: usd\nSELECT 1 AS usd", MIXED, con)


def test_compute_and_load_gold_cache(con, tmp_path):
    wh = tmp_path / "wh.duckdb"
    con.execute(
        f"ATTACH '{wh}' AS w; CREATE SCHEMA w.bronze; "
        "CREATE TABLE w.bronze.p AS FROM bronze.p; DETACH w"
    )
    sql_dir, gold_dir = tmp_path / "gold_sql", tmp_path / "gold"
    sql_dir.mkdir()
    (sql_dir / "q1.sql").write_text("SELECT count(*) AS count FROM bronze.p")
    (sql_dir / "q2.sql").write_text("SELECT nope FROM bronze.p")
    qs = {"q1": {"answer": {"count": "int"}}, "q2": {"answer": {"count": "int"}}}
    kw = {"warehouse": wh, "gold_dir": gold_dir, "sql_dir": sql_dir}
    status = grader.compute_gold(["q1", "q2", "q3"], qs, **kw)
    assert (
        status["q1"].startswith("computed")
        and status["q2"].startswith("ERROR")
        and status["q3"] == "no gold SQL"
    )
    assert grader.compute_gold(["q1"], qs, **kw)["q1"] == "up to date"
    assert grader.load_gold("q1", gold_dir=gold_dir, sql_dir=sql_dir)["answer"] == {"count": 4}
    (sql_dir / "q1.sql").write_text("SELECT 5 AS count")
    with pytest.raises(ValueError, match="stale"):
        grader.load_gold("q1", gold_dir=gold_dir, sql_dir=sql_dir)
    with pytest.raises(FileNotFoundError):
        grader.load_gold("q9", gold_dir=gold_dir, sql_dir=sql_dir)
    rec = json.loads((gold_dir / "q1.json").read_text())
    assert rec["shape"] == {"count": "int"} and len(rec["sql_sha256"]) == 64


def test_real_gold_sql_files_split_cleanly():
    root = Path(__file__).resolve().parent.parent
    qs = {}
    for p in (root / "eval" / "questions").glob("*.yaml"):
        if not p.name.startswith("leaks"):
            qs |= {q["id"]: q for q in yaml.safe_load(p.read_text())["questions"]}
    for sql in (root / "eval" / "gold_sql").glob("*.sql"):
        fields = parse_shape(qs[sql.stem]["answer"])
        parts = split_parts(sql.read_text())
        mixed = any(f.is_list for f in fields) and any(not f.is_list for f in fields)
        assert mixed == (parts[0][0] is not None), sql.stem
