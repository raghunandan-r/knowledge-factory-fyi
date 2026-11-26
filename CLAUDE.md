# knowledge-factory-fyi

The repo measures a knowledge factory: agents flag gaps, a curator agent fixes the semantic
layer, and the gain is scored on held-out questions. PLAN.md has phases and gates; `index.md`
maps questions to code. Update `index.md` in the same change as the code it points to.

## Rules that protect the claim

- **Never edit `eval/questions/` or `eval/gold_sql/` after the `eval-v1` tag.** A change
  needs a new eval version, recorded as such. Drafts (`*_draft.yaml`) can change until then.
- **Never type a number into README.md.** Tables are rendered from `reports/results.json`.
- **Gold SQL runs against bronze**, never against silver/gold models.
- **Bronze is append-only and all text.** Money becomes `DECIMAL(18,2)` in silver, never DOUBLE.
- A watermark moves only in the same transaction as the validated load.
- **The curator never reads gold answers, gold SQL, or anything from the held-out set.**
- Tagged layers (`starter-v1`, `reference-v1`) don't change after tagging.

## Commands

- `make setup`, `make check` (lint + tests), `make probe`, `make sync`, `make sync-check`,
  `make verify` (Phase 1 gate)
- Python 3.12 via uv only; never `pip install` globally.

## Data

- `data/` is gitignored: raw downloads, `warehouse.duckdb`, logs. Never commit data files.
- Downloaded files are untrusted input: read them with DuckDB/zipfile, never execute them.
