# knowledge-factory-fyi

**Why agents get answers wrong on messy public data, and what fixing the data layer buys you.**

I put the same agent (`claude-opus-5-5`, same prompt, same effort) in front of CMS Open Payments + NPPES at four levels of context and graded it on 50 questions whose answers were written in SQL before any agent ran. Every wrong answer is traced to a cause. The fixes go into the data layer, not the prompt, and are scored on 20 held-out questions the fixes never saw.

> ⚠️ Target layout. Every "—" is filled in by `make report` from `reports/results.json`. No number in this file is typed by hand, and `make report` refuses to run unless gold reconciles to CMS-published totals (table 5).

Eval frozen at tag [`eval-v1`](#) (`eval/MANIFEST.sha256`) before the first agent run.

---

## 1. Headline: same agent, four levels of context

50 questions × 3 runs. Permission-leak questions are scored separately (10).

| Context given to the agent | Accuracy | 95% CI | Cost / correct answer | p95 tool call @100 agents | p95 end-to-end | 429s / 1k tool calls | Leaks (of 10) | Permissions enforced in |
|---|---|---|---|---|---|---|---|---|
| **L1** Live API (Open Payments + NPPES) | — | — | — | — | — | — | — | prompt |
| **L2** Raw tables (DuckDB bronze) | — | — | — | — | — | — | — | prompt |
| **L3** Semantic layer (dbt + MetricFlow) | — | — | — | — | — | — | — | data layer |
| **L4** Semantic layer + curator (round N) | — | — | — | — | — | — | — | data layer |

## 2. Why answers were wrong

Wrong answers by root cause. Each question is tagged with the trap it targets ([`eval/taxonomy.md`](eval/taxonomy.md)).

| Cause | What goes wrong | L1 | L2 | L3 | L4 |
|---|---|---|---|---|---|
| `GRAIN` | Counts rows instead of payments or distinct recipients | — | — | — | — |
| `REPUB` | Double-counts republished or changed records | — | — | — | — |
| `WIDE` | Reads only product/specialty column 1 of 5–6 | — | — | — | — |
| `RTYPE` | Mixes physicians, NPPs and teaching hospitals | — | — | — | — |
| `FILES` | Misses or double-counts across General/Research/Ownership | — | — | — | — |
| `ENTITY` | Splits one manufacturer or recipient across name variants | — | — | — | — |
| `TIME` | Confuses program year with payment date | — | — | — | — |
| `PAGE` | Truncated pages read as complete (500-row cap; NPPES `skip` silently stops at 1000) | — | — | — | — |
| `TAXON` | Matches specialty strings instead of taxonomy codes | — | — | — | — |
| Other / agent reasoning | Not a data problem | — | — | — | — |

## 3. Curator loop: does it generalize?

Agents flag gaps. The curator turns the dev-set flags into a semantic-layer YAML pull request, I review and merge it, then both sets are rerun. The curator never sees held-out questions, answers or flags.

| Round | Merged PR | What changed | Dev (30) | Held-out (20) | Dev − held-out |
|---|---|---|---|---|---|
| 0 (= L3) | — | — | — | — | — |
| 1 | — | — | — | — | — |
| 2 | — | — | — | — | — |
| 3 | — | — | — | — | — |

The held-out set has n=20, so its CI is about ±20 points. Read the column as a trend. A widening dev − held-out gap means the curator is overfitting.

## 4. Entity resolution: payment recipient → NPI

2,000 recipients with their NPI masked, plus 300 where the true NPI was removed from the candidates (correct answer: `NO_MATCH`). Run through Message Batches.

| Model | `model_version` | Precision | Recall | Correct `NO_MATCH` | Cost / 1k records |
|---|---|---|---|---|---|
| `claude-haiku-5-5` | — | — | — | — | — |
| `claude-opus-5-5` | — | — | — | — | — |

Only about 7k PY2025 physician/NPP rows have a blank NPI (42,823 blank in total, 35,569 of them teaching hospitals). The benchmark measures ER quality; the gap-fill is small and only uses matches above 0.99 precision.

## 5. Reconciliation gate: gold vs CMS-published totals

Source: CMS national summary file (`PBLCTN_NTNL_SMRY_P06302026_06032026`). Payment counts must match exactly, and dollars to within $1. If any row fails, `make report` stops.

| Program year | Type | Recipient | CMS published $ | CMS payments | Gold $ | Gold payments | Status |
|---|---|---|---|---|---|---|---|
| 2025 | General | Physician | 2,624,554,749.77 | 10,129,623 | — | — | — |
| 2025 | General | NPP | 270,515,603.54 | 5,966,664 | — | — | — |
| 2025 | Research | Physician | 62,512,736.88 | 26,575 | — | — | — |
| 2025 | Research | NPP | 584,754.39 | 1,206 | — | — | — |
| 2024 | … | … | — | — | — | — | — |
| 2023 | … | … | — | — | — | — | — |
| all | General | Teaching hospital | not in CMS summary | — | — | — | reported, not reconciled |
| all | Ownership | all | not in CMS summary | — | — | — | reported, not reconciled |

---

## How each number is measured

- **Accuracy:** a question is correct if the answer matches the frozen gold SQL (numbers ±0.5%, sets as sets, rankings top-k in order, refusals as a class). Gold SQL runs against **bronze**, so the semantic layer is never graded against itself.
- **Cost / correct answer:** Anthropic spend from the `usage` fields × [`config/pricing.yml`](config/pricing.yml) (pinned prices with an as-of date), divided by correct answers.
- **Latency and 429s:** recorded tool-call traces replayed at 100 concurrent workers. 429s are counted **against a local shim with a documented limit** (240/min, openFDA's published limit), because neither CMS API publishes a limit, and load-testing a public API isn't acceptable. Anthropic-side 429s are counted separately.
- **Leaks:** any disallowed value in an answer or a quoted tool result. Personas are in [`config/personas.yml`](config/personas.yml).

## Data

CMS Open Payments PY2023–2025 (General, Research, Ownership) and NPPES V.2, loaded from bulk files with a watermark (`meta.sync_state`). The APIs are what the L1 agents get, not how bronze is loaded: the Open Payments API caps pages at 500 rows, and PY2025 General alone has 16.1M rows. See [`PLAN.md`](PLAN.md) §1 for the probed limits.

## Run it

```bash
make probe && make sync && make dbt && make er && make eval L=all && make curate && make load && make report
```
