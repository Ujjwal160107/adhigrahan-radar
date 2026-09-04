# Adhigrahan Radar — SIH 2026 Idea Submission Content

**Problem statement:** SIH26017 — *Predictive Analytics System for Early Detection of Land
Acquisition Delays*
**Organisation:** Ministry of Rural Development, Dept. of Land Resources
**Category:** Software · **Theme:** Smart Automation
**Team:** Vellore Institute of Technology, Vellore
**Design references:** `docs/specs/2026-08-30-sih26017-acquisition-delay-design.md`,
`docs/architecture/adhigrahan-radar-architecture.md`, `pipeline/README.md`

> Every number in this document traces to a build artifact. Do not pad any of them for a
> slide. Where a figure is commonly cited rather than measured by us, it says so.

---

## 1. Problem statement

**SIH26017 — Predictive Analytics System for Early Detection of Land Acquisition Delays.**

### The gap in one line

India monitors land acquisition **after** it has already slipped. Nothing flags a project as
likely to stall *before* the statutory clock runs out.

### Detailed explanation

Land acquisition is the critical path of nearly every infrastructure project — highways,
canals, industrial corridors, rail. It is also the phase where projects die quietly.

The acquisition lifecycle is **legally time-boxed**, and those boxes are the whole point:

| Transition | Deadline | Consequence of breach |
|---|---|---|
| §3A → §3D (NH Act 1956) | 365 days | 3A notification **lapses** under s.3D(3) |
| §11 → §19 (RFCTLARR 2013) | 365 days | Preliminary notification lapses, s.19(7) |
| §19 → §23 award | 365 days | Acquisition proceedings lapse, s.25 |

A delay here is not an administrative inconvenience. **Missing the clock voids the
acquisition.** The state restarts a multi-year process, re-notifies, re-values the land at a
higher rate, and pays more compensation for the same parcels.

**Why it is not already solved.** Every driver of delay is already recorded somewhere — in a
court file, a gazette notification, a compensation register, an R&R roll. Each lives in a
different department's system, keyed differently, and none is read together. Monitoring
today is **retrospective reporting**: a monthly return telling an officer that a project has
already crossed 300 days, at which point there is nothing left to do.

The single largest driver — **pending litigation on the parcels being acquired** — is the
least visible of all, because court records are indexed by **party name** and land records by
**survey number**. The two systems have never agreed on a key. Nobody can currently answer
*"how many parcels in this corridor are under active dispute?"*

The PS asks for prediction, risk scoring, driver identification, explainability, dashboards,
GIS, alerts, recommendations, continuous learning, APIs and role-based access. All twelve
deliverables reduce to one question: **which projects are about to breach, and why?**

---

## 2. Innovation and uniqueness

### The core innovation: we compute a feature nobody else can

Every team attempting this PS will build a risk model from administrative metadata already
sitting in the project file — days elapsed, approvals pending, area, families. Such a model
can only tell an officer what the file already tells them.

**We compute the litigation feature.** For any project we can state: *11 of 34 parcels in
this corridor carry an active court case; one carries an interim injunction; median pendency
2.6 years.*

That requires a **court↔parcel record-linkage layer** — which we have already built, and
which no land record in India carries. Our working prototype earns a match at **0.9105
confidence** where the court writes survey `1365/1` in village *Madanpur Paniyar* and the
land record writes `1365-1` in *Madanpur Panyar*, reconciled by survey normalisation, a
village gazetteer, and fuzzy name matching. That is the moat.

### Five more genuine differences

| # | Innovation | Why it matters |
|---|---|---|
| 1 | **Statutory labels, not opinion labels** | "Delayed" is defined by s.3D(3), s.19(7) and s.25 — not by a threshold we invented. The target variable is law. |
| 2 | **Per-stage prediction, not one project score** | The PS asks for probability of delay at *different stages*. One calibrated model per lifecycle transition, so the answer is actionable: *this project's 3D clock is the one at risk.* |
| 3 | **Recommendations are retrieved, never generated** | Every action comes from a `driver → action` rule table with a rule id. No language model invents an administrative instruction. |
| 4 | **Precision-first bands with a suppression rule** | HIGH is emitted only if it clears ≥ 0.70 precision on held-out data. If it cannot, the band is **not shown**. A false HIGH panics a project office; we would rather say less. |
| 5 | **Provenance on every row, and a hard honesty rule** | Synthetic data may train the model; **only real data may appear in a reported metric.** |

