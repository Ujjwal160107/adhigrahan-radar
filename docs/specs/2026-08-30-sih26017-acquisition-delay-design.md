# Adhigrahan Radar — SIH26017 Extension Design

**Date:** 2026-08-30
**Status:** Approved in brainstorming session (2026-08-30)
**Problem statement:** SIH26017 — *Predictive Analytics System for Early Detection of Land
Acquisition Delays*. Ministry of Rural Development, Dept. of Land Resources. Software.
**Parent documents:** `docs/product/vivaad-radar-prd.md`,
`docs/specs/2026-08-20-vivaad-radar-architecture-design.md`
**Companion diagrams:** `docs/architecture/vivaad-radar-architecture.excalidraw` (regenerated),
`docs/architecture/adhigrahan-ml-pipeline.excalidraw` (new)

---

## 0. One-paragraph summary

Adhigrahan Radar predicts which land-acquisition projects will miss their statutory
deadlines, before they miss them. It is built by adding a risk layer on top of the existing
Vivaad Radar court↔parcel linkage engine, which becomes a **feature provider**: the share of
a project's parcels under active litigation is the one delay driver no competing team can
compute, because computing it requires the court-to-parcel join this repo already runs.
Stages `s0`–`s7` are not modified. Stages `s8`–`s15` are added. All 47 existing tests must
stay green; that green suite is the operational definition of "minimal change".

---

## 1. Decisions taken (2026-08-30)

| Decision | Choice | Rejected |
|---|---|---|
| Change shape | Additive: `s0`–`s7` untouched, `s8`–`s15` appended | Split into `pipeline/linkage` + `pipeline/risk` (churns every import for zero demo value); rebuild around `AcquisitionProject` as root (discards a working golden suite) |
| Acquisition data | **Hybrid** — real cached Bhoomi Rashi 3A/3D notifications as the labelled spine, synthetic Sultanpur-bound projects for the drill-down demo, both provenance-labelled | Real-only (loses the litigation drill-down; NHAI corridors do not overlap the Sultanpur court corpus); synthetic-only (a model trained on invented data is the first thing a judge attacks) |
| Vivaad Radar's citizen surface | **Demoted to an internal drill-down** — the "why is this project at risk" evidence panel | Keep both surfaces (invites "this is two projects"); remove entirely (discards the only real data in the build) |
| ML | **Per-stage calibrated gradient boosting + SHAP**, with logistic-regression and base-rate baselines reported alongside | LR-only (reads weak for an AI/ML PS); survival model (correct frame, heavier to explain and to make explainable) |
| Geographic scope | **Multi-district Uttar Pradesh**, at least 8 districts, Sultanpur among them | Single district (cannot render the district-wise and state-wise trends the PS demands); multi-state (dashboard looks better, data becomes mostly synthetic) |
| Role-based access + audit trail | **Build it, labelled as mocked**; first item on the cut list | Skip it (the PS names it explicitly as deliverable 12) |
| Product naming | Product = **Adhigrahan Radar**. **Vivaad Radar** is retained as the name of its court↔parcel linkage engine, now a subsystem | Rename everything |
| `vivaad.db` filename and `VIVAAD_DB` env var | **Unchanged, deliberately** | Renaming touches `backend/db.py`, `backend/fallback.py`, every test and every fallback path, and buys nothing a judge will ever see. Documented in `pipeline/README.md` instead |

---

## 2. Architecture

```
  cases.parquet  ──┐
                   ├─ s0..s7 ──▶ vivaad.db (8 tables) ──▶ backend (8 endpoints) ──▶ UI
  parcels.parquet ─┘                     │     UNTOUCHED
                                         │
                  litigation features per parcel (Parcel.status, ParcelCaseLink)
                                         │
  bhoomirashi cache ──▶ s8..s15 ─────────┴──▶ +5 tables, +6 endpoints, +3 screens
```

**The architectural idea in one sentence:** the linkage engine stops being a product and
becomes a feature provider.

