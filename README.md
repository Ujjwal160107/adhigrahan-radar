# Adhigrahan Radar

**Predicting which land-acquisition projects will miss their statutory deadlines — before
they miss them.**

Smart India Hackathon 2026 · **SIH26017** — *Predictive Analytics System for Early Detection
of Land Acquisition Delays* · Ministry of Rural Development, Dept. of Land Resources

---

## The problem

Land acquisition is legally time-boxed, and the boxes are the point:

| Transition | Deadline | Clock | Consequence of breach |
|---|---|---|---|
| §3A → §3D (NH Act 1956) / §11 → §19 (RFCTLARR 2013) | 365 days | statute | Notification **lapses**, s.3D(3) / s.19(7) |
| §19 → §23 award | 365 days | statute | Acquisition proceedings lapse, s.25 |
| Award → compensation disbursed | 90 days | administrative target | none statutory |
| Award → possession | 90 days | administrative target | none statutory |

Missing the clock **voids the acquisition**. The state re-notifies, re-values the land at a
higher rate, and pays more compensation for the same parcels. Meanwhile the affected families
have already lost planning certainty over land that is neither theirs to use nor paid for.

India monitors this **retrospectively** — a monthly return telling an officer that a project
already crossed 300 days, when nothing can be done. This system flags a project *before* the
clock runs out, per stage, with the specific factors driving the risk.

## The idea

Every driver of delay is already written down somewhere — a court file, a gazette
notification, a compensation register. Each lives in a different department's system, keyed
differently, and none is read together.

The least visible driver is the biggest: **pending litigation on the parcels being
acquired**. Court records are indexed by **party name**, land records by **survey number**.
The two systems never agreed on a key, so nobody can currently answer *"how many parcels in
this corridor are under active dispute?"*

**This repo answers it.** A court↔parcel record-linkage engine feeds a per-stage delay model
as one of four feature families:

```
Vivaad Radar  (linkage engine, s0-s7)      ->   feature provider
court <-> parcel linkage                        share_parcels_red, n_active_cases,
                                                has_interim_order, max_pendency_days
                                                             |
                                                             v
                                   Adhigrahan Radar  (risk engine, s8-s15)
                                   per-stage delay model + drivers + retrieved actions
```

The linkage engine earns its matches. Where the court writes survey `1365/1` in village
*Madanpur Paniyar* and the land record writes `1365-1` in *Madanpur Panyar*, the pipeline
reconciles both through survey normalisation, a village gazetteer and fuzzy name matching,
and scores the link at **0.9105**. The risk engine reuses the same normaliser and gazetteer to
bind acquisition projects to parcels — no second matcher.

---

## Status

Both halves are built end to end: offline pipeline, SQLite store, FastAPI backend, React
frontend.

| Component | State |
|---|---|
| Linkage engine `s0`–`s7` | **Built.** 38 real High Court cases, 135 parcels, 84 links |
| Risk engine `s8`–`s15` | **Built.** 96 projects / 385 stage-rows across 8 UP districts, 5 calibrated per-stage models, 48 open stages scored |
| Backend — 9 linkage/litigation endpoints | **Built** |
| Backend — 10 risk/project/model/auth endpoints | **Built** |
| Frontend — risk dashboard, project portfolio, project detail, model registry | **Built** |
| Frontend — litigation search, result, officer heatmap, watchlist | **Built** (demoted to `/lookup/*`, the evidence drill-down layer) |
| Ingestion `ingest/` | **Built.** Real §3A/§3D notifications from the Gazette of India; **not yet consumed by the risk engine** |
| Tests | **362** (324 backend/pipeline/ingest + 38 frontend), all green |

Current build:

| | |
|---|---|
| Linkage district | Sultanpur, Uttar Pradesh (the only district with a real litigation corpus) |
| Cases / parcels | 38 real cases (8 active) / 135 synthetic parcels, 22 villages |
| Links surfaced | 84 (43 HIGH, 41 MEDIUM) from 1,678 scored pairs |
| Parcel status | 12 RED · 62 AMBER · 61 GREEN |
| Flagship parcel | `P-B01` = RED @ 0.9105 · `P-A01` = GREEN |
| Longest litigation pendency | **~2.6 years** |
| Acquisition districts | 8 (Sultanpur, Amethi, Pratapgarh, Raebareli, Ayodhya, Barabanki, Gonda, Basti) — **synthetic**, see [Honesty rules](#honesty-rules) |
| Acquisition projects / stage-rows | 96 / 383 (336 closed, 47 open) |
| Flagship project | `PRJ-SUL-001` — MEDIUM risk, 19.53% delay probability, bound to `P-B01` |
| Risk bands on open stages | 15 HIGH · 29 MEDIUM · 3 LOW |
| Median lead time (open stages) | 53 days |
| Models shipped | hgb_calibrated (2 stages) · base_rate (2 — the naive prior beat both learned models there; HIGH suppressed for both) · logistic_regression (1) |
| Tests | 362, all green |

---

## Quickstart

```bash
make doctor    # verify Python 3.11-3.13 and Node >=20 before installing anything
make setup     # python venv + pip install + npm install
make build     # regenerate data/output from the committed contract (s0-s15)
make ingest    # refresh data/raw from the live sources - separate, never part of build
make test      # 324 backend/pipeline/ingest tests + 38 frontend tests
make api       # http://localhost:8000
make web       # http://localhost:5173   (separate terminal)
```

Without `make` (works on both POSIX and Windows; substitute `.venv/Scripts/` for `.venv/bin/`
on Windows):

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python pipeline/run_all.py --skip-handoff
.venv/bin/python -m pytest
.venv/bin/uvicorn backend.main:app --reload --port 8000
cd frontend && npm install && npm test && npm run dev
```

### Why `--skip-handoff` is the default

`s0_handoff` regenerates the synthetic land side from an external High Court corpus that is
**not part of this repo**. `data/input/cases.parquet` and `data/input/parcels.parquet` are
the committed data contract, and every stage downstream of them is fully reproducible. `make
build` therefore starts at `s1`. Use `make build-all` only if you have the external corpus.
`s8` onward (the risk engine) has no external dependency and always runs.

`python pipeline/run_all.py --risk-only` reruns only `s8`–`s15` against an existing
`vivaad.db`, for iterating on the risk engine without rebuilding the linkage side.

`data/output/` is **not committed** — it is a build artifact. CI runs `make build` on every
push precisely so a broken build cannot reach a fresh clone silently.

### Environment variables

See [`.env.example`](.env.example). Nothing here is a secret.

| Variable | Default | Purpose |
|---|---|---|
| `VIVAAD_DB` | `data/output/vivaad.db` | SQLite build artifact the API reads |
| `VITE_API_URL` | `http://localhost:8000` | Frontend → API base URL |
| `CORS_ORIGINS` | the two localhost dev origins | Comma-separated CORS allowlist |
| `VIVAAD_FALLBACK_DIR` | `data/output/fallback` | Tier-2 fallback cache location |

---

## Layout

```
adhigrahan-radar/
├── backend/           FastAPI, read-only over the SQLite build artifact
│   ├── auth.py         demo-grade X-Role gate + AuditLog writer
│   ├── routers/        parcels · cases · dashboard · watchlist · projects · models · auth
│   ├── fallback.py     serves cached JSON at the same URLs when the DB is gone
│   └── tests/          40 API tests
├── frontend/           React (Vite) + Tailwind + Leaflet + react-router-dom
│   └── src/pages/       RiskDashboard · ProjectPortfolio · ProjectDetail · ModelHistory ·
│                        LookupApp (Search · Processing · Result · OfficerDashboard · Watchlist)
├── ingest/             dynamic ingestion from the live government sources
│   ├── egazette/       gazette catalog, retrieval and notification parsing
│   ├── bhoomirashi/    the portal's open district/tehsil master data
│   └── README.md       what it produces, and the four things worth knowing
├── pipeline/           the offline build, one file per stage
│   ├── s0..s7          linkage engine (built)
│   ├── s8..s15         risk engine (built)
│   ├── recommendations.py  static driver -> action rule table
│   └── README.md       what each stage does, and every deliberate deviation
├── data/
│   ├── input/          cases/parcels.parquet committed (data contract);
│   │                   acquisitions/project_stages/features.parquet regenerated every build
│   ├── raw/             cached source snapshots - the demo never hits the network
│   └── output/          build artifacts - gitignored, except trained models
├── tests/               pipeline golden suite (44 tests: linkage + acquisition + features + model + risk)
└── docs/
    ├── architecture/    system architecture, design system, excalidraw + generator
    ├── plans/           implementation plans, including this build's blueprint
    ├── specs/           approved designs
    ├── product/         PRD and SIH submission content
    └── research/        source discovery and verified data sources
```

**Dependency direction is one-way and never reversed:**

```
pipeline  ->  vivaad.db  ->  backend  ->  frontend
```

The database file is the only interface between the pipeline and the backend; the REST
contract is the only interface between the backend and the frontend.

> The artifact is still named `vivaad.db` (and the env var `VIVAAD_DB`) deliberately.
> Renaming touches `backend/db.py`, `backend/fallback.py`, every test and every fallback
> path, for something no user ever sees. **Vivaad Radar** is the name of the linkage engine;
> **Adhigrahan Radar** is the product built on it.

---

## The rule that shapes the architecture

**Everything expensive happens before the demo.** Fuzzy matching, feature building,
training, calibration and SHAP all run in the offline build. At request time every endpoint
is a `SELECT` plus JSON shaping — no scoring, no model load, no SHAP.

That buys three things: sub-second responses, a system that cannot fail live because a model
failed to load, and a three-tier fallback where killing the database mid-demo still answers
the same URLs.

| Tier | Mechanism | Trigger |
|---|---|---|
| 1 — live | frontend → FastAPI → SQLite | normal |
| 2 — cached | `fallback.py` serves exported JSON at the same URLs (`s7`/`s15` write it) | DB missing or query failure |
| 3 — bundled | flagship litigation payloads compiled into `api/client.ts` | `?demo=1` or a failed fetch |

Tier 3 currently covers the litigation-lookup flagship payloads only; the risk-engine
endpoints rely on tiers 1 and 2 (both fully implemented and tested against the real build).

---

## Honesty rules

These are enforced by tests, not by discipline. They exist because the fastest way to lose a
technical reviewer is an unlabelled fabricated number.

1. **Provenance on every row.** `real` · `synthetic` · `mocked` · `derived` ·
   `model_generated` · `cached`.
2. **Synthetic data may train a model; only real data may score it — per stage.** The
   corpus is hybrid: 55 real projects from the Gazette of India sit beside 96 synthetic
   ones, separable by `source_label` on every row and badged on every screen. Only the
   §3A→§3D clock is ever gazetted, so `notification_3a_11` is the one stage with real
   holdout rows (`n_test_real = 92` landmark rows from 40 real intervals, scored on their own
   as `real_holdout`); stages 2–5 report `n_test_real = 0` and say why in `notes`. A young
   harvest's real intervals are all on time — a lapsed §3A never yields a §3D — so real
   discrimination metrics stay undefined until one lapses; real Brier is reported meanwhile,
   and it is **not** evidence of skill: every real row's top driver lies beyond the training
   range (see rule 6). Everywhere the holdout is synthetic, the metrics are a diagnostic of
   the mechanism, not a validated real-world performance claim. The model registry
   (`/models` in the app) states all of this prominently, per stage, not in fine print.
3. **Statutory clocks are labelled.** Three of five stage deadlines come from law; two are
   administrative targets we chose. Every `ProjectStage` row carries `clock_source` and every
   screen renders the distinction via the `ClockSourceBadge` component.
4. **Open stages are right-censored** (`is_delayed = NULL`), never scored as on-time.
5. **Derived court dates say so.** No case in the corpus carries a real next-hearing date, so
   the pipeline derives one for active cases only and stamps `next_hearing_source='derived'`.
6. **Precision-first bands.** RED requires a HIGH-confidence identifier match on an active
   case. HIGH delay risk is emitted only if it clears ≥ 0.70 precision on held-out data
   **and sits at or above the rate at which that stage overruns anyway** — otherwise the
   band is suppressed entirely, and the model registry says why. The base-rate floor is
   what makes the band mean "elevated": below it, HIGH fires on the ordinary project and
   is unexplainable by construction — the score is the model baseline plus each feature's
   contribution, so a row *below* the baseline reaches HIGH with every SHAP driver pointing
   at lower risk. HIGH is currently suppressed for `award_3g_23`, `compensation_disbursed`
   and `possession`, where `base_rate` beat both learned models on this corpus. MEDIUM is
   the *most* selective cutoff that still recalls 80% of holdout overruns, and a driver whose
   value lies outside anything the model saw in training is flagged **beyond training
   range** on the project page rather than clipped — every real gazette project's top driver
   currently is, because the synthetic corpus tops out at 3 villages and 140 ha where the
   gazette runs to 50 and 910.
7. **No hardcoded demo data — or business rules — in the frontend.** Every dashboard number,
   chart, table row, badge and filter option is read from the database through the API,
   including the district list itself. Where a value cannot be computed (e.g. a district
   with no litigation corpus), the UI says so explicitly rather than substituting a default.
   The frontend also never re-derives a decision the pipeline made: the model registry
   reports the calibration branch `s12` recorded, it does not recompute it from a row count.

---

## Real acquisition data

`data/raw/bhoomirashi/` was an empty placeholder, which is why every acquisition row used to
be synthetic and `n_test_real = 0` everywhere. The implementation blueprint calls that risk
**X-1**, "the single largest risk". It is closed for the one stage the public record labels.

`ingest/` closes it at the source. It harvests real §3A and §3D notifications from the
Gazette of India — the authoritative publication channel for National Highways Act
acquisitions — and writes them to `data/raw/` as a contract the risk engine can consume:

```bash
make ingest                        # long-running, resumable, incremental
make ingest ARGS="--limit 200"     # a bounded first run
```

A §3D notification is self-describing: it states its own date and recites the date of the
§3A it closes, so **one document yields a complete, real, labelled interval** against the
365-day s.3D(3) clock — the one whose breach voids the notification. The committed harvest
holds forty closed intervals, from 35 to 364 days; the longest three (364, 356 and 335
days) came within weeks of lapsing.

**Only stage 1 can ever be real.** Awards under §3G, compensation disbursement and taking of
possession are never gazetted, so the other four stages stay synthetic. That is a limit of
the public record, and it means `n_test_real` has to be reported *per stage* rather than
globally.

`make build` still never touches the network — `make ingest` is a separate, explicit,
human-run command, and the build reads only what it left behind.

The contract is wired into the build, per
[`docs/specs/2026-09-10-acquisition-contract-handoff.md`](docs/specs/2026-09-10-acquisition-contract-handoff.md):

- `s8` windows it at the build's "now" (`common.TODAY`, pinned to the harvest date — a
  notification published after it does not exist yet, a declaration published after it has
  not happened yet) and appends every gazetted project as a `real` row carrying its one
  gazetted stage. Measures the gazette never publishes stay NULL.