### What we are deliberately not building

Not a blockchain registry. Not an OCR digitisation tool. Not a chatbot. Not a land-record
portal. **A prediction and explanation layer over records that already exist** — it creates,
corrects and adjudicates nothing.

---

## 3. Technical approach and methodology

### Architecture in one rule

**Everything expensive happens before the demo.** Fuzzy matching, feature building,
training, calibration and SHAP all run in an offline build. At request time the API does
nothing but `SELECT`. Result: sub-second responses, no model in the request path, and a
system that cannot fail live because a model failed to load.

### The layering

```
Vivaad Radar (built)          ->   feature provider
court<->parcel linkage engine      share_parcels_red, n_active_cases,
38 real HC cases                   has_interim_order, max_pendency_days
135 parcels, 84 links                          |
                                               v
                              Adhigrahan Radar (new)
                              per-stage delay model + drivers + actions
```

The linkage engine stops being a product and becomes an input. That is why the extension is
additive: stages `s0`–`s7` are not modified, `s8`–`s15` are appended, and all **47 existing
tests stay green** — that green suite is the proof the change is safe.

### Stack

| Layer | Choice | Why |
|---|---|---|
| Pipeline | Python, staged, inspectable artifact + report per stage | Every stage re-runnable; the reports are the evaluation slide |
| ML | scikit-learn `HistGradientBoostingClassifier` + `CalibratedClassifierCV`, SHAP `TreeExplainer` | Calibrated probabilities; tree SHAP gives exact additive attribution |
| Matching | regex + RapidFuzz + village gazetteer | Deterministic, explainable, already validated |
| Store | SQLite, 13 tables, everything precomputed | Zero setup; relational + link tables, not a graph DB |
| API | FastAPI, 14 read endpoints | One `SELECT` each |
| UI | React (Vite) + Tailwind + Leaflet | Existing "Legal Neubrutalism" design system |

### Methodology — six steps

1. **Ingest.** Cached Bhoomi Rashi 3A/3D notifications across ≥ 8 UP districts (real, dated),
   plus provenance-labelled synthetic projects bound to the existing Sultanpur corpus.
2. **Bind.** Project → parcels using the *same* survey normaliser and village gazetteer that
   already reconcile `1365-1`/`1365/1`. **No new matcher is written.**
3. **Featurise.** One row per (project, stage), every value computed **as of stage entry**,
   with a build-time leakage audit that raises on violation.
4. **Train.** Time-based split — train on stages closed before a cutoff, test after. Never a
   random shuffle on temporal data.
5. **Calibrate and threshold.** Isotonic or Platt calibration, then band cut-points chosen on
   the holdout to hit a stated precision target.
6. **Explain and act.** SHAP top-5 drivers per project persisted at build time; drivers mapped
   to a retrieved action table.

### Verification

Ten new tests, of which four are the ones that matter: no future leakage, district aggregates
train-only, holdout Brier beats base rate, and every reported metric row is real.

---

## 4. Data flow — start to finish

```
(1) Bhoomi Rashi 3A/3D snapshot --+
    eCourts / HC order corpus  ---+
    Land parcel records        ---+
             |  raw, immutable, provenance-stamped
             v
(2) NORMALISE   survey 1365-1 = 1365/1 | village Panyar ~ Paniyar | names
             v
(3) LINK        court case <-> parcel, weighted fuzzy score
                -> HIGH / MEDIUM / LOW + evidence JSON
             v
(4) BIND        acquisition project <-> its parcels (same normaliser, no new matcher)
             v
(5) FEATURISE   one row per (project, stage), every value as of stage entry
                <- litigation features flow in from (3)
             v
(6) TRAIN       time split -> GBM -> calibrate -> choose bands on holdout
             v
(7) SCORE       open stages -> probability + band + SHAP drivers + retrieved action
             v
(8) PERSIST     13 tables in SQLite; every response also rendered to a JSON cache
        ============ OFFLINE BOUNDARY ============
(9) SERVE       FastAPI, 14 endpoints, one SELECT each, no model loaded
             v
(10) RENDER     risk dashboard -> project timeline -> driver panel -> parcel evidence
```