`s11_features` reads `vivaad.db` *after* `s6` has built it. Reading a database an earlier
stage of the same offline build produced is legitimate; nothing in the request path changes.

### 2.1 Stage list

`s8`–`s15` mirror the rhythm of `s0`–`s7` deliberately — a handoff stage that builds the
contract, an ingest stage that validates it and fails loudly, then transform, load, export.

| Stage | Responsibility | Output |
|---|---|---|
| `s8_acquisition_handoff` | Build the acquisition data contract from (a) the cached Bhoomi Rashi snapshot in `data/raw/bhoomirashi/` and (b) a synthetic generator seeded on the real Sultanpur villages. Mirrors `s0`, which likewise reads a real corpus and generates its synthetic counterpart | `data/input/acquisitions.parquet`, `data/input/project_stages.parquet` |
| `s9_acquisition_ingest` | Validate the contract: required columns, provenance label on every row, a resolvable statutory clock for every stage, flagship project present. **Raises on any violation** | `acquisitions.json` |
| `s10_project_bind` | Bind project → parcels. Reuses `s2`'s survey normaliser and village gazetteer and `s3`'s village blocking; `binding_confidence` is `s4._village` and `s4._identifier` reweighted over just those two features. **No new matcher is written** | `project_parcels.json` |
| `s11_features` | Build the per-`(project, stage)` feature matrix as of stage entry. Runs the leakage audit | `features.parquet`, `s11_report.json` |
| `s12_train` | Per-stage calibrated GBM plus LR and base-rate baselines. Time-based split. Choose risk-band thresholds on the holdout | `data/output/models/` (committed — small, and a fresh clone must serve without retraining), `s12_report.json` |
| `s13_risk_score` | Score every open stage; SHAP drivers; map drivers to retrieved recommendations | `project_risk.json` |
| `s14_load_risk_db` | `CREATE` the five new tables in `vivaad.db` and fill them. **Never `DROP`s any of the existing eight** | `vivaad.db` (13 tables) |
| `s15_export_risk_fallback` | Render every new endpoint response to `data/output/fallback/`, using the two naming conventions `backend/fallback.py` already resolves: per-resource routes nest (`/projects/PRJ-B01/risk` → `projects/PRJ-B01/risk.json`), dashboard routes are flat (`/dashboard/risk` → `dashboard_risk.json`) | `fallback/projects/*`, `fallback/dashboard_risk.json`, `fallback/models_history.json` |

`run_all.py` gains the eight stages in order. `--skip-handoff` continues to skip `s0`; a new
`--risk-only` flag runs `s8`–`s15` against an existing `vivaad.db` for fast model iteration.

### 2.2 What is explicitly not changed

- The `s0`–`s7` source files.
- The eight existing tables, their columns, and `Parcel.status` semantics.
- The eight existing endpoints and their response shapes.
- `backend/fallback.py` resolution logic — new files follow the same two naming conventions.
- The 47 existing tests.

---

## 3. Data model — five new tables (8 → 13)

The PRD's **"exactly eight tables"** invariant (PRD §19, architecture design §5b) is
**superseded by this document**. Recording that explicitly so it does not read as drift.

`AuditLog` (§7.1) would be a fourteenth table. It is deliberately not counted among the five:
it exists only if the role stub is built, and the role stub is first on the cut list.

### `AcquisitionProject`

`id, name, project_type, executing_agency, act, state, district, block, nh_no, gazette_ref,
area_hectares, affected_families, budget_estimate_inr, current_stage, stage_entered_on,
status, source_label`

- `act` ∈ `{NH_1956, RFCTLARR_2013}` — decides which statutory clock applies.
- `status` ∈ `{open, completed, lapsed}`.
- `source_label` ∈ the PRD §21 provenance set. Mandatory, as everywhere else in this repo.

### `ProjectStage`