- `s9` refuses a real row that carries such a measure, or a real project with a non-real
  stage. `s10` binds by (district, village), so a real village name can never fabricate a
  link into another district's parcels.
- `s11`/`s12` carry stage provenance through to a per-stage `n_test_real` and a separately
  scored real holdout; `s13` scores every open real notification like any other open stage.

The shipped build holds **151 projects — 96 synthetic and 55 real across 17 states — 40 real
§3A→§3D intervals in the holdout, and 15 real open notifications on the risk dashboard.**
Every real figure traces to a gazette document id in `gazette_ref`.

---

## The ML, in short

One calibrated classifier **per lifecycle stage**, because the problem statement asks for the
probability of delay *at different stages*.

```
HistGradientBoostingClassifier(max_depth=3, max_leaf_nodes=8, l2_regularization=1.0)
  -> CalibratedClassifierCV(isotonic if n_train>=200 else sigmoid)     [always sigmoid on this corpus]
  -> compared against base_rate and logistic_regression on the same holdout (Brier score)
  -> whichever wins ships; base_rate winning is reported, not hidden
  -> thresholds chosen on the holdout: t_high at precision >= 0.70, t_med at recall >= 0.80
  -> SHAP Explainer wraps the shipped model's predict_proba directly (works for any of the
     three algo types, not just tree models) - top-5 signed drivers, persisted at build time
  -> driver -> action rule table (pipeline/recommendations.py, retrieved, never generated)
```