**What changes at each step:** raw text becomes normalised identifiers (2); identifiers
become scored links with evidence (3, 4); links become a numeric feature matrix (5); the
matrix becomes a calibrated probability and a ranked driver list (6, 7); and that becomes a
row the API reads without computing anything (8, 9).

---

## 5. ML pipeline and feature vectors

### Pipeline

```
features.parquet
   |  one row = (project_id, stage), label from statute
   +- split:    train = stages closed before cutoff
   |            test  = stages closed after cutoff
   |            censored = still open -> excluded
   +- model:    HistGradientBoostingClassifier(max_depth=3, max_leaf_nodes=8,
   |                                           l2_regularization=1.0, early_stopping=True)
   +- calibrate: CalibratedClassifierCV(isotonic if n >= 200 else sigmoid)
   +- baselines: base rate | L2 logistic regression   <- reported in the same table
   +- threshold: t_high = lowest p with holdout precision >= 0.70
   |             t_med  = lowest p with holdout recall    >= 0.80
   +- explain:   SHAP TreeExplainer -> top-5 signed drivers, persisted at build time
```

**One model per stage.** Stages: `notification_3a_11`, `declaration_3d_19`, `award_3g_23`,
`compensation_disbursed`, `possession`.

**Label:** `is_delayed = (completed_on − started_on) > statutory_days`; `NULL` if the stage is
still open (right-censored → excluded from training, included in scoring).

**Metrics:** ROC-AUC, PR-AUC, **Brier score**, reliability curve — against the base-rate and
LR baselines. *If LR wins on the holdout, LR ships and the slide says so.*

### Feature vector — 20 features per (project, stage) row

**A · Litigation block — the differentiator (9)**

| Feature | Type | Range / unit |
|---|---|---|
| `share_parcels_red` | float | 0–1 |
| `share_parcels_amber` | float | 0–1 |
| `n_active_cases` | int | count |
| `max_case_pendency_days` | int | days |
| `median_case_pendency_days` | int | days |
| `n_acquisition_compensation_cases` | int | count |
| `n_title_partition_cases` | int | count |
| `has_interim_order` | bool | 0/1 — strongest single stop signal |
| `n_high_confidence_links` | int | count |

**B · Project intrinsics (5)**

| Feature | Type |
|---|---|
| `area_hectares` | float |
| `n_parcels` | int |
| `n_villages` | int |
| `affected_families` | int |
| `act` | categorical — `NH_1956` / `RFCTLARR_2013` |

**C · Administrative (4)**

| Feature | Type | Note |
|---|---|---|
| `days_in_current_stage` | int | |
| `n_prior_stage_overruns` | int | this project's own history |
| `gazette_republication_count` | int | a 3A extension is itself a recorded delay signal |
| `compensation_disbursed_share` | float 0–1 | |

**D · District context (3)** — *computed on the training split only, never the full corpus*

| Feature | Type |
|---|---|
| `district_median_3a_to_3d_days` | float |
| `district_active_land_cases` | int |
| `district_completed_projects` | int |

**Leakage rule.** Every feature carries `computed_asof`; the build asserts no contributing row
post-dates `stage.started_on`. A violation raises at build time.

**`predicted_overrun_days` is not a second model** — it is the empirical median overrun among
delayed holdout stages of the same stage and band, rendered as *"typically N days late when
this happens."*

---

## 6. Feasibility

### Strong

