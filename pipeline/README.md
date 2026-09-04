# Adhigrahan Radar — offline pipeline

Everything here runs **before** the demo. Nothing in this package may be
imported by request-time code (PRD §36, §48, §49).

```
python pipeline/run_all.py          # full build, s0 -> s15
python pipeline/run_all.py --skip-handoff   # rebuild without regenerating data/input
python pipeline/run_all.py --risk-only      # s8 -> s15 only, against an existing vivaad.db
python -m pytest tests/ backend/tests/ -q   # 47 tests today
```

Dependency direction is one-way and never reversed:
`pipeline → vivaad.db → backend → frontend`.

**Naming.** The product is **Adhigrahan Radar**; **Vivaad Radar** is the name of
its court↔parcel linkage engine (stages s0–s7), now a subsystem. The database
file stays `vivaad.db` and the env var stays `VIVAAD_DB` — renaming them would
touch `backend/db.py`, `backend/fallback.py`, every test and every fallback path
for something no judge will ever see. Deliberate, not an oversight.

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
| s6_load_db | Eight §19 tables | `data/output/vivaad.db` |
| s7_export_fallback | Every §37 endpoint response + flagship payloads | `data/output/fallback/` |

### s8–s15 — the risk engine (designed, not built)

| Stage | Does | Writes |
|---|---|---|
| s8_acquisition_handoff | Builds the acquisition contract from the cached Bhoomi Rashi snapshot (`data/raw/bhoomirashi/`) **and** a synthetic generator seeded on the real Sultanpur villages. Mirrors s0 | `data/input/{acquisitions,project_stages}.parquet` |
| s9_acquisition_ingest | Validates: columns, provenance on every row, a resolvable statutory clock per stage, flagship project present. **Raises on any violation** | `acquisitions.json` |
| s10_project_bind | Binds project → parcels using s2's normaliser and gazetteer and s3's blocking. `binding_confidence` is `s4._village` + `s4._identifier` reweighted over those two features. **No new matcher** | `project_parcels.json` |
| s11_features | Per-(project, stage) feature matrix, every value as of `stage.started_on`. Runs the leakage audit | `features.parquet` |
| s12_train | Per-stage calibrated GBM + LR baseline + base-rate baseline. Time-based split. Picks risk-band thresholds on the holdout | `data/output/models/` (committed) |
| s13_risk_score | Scores every open stage; SHAP drivers; maps drivers to retrieved recommendations | `project_risk.json` |
| s14_load_risk_db | `CREATE`s the five new tables and fills them. **Never `DROP`s the original eight** | `vivaad.db` (13 tables) |
| s15_export_risk_fallback | Renders every new endpoint response to the fallback cache | `fallback/projects/*`, `fallback/dashboard_risk.json` |

Intermediates land in `data/intermediate/` (gitignored, regenerable) alongside
one `sN_report.json` per stage — those reports are the numbers for the §53
technical story and the §43 evaluation slide. Model artifacts under
`data/output/models/` **are** committed: they are small, and a fresh clone must
be able to serve without retraining.

---

## The one idea that connects the two halves

s11 reads `vivaad.db` **after** s6 has built it. The linkage engine stops being a
product and becomes a **feature provider**: `share_parcels_red`,
`n_active_cases`, `has_interim_order` and six other litigation features come
straight out of `Parcel.status` and `ParcelCaseLink`.

Reading a database an earlier stage of the same offline build produced is
legitimate. Nothing in the request path changes.

---

## What the backend consumes

`Parcel` carries **derived `status`, `confidence`, `note`, `closed_history`**
columns, so `GET /parcels/{id}/litigation` is a SELECT and a join — no scoring
at query time. `ParcelCaseLink.evidence` is the JSON the methodology panel
renders. `land_events` is JSON on `Parcel` rather than a ninth table.

`ProjectRisk` follows the identical pattern: `delay_probability`, `risk_band`,
`drivers` and `recommendations` are all precomputed by s13. **No model is loaded
and no SHAP value is computed in the request path.**

Fallback filenames are URL-shaped so the middleware maps a path to a file with
one replace:

```
GET /parcels/P-B01/litigation  ->  data/output/fallback/parcels/P-B01/litigation.json
GET /projects/PRJ-B01/risk     ->  data/output/fallback/projects/PRJ-B01/risk.json
GET /dashboard/heatmap         ->  data/output/fallback/dashboard_heatmap.json
GET /dashboard/risk            ->  data/output/fallback/dashboard_risk.json
GET /cases/UPHC020611812025    ->  data/output/fallback/cases/UPHC020611812025.json
```
`flagship.json` holds the tier-3 payloads to bundle into `api/client.ts`.

---

## Current build (s0–s7)

| | |
|---|---|
| District | Sultanpur |
| Cases / parcels | 38 real cases (8 active) / 135 synthetic parcels |
| Links surfaced | 84 (43 HIGH, 41 MEDIUM) from 1,678 scored pairs |
| Parcel status | 12 RED · 62 AMBER · 61 GREEN |
| Sale during pendency | 8 of 12 RED parcels (PRD 52 evidence) |
| Flagship | **P-B01 = RED @ 0.9105**, P-A01 = GREEN |
| Tests | 47, all green |

s8–s15 numbers land here once the risk engine is built. Until then this table
must not be padded with projected figures.

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
   one (0 of 38), but PRD 37, 16 screen 5, 50 and 55 all display one. So the
   pipeline derives it for **active cases only**, anchored on the later of the
   order date or today plus a varied 3-14 week listing gap, and stamps
   `next_hearing_source = 'derived'`. Disposed cases never get one. Three
   golden tests enforce this, because an unlabelled fabricated court date is
   the easiest thing for a judge to check and the most expensive thing to lose.

### Risk engine (s8–s15)

3. **Two of five stage clocks are administrative targets, not statute.**
   3A→3D, s.11→s.19 and s.19→award are statutory (365 days each). Award→
   disbursement and award→possession are 90-day targets we chose. Every row
   carries `clock_source` and every screen must render the distinction. Same
   rule, same reason, as the derived next-hearing date.
4. **Open stages are right-censored, never scored as on-time.** A stage that
   has not finished has `is_delayed = NULL`, not `0`. Censored rows are excluded
   from training and included in scoring; the evaluation slide states both
   counts.
5. **Synthetic rows may train; only real rows may score.** `s12` writes
   `n_test_real` and `n_test_synthetic` into `ModelRun.metrics` and a test
   asserts every reported holdout row is `source_label = 'real'`.
6. **Small-n model discipline.** The real corpus is small, so the feature list
   is capped at 20 per stage model, trees are shallow (`max_depth=3`,
   `max_leaf_nodes=8`), and a logistic-regression and a base-rate baseline are
   reported in the same table. **If LR beats the GBM on the holdout, LR ships
   and the slide says so.**
7. **`predicted_overrun_days` is not a second model.** It is the empirical
   median overrun among delayed holdout stages of the same stage and risk band,
   rendered as "typically N days late when this happens". A regression head is
   not justified at this corpus size.

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
this, so the property cannot silently regress.

The same normaliser and the same gazetteer bind acquisition notifications to
parcels in s10 — which is why the risk engine adds no new matcher.

---

## Leakage discipline (s11)

Every feature carries a `computed_asof`. The s11 audit asserts no contributing
row has a date later than `stage.started_on`, and district-context aggregates
are computed on the training split only. A violation raises at build time,
exactly like the s1 contract check.

`tests/test_features.py::test_no_future_leakage` enforces it. Leakage is the
most likely fatal flaw in a hackathon ML pipeline and the first thing a
technical judge will probe.
