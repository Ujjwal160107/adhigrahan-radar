# Adhigrahan Radar — System Architecture

**Date:** 2026-08-30 (design) · updated 2026-09-09 (build)
**Status:** Built. Offline pipeline s0-s15, 15-table SQLite store, 19 API endpoints, full
risk-engine frontend. See the root README and `docs/plans/2026-09-09-adhigrahan-radar-
implementation-blueprint.md` for what changed since the design below and why.
**Problem statement:** SIH26017 — *Predictive Analytics System for Early Detection of Land
Acquisition Delays*. Ministry of Rural Development, Dept. of Land Resources. Software.
**Design spec:** `docs/specs/2026-08-30-sih26017-acquisition-delay-design.md`
**Predecessor:** `docs/specs/2026-08-20-vivaad-radar-architecture-design.md`
**Companion diagrams:** `vivaad-radar-architecture.excalidraw`, `adhigrahan-ml-pipeline.excalidraw`

---

## 1. What this system is

Adhigrahan Radar predicts which land-acquisition projects will miss their **statutory**
deadlines, and says why, before the deadline passes.

It is built on top of Vivaad Radar, which answered a different question — *"is this parcel in
court?"*. Vivaad Radar is not discarded and is not a separate product. It becomes a
**feature provider**: the share of a project's parcels under active litigation is one of the
delay model's strongest inputs, and computing it requires a court↔parcel join that this repo
already runs and no competing system has.

**One sentence:** *land acquisition stalls mostly for reasons already written down somewhere
— a pending suit, an expiring gazette clock, an undisbursed award — and this system reads
those sources together and puts a calibrated probability on the stall.*

### 1.1 Two products, one build

| | Vivaad Radar (subsystem) | Adhigrahan Radar (product) |
|---|---|---|
| Question | Is this parcel in court? | Will this project miss its statutory deadline? |
| Unit | Parcel | (Project, lifecycle stage) |
| Output | GREEN / AMBER / RED + evidence | Delay probability + risk band + drivers + action |
| Method | Deterministic rules + fuzzy record linkage | Calibrated supervised classification |
| User | (was buyer) now internal drill-down | District officer, CALA, policymaker |
| Status | Built, 44 tests green | Built, 64 tests green |

---

## 2. Layered view

```
┌── SOURCES ──────────────────────────────────────────────────────────────┐
│  Allahabad HC order corpus (REAL)        No real Bhoomi Rashi snapshot  │
│  38 cases, Sultanpur                     available (data/raw/ empty) - │
│  Synthetic land parcels (LABELLED)       acquisition side fully        │
│                                           SYNTHETIC, RISK_SEED-driven   │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
┌── OFFLINE BUILD ────────────▼───────────────────────────────────────────┐
│  s0..s7   linkage engine    ──▶  Parcel.status, ParcelCaseLink          │
│                                          │ litigation features          │
│  s8..s15  risk engine       ◀────────────┘                              │
│           bind ▸ features ▸ train ▸ calibrate ▸ threshold ▸ SHAP ▸ act  │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │      ══ OFFLINE BOUNDARY ══
┌── SERVING ──────────────────▼───────────────────────────────────────────┐
│  vivaad.db (15 tables, everything precomputed)                          │
│  FastAPI — 19 endpoints, every one a SELECT. No model in the request    │
│  path. Fallback middleware serves cached JSON at the same URLs.         │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
┌── UI ───────────────────────▼───────────────────────────────────────────┐
│  RiskDashboard ▸ ProjectPortfolio ▸ ProjectDetail ▸ ModelHistory         │
│                        └─ driver panel ─▶ parcel evidence (Vivaad)      │
└─────────────────────────────────────────────────────────────────────────┘
```

**The offline boundary is the load-bearing rule.** Everything expensive, fuzzy, or
model-driven happens before the demo. At request time the system does nothing but read.

---

## 3. Dependency direction

```
pipeline  ──▶  vivaad.db  ──▶  backend  ──▶  frontend
```

One-way, never reversed, unchanged from the predecessor design. The database file is the
only interface between the pipeline and the backend; the REST contract is the only interface
between the backend and the frontend. Three workstreams proceed in parallel against those
two contracts.

The risk layer does not create a third interface. It adds tables to the same database and
endpoints to the same API.

---

## 4. Data model — 15 tables

### 4.1 Existing eight (Vivaad Radar, unchanged)

`Parcel` · `Person` · `CourtCase` · `CaseParty` · `CourtEvent` · `ParcelCaseLink` ·
`Watchlist` · `SourceRecord`

