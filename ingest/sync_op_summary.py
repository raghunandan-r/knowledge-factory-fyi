"""Load CMS Open Payments summary files into bronze, one release at a time.

Each summary file covers all program years, so a release replaces nothing: it is
appended as its own partition, and the watermark moves in the same transaction.
Only used to reconcile semantic layers against CMS's own totals.

Usage: uv run python -m ingest.sync_op_summary [--check-only]
"""

from __future__ import annotations

import argparse
import re
import sys
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

SOURCE = "open_payments_summary"
RELEASE_RE = re.compile(r"/SMRY_RPTS_(P\d{8}_\d{8})/")


def discover(cfg: dict, client) -> list[dict]:
    items = get_json(client, cfg["open_payments"]["metastore_url"])
    found = []
    for key, spec in cfg["open_payments"]["summaries"].items():
        matches = [i for i in items if i.get("title") == spec["title"]]
        if len(matches) != 1:
            raise RuntimeError(f"expected 1 catalog entry titled {spec['title']!r}")
        urls = [d["downloadURL"] for d in matches[0].get("distribution", []) if "downloadURL" in d]
        csvs = [u for u in urls if u.lower().endswith(".csv")]
        if len(csvs) != 1:
            raise RuntimeError(f"{spec['title']}: expected 1 CSV, got {csvs}")
        m = RELEASE_RE.search(csvs[0])
        if not m:
            raise ValueError(f"no release id in {csvs[0]}")
        found.append(
            {
                "key": key,
                "table": spec["table"],
                "url": csvs[0],
                "release": m.group(1),
                "identifier": matches[0]["identifier"],
            }
        )
    return found


def load(con, ds: dict, csv: Path, expected: int) -> int:
    table = f"bronze.{ident(ds['table'])}"
    run_id = wm.new_run_id()
    started = wm.now()
    before = wm.get(con, SOURCE, ds["key"])
    file_hash = sha256(csv)
    src = "read_csv(?, all_varchar = true, header = true, normalize_names = true)"
    con.execute("BEGIN TRANSACTION")
    try:
        cols = list(con.sql(f"SELECT * FROM {src} LIMIT 0", params=[str(csv)]).columns)
        wm.check_schema(con, SOURCE, ds["key"], cols, ds["release"])
        col_defs = ", ".join(f"{ident(c)} VARCHAR" for c in cols)
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {table} ({col_defs}, _release VARCHAR, "
            "_source_file VARCHAR, _file_sha256 VARCHAR, _run_id VARCHAR, "
            "_ingested_at TIMESTAMPTZ)"
        )
        con.execute(f"DELETE FROM {table} WHERE _release = ?", [ds["release"]])
        con.execute(
            f"INSERT INTO {table} BY NAME SELECT *, ? AS _release, ? AS _source_file, "
            f"? AS _file_sha256, ? AS _run_id, now() AS _ingested_at FROM {src}",
            [ds["release"], csv.name, file_hash, run_id, str(csv)],
        )
        n = con.execute(f"SELECT count(*) FROM {table} WHERE _run_id = ?", [run_id]).fetchone()[0]
        if n != expected:
            raise RuntimeError(f"{ds['key']}: loaded {n} rows, CMS reports {expected}")
        wm.set_(con, SOURCE, ds["key"], ds["release"], {"url": ds["url"]})
        wm.log_run(
            con,
            run_id=run_id,
            source=SOURCE,
            dataset=ds["key"],
            started_at=started,
            finished_at=wm.now(),
            status="loaded",
            rows_loaded=n,
            expected_rows=expected,
            file_url=ds["url"],
            file_sha256=file_hash,
            file_bytes=csv.stat().st_size,
            watermark_before=before,
            watermark_after=ds["release"],
            message=None,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args(argv)
    with run_lock(cfg), http_client(cfg) as client:
        con = connect(cfg)
        wm.ensure_meta(con)
        todo = [d for d in discover(cfg, client) if wm.get(con, SOURCE, d["key"]) != d["release"]]
        print(f"{len(todo)} summary file(s) to load")
        if args.check_only:
            return 0
        for d in todo:
            dest = path(cfg, "raw_dir") / SOURCE / d["release"] / Path(d["url"]).name
            csv = download(client, d["url"], dest, cfg["http"]["retries"])
            expected = int(
                get_json(
                    client,
                    f"{cfg['open_payments']['datastore_url']}/{d['identifier']}/0",
                    limit=1,
                    results="false",
                    count="true",
                    schema="false",
                )["count"]
            )
            print(f"  {d['key']}: {load(con, d, csv, expected)} rows (release {d['release']})")
            if cfg["open_payments"]["delete_raw_after_load"]:
                csv.unlink()
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