`id, project_id, stage, statutory_days, clock_source, started_on, completed_on,
overdue_days, is_delayed, source_label`

- `stage` ∈ `{notification_3a_11, declaration_3d_19, award_3g_23, compensation_disbursed,
  possession, r_and_r}`. `r_and_r` runs in parallel with the others, not in sequence.
- `is_delayed` is `NULL` for open (right-censored) stages — never `0`. A stage that has not
  finished has not yet been on time.

### `ProjectParcel`

`project_id, parcel_id, village_canon, binding_confidence, binding_evidence`

`binding_evidence` uses the same JSON shape as `ParcelCaseLink.evidence`, so the frontend
evidence renderer is reused rather than rewritten.

### `ProjectRisk`

`project_id, stage, delay_probability, risk_band, predicted_overrun_days, model_version,
scored_at, drivers, recommendations`

- `drivers` and `recommendations` are JSON computed at build time. **No SHAP and no model
  inference at query time** — PRD §36/§48 survive intact.
- `risk_band` ∈ `{LOW, MEDIUM, HIGH}`.
- `predicted_overrun_days` is **not a second model**. It is the empirical median overrun
  among delayed holdout stages of the same `stage` and `risk_band`, looked up from
  `ModelRun.metrics`, and rendered as "typically N days late when this happens". A second
  regression head is not justified at this corpus size (YAGNI), and an unlabelled point
  estimate would be worse than an honest empirical median.

### `ModelRun`

`id, model_version, trained_at, algo, n_train, n_test, cutoff_date, metrics, feature_list,
thresholds, notes`

The model registry. `GET /models/history` reads it. This is how "continuous model learning"
(PS deliverable 10) is demonstrated honestly.

### One-column change to an existing table

`Watchlist` gains a nullable `project_id`. That is the entire implementation of PS
deliverable 8 (automated alerts): the mocked-badge mechanism already exists and already has
a UI. A second notification subsystem would be a duplicate.

---

## 4. Labels — statutory, not invented

| Clock | Days | `clock_source` |
|---|---|---|
| 3A → 3D | 365 | `statute` — NH Act 1956, s.3D(3) lapse clock |
| s.11 → s.19 | 365 | `statute` — RFCTLARR 2013, s.19(7) |
| s.19 → s.23 (award) | 365 | `statute` — RFCTLARR 2013, s.25 |
| award → compensation disbursed | 90 | `administrative_target` |
| award → possession | 90 | `administrative_target` |

```
is_delayed = (completed_on - started_on) > statutory_days
```

**The `clock_source` column is not decoration.** Two of the five clocks are administrative
targets we chose, not law. Every screen and every slide showing a deadline must show which
kind it is. Same rule as `next_hearing_source = 'derived'`, for the same reason: an
unlabelled fabricated deadline is the cheapest possible credibility loss.

**Censoring.** Open stages carry `is_delayed = NULL`. They are excluded from training and
included in scoring. The evaluation slide states both counts explicitly.

---

## 5. Features

One row per `(project, stage)`. Every value computed **as of `stage.started_on`**.

### 5.1 Litigation block — the differentiator

Sourced from `Parcel.status`, `ParcelCaseLink` and `CourtCase` via `ProjectParcel`.

| Feature | Definition |
|---|---|
| `share_parcels_red` | RED parcels ÷ bound parcels |
| `share_parcels_amber` | AMBER parcels ÷ bound parcels |
| `n_active_cases` | Distinct active `CourtCase` on any bound parcel |
| `max_case_pendency_days` | Longest `stage_start − filing_date` among those |
| `median_case_pendency_days` | Median of the same |
| `n_acquisition_compensation_cases` | `case_type = 'acquisition_compensation'` — already a type in `CASE_TYPE_RELEVANCE` |
| `n_title_partition_cases` | `case_type ∈ {title_declaration, partition, succession_inheritance}` — the PS's "land ownership conflicts" |
| `has_interim_order` | Any bound parcel carries a `CourtEvent` of type `interim_order` before stage entry. An injunction is the strongest single stop signal in the feature set |
| `n_high_confidence_links` | `ParcelCaseLink.confidence_band = 'HIGH'` on bound parcels |

