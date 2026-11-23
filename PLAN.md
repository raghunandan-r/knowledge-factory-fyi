# knowledge-factory-fyi: plan

**The one claim:** I can find why an agent gets answers wrong on messy data, fix the data layer (not the prompt), and show the gain in numbers that hold up on questions the fixes never saw.

Everything below exists to make that claim checkable. Anything that doesn't serve it is on the cut list.

---

## 1. What the APIs actually do (probed 2026-11-22)

These were checked with live requests, not taken from docs. Neither CMS API's docs page renders without JavaScript, so the probe results are the source of truth. Re-run `ingest/api_probe.py` before relying on them.

| Source | Auth | Page size | Rate limit | Watermark available | Use it for |
|---|---|---|---|---|---|
| **Open Payments** datastore API `openpaymentsdata.cms.gov/api/1/datastore/query/{dataset}/0` | None | **Max 500 rows** (`limit=1000` → HTTP 400) | Not documented; no rate-limit headers returned; responses CDN-cached 1h | Dataset `modified` in `/api/1/metastore/schemas/dataset/items` (one date per publication cycle, e.g. `2026-06-30`); per-record `Change_Type` ∈ {NEW, ADD, CHANGED, UNCHANGED} | L1 "live API" agents only |
| **Open Payments** bulk CSV `download.cms.gov/openpayments/PGYR{yyyy}_P{pubdate}/...` | None | Whole file | n/a | Publication date in the folder name | **Bronze load** |
| **NPPES** registry API `npiregistry.cms.hhs.gov/api/?version=2.1` | None | Max 200 (`limit=201` is silently capped) | Not documented; no rate-limit headers | None | L1 agents only |
| **NPPES** bulk `download.cms.gov/nppes/NPI_Files.html` | None | Monthly full V.2 (~1.08 GB zip), weekly incremental (~6 MB), monthly deactivation (~2.6 MB) | n/a | File date in the name. V.1 files were retired 2026-03-03, so use V.2 | **Bronze load** |
| **openFDA** (fallback) | Free key required | 1,000 per request; `skip` max 25,000 | 240/min; 1,000/day without a key, 120,000/day with one | Bulk zips; full re-download required on change | Fallback only, and the source for the shim's documented limit |

**Probe findings to build the eval around:**

1. **Pulling bronze through the API isn't practical.** PY2025 General Payments has **16,131,856 rows**, which is about 32,300 requests at 500 rows each, per file per year. Bronze comes from the bulk CSVs, and the API is what the L1 agents get.
2. **NPPES silently clamps `skip` at 1000.** `skip=1001` returns the same rows as `skip=1000`, with HTTP 200 and no error, so at most 1,200 records are reachable per query. An agent that pages past that gets duplicates and a wrong count without knowing it. This is a ready-made L1 failure case.
3. **Rows, payments and recipients are three different counts.** PY2025 physician rows (10,129,623) and NPP rows (5,966,664) exactly match CMS's published "payment counts". The remaining 35,569 rows are teaching hospitals, which the national summary doesn't cover at all. CMS's physician count (666,748) is distinct recipients. An agent asked "how many payments" has three plausible numbers to choose from.
4. **The NPI is already on most rows.** Only 42,823 of 16.1M PY2025 rows have a blank `Covered_Recipient_NPI`, and 35,569 of those are teaching hospitals. So entity resolution is scored on a **masked-NPI** benchmark, and it fills only the roughly 7k true gaps in gold. Stated honestly in the README, this is a strength.

**Decision:** sync **PY2023–PY2025** (General, Research, Ownership) plus NPPES V.2. That's roughly 50M general-payment rows, which DuckDB handles on a laptop, and three years is enough for cross-year and republication traps.

---

## 2. Build order

Each phase ends in a gate. Don't start the next phase until the gate passes. The order is deliberate: **the eval and the baseline failures come before any modeling**, so the fixes are driven by observed failures and not by guesses.

### Phase 0: Skeleton (small)
- `uv` project, `Makefile`, pinned versions (`duckdb`, `dbt-core`, `dbt-duckdb`, `dbt-metricflow`, `anthropic`), and `.env.example`.
- `ingest/api_probe.py` reproduces every fact in §1 and writes `reports/api_probe.json`.
- **Gate:** `make probe` passes on a clean checkout.

