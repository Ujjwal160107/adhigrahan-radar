# Adhigrahan Radar — System Architecture

**Date:** 2026-08-30
**Status:** Design approved; implementation not started
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
| Status | Built, 47 tests green | Designed |

---

## 2. Layered view

```
┌── SOURCES ──────────────────────────────────────────────────────────────┐
│  Allahabad HC order corpus (REAL)        Bhoomi Rashi 3A/3D (REAL,      │
│  38 cases, Sultanpur                     cached snapshot, ≥8 UP dists)  │
│  Synthetic land parcels (LABELLED)       Synthetic projects (LABELLED)  │
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
│  vivaad.db (13 tables, everything precomputed)                          │
│  FastAPI — 14 endpoints, every one a SELECT. No model in the request    │
│  path. Fallback middleware serves cached JSON at the same URLs.         │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
┌── UI ───────────────────────▼───────────────────────────────────────────┐
│  RiskDashboard ▸ ProjectPortfolio ▸ ProjectDetail                        │
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

## 4. Data model — 13 tables

### 4.1 Existing eight (Vivaad Radar, unchanged)

`Parcel` · `Person` · `CourtCase` · `CaseParty` · `CourtEvent` · `ParcelCaseLink` ·
`Watchlist` · `SourceRecord`

`Parcel.status` / `.confidence` are precomputed by `s5`. `ParcelCaseLink.evidence` is the
JSON the methodology panel renders. Neither is recomputed at query time.

### 4.2 New five (Adhigrahan Radar)

| Table | Holds | Grain |
|---|---|---|
| `AcquisitionProject` | Project identity, act, agency, district, area, families, current stage | one project |
| `ProjectStage` | Stage clocks: statutory days, start, completion, overdue, delay label | one (project, stage) |
| `ProjectParcel` | Project→parcel binding with confidence and evidence | one (project, parcel) |
| `ProjectRisk` | Delay probability, band, drivers, recommendations, model version | one (project, stage) |
| `ModelRun` | Model registry: algo, split, metrics, thresholds, feature list | one training run |

`AuditLog` is a conditional fourteenth table, present only if the role stub is built.

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

### 6.1 Endpoints — 8 existing + 6 new

| Existing (Vivaad) | New (Adhigrahan) |
|---|---|
| `GET /parcels/search` | `GET /projects` |
| `GET /parcels/{id}` | `GET /projects/{id}` |
| `GET /parcels/{id}/litigation` | `GET /projects/{id}/risk` |
| `GET /cases/{id}` | `GET /dashboard/risk` |
| `GET /dashboard/overview` | `GET /dashboard/risk-map` |
| `GET /dashboard/heatmap` | `GET /models/history` |
| `POST /watchlist` · `GET /watchlist` | *(extended to accept `project_id`)* |

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

### 6.3 Access model (conditional)

An `X-Role` header (`officer | policymaker | viewer`) gates response fields; an append-only
`AuditLog` records every request. **Explicitly a mocked access model, not a security
system**, labelled as such on screen. It exists because the problem statement names it; it
is first on the cut list.

---

## 7. Frontend

| Screen | Route | Purpose |
|---|---|---|
| `RiskDashboard` | `/risk` | State and district rollups, delay trends, corridor map |
| `ProjectPortfolio` | `/projects` | Projects ranked by delay probability, filterable |
| `ProjectDetail` | `/projects/:id` | Stage timeline, per-stage risk, drivers, actions |
| `Result` *(existing)* | `/parcel/:id` | Reached from the driver panel: why this parcel is a driver |
| `Search`, `Watchlist`, `OfficerDashboard` *(existing)* | — | Retained, reframed for officers |

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
| Bhoomi Rashi projects and notification dates | `real` (cached snapshot) |
| Land parcels, synthetic projects | `synthetic` |
| Link scores, parcel status, delay labels | `derived` |
| Delay probabilities, SHAP drivers | `model_generated` |
| Alerts and notifications | `mocked` |

**Hard rule: no `synthetic` row contributes to any metric shown to a judge.** Synthetic rows
may train; only real rows may score.

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

## 10. Open items

| Item | Resolve by |
|---|---|
| Bhoomi Rashi snapshot size and district spread actually obtainable | Before `s12` — it sets `n_train` and therefore whether GBM or LR ships |
| Whether the role stub and `AuditLog` survive the timebox | Cut-list decision at build time |
| Excalidraw regeneration (`vivaad-radar-architecture`, new `adhigrahan-ml-pipeline`) | Pending |
| PRD Part 16, `CONTEXT` §11, `DESIGN_GUIDELINES` risk tokens, PS traceability doc | Pending |
