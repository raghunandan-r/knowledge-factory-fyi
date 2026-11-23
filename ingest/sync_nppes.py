"""Incremental sync of the NPPES NPI registry (V.2 files) into DuckDB bronze.

Watermarks:
  full    the monthly full file's name (e.g. November_2026) and its as-of date
  weekly  the end date of the last weekly update applied

The download page lists only the latest two weekly files. If the job misses more
than that, the gap is detected and the monthly full file is reloaded instead.

Usage:
  uv run python -m ingest.sync_nppes [--check-only] [--accept-schema-change]
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from ingest import watermark as wm
from ingest.common import connect, download, http_client, ident, load_config, path, run_lock, sha256

SOURCE = "nppes"
FULL_RE = re.compile(r"NPPES_Data_Dissemination_([A-Za-z]+_\d{4})_V2\.zip")
WEEKLY_RE = re.compile(r"NPPES_Data_Dissemination_(\d{6})_(\d{6})_Weekly_V2\.zip")
FULL_DATE_RE = re.compile(r"Dissemination V\.2 \(([A-Z][a-z]+ \d{1,2}, \d{4})\)")


def mmddyy(s: str) -> date:
    return datetime.strptime(s, "%m%d%y").date()


def parse_index(html: str) -> dict:
    fulls = sorted(set(FULL_RE.findall(html)))
    if len(fulls) != 1:
        raise RuntimeError(f"expected one monthly V2 full file, found {fulls}")
    m = FULL_DATE_RE.search(html)
    if not m:
        raise RuntimeError("could not find the full file's as-of date on the index page")
    weeklies = sorted(
        {
            (mmddyy(a), mmddyy(b), f"NPPES_Data_Dissemination_{a}_{b}_Weekly_V2.zip")
            for a, b in WEEKLY_RE.findall(html)
        }
    )
    return {
        "full_name": fulls[0],
        "full_file": f"NPPES_Data_Dissemination_{fulls[0]}_V2.zip",
        "full_date": datetime.strptime(m.group(1), "%B %d, %Y").date(),
        "weeklies": weeklies,
    }


def plan(state_full: str | None, state_weekly: date | None, idx: dict) -> dict:
    """Decide what to load. Pure function, unit-tested."""
    reload_full = state_full != idx["full_name"]
    since = idx["full_date"] if reload_full else state_weekly
    pending = [w for w in idx["weeklies"] if since is None or w[1] > since]
    if pending and since is not None and pending[0][0] > since + timedelta(days=1):
        if reload_full or idx["full_date"] <= (state_weekly or date.min):
            raise RuntimeError(
                f"gap: last applied {since}, earliest available weekly starts {pending[0][0]}, "
                "and no newer full file is published yet"
            )
        reload_full, since = True, idx["full_date"]
        pending = [w for w in idx["weeklies"] if w[1] > since]
    return {"reload_full": reload_full, "weeklies": pending}


def extract(zip_path: Path, members: dict[str, str]) -> dict[str, Path]:
    """Extract only the expected CSVs, by basename, into a sibling folder."""
    out_dir = zip_path.with_suffix("")
    out_dir.mkdir(exist_ok=True)
    found: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            base = Path(info.filename).name
            low = base.lower()
            if not low.endswith(".csv") or "fileheader" in low:
                continue
            prefix = next((p for p in members if low.startswith(p)), None)
            if prefix is None:
                continue
            dest = out_dir / base  # basename only: no path traversal
            if not dest.exists():
                with z.open(info) as src, open(dest, "wb") as dst:
                    while chunk := src.read(8 << 20):
                        dst.write(chunk)
            found[members[prefix]] = dest
    if "nppes_npidata" not in found:
        raise RuntimeError(f"{zip_path.name}: no npidata CSV inside")
    return found


def read_csv_sql() -> str:
    return "read_csv(?, all_varchar = true, header = true, normalize_names = true)"


def load_zip(
    con, cfg, zip_path: Path, mode: str, label: str, accept: bool, url: str
) -> tuple[str, int]:
    """mode = 'replace' (monthly full) or 'upsert' (weekly).

    Leaves the transaction open on success so the caller moves the watermark
    and commits atomically; rolls back on failure.
    """
    files = extract(zip_path, cfg["nppes"]["members"])
    run_id = wm.new_run_id()
    started = wm.now()
    zip_hash = sha256(zip_path)
    rows_npidata = 0
    con.execute("BEGIN TRANSACTION")
    try:
        for table_name, csv in files.items():
            table = f"bronze.{ident(table_name)}"
            cols = list(
                con.sql(f"SELECT * FROM {read_csv_sql()} LIMIT 0", params=[str(csv)]).columns
            )
            added = wm.check_schema(con, SOURCE, table_name, cols, label, accept)
            select = (
                f"SELECT *, ? AS _file, ? AS _run_id, now() AS _ingested_at FROM {read_csv_sql()}"
            )
            params = [csv.name, run_id, str(csv)]
            if mode == "replace":
                con.execute(f"CREATE OR REPLACE TABLE {table} AS {select}", params)
            else:
                for c in added:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {ident(c)} VARCHAR")
                con.execute(
                    f"DELETE FROM {table} WHERE npi IN (SELECT npi FROM {read_csv_sql()})",
                    [str(csv)],
                )
                con.execute(f"INSERT INTO {table} BY NAME {select}", params)
            n = con.execute(f"SELECT count(*) FROM {table} WHERE _run_id = ?", [run_id]).fetchone()[
                0
            ]
            if table_name == "nppes_npidata":
                rows_npidata = n
            print(f"  {table_name}: {n:,} rows")
        dupes = con.execute(
            "SELECT count(*) - count(DISTINCT npi) FROM bronze.nppes_npidata"
        ).fetchone()[0]
        if dupes:
            raise RuntimeError(f"{label}: {dupes} duplicate NPIs in nppes_npidata")
        if mode == "replace" and rows_npidata < 1_000_000:
            raise RuntimeError(f"{label}: only {rows_npidata:,} NPIs in a full file")
        wm.log_run(
            con,
            run_id=run_id,
            source=SOURCE,
            dataset=mode,
            started_at=started,
            finished_at=wm.now(),
            status="loaded",
            rows_loaded=rows_npidata,
            file_url=url,
            file_sha256=zip_hash,
            file_bytes=zip_path.stat().st_size,
            watermark_after=label,
        )
        return run_id, rows_npidata
    except Exception as e:
        con.execute("ROLLBACK")
        wm.log_run(
            con,
            run_id=run_id,
            source=SOURCE,
            dataset=mode,
            started_at=started,
            finished_at=wm.now(),
            status="failed",
            file_url=url,
            file_sha256=zip_hash,
            message=str(e)[:2000],
        )
        raise


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--accept-schema-change", action="store_true")
    args = ap.parse_args(argv)
    base = cfg["nppes"]["base_url"]

    with run_lock(cfg), http_client(cfg) as client:
        con = connect(cfg)
        wm.ensure_meta(con)
        r = client.get(cfg["nppes"]["index_url"])
        r.raise_for_status()
        idx = parse_index(r.text)
        state_full = wm.get(con, SOURCE, "full")
        sw = wm.get(con, SOURCE, "weekly")
        state_weekly = date.fromisoformat(sw) if sw else None
        p = plan(state_full, state_weekly, idx)
        print(
            f"full: have {state_full}, published {idx['full_name']} ({idx['full_date']})"
            f" -> {'RELOAD' if p['reload_full'] else 'up to date'}"
        )
        print(f"weekly: have {state_weekly}, to apply {[w[2] for w in p['weeklies']]}")
        if args.check_only or not (p["reload_full"] or p["weeklies"]):
            print("0 rows loaded")
            return 0

        raw = path(cfg, "raw_dir") / SOURCE
        total = 0
        if p["reload_full"]:
            url = base + idx["full_file"]
            print(f"downloading {url}")
            z = download(client, url, raw / idx["full_file"], cfg["http"]["retries"])
            _, n = load_zip(
                con, cfg, z, "replace", idx["full_name"], args.accept_schema_change, url
            )
            wm.set_(con, SOURCE, "full", idx["full_name"], {"as_of": str(idx["full_date"])})
            wm.set_(con, SOURCE, "weekly", str(idx["full_date"]), {"from": "full file"})
            con.execute("COMMIT")
            total += n
        for start, end, name in p["weeklies"]:
            url = base + name
            print(f"downloading {url}")
            z = download(client, url, raw / name, cfg["http"]["retries"])
            _, n = load_zip(con, cfg, z, "upsert", f"{start}_{end}", args.accept_schema_change, url)
            wm.set_(con, SOURCE, "weekly", str(end), {"file": name})
            con.execute("COMMIT")
            total += n
        print(f"done: {total:,} NPI rows written")
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
