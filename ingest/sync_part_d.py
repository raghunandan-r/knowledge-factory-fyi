"""Incremental sync of Medicare Part D prescriber files into DuckDB bronze.

data.cms.gov publishes one catalog entry per data year, each with a full CSV whose name
carries the release (MUP_DPR_RY26_P04_V10_DY24_NPIBN: release year 26, data year 24).
As with Open Payments there is no delta feed, so:
  1. read the catalog and skip any (dataset, data year) whose release is already loaded;
  2. append each new release to bronze as its own partition (never overwrite);
  3. move the watermark only after the row count matches the CMS data API's own count.

The files have no data-year column, so each row gets `_data_year`.

Usage:
  uv run python -m ingest.sync_part_d [--datasets prescriber,geo_drug] [--years 2024]
                                      [--check-only] [--accept-schema-change]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ingest import watermark as wm
from ingest.common import (
    connect,
    download,
    get_json,
    http_client,
    ident,
    load_config,
    path,
    run_lock,
    sha256,
)

SOURCE = "part_d"
DY_RE = re.compile(r"_DY(\d{2})_", re.IGNORECASE)
META_COLS = ["_data_year", "_release", "_source_file", "_file_sha256", "_run_id"]


@dataclass
class Dataset:
    name: str
    data_year: int
    title: str
    modified: str
    url: str
    api_url: str
    release: str
    table: str

    @property
    def key(self) -> str:
        return f"{self.name}_{self.data_year}"


def entry_year(item: dict) -> int | None:
    temporal = item.get("temporal")
    if isinstance(temporal, list) and temporal:
        temporal = temporal[0]
    if isinstance(temporal, dict):
        return int(temporal["startDate"][:4])
    if isinstance(temporal, str):
        return int(temporal[:4])
    return None


def discover(cfg: dict, catalog: dict, names: list[str], years: list[int]) -> list[Dataset]:
    """Find the one catalog entry per (dataset, data year). Pure function, unit-tested."""
    found = []
    for name in names:
        spec = cfg["part_d"]["datasets"][name]
        for dy in years:
            matches = [
                i
                for i in catalog["dataset"]
                if i.get("title", "").split(" : ")[0] == spec["title"] and entry_year(i) == dy
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    f"expected 1 catalog entry for {spec['title']!r} {dy}, got {len(matches)}"
                )
            item = matches[0]
            dists = item.get("distribution", [])
            csvs = [d["downloadURL"] for d in dists if d.get("downloadURL", "").endswith(".csv")]
            apis = [d["accessURL"] for d in dists if d.get("format") == "API"]
            if len(csvs) != 1 or not apis:
                raise RuntimeError(f"{spec['title']} {dy}: csv={csvs} api={apis}")
            release = Path(csvs[0]).stem
            m = DY_RE.search(release)
            if not m or 2000 + int(m.group(1)) != dy:
                raise RuntimeError(f"{spec['title']} {dy}: file name says {release}")
            found.append(
                Dataset(
                    name,
                    dy,
                    item["title"],
                    item.get("modified", ""),
                    csvs[0],
                    apis[0],
                    release,
                    spec["table"],
                )
            )
    return found


def expected_rows(client, ds: Dataset) -> int:
    return int(get_json(client, f"{ds.api_url}/stats")["total_rows"])


def csv_columns(con, csv: Path) -> list[str]:
    rel = con.sql(
        "SELECT * FROM read_csv(?, all_varchar = true, header = true, normalize_names = true) "
        "LIMIT 0",
        params=[str(csv)],
    )
    return list(rel.columns)


def load(con, client, ds: Dataset, csv: Path, accept_schema_change: bool) -> dict:
    """Load one file as a new bronze partition inside a single transaction."""
    table = f"bronze.{ident(ds.table)}"
    run_id = wm.new_run_id()
    started = wm.now()
    before = wm.get(con, SOURCE, ds.key)
    cols = csv_columns(con, csv)
    expected = expected_rows(client, ds)
    file_hash = sha256(csv)

    con.execute("BEGIN TRANSACTION")
    try:
        added = wm.check_schema(con, SOURCE, ds.name, cols, ds.release, accept_schema_change)
        col_defs = ", ".join(f"{ident(c)} VARCHAR" for c in cols + META_COLS)
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_defs}, _ingested_at TIMESTAMPTZ)")
        for c in added:
            con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {ident(c)} VARCHAR")
        con.execute(f"DELETE FROM {table} WHERE _release = ?", [ds.release])  # idempotent
        con.execute(
            f"""
            INSERT INTO {table} BY NAME
            SELECT *, ? AS _data_year, ? AS _release, ? AS _source_file, ? AS _file_sha256,
                   ? AS _run_id, now() AS _ingested_at
            FROM read_csv(?, all_varchar = true, header = true, normalize_names = true)
            """,
            [str(ds.data_year), ds.release, csv.name, file_hash, run_id, str(csv)],
        )
        loaded = con.execute(
            f"SELECT count(*) FROM {table} WHERE _run_id = ?", [run_id]
        ).fetchone()[0]
        if loaded != expected:
            raise RuntimeError(f"{ds.key}: loaded {loaded:,} rows, CMS reports {expected:,}")
        wm.set_(
            con,
            SOURCE,
            ds.key,
            ds.release,
            {"title": ds.title, "modified": ds.modified, "url": ds.url, "api": ds.api_url},
        )
        _log(con, ds, run_id, started, "loaded", loaded, expected, csv, file_hash, before, None)
        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        _log(con, ds, run_id, started, "failed", 0, expected, csv, file_hash, before, str(e))
        raise
    return {"dataset": ds.key, "release": ds.release, "rows": loaded}


def _log(con, ds, run_id, started, status, loaded, expected, csv, file_hash, before, error):
    wm.log_run(
        con,
        run_id=run_id,
        source=SOURCE,
        dataset=ds.key,
        started_at=started,
        finished_at=wm.now(),
        status=status,
        rows_loaded=loaded,
        expected_rows=expected,
        file_url=ds.url,
        file_sha256=file_hash,
        file_bytes=csv.stat().st_size,
        watermark_before=before,
        watermark_after=ds.release if status == "loaded" else before,
        message=error[:2000] if error else None,
    )


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    pd = cfg["part_d"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default=",".join(pd["datasets"]))
    ap.add_argument("--years", default=",".join(map(str, pd["data_years"])))
    ap.add_argument("--check-only", action="store_true", help="report what would load")
    ap.add_argument("--accept-schema-change", action="store_true")
    args = ap.parse_args(argv)
    names = args.datasets.split(",")
    years = [int(y) for y in args.years.split(",")]

    with run_lock(cfg), http_client(cfg) as client:
        con = connect(cfg)
        wm.ensure_meta(con)
        datasets = discover(cfg, get_json(client, pd["catalog_url"]), names, years)
        todo = [d for d in datasets if wm.get(con, SOURCE, d.key) != d.release]
        for d in datasets:
            state = "NEW RELEASE" if d in todo else "up to date"
            print(f"{d.key:22} {d.release:36} {state}")
        if args.check_only or not todo:
            print(f"{len(todo)} dataset(s) to load; 0 rows loaded")
            return 0

        total = 0
        for d in todo:
            dest = path(cfg, "raw_dir") / SOURCE / d.release / Path(d.url).name
            print(f"downloading {d.url}")
            csv = download(client, d.url, dest, cfg["http"]["retries"])
            print(f"loading {csv.name}")
            res = load(con, client, d, csv, args.accept_schema_change)
            total += res["rows"]
            print(f"  {res['dataset']}: {res['rows']:,} rows (release {res['release']})")
            if pd["delete_raw_after_load"]:
                csv.unlink()
        print(f"done: {total:,} rows loaded")
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
