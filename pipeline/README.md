# Adhigrahan Radar — offline pipeline

Everything here runs **before** the demo. Nothing in this package may be
imported by request-time code.

```
python pipeline/run_all.py                  # full build, s0 -> s15
python pipeline/run_all.py --skip-handoff   # rebuild without regenerating data/input (default)
python pipeline/run_all.py --risk-only      # s8 -> s15 only, against an existing vivaad.db
python -m pytest tests/ backend/tests/ -q   # 126 tests today
cd frontend && npm test                     # 38 frontend tests
```

Dependency direction is one-way and never reversed:
`pipeline → vivaad.db → backend → frontend`.

**Naming.** The product is **Adhigrahan Radar**; **Vivaad Radar** is the name of
its court↔parcel linkage engine (stages s0–s7), now a subsystem. The database
file stays `vivaad.db` and the env var stays `VIVAAD_DB` — renaming them would
touch `backend/db.py`, `backend/fallback.py`, every test and every fallback path
for something no user will ever see. Deliberate, not an oversight.

---

## Stages

### s0–s7 — the linkage engine (built, unchanged)

| Stage | Does | Writes |
|---|---|---|
| s0_handoff | Builds the data contract from the extracted HC corpus | `data/input/{cases,parcels}.parquet` |
| s1_ingest | Validates contract, provenance, flagship rows. **Raises on any violation** | `cases.json`, `parcels.json` |
| s2_normalize | Survey-no + name normalization, village gazetteer | `normalized.json` |
| s3_candidates | Village blocking; district fallback flagged `location_unconfirmed` | `candidates.json` |
| s4_score | §27 features, §28 bands, per-pair evidence JSON | `links.json` |
| s5_status | §30 status engine, worst-case wins | `parcel_status.json` |
| s6_load_db | The eight linkage tables (DELETE+refill, schema shared with s14) | `data/output/vivaad.db` |
| s7_export_fallback | Every linkage endpoint response + flagship payloads | `data/output/fallback/` |

### s8–s15 — the risk engine (built)

