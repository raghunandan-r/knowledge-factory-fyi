# knowledge-factory-fyi: plan

**The claim:** semantic layers are mainstream, but keeping them complete and current is not.
I measure whether a knowledge factory can do that upkeep. In a knowledge factory, agents flag
what they were missing, a curator agent turns the flags into semantic-layer changes, and a
human approves each change without writing any of it. The test is how much of the gap between
a week-one semantic layer and an expert-built one the factory closes, on questions it never saw.

"Knowledge factory" follows Harrison Chase's framing (LangChain blog, 2026-11-21): agents flag
missing knowledge, and a background curator turns flags and traces into maintained knowledge.
That post publishes no measurements. This repo is the measured version.

---

## 1. Data (loaded)

- **Open Payments** PY2023–2025: General, Research and Ownership files, from the bulk CSVs of
  release `P06302026_06032026`. These are the payments drug and device makers must report to
  doctors, non-physician practitioners (NPPs) and teaching hospitals.
- **NPPES** V.2, November 2026 full file plus the weekly update. This is the national provider
  registry: each provider's NPI, name, address and taxonomy (specialty) codes.
- **Medicare Part D prescribers**, data years 2023 and 2024 (`make sync-partd`), from
  data.cms.gov: *by Provider and Drug* (one row per prescriber NPI × drug × year),
  *by Provider* (one row per prescriber) and *by Geography and Drug* (national and state totals
  per drug). This is what Medicare's drug plans paid for, by the prescriber's NPI. It joins to
  Open Payments on NPI. 2023 and 2024 are the only years both sources cover: Part D 2025 is due
  around May 2027.
- Agents never use the CMS APIs. `make probe` keeps the probed API limits as a record only.

Three facts shape the eval:
1. Rows, individual payments and distinct recipients are three different counts. One row can
   bundle several payments (`Number_of_Payments_Included_in_Total_Amount`).
2. Teaching hospitals are a tiny share of rows but a large share of dollars, and the CMS
   national summary leaves them out.
3. **Only one release is loaded**, so no record appears twice. Republication double-counting
   (`REPUB`) can't happen, which means q008, q009 and q011 can't fail as written. Phase 3
   resolves this (see there).
4. **Part D drops small cells.** A prescriber × drug row with fewer than 11 claims is left out
   for patient privacy. So a missing row means "fewer than 11 or none", never "zero". It also
   means the per-prescriber rows add up to less than CMS's national total for the same drug.
5. **Part D has no manufacturer column.** It names drugs by brand and generic name only. Linking
   a company's payments to prescribing goes through the product names in Open Payments'
   product columns, which are free text ("OZEMPIC", "Ozempic").
6. **Part D covers Medicare patients only:** no commercial insurance and no Medicaid. Drugs given
   in a clinic (many infusions) are in Part B, which isn't loaded.

## 2. Arms: what the agent is given

The agent is the same in every arm: my own Messages API tool loop (`agents/harness.py`),
`claude-opus-5-5`, with a fixed prompt and effort. Only the context changes.

| Arm | Agent gets | Built by | Role |
|---|---|---|---|
| Raw | Read-only `sql()` on bronze, plus the CMS data dictionary and methodology docs | nobody | The "just give it the docs" baseline |
| Starter (round 0) | dbt models and MetricFlow YAML written from CMS docs only, time-boxed, tag `starter-v1` | me, before any agent run | What a data team ships in week one |
| Factory round N | Starter plus curator changes merged through round N | curator agent; I approve or reject each change | The claim |
| Reference | A semantic layer I write after studying dev failures, tag `reference-v1`, with my hours logged | me | Hand-built comparison. It isn't a ceiling: the factory may beat it |

Tools in the starter, factory and reference arms: `list_metrics`, `query_metrics`,
`explain_metric`, read-only `sql()` on silver/gold (never bronze), and `flag_gap(question_id,
what_was_missing, evidence)`.

## 3. The curator: what it sees and what it may change

The curator gets what a company has and nothing a company lacks.

| The curator sees | The curator never sees |
|---|---|
| The data, the CMS docs, and the current semantic layer and models | Gold answers or gold SQL |
| Agent traces and flags from dev runs | Anything from held-out: questions, traces, flags or scores |
| Right or wrong for each dev question | |
| Reviewer corrections on dev questions | |