### 5.2 Project intrinsics

`area_hectares`, `n_parcels`, `n_villages`, `affected_families`, `project_type`,
`executing_agency`, `act`.

### 5.3 Administrative

`days_in_current_stage`, `n_prior_stage_overruns`, `gazette_republication_count` (a 3A
extension is itself a recorded delay signal), `compensation_disbursed_share`.

### 5.4 District context

`district_median_3a_to_3d_days` (computed on the training split only),
`district_active_land_cases`, `district_completed_projects`.

### 5.5 Leakage discipline

`s11` runs a mandatory audit: every feature carries a `computed_asof`, and the audit asserts
no contributing row has a date later than `stage.started_on`. District-context aggregates
are computed on the training split only, never on the full corpus. A failure raises, exactly
like the `s1` contract check.

`tests/test_features.py::test_no_future_leakage` enforces this. It exists because leakage is
the most likely fatal flaw in a hackathon ML pipeline, and because a technical judge will ask.

### 5.6 Feature-count discipline

The real corpus is small. The feature list is capped at **20 features per stage model**; if
it grows, features are dropped by lowest holdout permutation importance rather than added.
Shallow trees (`max_depth=3`, `max_leaf_nodes=8`), strong `l2_regularization`, and
`early_stopping` on a time-ordered validation slice.

---

## 6. Model

### 6.1 Algorithm

Per stage: `HistGradientBoostingClassifier` wrapped in `CalibratedClassifierCV`
(`method='isotonic'` where the stage has ≥ 200 closed rows, `'sigmoid'` otherwise).

The split is **time-based**: train on stages that closed before `cutoff_date`, test on those
that closed after. No random shuffle — a random split on temporal data overstates skill.

### 6.2 Baselines, reported in the same table

1. **Base rate** — always predict the training-set delay frequency.
2. **Logistic regression** on the same features, standardized, L2.
3. The calibrated GBM.

Metrics: ROC-AUC, PR-AUC, **Brier score**, and a reliability curve.

**If the LR baseline beats the GBM on the holdout, ship the LR and say so on the slide.**
With a corpus this size that is a live possibility, and reporting it is more credible than
hiding it.

**Metrics are reported on real projects only.** Synthetic projects may appear in training;
no synthetic row contributes to any number shown to a judge. Enforced by `s12` writing
`n_test_real` and `n_test_synthetic` into `ModelRun.metrics`, and by a test asserting the
reported holdout is `source_label = 'real'`.

### 6.3 Risk bands — precision-first, mirroring the RED rule

Thresholds are **chosen on the holdout to hit a stated precision target**, not picked by
hand, and are written into `ModelRun.thresholds`:

Two cut points per stage, `t_high` and `t_med`, giving disjoint bands:

- `t_high` — the lowest probability at which holdout precision is still ≥ 0.70.
  `HIGH` is `p ≥ t_high`.
- `t_med` — the lowest probability at which holdout recall of true delays is ≥ 0.80.
  `MEDIUM` is `t_med ≤ p < t_high`.
- `LOW` is `p < t_med`.

If the two cut points invert (`t_med > t_high`, which happens when the model has little
skill on a stage), `t_med` is clamped to `t_high` and the stage is flagged
`low_separation` in `ModelRun.notes`.

If no threshold achieves the HIGH precision target for a stage, **the HIGH band is not
emitted for that stage** and the UI shows MEDIUM as the top band with a stated reason. This
is the direct analogue of PRD §17's "a false RED is more damaging than a missed weak match".

### 6.4 Explainability

SHAP `TreeExplainer` at build time. Top-5 signed drivers per `(project, stage)` persisted to
`ProjectRisk.drivers`. Global mean-|SHAP| ranking persisted to `ModelRun.metrics` for the
"what drives delay across UP" slide.

