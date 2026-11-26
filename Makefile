.PHONY: setup probe sync sync-op sync-nppes sync-partd sync-summary sync-check verify test lint fmt check

-include .env      # ANTHROPIC_API_KEY for make pilot (gitignored)
export ANTHROPIC_API_KEY

UV := PYTHONUNBUFFERED=1 uv run  # stream progress into logs as it happens

setup:            ## install pinned deps into .venv (Python 3.12)
	uv sync --all-extras

probe:            ## re-check API facts; exit 1 if any changed
	$(UV) python -m ingest.api_probe

sync: sync-op sync-nppes sync-partd sync-summary   ## incremental sync of all sources

sync-op:
	$(UV) python -m ingest.sync_open_payments

sync-nppes:
	$(UV) python -m ingest.sync_nppes

sync-partd:
	$(UV) python -m ingest.sync_part_d

sync-summary:     ## CMS national summary, for reconciliation
	$(UV) python -m ingest.sync_op_summary

sync-check:       ## show what a sync would load, download nothing
	$(UV) python -m ingest.sync_open_payments --check-only
	$(UV) python -m ingest.sync_nppes --check-only
	$(UV) python -m ingest.sync_part_d --check-only
	$(UV) python -m ingest.sync_op_summary --check-only

verify:           ## Phase 1 gate
	$(UV) python -m ingest.verify

test:
	$(UV) pytest -q

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

fmt:
	$(UV) ruff check --fix .
	$(UV) ruff format .

check: lint test  ## what pre-commit and CI run

# ── docs and semantic layers (ingest/fetch_docs.py, dbt/) ──
.PHONY: docs layer reconcile
LAYER ?= starter
DBT := KF_LAYER=$(LAYER) DBT_PROFILES_DIR=dbt $(UV) --extra transform dbt
MF := KF_LAYER=$(LAYER) KF_TARGET=query DBT_PROFILES_DIR=dbt DBT_PROJECT_DIR=dbt $(UV) --extra transform mf

docs:             ## download CMS docs to data/docs, text to data/docs/text
	$(UV) python -m ingest.fetch_docs

layer:            ## build data/layers/$(LAYER).duckdb (dbt build) and validate MetricFlow
	mkdir -p data/layers data/tmp
	$(DBT) build --project-dir dbt
	$(MF) validate-configs

reconcile:        ## layer vs CMS national summary (PY2023-25 x General/Research x Physician/NPP)
	$(DBT) test --project-dir dbt --select reconcile_national_summary

# ── agent harness and pilot (agents/, eval/grader.py) ──
.PHONY: gold pilot pilot-report layer-manifest
ARM ?= raw
RUNS ?= 3
PILOT_ARGS ?=
LAYER ?= starter

gold:             ## (re)compute gold results from eval/gold_sql into data/gold_results
	$(UV) python -m eval.grader

pilot:            ## make pilot ARM=raw|starter RUNS=3 [PILOT_ARGS="--limit 2 --dry-run"]
	$(UV) --extra transform python -m agents.pilot run --arm $(ARM) --runs $(RUNS) $(PILOT_ARGS)

pilot-report:     ## grade every pilot run into reports/pilot.json
	$(UV) python -m agents.pilot report

layer-manifest:   ## snapshot dbt's semantic manifest for LAYER into data/layers
	$(DBT) parse --project-dir dbt --quiet
	mkdir -p data/layers
	cp dbt/target/semantic_manifest.json data/layers/$(LAYER).semantic_manifest.json
