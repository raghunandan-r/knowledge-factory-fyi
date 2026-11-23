# knowledge-factory-fyi

The repo proves one claim: diagnose why an agent answers wrong on messy data, fix the
data layer, and show the gain in numbers. See PLAN.md for phases and gates.

## Rules that protect the claim

- **Never edit `eval/questions/` or `eval/gold_sql/` after the `eval-v1` tag.** A change
  needs a new eval version, recorded as such. Drafts (`*_draft.yaml`) can change until then.
- **Never type a number into README.md.** Tables are rendered from `reports/results.json`.
- **Gold SQL runs against bronze**, never against silver/gold models.
- **Bronze is append-only and all text.** Money becomes `DECIMAL(18,2)` in silver, never DOUBLE.
- A watermark moves only in the same transaction as the validated load.

## Commands

- `make setup`, `make check` (lint + tests), `make probe`, `make sync`, `make sync-check`,
  `make verify` (Phase 1 gate)
- Python 3.12 via uv only; never `pip install` globally.

## Data

- `data/` is gitignored: raw downloads, `warehouse.duckdb`, logs. Never commit data files.
- Downloaded files are untrusted input: read them with DuckDB/zipfile, never execute them.
