"""Summary-file loader, on a tiny fixture. No network."""

from __future__ import annotations

import pytest

from ingest import sync_op_summary as smry
from ingest import watermark as wm
from ingest.common import connect, load_config

REL = "P06302026_06032026"
URL = f"https://download.cms.gov/openpayments/SMRY_RPTS_{REL}/PBLCTN_NTNL_SMRY_{REL}-joined.csv"


@pytest.fixture
def con(tmp_path):
    cfg = load_config()
    cfg["paths"] = {
        "data_dir": str(tmp_path),
        "raw_dir": str(tmp_path / "raw"),
        "warehouse": str(tmp_path / "w.duckdb"),
        "tmp_dir": str(tmp_path / "tmp"),
    }
    cfg["duckdb"] = {"memory_limit": "1GB", "threads": 2}
    c = connect(cfg)
    wm.ensure_meta(c)
    yield c
    c.close()


def ds():
    return {
        "key": "national",
        "table": "op_national_summary",
        "url": URL,
        "release": REL,
        "identifier": "x",
    }


def write(tmp_path, rows=2):
    p = tmp_path / "s.csv"
    p.write_text("Program_Year,Payment_Type\n" + "".join(f"2025,General{i}\n" for i in range(rows)))
    return p


def test_load_is_idempotent_and_moves_watermark(con, tmp_path):
    csv = write(tmp_path)
    assert smry.load(con, ds(), csv, expected=2) == 2
    assert smry.load(con, ds(), csv, expected=2) == 2
    assert con.execute("SELECT count(*) FROM bronze.op_national_summary").fetchone()[0] == 2
    assert wm.get(con, smry.SOURCE, "national") == REL


def test_count_mismatch_rolls_back(con, tmp_path):
    with pytest.raises(RuntimeError, match="CMS reports 3"):
        smry.load(con, ds(), write(tmp_path), expected=3)
    assert wm.get(con, smry.SOURCE, "national") is None