- **Reviewer corrections** stand in for a human correcting the agent during a session. Each dev
  question has a frozen `correction:` field: the definition a domain expert would give
  ("count all five product columns"), never a number. When a dev question is wrong in most
  runs, its correction goes to the curator.
- **The curator may change** semantic YAML, metric and dimension descriptions, silver/gold dbt
  SQL, and its own seeds.
- **Every change is a pull request.** CI runs `dbt build`, `mf validate-configs`, the
  reconciliation test and `curator/guardrails.py`. The guardrails reject any diff that contains
  a literal answer value, a question ID or text from the held-out set.
- **I only approve or reject, and never edit.** If I edited the curator's changes, the factory
  would really be me.

## 4. Build order

Each phase ends in a gate. Don't start the next phase until the gate passes.

### Phase 0: Skeleton (done)
`make probe` passes.

### Phase 1: Sync to bronze (done)
`make verify` passes: running the sync twice loads 0 rows the second time, and bronze counts
match the source.

### Phase 2: Feasibility pilot (next; this phase can kill the project)
This tests whether a week-one semantic layer leaves enough wrong answers for a factory to fix.
1. Pick 12 dev draft questions with at least one per trap, skipping `REPUB`. Write their gold
   SQL against bronze. Never run the held-out drafts before the freeze.
2. Build the starter layer from the CMS docs only, time-boxed to 2 days. It includes the
   reconciliation test against the CMS national summary (`PBLCTN_NTNL_SMRY_*`): counts must
   match exactly, and dollars to within $1 per cell, by program year × General/Research ×
   Physician/NPP. Tag `starter-v1`. Don't touch it after seeing pilot results.
3. Write a minimal harness and grader. Numbers pass within ±0.5%, sets compare as sets,
   rankings compare as the top k in order.
4. Run the raw and starter arms 3 times each on the 12 questions.

**Decide:**
- If the starter fails 4 or more of the 12 (in most runs), across 3 or more traps, there's room
  for the factory, so continue.
- If it fails fewer than 4, the questions are too easy for a semantic layer. Add harder
  long-tail questions (medians, shares, multi-file, company families) before continuing, or stop.
- If raw and starter score about the same, the semantic layer adds nothing by itself. Note it,
  because it changes the story.

**Rule:** the pilot questions and their results go in the README, labeled "pilot." Questions
are never dropped one at a time for being answered correctly. Any cut is a whole trap, logged in
§8 with the reason. Otherwise I'd be keeping only the questions the starter fails.

**Gate:** the decision and its numbers are logged in §8.

### Phase 3: Write and freeze the eval
- 30 dev and 20 held-out hand-written questions, stratified by trap. Every dev question gets a
  `correction:`. Resolve every `verify:` item against the data.