| Factor | Evidence |
|---|---|
| The hardest component already works | 38 real HC cases, 135 parcels, 1,678 pairs scored → 84 links, flagship RED at 0.9105, 47 tests green |
| Labels need no annotation | Statute defines "delayed". Zero human labelling cost. |
| A real dated source exists and is public | Bhoomi Rashi carries ~1,467 NHAI projects with 3A/3D notification dates in the e-Gazette |
| No GPU, no cloud | scikit-learn on a laptop, SQLite, FastAPI. The existing build runs in ~1.3 s. |
| The change is additive | `s0`–`s7` untouched; the existing test suite is the regression gate |

### Honest weaknesses

| Weakness | Position we take |
|---|---|
| The PS assumes "large volumes" of acquisition data. Publicly, at ML scale, it does not exist. | Say so on the slide. Real Bhoomi Rashi projects are the labelled spine; synthetic rows are labelled and never enter a reported metric. |
| Small `n` risks overfitting | Capped at 20 features, `max_depth=3`, time split, two baselines reported. If LR wins, LR ships. |
| Cadastral geometry is not publicly available as clean GeoJSON for our states | Corridors render schematically and the screen says so. We do not borrow another state's polygons. |

**Verdict:** feasible as a working district-scale prototype within the hackathon window,
because the expensive half is already built and the labels come from statute rather than from
annotation.

---

## 7. Challenges and mitigations

| # | Challenge | Mitigation |
|---|---|---|
| 1 | **Thin real training data** | Statutory labels need no annotation; synthetic rows train but never score; both baselines reported so skill is not overstated |
| 2 | **Feature leakage inflating metrics** | `computed_asof` on every feature, build-time audit that raises, district aggregates train-only, two dedicated tests |
| 3 | **A false HIGH panics a project office** | HIGH emitted only if holdout precision ≥ 0.70; otherwise **suppressed entirely**, and the UI states why |
| 4 | **Overfitting on a small corpus** | 20-feature cap, shallow trees, strong L2, early stopping, time-based split |
| 5 | **Court identifiers never match land identifiers** | Already solved: survey normalisation + village gazetteer + fuzzy names, proven on the `1365-1`/`1365/1`, `Panyar`/`Paniyar` case |
| 6 | **Portal scraping terms and availability** | Cached snapshot committed to the repo; the demo never touches the network |
| 7 | **"Probability" read as certainty by an officer** | Calibration + reliability curve; `RiskBadge` carries a baked-in disclaimer — *predicted risk of missing a statutory deadline, not an administrative finding* |
| 8 | **Two of five stage clocks are targets, not statute** | Every row carries `clock_source`; every screen renders the distinction |
| 9 | **Demo-day failure** | Three-tier fallback: live DB → cached JSON at the same URLs → flagship payloads bundled in the frontend. Kill the database mid-demo and it still answers. |
| 10 | **"This is just your old project relabelled"** | Litigation is one of four feature families. New unit of analysis, new users, new model, new screens. The old spec is retained *as a subsystem*, which makes the layering visible rather than hidden. |

---

## 8. Impact and benefits

### Who is affected, and how

**Landowners and affected families — who bear the cost today.**
When an acquisition lapses under s.3D(3) or s.25, families who have already lost planning
certainty over their land go back to the start of a multi-year process. Their land is neither
theirs to use freely nor paid for. Early intervention on a stalling project directly reduces
that limbo. Where the delay driver is a pending case, surfacing it early routes the family to
the LARR Authority years sooner than a monthly return would.

**District officers and CALA.**
Today: a monthly report saying a project already crossed 300 days. With this: a ranked list
saying *these four projects will breach in under 90 days, and here is the specific driver for
each.* Monitoring shifts from reactive to prospective — precisely the shift the problem
statement asks for.

**Public expenditure.**
A lapsed notification means re-notification at current market value plus the statutory
multiplier, plus contractor idling on a project that cannot start. Every avoided lapse is an
avoided re-valuation.

**Infrastructure delivery.**
Land is the critical path. Compressing the tail of acquisition compresses the project.

