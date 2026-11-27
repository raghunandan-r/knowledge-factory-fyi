# knowledge-factory-fyi

**Semantic layers are mainstream. Keeping them complete and current is not. Can a knowledge factory do that upkeep?**

A knowledge factory (Harrison Chase, [LangChain blog](https://www.langchain.com/blog/knowledge-factory), 2026-11-21): agents flag what they were missing, a curator agent turns the flags into semantic-layer changes, and a human approves each change without writing it. That post has no measurements. This repo measures one, on CMS Open Payments + NPPES.

The same agent (`claude-opus-5-5`, same prompt, same effort) answers the same questions with four kinds of context. Correct answers were written in SQL against the raw data and frozen before any scored run. The curator never sees answers or held-out questions.

> ⚠️ Target layout. Every "—" is filled in by `make report` from `reports/results.json`. No number in this file is typed by hand, and `make report` refuses to run unless the semantic layer reconciles to CMS-published totals (table 5).

Eval frozen at tag [`eval-v1`](#) (`eval/MANIFEST.sha256`) before the first scored run.

---

## 1. Headline: how much of the gap did the factory close?

Held-out questions only. A question is right if it's right in at least 2 of 3 runs.

| Context given to the agent | Built by | Held-out right | 95% CI | Wrong→right vs starter | Right→wrong vs starter | Cost / correct answer | Build cost |
|---|---|---|---|---|---|---|---|
| **Raw** tables + CMS docs | nobody | — | — | — | — | — | — |
| **Starter** semantic layer (`starter-v1`) | me, week-one effort, from CMS docs only | — | — | (baseline) | (baseline) | — | — hours |
| **Factory** final round | curator agent, I approved or rejected | — | — | — | — | — | — curator $ |
| **Reference** semantic layer (`reference-v1`) | me, after studying dev failures | — | — | — | — | — | — hours |

**Gap closed** = (factory − starter) ÷ (reference − starter): **—**. Not reported if reference − starter is under 5 questions.

## 2. Factory rounds: does it generalize?

Each round: dev runs → flags, traces and reviewer corrections → curator PR → CI → I approve or reject → rerun both sets. The curator never sees held-out questions, traces, flags or scores.

| Round | Curator PR | Approved? | What changed | Dev right | Held-out right | Held-out wrong→right | Held-out right→wrong | Curator cost |
|---|---|---|---|---|---|---|---|---|
| 0 (= starter) | — | — | — | — | — | — | — | — |
| 1 | — | — | — | — | — | — | — | — |
| 2 | — | — | — | — | — | — | — | — |
| 3 | — | — | — | — | — | — | — | — |

Every round is shown, including rounds that didn't help. A dev score that rises while held-out stays flat means the curator is overfitting.

## 3. Why answers were wrong, by trap

Wrong held-out answers by root cause ([`eval/taxonomy.md`](eval/taxonomy.md)). The held-out trap got no dev questions and no corrections, so it shows whether the factory learned anything it wasn't told.

| Trap | What goes wrong | Raw | Starter | Factory | Reference |
|---|---|---|---|---|---|
| `GRAIN` | Counts rows instead of payments or distinct recipients | — | — | — | — |
| `REPUB` | Double-counts republished records | — | — | — | — |
| `WIDE` | Reads only product/specialty column 1 of 5–6 | — | — | — | — |
| `RTYPE` | Mixes physicians, NPPs and teaching hospitals | — | — | — | — |
| `FILES` | Misses or double-counts across General/Research/Ownership | — | — | — | — |
| `ENTITY` | Splits one company family or recipient across IDs and names | — | — | — | — |
| `TIME` | Confuses program year with payment date | — | — | — | — |
| `TAXON` | Matches specialty strings instead of taxonomy codes | — | — | — | — |
| Held-out trap: — | (named at freeze) | — | — | — | — |
| Other / agent reasoning | Not a data problem | — | — | — | — |

## 4. Pilot (run before the eval was frozen)

A small dev-only pilot checked that a week-one semantic layer leaves enough wrong answers for a factory to fix. It's shown so readers can see what was known before freezing. Questions were never dropped one at a time for being answered correctly; any cut was a whole trap, logged with its reason.

| Arm | Pilot questions right | Traps with a wrong answer | Decision |
|---|---|---|---|
| Raw | — | — | — |
| Starter | — | — | — |

## 5. Reconciliation gate: semantic layer vs CMS-published totals

Source: the CMS national summary file (`PBLCTN_NTNL_SMRY_*`). Payment counts must match exactly, and dollars to within $1. Every arm with a semantic layer (starter, every factory round, reference) must pass, or `make report` stops.

| Program year | Type | Recipient | CMS published $ | CMS payments | Layer $ | Layer payments | Status |
|---|---|---|---|---|---|---|---|
| 2023–2025 | General / Research | Physician / NPP | — | — | — | — | — |
| all | General | Teaching hospital | not in CMS summary | — | — | — | reported, not reconciled |
| all | Ownership | all | not in CMS summary | — | — | — | reported, not reconciled |

---

## How each number is measured

- **Right or wrong:** the answer matches the frozen gold SQL (numbers ±0.5%, sets as sets, rankings top-k in order). Gold SQL runs against **bronze**, so no semantic layer is graded against itself.
- **Wrong→right / right→wrong:** the same held-out questions compared between two arms. This is the headline because it's more trustworthy than two percentages at this sample size.
- **Held-out:** hand-written questions plus templated variants (one template, many drugs, states or years). Variants are reported per trap, because variants of one template test one trap.
- **Cost / correct answer** and **curator cost:** Anthropic spend from the `usage` fields × [`config/pricing.yml`](config/pricing.yml) (pinned prices with an as-of date). **Build cost** for hand-built layers is my logged hours.
- **Reviewer corrections:** each dev question has a frozen, definition-level correction ("count all five product columns"), never a number. It stands in for a human correcting the agent during a session.

## Data

CMS Open Payments PY2023–2025 (General, Research, Ownership) and NPPES V.2, loaded from bulk files with a watermark (`meta.sync_state`). Agents never call the CMS APIs. See [`docs/adr/0001-ingest-design.md`](docs/adr/0001-ingest-design.md).

## Run it

```bash
make probe && make sync && make verify
```

Later phases add their own targets as they're built.