### Phase 1: Incremental sync → DuckDB bronze (medium)
- `ingest/watermark.py` keeps a `meta.sync_state` table: `(source, dataset_id, watermark, rows_loaded, file_sha256, synced_at)`.
- **Open Payments:** read the metastore. If a dataset's `modified` is later than the watermark, download the bulk CSV and `read_csv` it into `bronze.op_general_raw` with `_publication`, `_ingested_at` and `_file_sha256`. Upsert on `record_id`; `Change_Type` drives the merge and goes to an audit table.
- **NPPES:** apply the monthly full file once, then weekly incrementals newer than the watermark, then deactivations.
- Bronze is append-only. Never clean anything here.
- **Gate:** running `make sync` twice in a row loads 0 rows the second time. Bronze row counts match the API `count=true` for every dataset.

### Phase 2: Write the eval and freeze it (medium, and the most important phase)
- 50 questions in `eval/questions/{dev,holdout}.yaml`: 30 dev and 20 held-out, stratified by trap (§4).
- One `eval/gold_sql/qNNN.sql` per question, **written against bronze, not gold**. If the answer key were written against my own dbt models, the semantic layer would be graded against itself.
- 10 permission-leak questions in `eval/questions/leaks.yaml` (§5).
- `eval/grade.py` compares answers: numbers within ±0.5%, sets compared as sets, rankings as top-k in order, and refusals as an exact class.
- **Gate:** `git tag eval-v1` is created, and `eval/MANIFEST.sha256` is committed **before the first agent run**. The README cites the tag, and every later commit can show the eval didn't move.

### Phase 3: Baseline runs and diagnosis, L1 + L2 (medium)
- `agents/harness.py` is my own Messages API tool loop (`claude-opus-5-5`, fixed effort, fixed system prompt). The context level is the only variable. I use my own loop rather than Managed Agents because the tools are local DuckDB and I need per-tool-call timing.
- L1 tools: `http_get` against the Open Payments and NPPES APIs **through `agents/tools/api_shim.py`** (see §6).
- L2 tools: read-only `sql()` against bronze, plus `describe_table()`.
- Run 50 questions × 3 runs at each level. Every wrong answer gets a cause from `eval/taxonomy.md`, assigned by me with an LLM pre-label I review.
- **Gate:** the failure taxonomy table (README table 2) is filled in for L1/L2. This is the "diagnose" evidence.

### Phase 4: Silver/gold + reconciliation gate (medium)
- dbt Core on `dbt-duckdb`. Silver unpivots products 1–5 and specialties 1–6, collapses republished records using `Change_Type`, and conforms recipient types. Gold builds `fct_payment` (one row per record, with the grain documented), `dim_recipient`, `dim_manufacturer` and `dim_product`.
- `seeds/cms_published_totals.csv` comes from the national summary file (`PBLCTN_NTNL_SMRY_*`), at the grain program year × payment type (General/Research) × recipient type (Physician/NPP), with dollars and payment counts.
- `tests/reconcile_program_year_totals.sql`: counts must match exactly and dollars within $1 per cell. **`make report` depends on this test**, so no number reaches the README unless gold reconciles. Teaching hospitals and Ownership have no published total in that file, so they're reported as "not reconcilable against CMS summary" and not silently skipped.
- **Gate:** reconciliation passes for PY2023–2025.

### Phase 5: Entity-resolution batch job (medium)
- **Task:** given a payment recipient's name, address, specialty and license states, plus NPPES candidates from deterministic blocking (last name + state + ZIP3, top 10), return one NPI or `NO_MATCH`.
- **Benchmark:** 2,000 recipients with a known NPI, stratified (common surnames, multiple practice locations, NPPs, recent name changes), with the NPI masked. In another 300 cases the true NPI is **removed from the candidates**, so the correct answer is `NO_MATCH`. That catches confident wrong matches.
- **Two models through Message Batches** (50% discount): `claude-haiku-5-5` and `claude-opus-5-5`. Every output row carries `model_version`, `prompt_sha`, `batch_id`, `candidate_set_sha` and `run_at` (`silver.er_matches`).
- Cost comes from the `usage` fields multiplied by `config/pricing.yml` (pinned prices with a date), never estimated.
- Only matches from the better model above a precision threshold (≥ 0.99 on the benchmark) fill the roughly 7k real NPI gaps in gold.
- **Gate:** ER table (README table 4) filled in. Re-running the scorer from stored outputs reproduces it exactly.

