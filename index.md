# index.md: where to look

| Question | Go to |
|---|---|
| Plan, phases, gates, decision log | `PLAN.md` |
| Which sources, years, paths and limits? | `config/sources.yml` |
| What did the API probe find? | `ingest/api_probe.py` → `reports/api_probe.json` |
| How does Open Payments get into bronze? | `ingest/sync_open_payments.py` |
| How does NPPES get into bronze? | `ingest/sync_nppes.py` |
| How does Medicare Part D get into bronze? | `ingest/sync_part_d.py` |
| Watermarks, sync runs, schema registry | `ingest/watermark.py`, `ingest/common.py` |
| Phase 1 gate checks | `ingest/verify.py` |
| Why bulk files, per-release sync, all-text bronze | `docs/adr/0001-ingest-design.md` |
| Draft eval questions | `eval/questions/draft.yaml` |
| Tests | `tests/test_ingest.py` |

Bronze tables live in `data/warehouse.duckdb` (schemas `bronze`, `meta`); query it read-only.