20 features per `(project, stage)` row in four families: **litigation** (9, the
differentiator — `share_parcels_red`, `has_interim_order`, `n_active_cases`,
`litigation_coverage`, …), project intrinsics (4), administrative (4), district context (3,
computed on the training split only, using a shared `cutoff_date` written by `s11` and reused
by `s12`). Every feature carries a leakage-safe observation point: open stages observe at the
real build "now"; closed (training) stages observe at a point strictly before their own
completion, so no feature can smuggle in the outcome it is meant to predict. A dedicated test
suite (`tests/test_features.py`) enforces this mechanically.

On this corpus, `logistic_regression` won 4 of 5 stages and `base_rate` won the fifth
(`possession`) — reported plainly per rule 2 above, not smoothed over.

Full specification: [`docs/specs/2026-08-30-sih26017-acquisition-delay-design.md`](docs/specs/2026-08-30-sih26017-acquisition-delay-design.md)
(design intent) · [`pipeline/README.md`](pipeline/README.md) (what actually runs).

---

## What this is not

Not a blockchain land registry. Not an OCR digitisation tool. Not a legal chatbot. Not a
land-record portal or a grievance system.

**A prediction and explanation layer over records that already exist.** It creates, corrects
and adjudicates nothing. It does not predict how a court will rule, does not replace the
CALA's judgement, and a LOW risk band is not a guarantee of on-time completion. Access control
is demo-grade (an `X-Role` header, not a login) and is labelled as such everywhere it appears.

---

## Documentation

| Document | What it covers |
|---|---|
| [`docs/plans/2026-09-09-adhigrahan-radar-implementation-blueprint.md`](docs/plans/2026-09-09-adhigrahan-radar-implementation-blueprint.md) | The audit and implementation plan this build executed |
| [`docs/architecture/adhigrahan-radar-architecture.md`](docs/architecture/adhigrahan-radar-architecture.md) | Layered view, 15-table data model, statutory clocks, serving contract, guardrails |
| [`pipeline/README.md`](pipeline/README.md) | Every stage s0-s15, corpus facts, and every deliberate deviation |
| [`docs/specs/`](docs/specs) | Approved designs, newest first |
| [`docs/product/vivaad-radar-prd.md`](docs/product/vivaad-radar-prd.md) | The linkage-subsystem PRD (§ references throughout the codebase point here) |
| [`docs/product/SIH26017-idea-ppt-content.md`](docs/product/SIH26017-idea-ppt-content.md) | Submission content, with every figure traced to its source |
| [`docs/research/`](docs/research) | Verified data sources, dead ends, and what was checked |
| `docs/architecture/*.excalidraw` | System and ML diagrams — regenerate with `make diagrams` |

---

## License

MIT — see [LICENSE](LICENSE).