### 6.5 Recommendations — retrieved, never generated

A static `driver → action` rule table in `pipeline/recommendations.py`. Every emitted
recommendation carries the driver that triggered it and the rule id.

| Driver | Action |
|---|---|
| `has_interim_order` | Seek vacation of the interim order; list before the LARR Authority before the 3D clock expires |
| `share_parcels_red` high | Route the N litigated parcels to the district legal cell |
| `compensation_disbursed_share` low | Escalate disbursement to the CALA |
| `gazette_republication_count > 0` | 3A already extended once; a second extension risks lapse under s.3D(3) |

No language model writes an action. The discipline is the same as Ankur's "cite, never
invent".

### 6.6 Continuous learning

Re-running `s12` mints a new `model_version` (`mv-YYYYMMDD-NN`), appends a `ModelRun` row,
and `s13` rescores. `GET /models/history` returns the registry so metric drift across runs is
visible. Training never happens in the request path.

---

## 7. API — six new endpoints

| Endpoint | Returns |
|---|---|
| `GET /projects` | List, filterable by `district`, `stage`, `risk_band`; sortable by `delay_probability` |
| `GET /projects/{id}` | Detail: stages with statutory clocks, bound parcels, timeline |
| `GET /projects/{id}/risk` | Per-stage delay probability, band, drivers, recommendations, `model_version` |
| `GET /dashboard/risk` | State and district rollups, delay trends, top-N at-risk projects, comparative analytics |
| `GET /dashboard/risk-map` | GeoJSON of project corridors coloured by risk band |
| `GET /models/history` | `ModelRun` registry and metric drift |

Every one is a `SELECT` plus JSON shaping. No model loading, no SHAP, no scoring at request
time.

`POST /watchlist` accepts either `parcel_id` or `project_id`.

### 7.1 Role stub and audit log (PS deliverable 12)

An `X-Role` header (`officer | policymaker | viewer`) gates which fields each endpoint
returns, and an append-only `AuditLog` table records `(ts, role, method, path, status)` on
every request. Roughly 40 lines of middleware.

**Labelled on screen as a mocked access model, not a security system.** This is the one place
the PS collides with the repo's own guardrail against building auth, and it is resolved by
demonstrating the requirement rather than implementing an identity provider. **First item on
the cut list** if the timebox tightens.

---

## 8. Frontend

Three new screens. Each reuses existing components rather than adding new patterns.

| Screen | Route | Reuses |
|---|---|---|
| `ProjectPortfolio.tsx` | `/projects` | Existing dossier table and filter patterns |
| `ProjectDetail.tsx` | `/projects/:id` | `Timeline.tsx`; `EvidencePanel.tsx` → `DriverPanel.tsx` |
| `RiskDashboard.tsx` | `/risk` | `OfficerDashboard.tsx`, `ParcelMap.tsx` |

### 8.1 `Timeline.tsx` extension

Today it plots `filed → interim order → sale → next hearing` on one horizontal axis. For a
project it plots `3A → 3D → award → possession` on the same axis, adding:

- a **vertical statutory-deadline marker** per stage,
- **overshoot shading** between the deadline and today for an overdue stage,
- a `clock_source` badge distinguishing statute from administrative target.

Same SVG grammar, no charting library, and a strictly better story than the version it
extends.

### 8.2 Risk bands in the design system

`RiskBadge` renders LOW/MEDIUM/HIGH on the **existing green/amber/red tokens** from
`DESIGN_GUIDELINES.md` §3 — shared tokens, separate copy. Its mandatory disclaimer differs
from `StatusBadge`'s and is baked into the component the same way:

> Predicted risk of missing a statutory deadline. Not an administrative finding.

`DESIGN_GUIDELINES.md` gains a risk-token table and a stage-bar spec. The aesthetic is not
touched.

### 8.3 Existing screens

