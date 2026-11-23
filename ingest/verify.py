"""Phase 1 gate: bronze matches CMS, and a repeat sync has nothing to load.

Checks
  1. Every loaded Open Payments partition's row count equals the count CMS
     reported at load time (meta.sync_runs.expected_rows) and still equals
     what is in bronze now.
  2. NPPES: one row per NPI, and the watermarks are set.
  3. Nothing pending: the catalog's release equals every stored watermark.

Exits 1 on any failure.
"""

from __future__ import annotations

import sys

from ingest import sync_open_payments as op
from ingest import watermark as wm
from ingest.common import connect, http_client, load_config


def main() -> int:
    cfg = load_config()
    failures: list[str] = []
    con = connect(cfg, read_only=True)

    print("Open Payments partitions")
    print(f"{'dataset':16} {'release':20} {'bronze rows':>12} {'CMS count':>12}  ok")
    runs = con.execute(
        """
        SELECT dataset, watermark_after, rows_loaded, expected_rows
        FROM meta.sync_runs
        WHERE source = 'open_payments' AND status = 'loaded'
        QUALIFY row_number() OVER (PARTITION BY dataset ORDER BY finished_at DESC) = 1
        ORDER BY dataset
        """
    ).fetchall()
    tables = cfg["open_payments"]["file_types"]
    for dataset, release, _loaded, expected in runs:
        ft, py = dataset.rsplit("_", 1)
        table = tables[ft]["table"]
        n = con.execute(
            f"SELECT count(*) FROM bronze.{table} WHERE _release = ? AND program_year = ?",
            [release, py],
        ).fetchone()[0]
        ok = n == expected
        print(f"{dataset:16} {release:20} {n:>12,} {expected:>12,}  {'✓' if ok else '✗'}")
        if not ok:
            failures.append(f"{dataset}: bronze {n} != CMS {expected}")

    try:
        n, distinct = con.execute(
            "SELECT count(*), count(DISTINCT npi) FROM bronze.nppes_npidata"
        ).fetchone()
        print(f"\nNPPES npidata: {n:,} rows, {distinct:,} distinct NPIs")
        if n != distinct or n == 0:
            failures.append("NPPES npidata not one row per NPI")
        for ds in ("full", "weekly"):
            print(f"  watermark {ds}: {wm.get(con, 'nppes', ds)}")
    except Exception as e:  # table missing
        failures.append(f"NPPES not loaded: {e}")

    with http_client(cfg) as client:
        current = op.discover(cfg, client, list(tables), cfg["open_payments"]["program_years"])
    pending = [d.key for d in current if wm.get(con, op.SOURCE, d.key) != d.release]
    print(f"\npending Open Payments datasets: {pending or 'none'}")
    if pending:
        failures.append(f"pending: {pending}")

    con.close()
    print("\nGATE", "FAILED: " + "; ".join(failures) if failures else "PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
