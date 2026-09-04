# Adhigrahan Radar

**Predicting which land-acquisition projects will miss their statutory deadlines — before
they miss them.**

Smart India Hackathon 2026 · **SIH26017** — *Predictive Analytics System for Early Detection
of Land Acquisition Delays* · Ministry of Rural Development, Dept. of Land Resources

---

## The problem

Land acquisition is legally time-boxed, and the boxes are the point:

| Transition | Deadline | Consequence of breach |
|---|---|---|
| §3A → §3D (NH Act 1956) | 365 days | 3A notification **lapses**, s.3D(3) |
| §11 → §19 (RFCTLARR 2013) | 365 days | Preliminary notification lapses, s.19(7) |
| §19 → §23 award | 365 days | Acquisition proceedings lapse, s.25 |

Missing the clock **voids the acquisition**. The state re-notifies, re-values the land at a
higher rate, and pays more compensation for the same parcels. Meanwhile the affected families
have already lost planning certainty over land that is neither theirs to use nor paid for.

India monitors this **retrospectively** — a monthly return telling an officer that a project
already crossed 300 days, when nothing can be done. Nothing flags a project *before* the
clock runs out.

## The idea

Every driver of delay is already written down somewhere — a court file, a gazette
notification, a compensation register. Each lives in a different department's system, keyed
differently, and none is read together.

The least visible driver is the biggest: **pending litigation on the parcels being
acquired**. Court records are indexed by **party name**, land records by **survey number**.
The two systems never agreed on a key, so nobody can currently answer *"how many parcels in
this corridor are under active dispute?"*

**This repo can.** It contains a working court↔parcel record-linkage engine, and the delay
model consumes it as a feature source:

```
Vivaad Radar  (built, s0-s7)          ->   feature provider
court <-> parcel linkage                   share_parcels_red, n_active_cases,
                                           has_interim_order, max_pendency_days
                                                        |
                                                        v
                              Adhigrahan Radar  (designed, s8-s15)
                              per-stage delay model + drivers + retrieved actions
```

The linkage engine earns its matches. Where the court writes survey `1365/1` in village
*Madanpur Paniyar* and the land record writes `1365-1` in *Madanpur Panyar*, the pipeline
reconciles both through survey normalisation, a village gazetteer and fuzzy name matching,
and scores the link at **0.9105**.

---

## Status

| Component | State |
|---|---|
| Linkage engine `s0`–`s7` | **Built.** 38 real High Court cases, 135 parcels, 84 links, 47 tests green |
| Risk engine `s8`–`s15` | **Designed, not implemented.** See [`docs/specs/`](docs/specs) |
| Backend — 8 linkage endpoints | **Built** |
| Backend — 6 risk endpoints | Designed |
| Frontend — search, result, officer dashboard, watchlist | **Built** |
| Frontend — risk dashboard, project portfolio, project detail | Designed |

Current build, from `s1_report.json` / `s5_report.json`:

| | |
|---|---|
| District | Sultanpur, Uttar Pradesh |
| Cases / parcels | 38 real cases (8 active) / 135 synthetic parcels, 22 villages |
| Links surfaced | 84 (43 HIGH, 41 MEDIUM) from 1,678 scored pairs |
| Parcel status | 12 RED · 62 AMBER · 61 GREEN |
| Flagship | `P-B01` = RED @ 0.9105 · `P-A01` = GREEN |
| Longest pendency in corpus | **~2.6 years** (not the PRD's illustrative "6 years") |
| Tests | 47, all green |

---

## Quickstart

```bash
make setup     # python venv + pip install + npm install
make build     # regenerate data/output from the committed contract
make test      # 47 tests
make api       # http://localhost:8000
make web       # http://localhost:5173   (separate terminal)
```

Without `make`:

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # POSIX: .venv/bin/pip
.venv/Scripts/python pipeline/run_all.py --skip-handoff
.venv/Scripts/python -m pytest
.venv/Scripts/uvicorn backend.main:app --reload --port 8000
cd frontend && npm install && npm run dev
```

### Why `--skip-handoff` is the default

`s0_handoff` regenerates the synthetic land side from an external High Court corpus that is
**not part of this repo**. `data/input/cases.parquet` and `data/input/parcels.parquet` are
the committed data contract, and every stage downstream of them is fully reproducible. `make
build` therefore starts at `s1`. Use `make build-all` only if you have the external corpus.

`data/output/` is **not committed** — it is a build artifact. CI runs `make build` on every
push precisely so a broken build cannot reach a fresh clone silently.

---

## Layout

```
adhigrahan-radar/
├── backend/           FastAPI, read-only over the SQLite build artifact
│   ├── routers/       parcels · cases · dashboard · watchlist
│   ├── fallback.py    serves cached JSON at the same URLs when the DB is gone
│   └── tests/         34 API tests
├── frontend/          React (Vite) + Tailwind + Leaflet
│   └── src/pages/     Search · Processing · Result · OfficerDashboard · Watchlist
├── pipeline/          the offline build, one file per stage
│   ├── s0..s7         linkage engine (built)
│   ├── s8..s15        risk engine (designed)
│   └── README.md      what each stage does, and every deliberate deviation
├── data/
│   ├── input/         the data contract - committed
│   ├── raw/           cached source snapshots - the demo never hits the network
│   └── output/        build artifacts - gitignored, except trained models
├── tests/             pipeline golden suite (13 tests)
└── docs/
    ├── architecture/  system architecture, design system, excalidraw + generator
    ├── specs/         approved designs
    ├── plans/         executed implementation plans
    ├── product/       PRD and SIH submission content
    └── research/      source discovery and verified data sources
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
| 2 — cached | `fallback.py` serves exported JSON at the same URLs | DB missing or query failure |
| 3 — bundled | flagship payloads compiled into `api/client.ts` | `?demo=1` or a failed fetch |

---

## Honesty rules

These are enforced by tests, not by discipline. They exist because the fastest way to lose a
technical reviewer is an unlabelled fabricated number.

1. **Provenance on every row.** `real` · `synthetic` · `mocked` · `derived` ·
   `model_generated` · `cached`.
2. **Synthetic data may train a model; only real data may score it.** No synthetic row
   contributes to any reported metric.
3. **Statutory clocks are labelled.** Three of five stage deadlines come from law; two are
   administrative targets we chose. Every row carries `clock_source` and every screen renders
   the distinction.
4. **Open stages are right-censored** (`is_delayed = NULL`), never scored as on-time.
5. **Derived court dates say so.** No case in the corpus carries a real next-hearing date, so
   the pipeline derives one for active cases only and stamps `next_hearing_source='derived'`.
6. **Precision-first bands.** RED requires a HIGH-confidence identifier match on an active
   case. HIGH delay risk is emitted only if it clears ≥ 0.70 precision on held-out data —
   otherwise the band is suppressed entirely.

---

## The ML, in short

One calibrated classifier **per lifecycle stage**, because the problem statement asks for the
probability of delay *at different stages*.

```
HistGradientBoostingClassifier(max_depth=3, max_leaf_nodes=8, l2_regularization=1.0)
  -> CalibratedClassifierCV(isotonic if n>=200 else sigmoid)
  -> thresholds chosen on the holdout: t_high at precision >= 0.70, t_med at recall >= 0.80
  -> SHAP TreeExplainer, top-5 signed drivers, persisted at build time
  -> driver -> action rule table (retrieved, never generated)
```

20 features per `(project, stage)` row in four families: **litigation** (9, the
differentiator), project intrinsics (5), administrative (4), district context (3, computed on
the training split only). Every feature is computed as of stage entry, and a build-time
leakage audit raises if any contributing row post-dates it.

Reported against a **base-rate** and a **logistic-regression** baseline on ROC-AUC, PR-AUC and
Brier score. *If the LR baseline wins on the holdout, the LR ships and the slide says so.*

Full specification: [`docs/specs/2026-08-30-sih26017-acquisition-delay-design.md`](docs/specs/2026-08-30-sih26017-acquisition-delay-design.md)

---

## What this is not

Not a blockchain land registry. Not an OCR digitisation tool. Not a legal chatbot. Not a
land-record portal or a grievance system.

**A prediction and explanation layer over records that already exist.** It creates, corrects
and adjudicates nothing. It does not predict how a court will rule, does not replace the
CALA's judgement, and a LOW risk band is not a guarantee of on-time completion.

---

## Documentation

| Document | What it covers |
|---|---|
| [`docs/architecture/adhigrahan-radar-architecture.md`](docs/architecture/adhigrahan-radar-architecture.md) | Layered view, 13-table data model, statutory clocks, serving contract, guardrails |
| [`pipeline/README.md`](pipeline/README.md) | Every stage, corpus facts worth quoting, and all seven deliberate deviations |
| [`docs/specs/`](docs/specs) | Approved designs, newest first |
| [`docs/product/vivaad-radar-prd.md`](docs/product/vivaad-radar-prd.md) | The linkage-subsystem PRD (§ references throughout the codebase point here) |
| [`docs/product/SIH26017-idea-ppt-content.md`](docs/product/SIH26017-idea-ppt-content.md) | Submission content, with every figure traced to its source |
| [`docs/research/`](docs/research) | Verified data sources, dead ends, and what was checked |
| `docs/architecture/*.excalidraw` | System and ML diagrams — regenerate with `make diagrams` |

---

## License

MIT — see [LICENSE](LICENSE).