`Parcel.status` / `.confidence` are precomputed by `s5`. `ParcelCaseLink.evidence` is the
JSON the methodology panel renders. Neither is recomputed at query time.
`Watchlist` gained a nullable `project_id` column (§4.2 below).

### 4.2 New six (Adhigrahan Radar) + AuditLog

| Table | Holds | Grain |
|---|---|---|
| `AcquisitionProject` | Project identity, act, agency, district, area, families, current stage | one project |
| `ProjectStage` | Stage clocks: statutory days, start, completion, overdue, delay label | one (project, stage) |
| `ProjectParcel` | Project→parcel binding with confidence and evidence | one (project, parcel) |
| `ProjectRisk` | Delay probability, band, drivers, recommendations, model version | one (project, stage) |
| `ModelRun` | Model registry: algo, split, metrics, thresholds, feature list | one (model_version, stage) |
| `Intervention` | Officer-recorded follow-up action, with a risk-band snapshot | one recorded action |

`AuditLog` is the fifteenth table: append-only, written by `backend/auth.py` on every
request, never touched by the pipeline. The predecessor design's "13 tables" figure did not
account for `Intervention` (needed for the officer workflow's "record an action" step) or the
always-built `AuditLog`; both are built and this is the corrected count.

### 4.3 Two deliberate echoes

`ProjectRisk` is intentionally the same shape as `ParcelCaseLink`: a precomputed score, a
band, and an evidence JSON. `ProjectParcel.binding_evidence` reuses
`ParcelCaseLink.evidence`'s schema so the frontend evidence renderer is shared, not forked.

The architecture has exactly one grammar for *"here is a scored link and here is why"*, and
both layers speak it.

### 4.4 The invariant that changed

The predecessor design fixed the schema at **exactly eight tables**. That invariant is
superseded here and the reason is recorded so it does not read as drift: the delay model
introduces a genuinely new entity (the project) that cannot be expressed as a column on an
existing one.

---

## 5. Statutory clocks

The delay label is not a judgement call. It comes from law.

| Transition | Days | Source | `clock_source` |
|---|---|---|---|
| 3A → 3D | 365 | NH Act 1956, s.3D(3) — 3A lapses if 3D is not published | `statute` |
| s.11 → s.19 | 365 | RFCTLARR 2013, s.19(7) | `statute` |
| s.19 → s.23 award | 365 | RFCTLARR 2013, s.25 | `statute` |
| award → compensation disbursed | 90 | project chosen | `administrative_target` |
| award → possession | 90 | project chosen | `administrative_target` |

Three of five clocks are statutory; two are targets we chose. **Every screen and slide
showing a deadline must show which kind it is**, via `clock_source`. This is the same rule
as `next_hearing_source = 'derived'` in the linkage layer, for the same reason: an
unlabelled fabricated deadline is the cheapest possible credibility loss.

---

## 6. Serving layer

### 6.1 Endpoints — 19 total (7 existing + 12 new)

| Existing (Vivaad) | New (Adhigrahan) |
|---|---|
| `GET /parcels/search` | `GET /projects` (filter/sort/paginate) |
| `GET /parcels/{id}` | `GET /projects/{id}` |
| `GET /parcels/{id}/litigation` | `GET /projects/{id}/risk` |
| `GET /cases/{id}` | `GET /projects/{id}/parcels` |
| `GET /dashboard/overview` | `GET /projects/{id}/interventions` (GET + POST) |
| `GET /dashboard/heatmap` | `GET /dashboard/risk` |
| `GET /dashboard/map` | `GET /dashboard/risk-map` |
| `POST /watchlist` · `GET /watchlist` *(extended to accept `project_id`)* | `GET /models/history` |
| | `GET /auth/session` |

Every endpoint is a `SELECT` plus JSON shaping. No scoring, no model load, no SHAP at
request time.

### 6.2 Reliability tiers

Unchanged from the predecessor design and extended to the new routes.

| Tier | Mechanism | Trigger |
|---|---|---|
| 1 — Live | frontend → FastAPI → SQLite | Normal |
| 2 — Cached | `fallback.py` serves `s7`/`s15` JSON at the same URLs; the frontend never knows | DB missing or query failure |
| 3 — Emergency | Flagship payloads bundled into `api/client.ts` | `?demo=1` or a failed fetch |

No tier touches the network during a demo.

### 6.3 Access model

An `X-Role` header (`officer` default, `district_officer`, `reviewer`, `admin`) resolved by
`current_role()` gates writes: `reviewer` is read-only by design (`WRITE_ROLES` excludes it),
every other role can write. An append-only `AuditLog` table records every request's role,
method, path, status, and timestamp, written by outermost middleware in `main.py` so it never
misses a request the router layer sees. **Explicitly a demo-grade access model, not
authentication** — there is no password, session, or token, and any client can claim any role
by setting the header — labelled `auth_mode: "demo_role_header"` in `/auth/session`.

---

## 7. Frontend

| Screen | Route | Purpose |
|---|---|---|
| `RiskDashboard` | `/` | State/district rollups, risk-band counts, officer heatmap, watchlist |
| `ProjectPortfolio` | `/projects` | Projects ranked by delay probability, server-filtered/paginated |
| `ProjectDetail` | `/projects/:id` | Stage timeline, per-stage risk, drivers, recommendations, intervention form |
| `ModelHistory` | `/models` | Model registry: shipped algo, metrics, baselines per stage |
| `LookupApp` *(existing linkage UI)* | `/lookup`, `/lookup/parcel/:id` | Parcel search, case/litigation drill-down |
| `Watchlist` *(existing)* | `/watchlist` | Retained, extended to project subscriptions |

### 7.1 `Timeline.tsx` — the component that carries the story

It already plots `filed → interim order → sale → next hearing` on one horizontal SVG axis.
For a project it plots `3A → 3D → award → possession` on the same axis and adds a **vertical
statutory-deadline marker**, **overshoot shading** from deadline to today, and a
`clock_source` badge. Same grammar, no charting library, materially better story.

### 7.2 Design system

`RiskBadge` renders LOW / MEDIUM / HIGH on the **existing** green / amber / red tokens from
`DESIGN_GUIDELINES.md` §3. Shared tokens, separate copy — the mandatory disclaimer differs
and is baked into the component:

> Predicted risk of missing a statutory deadline. Not an administrative finding.

The Legal Neubrutalism aesthetic is not touched.

---

## 8. Provenance

Every row in the build carries a label from the PRD §21 set: `real`, `synthetic`, `mocked`,
`derived`, `model_generated`, `cached`.

| Layer | Provenance |
|---|---|
| Court cases, orders, CNRs | `real` |
| Acquisition projects, stages, notification dates | `synthetic` — no real Bhoomi Rashi snapshot is available in this environment (`data/raw/bhoomirashi/` is an empty placeholder); see `pipeline/README.md` deviation 5 |
| Land parcels | `synthetic` |
| Link scores, parcel status, delay labels | `derived` |
| Delay probabilities, SHAP drivers | `model_generated` |
| Alerts and notifications | not built — no notification/alert feature exists in this system |

**Hard rule: no `synthetic` row contributes to any metric reported as real.** Synthetic rows
may train; only real rows may score, and they are scored on their own, per stage
(`runs[algo].real_holdout`, `n_test_real` per stage). The gazette labels one clock, so
`notification_3a_11` carries the real holdout (40 §3A→§3D intervals in the shipped build)
and stages 2–5 report `n_test_real=0` with the reason in `ModelRun.notes`.

---

## 9. Guardrails

Carried forward from the predecessor design and still binding. If any of these appears in
the repo, the project has drifted:

blockchain land registry · OCR / handwriting digitisation · legal chatbot · generic
grievance portal · land-record CRUD · payments · real auth · cloud orchestration · a graph
database · PostGIS · live scraping at demo time · retraining in the request path.

The system is a **prediction and explanation layer over records that already exist**. It
does not create, correct, or adjudicate any record.

---

## 10. Status of the open items above

| Item | Resolution |
|---|---|
| Bhoomi Rashi snapshot size and district spread | Never became available in this environment. `s8` generates the acquisition contract deterministically from `RISK_SEED` instead, fully disclosed as `synthetic`. `n_train` is 34-90 rows/stage; the model discipline in `pipeline/README.md` deviation 6 (20-feature cap, shallow trees, LR/HGB/base-rate comparison) exists because of this. |
| Role stub and `AuditLog` | Both built: `backend/auth.py`, `backend/routers/auth.py`, outermost audit middleware in `main.py`. |
| Excalidraw regeneration | Still pending; `make diagrams` regenerates the two `.excalidraw` files from the current schema/pipeline when run. |
| PRD Part 16, `CONTEXT` §11, `DESIGN_GUIDELINES` risk tokens, PS traceability doc | `DESIGN_GUIDELINES.md` §3 now documents the risk-band tokens; see that file for the current state of each remaining item. |