### Phase 6: MetricFlow semantic layer + permissions, L3 (medium)
- `models/semantic/*.yml` defines the semantic models and metrics (`total_payment_usd`, `payment_count`, `distinct_recipients`, `research_payment_usd`, ...), with descriptions written for an agent reader.
- Queries run locally with `mf query` / the MetricFlow Python API against DuckDB. No dbt Cloud.
- Personas are enforced in the data layer (§5).
- L3 tools: `list_metrics`, `list_dimensions`, `query_metrics`, `explain_metric`. **No raw SQL.**
- **Gate:** `mf validate-configs` passes, and the L3 row of every README table is filled in.

### Phase 7: Curator loop, L4 (medium)
- L4 agents get an extra tool, `flag_gap(question_id, what_was_missing, evidence)`.
- `curator/propose.py` clusters the **dev-set** flags, drafts semantic YAML changes (descriptions, synonyms, a new dimension or metric, a default filter), opens a branch, and runs `gh pr create`. I review and merge.
- CI on every curator PR runs `dbt build`, `mf validate-configs`, the reconciliation test, and `curator/guardrails.py`. The guardrails reject any diff that contains a literal answer value, a question ID, or text from the held-out set.
- After each merge, rerun dev and held-out. Run 3 rounds, or stop when held-out gains less than 1 question.
- **Gate:** the curator table (README table 3) shows the dev and held-out scores for every round, including rounds that didn't help.

### Phase 8: Load test, ~100 agents (small)
- `load/replay.py` replays the **recorded tool-call traces** from Phases 3–7 at 100 concurrent workers. That measures the data layer without paying for 100× the LLM calls.
- It records the p50/p95 of each tool call and the 429 count **by source** (data shim and Anthropic API kept separate).
- One live LLM run at 100 concurrency (a small sample) gives the end-to-end p95.
- **Gate:** the latency and 429 columns are filled in.

### Phase 9: Write-up (small)
- `reports/make_readme_table.py` renders every table from `reports/results.json`. No hand-typed numbers.
- Add one worked example per top failure cause: the question, the wrong answer, the root cause, the YAML or model fix, and the answer after the fix.

**Cut line if time-boxed:** in this order, drop Ownership payments, then the live 100-agent LLM run (keep the replay), then curator round 3. Never cut: the frozen eval, the reconciliation gate, the held-out split, the failure taxonomy.

---

## 3. Repo layout

```
knowledge-factory-fyi/
├── README.md                    # results first, method second
├── PLAN.md
├── Makefile                     # probe | sync | dbt | er | eval L=… | curate | load | report
├── pyproject.toml               # uv; pinned versions
├── .env.example                 # ANTHROPIC_API_KEY, OPENFDA_API_KEY
├── config/
│   ├── sources.yml              # dataset ids, program years, probed limits
│   ├── pricing.yml              # $/MTok per model, batch discount, as-of date
│   └── personas.yml             # who can see what
├── ingest/
│   ├── api_probe.py
│   ├── watermark.py
│   ├── sync_open_payments.py
│   └── sync_nppes.py
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml             # dbt-duckdb → data/warehouse.duckdb
│   ├── seeds/cms_published_totals.csv
│   ├── models/
│   │   ├── silver/              # stg_op_general, stg_op_research, stg_nppes, …
│   │   ├── gold/                # fct_payment, dim_recipient, dim_manufacturer, dim_product
│   │   ├── personas/            # one schema of secured views per persona
│   │   └── semantic/            # semantic_models.yml, metrics.yml, saved_queries.yml
│   └── tests/reconcile_program_year_totals.sql
├── er/
│   ├── build_candidates.py      # deterministic blocking against NPPES
│   ├── make_benchmark.py        # masked-NPI + NO_MATCH sets
│   ├── submit_batch.py
│   ├── collect_batch.py
│   ├── score.py
│   └── prompts/er_v1.md
├── agents/
│   ├── harness.py               # tool loop; logs every call w/ timing + usage
│   ├── levels/                  # l1_api.py, l2_raw.py, l3_semantic.py, l4_curated.py
│   └── tools/                   # api_shim.py, duckdb_sql.py, metricflow.py, flag_gap.py
├── curator/
│   ├── propose.py
│   └── guardrails.py
├── eval/
│   ├── questions/               # dev.yaml (30), holdout.yaml (20), leaks.yaml (10)
│   ├── gold_sql/                # q001.sql … q050.sql, written against bronze
│   ├── taxonomy.md              # failure causes, with definitions
│   ├── grade.py
│   └── MANIFEST.sha256
├── load/replay.py
├── reports/
│   ├── results.json             # single source for every README number
│   └── make_readme_table.py
├── docs/adr/                    # one page per decision (bulk vs API, gold SQL on bronze, …)
├── .github/workflows/ci.yml     # dbt build + mf validate + reconcile + guardrails
└── data/                        # gitignored: raw/, warehouse.duckdb, traces/
```

