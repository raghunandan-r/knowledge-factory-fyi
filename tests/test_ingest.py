"""Unit tests for the sync logic, on tiny fixtures. No network."""

from __future__ import annotations

import copy
import zipfile
from datetime import date

import pytest

from ingest import sync_nppes as nppes
from ingest import sync_open_payments as op
from ingest import sync_part_d as partd
from ingest import watermark as wm
from ingest.common import connect, load_config

URL = (
    "https://download.cms.gov/openpayments/PGYR2025_P06302026_06032026/"
    "OP_DTL_GNRL_PGYR2025_P06302026_06032026.csv"
)


@pytest.fixture
def cfg(tmp_path):
    c = copy.deepcopy(load_config())
    c["paths"] = {
        "data_dir": str(tmp_path),
        "raw_dir": str(tmp_path / "raw"),
        "warehouse": str(tmp_path / "w.duckdb"),
        "tmp_dir": str(tmp_path / "tmp"),
    }
    c["duckdb"] = {"memory_limit": "1GB", "threads": 2}
    return c


HEADER = "Change_Type,Record_ID,Program_Year,Total_Amount_of_Payment_USDollars"


def write_csv(p, rows, header=HEADER):
    p.write_text(header + "\n" + "\n".join(rows) + "\n")
    return p


def dataset(release="P06302026_06032026"):
    return op.Dataset(
        "general",
        2025,
        "id",
        "2025 General Payment Data",
        "2026-06-30",
        URL.replace("P06302026_06032026", release),
        release,
        "op_general",
    )


# ── Open Payments ──────────────────────────────────────────────────────────


def test_parse_release():
    assert op.parse_release(URL) == (2025, "P06302026_06032026")
    with pytest.raises(ValueError):
        op.parse_release("https://download.cms.gov/openpayments/SMRY_RPTS_P06302026/x.csv")


def test_load_is_idempotent_and_keeps_text(cfg, tmp_path, monkeypatch):
    csv = write_csv(tmp_path / "g.csv", ["NEW,0001,2025,10.50", "NEW,0002,2025,0.10"])
    monkeypatch.setattr(op, "expected_rows", lambda *a: 2)
    con = connect(cfg)
    wm.ensure_meta(con)
    op.load(cfg, con, None, dataset(), csv, False)
    op.load(cfg, con, None, dataset(), csv, False)  # same release again
    rows = con.execute(
        "SELECT record_id, total_amount_of_payment_usdollars FROM bronze.op_general ORDER BY 1"
    ).fetchall()
    assert rows == [("0001", "10.50"), ("0002", "0.10")]  # no dupes, leading zeros kept
    assert wm.get(con, op.SOURCE, "general_2025") == "P06302026_06032026"


def test_new_release_appends_partition(cfg, tmp_path, monkeypatch):
    csv = write_csv(tmp_path / "g.csv", ["NEW,1,2025,1.00"])
    monkeypatch.setattr(op, "expected_rows", lambda *a: 1)
    con = connect(cfg)
    wm.ensure_meta(con)
    op.load(cfg, con, None, dataset("P01012027_12012026"), csv, False)
    op.load(cfg, con, None, dataset("P06302027_06032027"), csv, False)
    n = con.execute("SELECT count(DISTINCT _release) FROM bronze.op_general").fetchone()[0]
    assert n == 2
    assert wm.get(con, op.SOURCE, "general_2025") == "P06302027_06032027"


def test_row_count_mismatch_rolls_back(cfg, tmp_path, monkeypatch):
    csv = write_csv(tmp_path / "g.csv", ["NEW,1,2025,1.00"])
    monkeypatch.setattr(op, "expected_rows", lambda *a: 999)
    con = connect(cfg)
    wm.ensure_meta(con)
    with pytest.raises(RuntimeError, match="CMS reports 999"):
        op.load(cfg, con, None, dataset(), csv, False)
    assert wm.get(con, op.SOURCE, "general_2025") is None
    status = con.execute("SELECT status FROM meta.sync_runs").fetchall()
    assert status == [("failed",)]