`Result.tsx` and `CaseDetailModal.tsx` are reached from `ProjectDetail`'s driver panel as
"why this parcel is a delay driver". `Search.tsx` survives as an officer parcel lookup with
reframed copy. No citizen framing anywhere.

---

## 9. Demo (3 minutes)

1. `RiskDashboard` — UP, N projects, X flagged HIGH, district trend map. (`N` and `X` are
   filled from `s12_report.json` at build time, never asserted in advance.)
2. Sort by delay probability. Top row: an NH corridor through Sultanpur.
3. `ProjectDetail` — stage timeline; the 3D statutory clock expires in 41 days; `p = 0.87`.
4. **Driver panel: 11 of 34 parcels carry active litigation.** Click through to the existing
   RED evidence panel — `1365-1` vs `1365/1`, `Madanpur Panyar` vs `Madanpur Paniyar`.
   **This step is the one no competing team on this PS can reproduce.**
5. Recommendation card, showing the rule id it was retrieved from.
6. Kill the database. The same URLs still answer from the `s15` fallback cache.
7. Drop a new stage-date file and re-run `s8`–`s15`; `model_version` increments and
   `/models/history` shows the metric move.

Flagship anchor: **`PRJ-B01`**, a synthetic NH corridor through Madanpur Paniyar containing
`P-B01`. It is to this build what `P-B01` is to the linkage build — the deterministic demo
pair that always works.

---

## 10. Testing

The 47 existing tests stay green, unmodified. That is the regression gate.

| New test | Asserts |
|---|---|
| `tests/test_features.py::test_no_future_leakage` | No feature references a date after `stage.started_on` |
| `tests/test_features.py::test_district_context_train_only` | District aggregates never see holdout rows |
| `tests/test_model.py::test_beats_base_rate` | Holdout Brier score beats the base-rate baseline |
| `tests/test_model.py::test_high_band_precision` | HIGH-band holdout precision ≥ 0.70, or the band is not emitted |
| `tests/test_model.py::test_metrics_are_real_only` | Every reported holdout row has `source_label = 'real'` |
| `tests/test_golden_project.py::test_flagship_project_high_risk` | `PRJ-B01` is HIGH and its top driver is a litigation feature |
| `tests/test_golden_project.py::test_censored_stages_excluded` | Open stages have `is_delayed IS NULL` and are absent from training |
| `tests/test_golden_project.py::test_eight_original_tables_intact` | `s14` did not drop or alter the original eight tables |
| `backend/tests/test_projects.py` | All six new endpoints return 200 against the real DB, flagship risk intact |
| `backend/tests/test_risk_fallback.py` | Every new endpoint resolves to an `s15` fallback file |

---

## 11. Documents and diagrams to update

| File | Change |
|---|---|
| `docs/product/vivaad-radar-prd.md` | New **Part 16 (§75–82)**: PS mapping, acquisition data model, feature set, ML spec, new APIs, new screens, risk register. §1–74 are retained and reframed as the linkage-subsystem spec |
| `pipeline/README.md` | `s8`–`s15` rows; new build stats; new "deliberate deviations" entries (administrative clocks, censoring, small-n model discipline); the `vivaad.db` naming note |
| `docs/research/CONTEXT-vivaad-radar.md` | New §11: the PS switch, what carries over, the new verified source (Bhoomi Rashi), new dead ends |
| `docs/architecture/DESIGN_GUIDELINES.md` | Risk-band tokens, `RiskBadge` disclaimer, stage-bar spec |
| `docs/architecture/vivaad-radar-architecture.excalidraw` | Regenerated with the `s8`–`s15` lane, five new tables, six endpoints, three screens, and an explicit ML-boundary box. The file is script-generated (`source: vivaad-radar`), so the generator is extended rather than the JSON hand-edited |
| `docs/architecture/adhigrahan-ml-pipeline.excalidraw` | **New.** The ML lifecycle — features → time split → train → calibrate → threshold → SHAP → retrieved actions — with the leakage boundary drawn as a hard line |
| | `docs/product/SIH26017-ps-traceability.md` | **New.** Every one of the PS's 12 expected-solution deliverables mapped 1:1 to where it is satisfied |

