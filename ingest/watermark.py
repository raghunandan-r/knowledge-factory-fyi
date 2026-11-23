"""Watermark and run-log tables in the `meta` schema.

Rule: a watermark only moves inside the same transaction that loaded and
validated the data. A failed load leaves the old watermark and old data live.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import duckdb

DDL = """
CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE TABLE IF NOT EXISTS meta.sync_state (
    source      VARCHAR NOT NULL,
    dataset     VARCHAR NOT NULL,
    watermark   VARCHAR NOT NULL,
    detail      JSON,
    updated_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, dataset)
);
CREATE TABLE IF NOT EXISTS meta.sync_runs (
    run_id          VARCHAR,
    source          VARCHAR,
    dataset         VARCHAR,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    status          VARCHAR,      -- loaded | skipped | failed
    rows_loaded     BIGINT,
    expected_rows   BIGINT,
    file_url        VARCHAR,
    file_sha256     VARCHAR,
    file_bytes      BIGINT,
    watermark_before VARCHAR,
    watermark_after  VARCHAR,
    message         VARCHAR
);
CREATE TABLE IF NOT EXISTS meta.schema_registry (
    source      VARCHAR NOT NULL,
    dataset     VARCHAR NOT NULL,
    columns     JSON NOT NULL,
    first_seen  VARCHAR NOT NULL,
    PRIMARY KEY (source, dataset)
);
"""


def now() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    return now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def ensure_meta(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def get(con: duckdb.DuckDBPyConnection, source: str, dataset: str) -> str | None:
    row = con.execute(
        "SELECT watermark FROM meta.sync_state WHERE source = ? AND dataset = ?",
        [source, dataset],
    ).fetchone()
    return row[0] if row else None


def set_(
    con: duckdb.DuckDBPyConnection, source: str, dataset: str, watermark: str, detail: dict
) -> None:
    con.execute(
        """
        INSERT INTO meta.sync_state VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (source, dataset) DO UPDATE SET
            watermark = excluded.watermark,
            detail = excluded.detail,
            updated_at = excluded.updated_at
        """,
        [source, dataset, watermark, json.dumps(detail), now()],
    )


def log_run(con: duckdb.DuckDBPyConnection, **row) -> None:
    cols = [
        "run_id",
        "source",
        "dataset",
        "started_at",
        "finished_at",
        "status",
        "rows_loaded",
        "expected_rows",
        "file_url",
        "file_sha256",
        "file_bytes",
        "watermark_before",
        "watermark_after",
        "message",
    ]
    con.execute(
        f"INSERT INTO meta.sync_runs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [row.get(c) for c in cols],
    )


def check_schema(
    con: duckdb.DuckDBPyConnection,
    source: str,
    dataset: str,
    columns: list[str],
    seen_in: str,
    accept_change: bool = False,
) -> list[str]:
    """Register columns on first sight; fail loudly if a later file differs.

    Returns columns that are new versus the registry (only when accept_change).
    """
    row = con.execute(
        "SELECT columns FROM meta.schema_registry WHERE source = ? AND dataset = ?",
        [source, dataset],
    ).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO meta.schema_registry VALUES (?, ?, ?, ?)",
            [source, dataset, json.dumps(columns), seen_in],
        )
        return []
    known = json.loads(row[0])
    added = [c for c in columns if c not in known]
    removed = [c for c in known if c not in columns]
    if not (added or removed):
        return []
    if not accept_change:
        raise RuntimeError(
            f"schema drift in {source}/{dataset} ({seen_in}): added={added} removed={removed}. "
            "Re-run with --accept-schema-change after reviewing."
        )
    con.execute(
        "UPDATE meta.schema_registry SET columns = ? WHERE source = ? AND dataset = ?",
        [json.dumps(known + added), source, dataset],
    )
    return added