def test_schema_drift_fails_unless_accepted(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(op, "expected_rows", lambda *a: 1)
    con = connect(cfg)
    wm.ensure_meta(con)
    op.load(cfg, con, None, dataset(), write_csv(tmp_path / "a.csv", ["NEW,1,2025,1"]), False)
    drift = write_csv(
        tmp_path / "b.csv",
        ["NEW,2,2025,1,x"],
        header="Change_Type,Record_ID,Program_Year,Total_Amount_of_Payment_USDollars,New_Col",
    )
    nxt = dataset("P06302027_06032027")
    with pytest.raises(RuntimeError, match="schema drift"):
        op.load(cfg, con, None, nxt, drift, False)
    op.load(cfg, con, None, nxt, drift, True)
    assert con.execute("SELECT count(new_col) FROM bronze.op_general").fetchone()[0] == 1


def test_wrong_program_year_rejected(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(op, "expected_rows", lambda *a: 1)
    con = connect(cfg)
    wm.ensure_meta(con)
    with pytest.raises(RuntimeError, match="different Program_Year"):
        op.load(cfg, con, None, dataset(), write_csv(tmp_path / "g.csv", ["NEW,1,2024,1"]), False)


# ── NPPES ──────────────────────────────────────────────────────────────────

INDEX = """
<a href='./NPPES_Data_Dissemination_November_2026_V2.zip'>
NPPES Data Dissemination V.2 (November 09, 2026)</a>
<a href='./NPPES_Data_Dissemination_110226_110826_Weekly_V2.zip'>w</a>
<a href='./NPPES_Data_Dissemination_110926_111526_Weekly_V2.zip'>w</a>
"""


def test_parse_index():
    idx = nppes.parse_index(INDEX)
    assert idx["full_name"] == "November_2026"
    assert idx["full_date"] == date(2026, 11, 9)
    assert [w[1] for w in idx["weeklies"]] == [date(2026, 11, 8), date(2026, 11, 15)]


def test_plan_first_run_loads_full_then_newer_weekly():
    p = nppes.plan(None, None, nppes.parse_index(INDEX))
    assert p["reload_full"] is True
    assert [w[2] for w in p["weeklies"]] == ["NPPES_Data_Dissemination_110926_111526_Weekly_V2.zip"]


def test_plan_up_to_date_is_noop():
    p = nppes.plan("November_2026", date(2026, 11, 15), nppes.parse_index(INDEX))
    assert p == {"reload_full": False, "weeklies": []}


def test_plan_gap_without_newer_full_raises():
    # missed the 11/16-11/22 weekly; the page now lists only later ones, no newer full file
    idx = nppes.parse_index(
        INDEX.replace("110226_110826", "112326_112926").replace("110926_111526", "113026_120626")
    )
    with pytest.raises(RuntimeError, match="gap"):
        nppes.plan("November_2026", date(2026, 11, 15), idx)


def test_plan_gap_with_newer_full_reloads():
    p = nppes.plan("October_2026", date(2026, 10, 20), nppes.parse_index(INDEX))
    assert p["reload_full"] is True


def test_extract_ignores_unexpected_and_traversal(tmp_path):
    z = tmp_path / "f.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("npidata_pfile_20050523-20261109.csv", "NPI\n1\n")
        zf.writestr("npidata_pfile_20050523-20261109_fileheader.csv", "NPI\n")
        zf.writestr("../../evil.csv", "x")
        zf.writestr("readme.pdf", "x")
    found = nppes.extract(z, {"npidata_pfile": "nppes_npidata"})
    assert list(found) == ["nppes_npidata"]
    assert found["nppes_npidata"].parent == tmp_path / "f"
    assert not (tmp_path.parent / "evil.csv").exists()


def test_remove_raw_deletes_zip_and_extracted_folder(tmp_path):
    z = tmp_path / "f.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("npidata_pfile_a.csv", "NPI\n1\n")
    nppes.extract(z, {"npidata_pfile": "nppes_npidata"})
    nppes.remove_raw(z)
    assert list(tmp_path.iterdir()) == []


def test_nppes_weekly_upsert_replaces_npi(cfg, tmp_path):
    members = {"npidata_pfile": "nppes_npidata"}
    cfg["nppes"]["members"] = members
    full = tmp_path / "full.zip"
    with zipfile.ZipFile(full, "w") as zf:
        zf.writestr("npidata_pfile_a.csv", "NPI,Provider_Last_Name\n1,OLD\n2,KEEP\n")
    weekly = tmp_path / "wk.zip"
    with zipfile.ZipFile(weekly, "w") as zf:
        zf.writestr("npidata_pfile_b.csv", "NPI,Provider_Last_Name\n1,NEW\n3,ADDED\n")
    con = connect(cfg)
    wm.ensure_meta(con)
    with pytest.raises(RuntimeError, match="only 2 NPIs"):  # full-file sanity floor
        nppes.load_zip(con, cfg, full, "replace", "t", False, "u")
    con.execute(
        "CREATE TABLE bronze.nppes_npidata (npi VARCHAR, provider_last_name VARCHAR,"
        " _file VARCHAR, _run_id VARCHAR, _ingested_at TIMESTAMPTZ)"
    )
    con.execute(
        "INSERT INTO bronze.nppes_npidata (npi, provider_last_name)"
        " VALUES ('1', 'OLD'), ('2', 'KEEP')"
    )
    con.execute(
        "INSERT INTO meta.schema_registry VALUES"
        " ('nppes', 'nppes_npidata', '[\"npi\", \"provider_last_name\"]', 't')"
    )
    nppes.load_zip(con, cfg, weekly, "upsert", "w", False, "u")
    con.execute("COMMIT")
    rows = con.execute(
        "SELECT npi, provider_last_name FROM bronze.nppes_npidata ORDER BY npi"
    ).fetchall()
    assert rows == [("1", "NEW"), ("2", "KEEP"), ("3", "ADDED")]


# ── Part D ─────────────────────────────────────────────────────────────────

PD_TITLE = "Medicare Part D Prescribers - by Provider and Drug"
PD_CSV = "https://data.cms.gov/sites/default/files/2026-05/x/MUP_DPR_RY26_P04_V10_DY24_NPIBN.csv"


def pd_catalog(csv=PD_CSV, year="2024"):
    entry = {
        "title": f"{PD_TITLE} : {year}-12-01",
        "temporal": [{"startDate": f"{year}-01-01", "endDate": f"{year}-12-31"}],
        "modified": "2026-05-21",
        "distribution": [
            {"format": "API", "accessURL": "https://data.cms.gov/data-api/v1/dataset/a/data"},
            {"downloadURL": csv},
        ],
    }
    by_provider = dict(entry, title=f"Medicare Part D Prescribers - by Provider : {year}-12-03")
    return {"dataset": [entry, by_provider]}


def test_part_d_discover_matches_exact_title_and_year(cfg):
    (ds,) = partd.discover(cfg, pd_catalog(), ["prescriber_drug"], [2024])
    assert (ds.key, ds.release) == ("prescriber_drug_2024", "MUP_DPR_RY26_P04_V10_DY24_NPIBN")
    assert ds.api_url.endswith("/data")
    with pytest.raises(RuntimeError, match="expected 1 catalog entry"):
        partd.discover(cfg, pd_catalog(), ["prescriber_drug"], [2023])


def test_part_d_discover_rejects_file_for_wrong_year(cfg):
    with pytest.raises(RuntimeError, match="file name says"):
        partd.discover(
            cfg, pd_catalog(csv=PD_CSV.replace("DY24", "DY23")), ["prescriber_drug"], [2024]
        )


def test_part_d_load_tags_year_is_idempotent_and_checks_count(cfg, tmp_path, monkeypatch):
    (ds,) = partd.discover(cfg, pd_catalog(), ["prescriber_drug"], [2024])
    csv = write_csv(
        tmp_path / "pd.csv",
        ["0123456789,Ozempic,12", "0123456789,Jardiance,40"],
        header="Prscrbr_NPI,Brnd_Name,Tot_Clms",
    )
    monkeypatch.setattr(partd, "expected_rows", lambda *a: 2)
    con = connect(cfg)
    wm.ensure_meta(con)
    partd.load(con, None, ds, csv, False)
    partd.load(con, None, ds, csv, False)  # same release again
    rows = con.execute(
        "SELECT prscrbr_npi, brnd_name, _data_year FROM bronze.partd_prescriber_drug ORDER BY 2"
    ).fetchall()
    assert rows == [("0123456789", "Jardiance", "2024"), ("0123456789", "Ozempic", "2024")]
    monkeypatch.setattr(partd, "expected_rows", lambda *a: 3)
    nxt = partd.Dataset(**{**ds.__dict__, "release": "MUP_DPR_RY27_P04_V10_DY24_NPIBN"})
    with pytest.raises(RuntimeError, match="CMS reports 3"):
        partd.load(con, None, nxt, csv, False)
    assert wm.get(con, partd.SOURCE, ds.key) == ds.release  # old watermark kept