**A public-interest side effect.**
The court↔parcel index built as a feature source is, independently, the linkage DILRMP has
stated as a long-term ambition — revenue, registration and court records against one parcel
ID. This project demonstrates that join is buildable today from public data.

### Measurable outcomes to claim

| Metric | Target |
|---|---|
| Lead time on a HIGH-risk flag before the statutory deadline | ≥ 90 days median |
| HIGH-band precision on held-out real projects | ≥ 0.70, else the band is suppressed |
| Brier score vs. base-rate baseline | Positive skill on the holdout |
| Parcels correctly linked to an active case (high-confidence precision) | ≥ 90% identifier-level |
| Time for an officer to see a project's delay drivers | < 3 s, vs. a manual court-by-court search today |

**What we will not claim:** we do not predict how a court will rule, we do not replace the
CALA's judgement, and a LOW risk band is not a guarantee of on-time completion.

---

## 9. Recommendations, business model, other pointers

### Deployment and sustainability model

This is **government-internal decision support**, not a consumer product, and pretending
otherwise weakens the pitch. The honest model:

| Layer | Model |
|---|---|
| **Primary** | Deployed as a module under DoLR / MoRTH monitoring, funded from existing project-monitoring budgets. Cost is a fraction of a single lapsed notification. |
| **Sustaining** | Per-district onboarding: gazetteer cleanup, court-establishment mapping, officer training. Repeatable, roughly ₹5–6 lakh per district pilot on our previous costing. |
| **Extension** | The court↔parcel index is independently valuable as an API to banks, lenders and title insurers doing pre-sanction diligence — the *same* index, a second consumer. Post-pilot, not in SIH scope. |
| **Not the model** | No per-citizen subscription, no ad-supported portal, no selling land data. |

### Roadmap after SIH

1. **Now** — district-scale prototype, one state, real court corpus, statutory labels
2. **Pilot (6 months)** — one district with an MoU for the acquisition feed; the officer uses
   the ranked list in the monthly monitoring meeting instead of a printed return
3. **Scale** — the architecture is district-agnostic by construction; scaling is a
   data-onboarding cost, not a rebuild
4. **Integration** — expose as an API into existing land-acquisition management systems
   (PS deliverable 11) rather than asking anyone to change their workflow

### Three pitch recommendations

**Lead with the demo beat, not the architecture.** The moment that wins is: *sort by risk →
open the top project → the #1 driver is "11 of 34 parcels under active litigation" → click
through to the actual court order matched to the actual survey number despite `1365-1` vs
`1365/1`.* No other team can reproduce that click.

**Make the honesty a feature.** Say out loud that the public data is thinner than the PS
implies, that synthetic rows never enter a metric, that two of five clocks are targets rather
than statute, and that if logistic regression beats gradient boosting you will ship logistic
regression. Judges remember the team that volunteered its own weaknesses.

**Correct one number before submitting.** The Vivaad Radar idea PDF says 42 automated tests.
The suite is **47**. Fix it — a judge who checks will find the discrepancy, and it costs
credibility for nothing.

---

## Appendix — figures and their sources

| Figure | Source |
|---|---|
| 38 real cases (8 active), 135 synthetic parcels, 22 villages | `pipeline/README.md`, current build |
| 1,678 pairs scored → 84 links (43 HIGH, 41 MEDIUM) | `s4_report.json` |
| 12 RED · 62 AMBER · 61 GREEN | `s5_report.json` |
| Flagship `P-B01` RED @ 0.9105 | `s5_report.json`, `tests/test_golden.py` |
| Longest pendency ~2.6 years | `s1_report.json` — **not** the PRD's illustrative "6 years" |
| 47 tests green | `pytest tests/ backend/tests/ --collect-only` |
| ~1,467 NHAI projects on Bhoomi Rashi | PIB release, portal figure |
| §3A/§3D, §11/§19/§23/§25 clocks | NH Act 1956; RFCTLARR 2013 |
| "~20 years to resolve a land dispute", "land is ~2/3 of civil litigation" | Commonly cited (NITI Aayog / Law Commission). **Attribute as commonly cited, never as measured by us.** |