---

## 4. The 50 questions: stratify by trap

Every question is tagged with the trap it targets, and the taxonomy uses the same tags. That's what lets the README say "fixing X fixed these N questions."

| Tag | Trap | Example question shape |
|---|---|---|
| `GRAIN` | rows vs payments (`Number_of_Payments_Included_in_Total_Amount`) vs distinct recipients | "How many payments did NPs receive in 2024?" |
| `REPUB` | republished or changed records double-counted across cycles | "Total general payments in PY2023" (after a refresh) |
| `WIDE` | products 1–5 and specialties 1–6 in wide columns; agents read only column 1 | "Total paid in connection with drug X" |
| `RTYPE` | physician vs NPP vs teaching hospital; NPPs only covered since PY2021 | "Share of payments to non-physicians" |
| `FILES` | General vs Research vs Ownership, double-counted or missed | "All money from manufacturer M to Dr. Y" |
| `ENTITY` | manufacturer name variants, Profile ID vs NPI | "Top 5 manufacturers by payments to cardiologists" |
| `TIME` | program year vs `Date_of_Payment`; dispute and delay flags | "Payments made in Q4 2024" |
| `PAGE` | API pagination limits (500-row cap, NPPES skip clamp at 1000) | "How many cardiologists in CA got >$10k?" |
| `TAXON` | pipe-delimited specialty strings vs NPPES taxonomy codes | "Payments to interventional cardiologists" |

Aim for 5–6 questions per tag, split proportionally between dev and held-out, so the held-out set covers every trap.

---

## 5. Permission model and leak questions

Open Payments is public, so the permission boundary has to be designed into a realistic deployment.

- **`mfr_<id>` (client persona):** sees its own manufacturer's records at row level. Competitors appear only in aggregates with a minimum group size of 11 recipients.
- **`public`:** aggregates only, no recipient-level rows.
- **`admin`:** everything, plus a synthetic `crm_territory` column (clearly labeled synthetic) to test column-level leaks.

**Enforcement is in DuckDB:** one schema of secured views per persona, and the agent's connection attaches only that schema. MetricFlow reads through the same views. L1/L2 get the persona rules **in the prompt only**, which is how most teams do it, and the leak column shows the difference.

Leak questions ask for a competitor's recipient-level rows, an aggregate below the minimum group size, the `crm_territory` column, and an indirect re-identification ("the only cardiologist in ZIP X"). A leak is any disallowed value in the answer or in a tool result the agent quotes.

---

## 6. Measuring 429s without hammering a public API

Neither CMS API publishes a rate limit or returns rate-limit headers. Running 100 concurrent agents against them would mostly measure their CDN, and it isn't a polite thing to do to a public service.

- `agents/tools/api_shim.py` is a local proxy that **records** real responses once (sequential, throttled), then **replays** them behind a token bucket. The default is openFDA's documented 240/min, and the limit is configurable.
- The README states that 429s are measured against the shim with a documented limit, and gives the limit.
- One small, sequential real-API run at L1 confirms that shim accuracy matches live accuracy.

---

## 7. Metric definitions (put these in the README too)

- **Accuracy:** share of the 50 questions answered correctly, averaged over 3 runs. 95% CI by bootstrap over questions.
- **Cost per correct answer:** total Anthropic spend for the level (from `usage` × `config/pricing.yml`, cache reads included) divided by the number of correct answers.
- **p95 latency:** per tool call (the data layer I own) and end to end, reported separately.
- **429s:** per 1,000 tool calls, split by source.
- **Held-out accuracy:** on 20 questions the curator never sees in any form. At n=20 the CI is about ±20 points, so read rounds as a trend and not as a precise number.
