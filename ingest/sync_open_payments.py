"""Incremental sync of CMS Open Payments into DuckDB bronze.

CMS reissues whole files per release (e.g. P06302026_06032026); there is no
delta feed. So the transport is full-file, and the incrementality is:
  1. check the catalog (cheap) and skip any dataset whose release is already loaded;
  2. append each new release to bronze as its own partition (never overwrite);
  3. move the watermark only after the row count matches CMS's own count.

Usage:
  uv run python -m ingest.sync_open_payments [--types general,research] [--years 2025]
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

SOURCE = "open_payments"
RELEASE_RE = re.compile(r"/PGYR(\d{4})_(P\d{8}_\d{8})/")
META_COLS = ["_release", "_source_file", "_file_sha256", "_run_id", "_ingested_at"]


@dataclass
class Dataset:
    file_type: str
    program_year: int
    identifier: str
    title: str
    modified: str
    url: str
    release: str
    table: str

    @property
    def key(self) -> str:
        return f"{self.file_type}_{self.program_year}"


def parse_release(url: str) -> tuple[int, str]:
    m = RELEASE_RE.search(url)
    if not m:
        raise ValueError(f"no release id in {url}")
    return int(m.group(1)), m.group(2)


def discover(cfg: dict, client, types: list[str], years: list[int]) -> list[Dataset]:
    """Find the current catalog entry for each (file type, program year)."""
    items = get_json(client, cfg["open_payments"]["metastore_url"])
    found = []
    for ft in types:
        spec = cfg["open_payments"]["file_types"][ft]
        for py in years:
            title = f"{py} {spec['title']}"
            matches = [i for i in items if i.get("title") == title]
            if len(matches) != 1:
                raise RuntimeError(f"expected 1 catalog entry titled {title!r}, got {len(matches)}")
            item = matches[0]
            urls = [d["downloadURL"] for d in item.get("distribution", []) if "downloadURL" in d]
            csvs = [u for u in urls if u.lower().endswith(".csv")]
            if len(csvs) != 1:
                raise RuntimeError(f"{title}: expected 1 CSV distribution, got {csvs}")
            url_py, release = parse_release(csvs[0])
            if url_py != py:
                raise RuntimeError(f"{title}: URL says PY{url_py}")
            found.append(
                Dataset(
                    ft,
                    py,
                    item["identifier"],
                    title,
                    item.get("modified", ""),
                    csvs[0],
                    release,
                    spec["table"],
                )
            )
    return found


def expected_rows(cfg: dict, client, ds: Dataset) -> int:
    data = get_json(
        client,
        f"{cfg['open_payments']['datastore_url']}/{ds.identifier}/0",
        limit=1,
        results="false",
        count="true",
        schema="false",
    )
    return int(data["count"])


def csv_columns(con, csv: Path) -> list[str]:
    rel = con.sql(
        "SELECT * FROM read_csv(?, all_varchar = true, header = true, normalize_names = true) "
        "LIMIT 0",
        params=[str(csv)],
    )
    return list(rel.columns)


def load(cfg: dict, con, client, ds: Dataset, csv: Path, accept_schema_change: bool) -> dict:
    """Load one file as a new bronze partition inside a single transaction."""
    table = f"bronze.{ident(ds.table)}"
    run_id = wm.new_run_id()
    started = wm.now()
    before = wm.get(con, SOURCE, ds.key)
    cols = csv_columns(con, csv)
    expected = expected_rows(cfg, client, ds)
    file_hash = sha256(csv)

    con.execute("BEGIN TRANSACTION")
    try:
        added = wm.check_schema(con, SOURCE, ds.file_type, cols, ds.release, accept_schema_change)
        col_defs = ", ".join(f"{ident(c)} VARCHAR" for c in cols + META_COLS[:-1])
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_defs}, _ingested_at TIMESTAMPTZ)")
        for c in added:
            con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {ident(c)} VARCHAR")
        # idempotent: replace this release's partition for this program year
        con.execute(
            f"DELETE FROM {table} WHERE _release = ? AND program_year = ?",
            [ds.release, str(ds.program_year)],
        )
        con.execute(
            f"""
            INSERT INTO {table} BY NAME
            SELECT *, ? AS _release, ? AS _source_file, ? AS _file_sha256,
                   ? AS _run_id, now() AS _ingested_at
            FROM read_csv(?, all_varchar = true, header = true, normalize_names = true)
            """,
            [ds.release, csv.name, file_hash, run_id, str(csv)],
        )
        loaded = con.execute(
            f"SELECT count(*) FROM {table} WHERE _run_id = ?", [run_id]
        ).fetchone()[0]
        if loaded != expected:
            raise RuntimeError(f"{ds.key}: loaded {loaded:,} rows, CMS reports {expected:,}")
        bad_py = con.execute(
            f"SELECT count(*) FROM {table} WHERE _run_id = ? AND program_year <> ?",
            [run_id, str(ds.program_year)],
        ).fetchone()[0]
        if bad_py:
            raise RuntimeError(f"{ds.key}: {bad_py} rows with a different Program_Year")
        wm.set_(
            con,
            SOURCE,
            ds.key,
            ds.release,
            {"identifier": ds.identifier, "modified": ds.modified, "url": ds.url},
        )
        wm.log_run(
            con,
            run_id=run_id,
            source=SOURCE,
            dataset=ds.key,
            started_at=started,
            finished_at=wm.now(),
            status="loaded",
            rows_loaded=loaded,
            expected_rows=expected,
            file_url=ds.url,
            file_sha256=file_hash,
            file_bytes=csv.stat().st_size,
            watermark_before=before,
            watermark_after=ds.release,
            message=None,
        )
        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        wm.log_run(
            con,
            run_id=run_id,
            source=SOURCE,
            dataset=ds.key,
            started_at=started,
            finished_at=wm.now(),
            status="failed",
            rows_loaded=0,
            expected_rows=expected,
            file_url=ds.url,
            file_sha256=file_hash,
            file_bytes=csv.stat().st_size,
            watermark_before=before,
            watermark_after=before,
            message=str(e)[:2000],
        )
        raise
    return {"dataset": ds.key, "release": ds.release, "rows": loaded}


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    op = cfg["open_payments"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default=",".join(op["file_types"]))
    ap.add_argument("--years", default=",".join(map(str, op["program_years"])))
    ap.add_argument("--check-only", action="store_true", help="report what would load")
    ap.add_argument("--accept-schema-change", action="store_true")
    args = ap.parse_args(argv)
    types = args.types.split(",")
    years = [int(y) for y in args.years.split(",")]

    with run_lock(cfg), http_client(cfg) as client:
        con = connect(cfg)
        wm.ensure_meta(con)
        datasets = discover(cfg, client, types, years)
        todo = [d for d in datasets if wm.get(con, SOURCE, d.key) != d.release]
        for d in datasets:
            state = "NEW RELEASE" if d in todo else "up to date"
            print(f"{d.key:16} {d.release}  {state}")
        if args.check_only or not todo:
            print(f"{len(todo)} dataset(s) to load; 0 rows loaded")
            return 0

        total = 0
        for d in todo:
            dest = path(cfg, "raw_dir") / SOURCE / d.release / Path(d.url).name
            print(f"downloading {d.url}")
            csv = download(client, d.url, dest, cfg["http"]["retries"])
            print(f"loading {csv.name}")
            res = load(cfg, con, client, d, csv, args.accept_schema_change)
            total += res["rows"]
            print(f"  {res['dataset']}: {res['rows']:,} rows (release {res['release']})")
            if op["delete_raw_after_load"]:
                csv.unlink()
        print(f"done: {total:,} rows loaded")
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