| Stage | Does | Writes |
|---|---|---|
| s8_acquisition_handoff | Builds the acquisition contract as a **hybrid** corpus: 96 synthetic projects generated deterministically (`RISK_SEED`) plus every real project in `data/raw/acquisition_projects.json` — the Gazette of India mirror `make ingest` writes — windowed at `common.TODAY`, stamped `source_label='real'` and carrying its one gazetted stage (§3A→§3D), with the measures the gazette never publishes left NULL. Synthetic `litigation_risk` archetype projects are routed onto villages with real RED/AMBER parcels (via `s6`'s already-built `vivaad.db`) so the litigation features and the simulated delay are causally consistent, not coincidental. Village pools are keyed **by district**, not by a hardcoded district name: a district with no linkage corpus simply has no pool | `data/input/{acquisitions,project_stages}.parquet` |
| s9_acquisition_ingest | Validates: columns, provenance on every row, a resolvable statutory clock per stage, flagship project present. Computes `deadline_on`/`overdue_days`/`is_delayed` once. **Raises on any violation** | `acquisitions.json`, `project_stages.json` |
| s10_project_bind | Binds project → parcels using `s2`'s `norm_place` + the already-computed gazetteer mapping from `normalized.json`. **No new matcher.** Only projects in a district the linkage corpus covers ever bind — today that is Sultanpur alone; `n_parcels=0` elsewhere is the honest answer, never a fabricated binding | `project_parcels.json` |
| s11_features | 19-feature matrix per `(project, stage, landmark)` row, leakage-safe: open stages are scored once at "now", closed stages contribute one row per *statutory* landmark (0.25/0.50/0.75 of the clock) they were still open at. Runs the leakage audit and writes the shared `cutoff_date` for the time-based split | `features.parquet`, `cutoff_date.json` |
| s12_train | Per-stage `base_rate` + `logistic_regression` + `hgb_calibrated`, time-based split, threshold selection on the holdout. Whichever wins on Brier score ships | `data/output/models/*.joblib` |
| s13_risk_score | Scores every open stage with its shipped model; real SHAP drivers (wraps `predict_proba` directly — works for any of the three algo types); recommendations retrieved from `recommendations.py`; `predicted_overrun_days` from the empirical median overrun in the same `(stage, band)` bucket | `project_risk.json` |
| s14_load_risk_db | `CREATE`s (via the shared `schema.sql`) and DELETE+refills the five risk tables. **Never touches the original eight**, `Watchlist`, or `AuditLog` | `vivaad.db` (13 pipeline-owned tables + `Watchlist` + `AuditLog`) |
| s15_export_risk_fallback | Renders every new endpoint response to the fallback cache, mirroring `s7`'s nested/flat convention | `fallback/projects/*`, `fallback/dashboard_risk.json`, etc. |

Intermediates land in `data/intermediate/` (gitignored, regenerable) alongside
one `sN_report.json` per stage. Model artifacts under `data/output/models/`
**are** committed as build output within a session: they are small, and a
fresh clone must be able to serve without retraining — though in practice
this repo regenerates them deterministically from `RISK_SEED` on every build,
so nothing is actually frozen.

---

## The one idea that connects the two halves

`s10`/`s11` read `vivaad.db` **after** `s6` has built it. The linkage engine stops
being a product and becomes a **feature provider**: `share_parcels_red`,
`n_active_cases`, `has_interim_order` and six other litigation features come
straight out of `Parcel.status` and `ParcelCaseLink` — recomputed as of each
feature row's leakage-safe observation point, not read as the final static
value.

Reading a database an earlier stage of the same offline build produced is
legitimate. Nothing in the request path changes.

---

## What the backend consumes

`Parcel` carries **derived `status`, `confidence`, `note`, `closed_history`**
columns, so `GET /parcels/{id}/litigation` is a SELECT and a join — no scoring
at query time. `ParcelCaseLink.evidence` is the JSON the methodology panel
renders. `land_events` is JSON on `Parcel` rather than a ninth table.

`ProjectRisk` follows the identical pattern: `delay_probability`, `risk_band`,
`drivers` and `recommendations` are all precomputed by `s13`. **No model is
loaded and no SHAP value is computed in the request path.**

Fallback filenames are URL-shaped so the middleware maps a path to a file with
one replace:

```
GET /parcels/P-B01/litigation  ->  data/output/fallback/parcels/P-B01/litigation.json
GET /projects/PRJ-SUL-001/risk ->  data/output/fallback/projects/PRJ-SUL-001/risk.json
GET /dashboard/heatmap         ->  data/output/fallback/dashboard_heatmap.json
GET /dashboard/risk            ->  data/output/fallback/dashboard_risk.json
GET /cases/UPHC020611812025    ->  data/output/fallback/cases/UPHC020611812025.json
```

---

## Current build (s0–s7)

| | |
|---|---|
| District | Sultanpur |
| Cases / parcels | 38 real cases (8 active) / 135 synthetic parcels |
| Links surfaced | 84 (43 HIGH, 41 MEDIUM) from 1,678 scored pairs |
| Parcel status | 12 RED · 62 AMBER · 61 GREEN |
| Sale during pendency | 8 of 12 RED parcels (systemic lis-pendens pattern, `tests/test_golden.py`) |
| Flagship | **P-B01 = RED @ 0.9105**, P-A01 = GREEN |
| Tests | 13 (linkage golden suite) + 47 (API suite) |

## Current build (s8–s15)

From `s8_report.json` … `s13_report.json`:

| | |
|---|---|
| Districts | 8 (Sultanpur, Amethi, Pratapgarh, Raebareli, Ayodhya, Barabanki, Gonda, Basti) |
| Projects | 96 (12 per district) |
| Stage-rows | 383 (336 closed, 47 open) |
| Project status | 47 completed · 47 open · 2 lapsed |
| Project↔parcel bindings | 116, across 12 Sultanpur projects (the only district with a real parcel corpus), 72 unique parcels touched |
| Flagship project | `PRJ-SUL-001`, bound to `P-B01`, MEDIUM risk @ 19.53% on `award_3g_23` |
| Shipped models | `hgb_calibrated` (2) · `base_rate` (2 — the naive prior won; HIGH suppressed there) · `logistic_regression` (1) |
| Open-stage risk bands | 15 HIGH · 29 MEDIUM · 3 LOW |
| Median lead time | 53 days |
| Tests | 66 (acquisition golden + features + model + risk scoring) |

s8–s15 numbers are **synthetic** — see the root README's Honesty rules for what that does and
does not mean for the reported model metrics.

---

## Corpus facts worth quoting accurately

Numbers for slides and the demo script. These come from `s1_report.json`, so
they stay honest as the data changes.

| | |
|---|---|
| Filing dates span | 2024-01-17 to 2025-12-10 |
| **Longest pendency** | **~2.6 years** |
| Cases with a real next-hearing date | **0 of 38** (all displayed ones are derived) |
| Cases stating a patronymic | 6 of 38 |

The PRD's illustrative "filed 2019, pending 6 years" is a worked example, not a
fact about this corpus. **Any slide or script must say ~2.6 years**, which is
the real maximum here. A judge who checks a filing date will find 2024, and an
inflated claim is the cheapest possible credibility loss.

---

## Deliberate deviations, all flagged

### Linkage engine (s0–s7)

1. **Absent features are redistributed, not scored zero.** Only 6 of 38 cases
   state a patronymic. Scoring the missing 0.15 as 0 would depress every case
   that simply did not write "s/o" - punishing a data artefact as if it were
   evidence. The weight is renormalised over the features that exist; the
   per-pair evidence records `weights_used` and `features_absent`.
2. **Next-hearing dates are derived, and say so.** No case in the corpus has
   one (0 of 38), so the pipeline derives it for **active cases only**,
   anchored on the later of the order date or today plus a varied 3-14 week
   listing gap, and stamps `next_hearing_source = 'derived'`. Disposed cases
   never get one.

### Risk engine (s8–s15)

3. **Two of five stage clocks are administrative targets, not statute.**
   3A→3D, s.11→s.19 and s.19→award are statutory (365 days each). Award→
   disbursement and award→possession are 90-day targets we chose. Every row
   carries `clock_source` and every screen renders the distinction, same rule
   as the derived next-hearing date.
4. **Open stages are right-censored, never scored as on-time.** A stage that
   has not finished has `is_delayed = NULL`, not `0`. Censored rows are excluded
   from training and included in scoring.
5. **Synthetic rows may train; only real rows may score — per stage.** The
   corpus is hybrid (s8), and only the §3A→§3D clock is ever gazetted, so
   `notification_3a_11` carries the real holdout (`n_test_real`, scored on its
   own as `real_holdout`) and stages 2–5 report `n_test_real=0` with the
   reason in `ModelRun.notes`, on every row.
6. **Small-n model discipline.** The corpus is small (130-351 train rows per
   stage), so the feature list is capped at 19 per stage model, trees are
   shallow (`max_depth=3`, `max_leaf_nodes=8`), and a logistic-regression and
   a base-rate baseline are reported in the same table. On this corpus
   `base_rate` wins three of five stages and ships there, reported plainly.
   A driver whose value lies outside the shipped model's training range is
   flagged `outside_training_range` on the driver itself (s13), never
   clipped: the real gazette projects sit outside it on village count and
   area, and the officer must see that the model is reasoning past its
   evidence there.
9. **A HIGH band must clear the stage's own base rate.** `t_high` is the
   lowest holdout probability reaching 0.70 precision *that is also at or
   above the rate at which the stage overruns anyway*; otherwise HIGH is
   suppressed. Without that floor the band fires on the ordinary project and
   is unexplainable by construction: the score is the model baseline plus
   each feature's contribution, so a row *below* the baseline reached HIGH
   with every SHAP driver pointing at lower risk — a HIGH badge over five
   reasons it is not risky, and (since recommendations only fire on
   risk-increasing drivers) no suggested action.
   `tests/test_model.py::test_high_band_sits_at_or_above_the_stage_base_rate`.
7. **`predicted_overrun_days` is not a second model.** It is the empirical
   median overrun among delayed holdout stages of the same stage and risk
   band, rendered as "typically N days late when this happens". A regression
   head is not justified at this corpus size.
8. **`litigation_risk` archetype projects are routed onto real RED/AMBER
   villages.** `s8` queries the already-built `vivaad.db` (from `s6`) so a
   project simulated with elevated delay actually touches parcels with real
   litigation exposure, keeping the label and the litigation feature block
   causally consistent instead of independently random.

---

## Why the linkage is not circular

The synthetic land side deliberately diverges from how the court cites it, and
s2/s4 have to earn the match back:

| Court says | Land record says | Reconciled by |
|---|---|---|
| `1365/1` | `1365-1` | s2 survey normalization |
| `Madanpur Paniyar` | `Madanpur Panyar` | s2 village gazetteer |
| `SHYAMDHAR DUBEY` | `Shyam Dhar Dubey` | s4 RapidFuzz name similarity |
| gata `153` | `153/1`, `153/2` | s4 sub-division kinship (0.6 partial) |

`tests/test_golden.py::test_flagship_link_survives_divergence` asserts exactly
this, so the property cannot silently regress. `s10_project_bind` reuses the
same gazetteer to bind acquisition projects — the flagship project (`PRJ-SUL-001`)
is forced onto "Madanpur Panyar" and correctly resolves to `P-B01` through
that same reconciliation.

---

## Leakage discipline (s11)

Every feature carries a `computed_asof`, and that observation point is
chosen **without reference to the outcome being predicted**.

Open stages are scored once, at the real build "now". Closed (training)
stages are observed at fixed **landmarks** on the *statutory* clock -
`started_on + f * statutory_days` for `f` in `(0.0, 0.25, 0.50, 0.75)` - and
a row is emitted only for the landmarks the stage was still open at, which
is exactly the condition under which `s13` scores a row in production. The
`0.0` landmark is the day a stage opens: a freshly gazetted notification is
scored the day it appears, and the first harvest's real open rows were all
under a week old, so the model must have seen elapsed-time zero in training.
Landmarks stay strictly below `1.0`: a stage still open at
`1.0 * statutory_days` has already breached its deadline, so its label
would be `1` by definition.

> **Why not "a random point before completion"?** That was the original
> design, and it leaked. `started_on + uniform(0.10, 0.95) *
> actual_duration` does sit before completion - but it is a *function of
> the duration*, so `days_in_current_stage` came out as
> `round(duration * frac)`: the label's own quantity scaled by noise,
> since `is_delayed` is `duration > statutory_days` and `statutory_days`
> is a per-stage constant. On the real corpus that single feature scored
> ROC-AUC 0.64-0.81 with no model at all. Observing *before* the outcome
> is not the same as being *independent of* it.

`n_prior_stage_overruns` counts only prior stages that had actually closed
by the observation point. District-context aggregates are computed on the
training split only, gated by a shared `cutoff_date` both `s11` and `s12`
read from the same file. `s12` splits whole *stages* on that date, never
individual rows, so a stage's landmark rows can never straddle the
train/test boundary; its calibration folds are grouped by project for the
same reason.

`tests/test_features.py::test_observation_point_is_independent_of_outcome`
is the regression test for the leak above, backed by
`::test_days_in_current_stage_takes_only_landmark_values` and
`::test_landmark_rows_cover_every_survived_landmark`.
`::test_no_future_leakage` is retained but is **necessary, not
sufficient** - it passed throughout the period the pipeline was leaking.
Leakage is the most likely fatal flaw in an ML pipeline and the first
thing a technical judge will probe.