---

## 12. PS traceability

Goes in `docs/product/SIH26017-ps-traceability.md`.

| # | PS deliverable | Satisfied by |
|---|---|---|
| 1 | AI/ML predictive models forecasting delays | `s12_train` — per-stage calibrated GBM |
| 2 | Automated identification of high-probability projects | `s13_risk_score` + `GET /projects?risk_band=HIGH` |
| 3 | Project-wise risk scoring and prioritisation | `ProjectRisk`; portfolio sorted by `delay_probability` |
| 4 | Identification of key delay drivers | SHAP top-5 in `ProjectRisk.drivers`; the litigation block is the differentiated driver |
| 5 | Explainable AI for transparency | Build-time SHAP, `DriverPanel.tsx`, signed contributions, global importance |
| 6 | Interactive dashboards (probability, categorisation, district and state trends, timeline, performance, comparative) | `RiskDashboard.tsx` + `GET /dashboard/risk` |
| 7 | GIS visualisation of high-risk projects | `GET /dashboard/risk-map` + `ParcelMap.tsx` |
| 8 | Automated alerts and notifications | `Watchlist` extended with `project_id`; mocked badge |
| 9 | Predictive recommendations for corrective action | `pipeline/recommendations.py` rule table — retrieved, not generated |
| 10 | Continuous model learning | `ModelRun` registry, re-runnable `s12`, `GET /models/history` |
| 11 | APIs for integration | The six new REST endpoints with documented shapes, plus the existing eight |
| 12 | Secure role-based access with audit trails | `X-Role` middleware + append-only `AuditLog`, labelled mocked |

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| Public acquisition data is thinner than the PS implies | Stated openly. Real Bhoomi Rashi projects are the labelled spine; synthetic rows are provenance-labelled and never appear in a reported metric |
| Small `n` overfits the GBM | Capped feature count, shallow trees, strong regularisation, time-based split, LR and base-rate baselines reported. If LR wins, LR ships |
| Bhoomi Rashi scraping terms or availability | Cached snapshot committed under `data/raw/`; the demo never touches the network. Same discipline as the eCourts decision |
| Feature leakage inflates metrics | `s11` audit raises at build time; two tests enforce it |
| "This is just Vivaad Radar relabelled" | The litigation block is one of four feature families; the product, users, model, and screens are new. The PRD retains the linkage spec as a subsystem, which makes the layering visible |
| A false HIGH panics a project office | Threshold chosen for ≥ 0.70 holdout precision; band suppressed entirely if unattainable; disclaimer baked into `RiskBadge` |
| Scope creep back into a platform | Guardrails from architecture design §12 stand. Nothing here adds blockchain, OCR, a chatbot, or land-record CRUD |

---

## 14. Cut order under time pressure

Cut in this order; each cut leaves a coherent demo.

1. `X-Role` middleware and `AuditLog` (PS deliverable 12)
2. `GET /models/history` and the continuous-learning demo beat
3. `GET /dashboard/risk-map` corridor GeoJSON — fall back to the district table
4. SHAP → LR coefficient attribution instead (still explainable, still satisfies deliverable 5)
5. Multi-district UP → Sultanpur only (loses the trend map)

Non-negotiable: `s8`–`s15`, the per-stage model with an honest baseline, the driver panel,
and the litigation drill-down. `s15` is in the non-negotiable set because demo step 6 (kill
the database, same URLs still answer) depends on it. That set is the product.

---

## 15. Out of scope

- Live scraping at demo time.
- Real notifications — alerts stay mocked, as the watchlist already is.
- A real identity provider.
- PostGIS or a graph database.
- Any state outside Uttar Pradesh.
- Retraining in the request path.
