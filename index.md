# index.md: where to look

| Question | Go to |
|---|---|
| Plan, phases, gates, decision log | `PLAN.md` (local only, gitignored) |
| Which sources, years, paths and limits? | `config/sources.yml` |
| What did the API probe find? | `ingest/api_probe.py` → `reports/api_probe.json` |
| How does Open Payments get into bronze? | `ingest/sync_open_payments.py` |
| How does NPPES get into bronze? | `ingest/sync_nppes.py` |
| How does Medicare Part D get into bronze? | `ingest/sync_part_d.py` |
| Watermarks, sync runs, schema registry | `ingest/watermark.py`, `ingest/common.py` |
| Phase 1 gate checks | `ingest/verify.py` |
| Why bulk files, per-release sync, all-text bronze | `docs/adr/0001-ingest-design.md` |
| How does the CMS national summary get into bronze? | `ingest/sync_op_summary.py` |
| Which CMS docs do agents get, and how are they fetched? | `ingest/fetch_docs.py`, `config/sources.yml` (`docs:`) |
| Draft eval questions | `eval/questions/draft.yaml` |
| Which questions are in the pilot? | `eval/pilot.yaml` |
| Gold answers (SQL on bronze) | `eval/gold_sql/qNNN.sql` |
| How is an answer graded? Gold SQL output convention? | `eval/grader.py` |
| Number tolerances and per-question overrides | `compare_scalar` in `eval/grader.py`; `tolerance:` in `eval/questions/draft.yaml` |
| Starter semantic layer: models, metrics, profile | `dbt/models/`, `dbt/profiles.yml` |
| Does a layer reconcile to CMS totals? | `dbt/tests/reconcile_national_summary.sql` |
| Agent model, prompt, effort, limits | `config/agent.yml` |
| The agent loop, arms and what each arm can see | `agents/harness.py`, `agents/tools.py` |
| Where layer agents see table and column descriptions (DESCRIBE, SHOW TABLES) | `TableDocs` in `agents/tools.py`, `LayerArm._sql` in `agents/harness.py` |
| How an `answer:` shape becomes the submit schema | `agents/shapes.py` |
| Running and scoring the pilot | `agents/pilot.py` → `reports/pilot.json` |
| Token prices used for cost | `config/pricing.yml` |
| Smoke-testing with another model (OpenRouter) | `.env.example`, `load_config` in `agents/harness.py` |
| Tests | `tests/` |

Bronze tables live in `data/warehouse.duckdb` (schemas `bronze`, `meta`); query it read-only.
Each semantic layer is built to `data/layers/<layer>.duckdb` (schemas `silver`, `gold`; no bronze).
Run traces: `data/runs/<pilot_id>/<arm>/run<k>/`.