- **Templated held-out variants.** About 10 templates (for example, "total PY{year} payments
  linked to {drug}"), each with a frozen parameter list and gold SQL generated from the same
  template. They're reported per trap, because 20 variants of one template test one trap.
- **One trap held out entirely,** with no dev questions and no corrections. It's chosen and
  logged in §8 before the freeze. It tests whether the factory learns anything it wasn't told.
- **Fix the trap list.** Drop `PAGE`, which only applied to API agents: retag q040–q044 by their
  next trap or rewrite them. Resolve `REPUB` by loading an earlier release of PY2023/PY2024
  if CMS still serves one, or else cut q008, q009 and q011. Pin q007's meaning of "added"
  (PY2024 uses `NEW`, while PY2025 uses both `NEW` and `ADD`).
- Delete `eval/questions/leaks_draft.yaml` (see §7).
- **Gate:** `git tag eval-v1` and a committed `eval/MANIFEST.sha256`, both before the first
  scored run.

### Phase 4: Baselines
Run the raw and starter arms on all of dev and held-out, 3 runs each. Every wrong answer gets a
trap label in `eval/taxonomy.md` (an LLM pre-labels; I review).
**Gate:** the raw and starter rows of the results table are filled in.

### Phase 5: Reference layer
I write it from dev failures only and log my hours. Tag `reference-v1` **before any factory
round**, so the reference can't copy the curator. Then run it.
**Gate:** the reference row is filled in.

### Phase 6: Factory rounds
Each round goes: dev runs → flags, traces and corrections → curator PR → CI → I approve or
reject → rerun dev and held-out. Run up to 3 rounds, and stop early when a round produces no new
held-out wrong→right flips.
**Gate:** every round is reported, including rounds that didn't help.

### Phase 7: Drift (stretch)
Build a starter variant against PY2023–2024 only, then load PY2025, which marks changes
differently (`ADD` appears) and brings new products. Then measure whether the factory adapts
from flags alone.

### Phase 8: Write-up
`reports/make_readme_table.py` renders every README table from `reports/results.json`. Add one
worked example per top trap: the question, the wrong answer, the flag, the curator's PR, and the
answer after the fix.

**Cut line if time-boxed,** in this order: drift, then factory round 3, then the raw arm.
**Never cut:** the frozen eval, the held-out set, reconciliation, the curator's no-answers rule,
or reporting rounds that didn't help.

## 5. Traps

Every question is tagged with the trap it targets, and the taxonomy uses the same tags.

| Tag | Trap | Example |
|---|---|---|
| `GRAIN` | Rows vs payments vs distinct recipients | "How many payments did NPs receive in 2024?" |
| `REPUB` | Republished records double-counted (only if a second release is loaded) | "Total general payments in PY2023" |
| `WIDE` | Products 1–5 and specialties 1–6 sit in separate columns; agents read only the first | "Total paid in connection with Ozempic" |
| `RTYPE` | Physicians vs NPPs vs teaching hospitals | "Share of payments to non-physicians" |
| `FILES` | General vs Research vs Ownership, missed or double-counted | "All money from company M to Dr. Y" |
| `ENTITY` | Company families and name variants; Profile ID vs NPI | "How much did J&J companies pay?" |
| `TIME` | Program year vs `Date_of_Payment`; dispute and delay flags | "Payments made in Q4 2024" |
| `TAXON` | Pipe-delimited specialty strings vs NPPES taxonomy codes | "Payments to interventional cardiologists" |
| `SUPPR` | Part D rows under 11 claims are missing; a missing row isn't zero | "How many paid consultants never prescribed the drug?" |

## 6. Measurement

- **Right or wrong:** a question counts as right for an arm if it's right in at least 2 of 3 runs.
- **Flips (the headline):** for each pair of arms, on held-out, count wrong→right and right→wrong.
  Comparing the same questions before and after is more trustworthy than comparing two
  percentages at this sample size.
- **Gap closed** on held-out = (factory − starter) ÷ (reference − starter), counted in
  questions. It's not reported if reference − starter is under 5 questions, because that's too
  small to divide by.
- Results are also reported **per trap**, with the held-out trap on its own.
- **Cost:** agent spend per correct answer and curator spend per round, both from `usage` ×
  `config/pricing.yml`, plus my logged hours for the reference layer.
- Accuracy gets a 95% CI by bootstrap over questions.

## 7. Cut (decided 2026-11-26)

- **Live-API agents (old L1), the API shim, 429 counting and the `PAGE` trap.** Nobody answers
  aggregate questions by paging an API.
- **Load test.** It would measure a laptop, not the data layer.
- **Entity-resolution benchmark.** It fills about 7k blank NPIs out of 16M rows, which is off
  the claim.
- **Permission-leak personas.** Access control belongs to the database (RBAC), not to agent
  instructions.

## 8. Decision log

- 2026-11-26: Pivot from "four context levels" to "measured knowledge factory" (§2–§3). The
  cuts are in §7.
- 2026-11-26: Added Medicare Part D 2023–2024 (§1) and 15 compliance and fraud-signal
  questions (§9). 14 went into the draft eval as q051–q064, adding the `SUPPR` trap. One is held
  back because it needs a clinical judgment seed. The draft pool is now 64; Phase 3 still picks
  30 dev and 20 held-out, and `SUPPR` needs at least 2 held-out questions like every trap.
- 2026-11-26: Phase 2 build (pilot not yet run).
  - **Pilot questions** (`eval/pilot.yaml`): q002, q004, q012, q013, q018, q019, q024, q026,
    q029, q035, q046, q064. Every trap except `REPUB` is a primary tag at least once. None
    needs a seed, so a wrong answer can't come from a list only the eval owns.
  - **Gold checks against CMS:** q002 (median $173.11) and q024's direct research
    ($62,512,736.88) equal the CMS national summary exactly.
  - **Starter built by an isolated Claude subagent, not by me.** It was told to work from CMS
    docs and the bronze schema only and never read `eval/`, this file or the README. That
    keeps the trap list out of the starter better than I could. It took about 35 minutes, not
    2 days. It reconciles exactly (12 of 12 cells, $0.00 off; CMS's payment count = records).
  - **Grading names:** a company or hospital is graded by ID, so gold accepts any name string
    that ID was reported under. The agent is told to report names as they appear in the data.
  - **q035's answer shape** changed from `month: str` to `month_number: int` so the grader
    doesn't have to parse month names.
  - **Agent settings** (`config/agent.yml`): `claude-opus-5-5`, effort high, 30 turns, no
    refusal fallback to another model (a refusal is recorded as wrong, never answered by a
    different model).

## 9. Compliance and fraud-signal questions

The 50 original drafts mostly total and rank money, like the journalism they were modeled on.
These 15 ask what a company's compliance team would check before a regulator or a
whistleblower's lawyer does. Each is a red flag drawn from the OIG's 2020 Special Fraud Alert
on speaker programs or from past Justice Department settlements.

**Rule:** a red flag becomes an eval question only when its definition fixes one right answer
(for example, "records dated after the NPI's deactivation date"). Whether any flagged payment
is improper is never graded, because the data can't say. No public list of "fraudulent
payments" exists to grade against; the closest, the OIG exclusion list (LEIE), isn't loaded.

| # | Red flag | Example | Data | Draft |
|---|---|---|---|---|
| 1 | The same doctors give paid talks over and over | One doctor paid for 40 talks by one company in a year | General | q051 |
| 2 | Speaker fees above what peers pay | $5,000 a talk where others pay $2,500 | General | q052 |
| 3 | The same doctors fed again and again | 60 meals from one company in a year | General | q053 |
| 4 | Travel to destinations abroad | Lodging in another country for a "meeting" | General | q054 |
| 5 | Payments to specialties outside the product's approved use | A cancer drug paying psychiatrists | General + a product-to-specialty seed | **held back** |
| 6 | Payments to an NPI already deactivated | Paid in March 2025, deactivated in 2024 | General + NPPES | q055 |
| 7 | Royalties plus ownership in the same company | A surgeon who owns part of the company that pays them royalties | General + Ownership | q056 |
| 8 | "Research" with no registered trial | Clinical research money with no ClinicalTrials.gov ID | Research | q057 |
| 9 | Money routed through a third party | A payment to an entity instead of the doctor | General | q058 |
| 10 | Messier reporting than peers | A company that resubmits nearly all of a year's records | General | q059 |
| 11 | Paid doctors prescribe more of the product | Paid vs unpaid Ozempic prescribers in Medicare | General + Part D | q060 |
| 12 | Speakers picked because they already prescribe a lot | Speakers in the top 10% of prescribers the year before | General + Part D | q061 |
| 13 | Paid consultants who barely use the product | $5,000+ for consulting or speaking, no Part D row for the drug | General + Part D | q062 |
| 14 | Prescribing rises after the first payment | Change in Mounjaro claims, first paid in 2024 vs never paid | General + Part D | q063 |
| 15 | Per-prescriber rows don't add up to the national total | Ozempic claims missing because of the 11-claim cutoff | Part D | q064 |

**Why #5 is held back:** "outside the approved use" needs a map from each product's therapeutic
area to the specialties that legitimately treat it. That's a clinical judgment, for instance
dermatologists do treat melanoma, so a cancer drug paying them isn't a red flag. Add it only
with a seed a clinician has reviewed.

**Checked against the data (2026-11-26):** each question returns a non-trivial answer. Two
first versions didn't and were reworded: PY2024 has only 327 disputed records in total, so #10
uses changed records instead; and only one physician got $10,000+ in Ozempic consulting, so #13
uses $5,000+ for consulting or speaking.

**Limits stated once:** Part D covers Medicare patients and 2023–2024 only, and rows under 11
claims are missing, so #11–#14 compare prescribers with 11+ claims. Year-level data can't show
whether prescribing rose before or after a payment within the same year.
