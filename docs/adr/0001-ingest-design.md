# ADR 0001: How data gets in

Status: accepted, 2026-11-23

## Bronze loads from bulk files, not the APIs

The Open Payments API caps pages at 500 rows; PY2025 General alone has 16.1M rows
(about 32,000 requests). The NPPES API caps at 200 results and silently stops paging
at skip 1,000. Bulk CSVs/zips are the only complete source. The APIs remain what
Level 1 agents use, because that is what an agent would naively reach for.

## "Incremental" means per release, not per row

CMS has no delta feed: each release reissues whole files for every program year
(today all of PY2019–2025 sit under release `P06302026_06032026`). So:

- the daily check reads the catalog (~80 KB) and compares release IDs with watermarks;
- a new release is downloaded in full and appended to bronze as its own partition
  (`_release`), never overwriting the previous one;
- silver picks the latest release per `Record_ID` and uses `Change_Type` to audit changes.

NPPES does publish weekly deltas, applied as upserts by NPI. The index page lists only
the latest two weeklies, so a gap longer than that triggers a reload of the monthly
full file, or fails loudly if no newer full file exists yet.

## A watermark moves only with a validated load

Each file loads inside one transaction: insert, compare row count with CMS's own
`count=true`, check Program_Year, then set the watermark and commit. Any failure rolls
back, logs a `failed` row in `meta.sync_runs`, and leaves yesterday's data live.

## Bronze is all text, with a schema registry

Every column is VARCHAR so NPIs, ZIP codes and record IDs keep leading zeros and no
type guess can silently drop rows. Each file's column list is checked against
`meta.schema_registry`; a new or missing column stops the load until reviewed
(`--accept-schema-change`).

## uv locally, Docker for CI and sandboxing

uv keeps every package in `.venv` on a pinned Python 3.12. Running 25 GB scans through
Docker Desktop's VM on macOS is slower for no isolation benefit. Docker is added later
for CI/reviewer reproducibility and to sandbox agent-written SQL.
