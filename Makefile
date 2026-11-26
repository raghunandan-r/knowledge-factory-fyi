.PHONY: setup probe sync sync-op sync-nppes sync-partd sync-check verify test lint fmt check

UV := PYTHONUNBUFFERED=1 uv run  # stream progress into logs as it happens

setup:            ## install pinned deps into .venv (Python 3.12)
	uv sync --all-extras

probe:            ## re-check API facts; exit 1 if any changed
	$(UV) python -m ingest.api_probe

sync: sync-op sync-nppes sync-partd   ## incremental sync of all sources

sync-op:
	$(UV) python -m ingest.sync_open_payments

sync-nppes:
	$(UV) python -m ingest.sync_nppes

sync-partd:
	$(UV) python -m ingest.sync_part_d

sync-check:       ## show what a sync would load, download nothing
	$(UV) python -m ingest.sync_open_payments --check-only
	$(UV) python -m ingest.sync_nppes --check-only
	$(UV) python -m ingest.sync_part_d --check-only

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
