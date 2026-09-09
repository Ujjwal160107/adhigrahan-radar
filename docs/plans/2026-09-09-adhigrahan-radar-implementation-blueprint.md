# Adhigrahan Radar — Implementation Blueprint

**Date:** 2026-09-09
**Status:** Audit complete, implementation not started
**Scope:** SIH26017 — Predictive Analytics System for Early Detection of Land Acquisition Delays

---

## Headline finding

The repository contains a **complete, working, well-engineered implementation of a different
product**. What is built is *Vivaad Radar* — a court↔parcel litigation-linkage lookup answering
"is this land in court?". What SIH26017 asks for is *Adhigrahan Radar* — per-stage statutory
deadline delay prediction for land-acquisition projects.

Measured, not inferred:

| Check | Result |
|---|---|
| `pipeline/run_all.py --skip-handoff` | **passes**, 0.3 s |
| `pytest` | **47 passed**, 0.26 s |
| All 9 HTTP routes | **200** against the real DB |
| `data/output/vivaad.db` | **8 tables**, 135 parcels / 38 cases / 84 links |
| Tables about projects, stages, deadlines, or risk | **0** |
| Lines of ML code (`sklearn`, `shap`) | **0** — both pinned, neither imported |
| `make api` on Linux/macOS | **fails**, `Scripts/uvicorn: No such file or directory` |
| `npm run build` | **fails**, `TS2322` at `fallbackData.ts:149` — CI `web` job is red |
| `pip install -r requirements.txt` on Python 3.14 | **fails**, `pyarrow==17.*` has no cp314 wheel |
| Accessibility attributes in `frontend/src` | **0** (`aria-*`, `role=`, `<label>`, `alt=`) |
| Frontend tests | **0** |

The foundation is genuinely good and must be preserved. The linkage engine is a legitimate
feature provider for the 9-feature litigation family the problem statement's differentiator
depends on. But **every deliverable named by SIH26017 is absent**: projects, acquisition
stages, statutory deadlines, delay probability, drivers, recommendations, alerts, RBAC.

This is not a polish job. It is: fix 5 build/config defects, then build the missing 60% on a
solid 40% base.

---

## A. Current architecture

### A.1 Dependency direction (enforced, one-way, correct)

```
pipeline/ (offline)  ->  data/output/vivaad.db  ->  backend/ (FastAPI)  ->  frontend/ (React)
                                                          |
                                                    data/output/fallback/*.json  (tier 2)
                                                          |
                                              frontend/src/api/fallbackData.ts  (tier 3)
```

`CONTRIBUTING.md` rule 1 forbids request-time code importing `pipeline/`. Verified: no
`backend/` module imports `pipeline/`. This is the single best architectural decision in the
repo and everything below preserves it.

### A.2 Frontend

- React 18.3 + Vite 5.4 + TypeScript 5.6 + Tailwind 3.4, Leaflet 1.9 / react-leaflet 4.2.
- **No router.** `App.tsx` is a `useState<'search'|'processing'|'pick'|'result'|'dashboard'|'watchlist'>`
  state machine. No deep links, no shareable URLs, no browser back/forward.
- 6 pages: `Search`, `Processing`, `ParcelPicker`, `Result`, `OfficerDashboard`, `Watchlist`.
- 5 components: `Header`, `AppFooter`, `ParcelMap`, `CaseDetailModal`, plus `resultModel.ts`
  (pure view-model helpers — the cleanest file in the frontend).
- Design system: "Legal Neubrutalism" per `docs/architecture/DESIGN_GUIDELINES.md` — 2 px
  borders, 0 radius, `#FAF3E0` paper, serif-italic headings, mono body. Executed well and
  consistently. Keep it.

### A.3 Backend

- FastAPI 0.115, 4 routers, **9 routes** (8 documented + `GET /dashboard/map`, which is
  implemented and consumed by the frontend but appears in no spec).
- `backend/db.py` — `sqlite3` + `Row` factory, path from `VIVAAD_DB`. No ORM. Justified at
  this scale: every query is a `SELECT` with 0–2 joins.
- `backend/fallback.py` — HTTP middleware. On 5xx or unhandled exception, resolves the
  request path to a cached JSON file (nested `a/b.json` then flat `a_b.json`), with a
  `relative_to` containment guard against traversal. Correct and tested.
- **No authentication, no authorization, no session, no user model.**
  `backend/routers/watchlist.py:20` hardcodes `user_ref = 'demo-user'`.
- Startup probe (`main.py:_check_db`) logs a warning if the DB or `Parcel` table is missing,
  rather than crashing. Good.

### A.4 Database

SQLite, **8 tables**, CamelCase, produced by `pipeline/s6_load_db.py`:

`Parcel`, `Person`, `CourtCase`, `CaseParty`, `CourtEvent`, `ParcelCaseLink`, `Watchlist`,
`SourceRecord`.

Derived values (`Parcel.status`, `.confidence`, `.note`, `.closed_history`) are precomputed by
`s5` and stored, so `GET /parcels/{id}/litigation` is a join with no query-time scoring. This
is the right pattern and the risk layer must follow it exactly.

**Schema is defined twice** — `backend/schema.sql` and the `SCHEMA` string in
`pipeline/s6_load_db.py:17-57`. They currently agree by hand-maintenance only. This is a
latent divergence bug and the documented cause of a past outage (`docs/specs/2026-08-20-integration-handoff.md`
records 500s on all 8 endpoints when the backend was built against an invented schema).

### A.5 ML

**There is none.**

- `pipeline/s4_score.py` is a fixed-weight linear rule scorer:
  `identifier 0.40 · name 0.25 · father_name 0.15 · village 0.10 · case_type 0.10`, with
  RapidFuzz string similarity and weight renormalisation over present features. Weights are
  correctly labelled "hackathon-initial values … explicitly unvalidated starting points".
- `pipeline/s5_status.py` is a pure decision table (RED requires HIGH band **and** identifier
  match **and** active case).
- `scikit-learn==1.5.*` and `shap==0.46.*` are pinned in `requirements.txt` under a comment
  reading `offline build: risk engine (s8-s15)` — **neither is imported anywhere.**
- `data/output/models/` is git-un-ignored in anticipation of trained artifacts. It does not
  exist.

### A.6 Authentication

Absent. No middleware, no header check, no roles, no audit log. Single implicit demo user.

### A.7 Deployment

- No `Dockerfile`, no `docker-compose.yml`, no container config of any kind.
- `Makefile` with 9 targets. `api` is broken on POSIX (see D-1).
- CI: two GitHub Actions jobs. `python` (3.11) runs build + pytest — green. `web` (Node 20)
  runs `npm ci && npm run build` — **red**, because `tsc` fails.
- No lint step in CI despite `make lint` and a configured `[tool.ruff]` section.

---

## B. Current state assessment

### B.1 Working (verified by execution)

| Item | Evidence |
|---|---|
| Offline build s0–s7, reproducible from committed parquet | build completes 0.3 s, deterministic (`SEED = 20260820`) |
| Build-time contract validation (`s1_ingest`) | raises `ContractError` on missing columns, bad provenance, duplicate PKs, out-of-district rows, disposed-case-with-hearing, absent flagship rows |
| Survey/village/name normalisation and gazetteer | `1365-1` ↔ `1365/1`, `Panyar` ↔ `Paniyar` reconciled; parity test guards backend↔pipeline drift |
| Weighted linkage scoring with per-pair evidence JSON | 1,678 pairs → 84 links (43 HIGH, 41 MEDIUM) |
| Worst-case-wins status engine | 12 RED · 62 AMBER · 61 GREEN |
| All 9 API routes | 200 against real DB |
| Three-tier fallback | tier 2 files present (314 written by s7); tier 3 verified via `?demo=1` |
| 47 tests | all pass, meaningful — they assert real invariants, not plumbing |
| Absent-feature honesty in UI | `Result.tsx:330-335` renders "Feature absent — weight redistributed" instead of a fabricated 0% or 100% |
| Derived-date labelling | `next_hearing_source='derived'` persisted and rendered as `[Derived from latest order date]` |
| Design system consistency | genuinely good; do not touch |

### B.2 Partially working

| Item | Gap |
|---|---|
| Officer dashboard | Real DB counts, but litigation-density only. No projects, stages, deadlines, or risk. |
| Watchlist | Real DB read/write, but single hardcoded `user_ref`, no dedup guard, `has_update` is a static flag never set by any process. |
| Parcel map | Real GeoJSON from DB (135 features), but geometry is synthetic squares from `s0`, and the map does not say so. |
| Fallback tier 3 | Works, but hand-maintained in `fallbackData.ts` rather than generated by `s7`, so it drifts from the DB silently. |
| Error states | Present on dashboard/watchlist/picker. Absent on `Result` — a failed case-detail fetch leaves the modal empty with no message. |

### B.3 Mocked / theatrical

| Item | Location |
|---|---|
| **Fake progress animation** — 5 hardcoded steps, ~5.3 s of timers labelled "extracting court references", "resolving entities", "scoring evidence". None of this happens at request time; it all ran offline. | `frontend/src/pages/Processing.tsx:17-23` |
| `has_update: true` on the bundled watchlist fallback — a fake notification | `client.ts:145` |
| Watchlist subscribe returns a fabricated random id on failure: `Math.floor(Math.random()*1000)+10` | `client.ts:162-166` |
| Watchlist page copy admits it: "The update stamp is a scripted flag — not a live court notification." | `Watchlist.tsx:67` |

### B.4 Hardcoded (see §D for the full inventory)

Brand strings, district names, statutory weights, default confidence values, quick-pick survey
numbers, map centre coordinates, disclaimer copy.

### B.5 Broken

| # | Defect | Impact |
|---|---|---|
| **D-1** | `Makefile:44` — `$(VENV)/Scripts/uvicorn`. The `ifeq ($(OS),)` block at lines 13-16 overrides only `PY` and `PIP`, not this path. | `make api` — the README's primary quickstart command — fails on every Linux and macOS machine. |
| **D-2** | `frontend/src/api/fallbackData.ts:149` sets `father_name_similarity: null`; `types/api.ts:50` declares `father_name_similarity?: number`. | `npm run build` fails `TS2322`. CI `web` job red. No production bundle can be produced. The pipeline emits `null` for **67 of 84** links, so the type is wrong, not the data. |
| **D-3** | `pyproject.toml` `requires-python = ">=3.11"` with no upper bound; `pyarrow==17.*` / `pandas==2.2.*` have no cp314 wheels. | `make setup` fails on Python 3.13+. Reproduced on Python 3.14.7: `ModuleNotFoundError: No module named 'pkg_resources'` while building pyarrow from source. |
| **D-4** | `.env.example:8` declares `VITE_API_BASE`; `client.ts:24` reads `VITE_API_URL`. | The documented env var is dead. Pointing the frontend at a non-default API requires editing source — violating the "no manual source edits" runbook requirement. `VIVAAD_FALLBACK_DIR` (read by `fallback.py:13`) is undocumented. |
| **D-5** | `ParcelMap.tsx:153` — tiles from `https://{s}.basemaps.cartocdn.com/light_all/...`. | Requires live network; currently renders **"API KEY REQUIRED"** watermarks across the map. Directly violates the repo's own stated guarantee that "the demo never hits the network". |
| **D-6** | `CaseDetailModal.tsx:41` — status always rendered `text-radar-red`. | A **disposed** case displays its status in alarm red. |
| **D-7** | `Result.tsx:381` — the "Public Legal Notice" is unconditional and reads *"GREEN means no matching active litigation was found…"*. | Rendered verbatim on RED and AMBER results, where it is semantically wrong. Verified on the flagship RED screen. |
| **D-8** | `Result.tsx:377` — on a not-found parcel, `parcel` is `null`, so `parcel?.source_label === 'synthetic'` is false and the badge falls through to **"State Revenue Land Registry"**. | The screen asserts a real government provenance for a record that does not exist. Verified. |
| **D-9** | `Result.tsx:313` — village column falls back to `primaryLink.court`. | A court name can be rendered in a village field. |
| **D-10** | `backend/tests/test_schema.py:9` — `test_schema_creates_exactly_eight_tables`. | Hard-blocks the 13-table migration. Must be rewritten, not deleted, in Phase 2. |

### B.6 Outdated

- Branding not propagated: `frontend/index.html:7` title "Vivaad Radar — Land Litigation
  Discovery"; `Header.tsx:17` renders `vivaad radar`; `package.json:2` name
  `vivaad-radar-frontend`. The `vivaad.db` filename and `VIVAAD_DB` env var are deliberately
  retained and documented as such — that decision is sound and should stand; the *user-facing*
  strings are not covered by it.
- `pipeline/README.md:9` documents a `--risk-only` flag. `run_all.py` implements
  `--skip-handoff` only.
- `pipeline/README.md:203` and the spec's test table cite `tests/test_features.py::test_no_future_leakage`
  and `::test_district_context_train_only`. The file does not exist.
- `pipeline/README.md:36` says s6 writes "Eight §19 tables"; the newer spec supersedes this
  with 13.
- Research docs (`docs/research/*`) target **Karnataka** (Bhoomi RTC, `service22`); the build
  is **Sultanpur, Uttar Pradesh** (Allahabad HC). The pivot is nowhere explained.

### B.7 Missing

Everything the problem statement is actually about:

1. `AcquisitionProject` — no concept of a project.
2. `ProjectStage` — no acquisition lifecycle.
3. Statutory deadlines — the 365/365/365/90/90-day clocks exist only in prose.
4. `ProjectParcel` — no project→parcel binding.
5. Delay-probability prediction — no model, no target, no training, no inference.
6. Drivers / explainability — no SHAP, no feature attribution.
7. Recommendations — no `driver → action` table.
8. `ModelRun` — no model registry, no versioning.
9. Alerts / notifications — `has_update` is a static column.
10. RBAC — no roles, no `X-Role`, no `AuditLog`.
11. Multi-district — 1 district in the DB against a stated target of ≥8.
12. Deployment containerisation.
13. Frontend tests, E2E tests, accessibility.

---

## C. Requirement matrix

Sources: `PDF` = `Adhigrahan Tracker.pdf`; `PPT` = `docs/product/SIH26017-idea-ppt-content.md`;
`SPEC` = `docs/specs/2026-08-30-sih26017-acquisition-delay-design.md`;
`ARCH` = `docs/architecture/adhigrahan-radar-architecture.md`; `PRD` = `docs/product/vivaad-radar-prd.md`.

Priority: **P0** = SIH26017 cannot be claimed without it. **P1** = required for a credible
demo. **P2** = required for the "government-usable" claim. **P3** = polish.

| # | Requirement | Source | Current state | Gap | Required change | Pri |
|---|---|---|---|---|---|---|
| R1 | Unify acquisition, court and project records | PDF p.2 | Court records only | No project/acquisition entity | Add `AcquisitionProject`, `ProjectStage`, `ProjectParcel` (§F) | P0 |
| R2 | Link court cases to individual parcels | PDF p.2 | **Built** — `ParcelCaseLink`, 84 links | none | preserve unchanged | — |
| R3 | Per-project **and** per-stage risk profile | PDF p.2, PPT §5 | absent | total | `ProjectRisk` grain = (project, stage) | P0 |
| R4 | Predict probability of missing **each** statutory deadline | PDF p.2, SPEC §4 | absent | total | per-stage calibrated classifier, s11–s13 | P0 |
| R5 | Litigation exposed as a predictive feature | PDF p.2, SPEC §5.1 | data exists, never consumed as features | s11 missing | 9 litigation features from `Parcel.status` + `ParcelCaseLink` | P0 |
| R6 | Statutory deadlines persisted with lapse consequence | PPT §1, SPEC §4 | prose only | not in schema | `ProjectStage.statutory_days` + `clock_source` | P0 |
| R7 | `clock_source` distinguishes statute from administrative target, on every screen | PPT §7.8, SPEC §4 | absent | total | column + mandatory UI badge | P0 |
| R8 | Open stages right-censored (`is_delayed = NULL`, never 0) | SPEC §4, `pipeline/README.md` dev. 4 | absent | total | nullable column; excluded from train, included in scoring | P0 |
| R9 | Calibrated probability, not an arbitrary score | PPT §5 | `s4` is a fixed-weight rule scorer | no calibration anywhere | `CalibratedClassifierCV`, isotonic if n≥200 else sigmoid | P0 |
| R10 | Top-5 signed SHAP drivers, precomputed | SPEC §6.4 | absent | total | `TreeExplainer` in s13 → `ProjectRisk.drivers` | P0 |
| R11 | Recommendations retrieved from a rule table, never generated | SPEC §6.5 | absent | total | `pipeline/recommendations.py`, rule id on every row | P1 |
| R12 | HIGH band emitted only at ≥0.70 holdout precision, else suppressed | PDF p.5, SPEC §6.3 | absent | total | threshold selection in s12; test gate | P0 |
| R13 | ≥90-day median lead time for HIGH warnings | PDF p.5 | absent | total | measure in s12; report; do not claim unmeasured | P1 |
| R14 | Officer identifies delay drivers in <3 s | PDF p.5 | n/a | no driver UI | driver panel above the fold on `ProjectDetail` | P1 |
| R15 | Baselines (base-rate + LR) reported in the same table; LR ships if it wins | PPT §5, SPEC §6.2 | absent | total | all three trained and compared in s12 | P1 |
| R16 | Metrics on real rows only; synthetic may train | PPT §2.5, `CONTRIBUTING.md` r.4 | rule stated, unenforceable (no model) | total | `n_test_real`/`n_test_synthetic` in `ModelRun.metrics` + test | P0 |
| R17 | 20-feature cap; `max_depth=3`, `max_leaf_nodes=8` | PDF p.4, SPEC §5.6 | absent | total | enforce cap in s11 (see K-1 conflict) | P1 |
| R18 | Leakage audit: no feature postdates `stage.started_on` | SPEC §5.5 | absent | total | `computed_asof` per feature; s11 raises | P0 |
| R19 | District-context features on training split only | SPEC §5.4 | absent | total | compute after split | P0 |
| R20 | No GPU / no cloud; lightweight | PDF p.4 | **satisfied** | none | keep — no torch, no服务 dependency | — |
| R21 | `predicted_overrun_days` is an empirical median, not a second model | `pipeline/README.md` dev. 7 | absent | total | median overrun of delayed holdout rows, same stage+band | P2 |
| R22 | Model versioning + continuous learning | PPT §1 (PS del.), SPEC §6.6 | absent | total | `ModelRun` + `mv-YYYYMMDD-NN` + `GET /models/history` | P2 |
| R23 | Role-based access | PPT §1 (PS del.), SPEC §7.1 | absent | total | `X-Role` middleware + `AuditLog`; label as demo-grade | P2 |
| R24 | Alerts | PPT §1 (PS del.), SPEC §3 | `has_update` static | no trigger | `Watchlist.project_id` + build-time band-transition flag | P2 |
| R25 | GIS / map | PPT §1 (PS del.) | parcel map built, external tiles | D-5 | vendor tiles locally or use offline schematic | P1 |
| R26 | Read-only API, one `SELECT` per route, no model in request path | PRD §36/48/49, `CONTRIBUTING.md` r.2 | **satisfied** | none | preserve for all 6 new routes | — |
| R27 | Provenance label on every row | `CONTRIBUTING.md` r.3 | **satisfied** for 8 tables | new tables must comply | `source_label` on all 5 new tables | P0 |
| R28 | Three-tier fallback | PRD §73 | **built** | tier 3 hand-maintained | generate tier 3 from `s7`/`s15` | P1 |
| R29 | ≥8 UP districts | SPEC §1 | 1 district | 7 missing | see Risk X-1 | P1 |
| R30 | Real Bhoomi Rashi 3A/3D spine | PPT §3.1 | `data/raw/bhoomirashi/` **empty** | total | see Risk X-1 — the single largest risk | P0 |
| R31 | Fresh developer runs locally with no source edits | task requirement | **fails** (D-1, D-3, D-4) | 3 defects | fix + Docker + `make doctor` | P0 |
| R32 | Accessibility | task requirement | **0** a11y attributes | total | labels, roles, focus trap, skip link, contrast audit | P2 |
| R33 | Deep-linkable URLs | implied by officer workflow | no router | total | `react-router-dom` | P1 |

---

## D. Hardcoded / mock data inventory

Every entry below was read from source. "Real" means the value ultimately originates from the
database.

### D.1 Frontend — presented as data, not from the database

| Value | Location | Assessment |
|---|---|---|
| `activeDistrict="Sultanpur (UP)"` | `App.tsx:123` | **Hardcoded.** `/dashboard/overview` already returns `district`. Wire it. |
| `activeDistrict = 'Sultanpur (UP)'` default | `Header.tsx:9` | Hardcoded default; remove, make required. |
| `vivaad radar` brand | `Header.tsx:17` | Wrong product name. |
| `Vivaad Radar — Land Litigation Discovery` | `index.html:7` | Wrong product name + wrong subject. |
| `vivaad-radar-frontend` | `package.json:2` | Wrong product name. |
| `onSearch('1365/1', 'Madanpur Paniyar')` on empty submit | `Search.tsx:17` | Hardcoded demo query masquerading as default behaviour. |
| 3 quick-pick chips with survey/village/label triples | `Search.tsx:85-115` | Hardcoded. Should come from a `GET /demo/shortcuts` or be derived from `/dashboard/heatmap`. |
| 5-step progress sequence + `delayMs` timings | `Processing.tsx:17-23` | **Pure theatre.** ~5.3 s simulating work that already ran offline. |
| `Sultanpur` village fallback | `Result.tsx:80` | Hardcoded. |
| `NOT_FOUND_CONFIDENCE = 0.97` | `resultModel.ts:3` | **Fabricated confidence.** A "97% confident this parcel is not in court" number with no computation behind it. |
| Not-found narrative prose (2 paragraphs asserting corpus coverage) | `Result.tsx:90`, `App.tsx:17` | Hardcoded claims about what was searched. |
| `weights.identifier ?? 0.4`, `?? 0.25`, `?? 0.1`, `?? 0.1` | `Result.tsx:308,317,324,342` | Hardcoded model weights as fallbacks. If `weights_used` is absent the UI invents the weights. |
| `'Land / revenue holding'` in the LAND RECORD column | `Result.tsx:339` | Hardcoded literal in an evidence table. |
| `'State Revenue Land Registry'` provenance badge | `Result.tsx:377` | **D-8** — false provenance on not-found. |
| "GREEN means…" legal notice, unconditional | `Result.tsx:381` | **D-7** — wrong on RED/AMBER. |
| `text-radar-red` on case status | `CaseDetailModal.tsx:41` | **D-6** — disposed cases shown in red. |
| `Source: Allahabad High Court Judgments Corpus` | `CaseDetailModal.tsx:105` | Hardcoded; `court` is on the payload. |
| `SULTANPUR: [26.2647, 82.0727]` map centre | `ParcelMap.tsx:8` | Hardcoded. Should be the centroid of returned features. |
| CARTO tile URL | `ParcelMap.tsx:153` | **D-5** — external network dependency. |
| `Section 52 TPA Lis Pendens Resolver` / `eCourts × Bhoomi Cadastral Linkage` | `AppFooter.tsx:32-34` | Hardcoded, and "Bhoomi" is Karnataka — the corpus is UP. |
| `'Sultanpur'` dashboard stat fallback | `OfficerDashboard.tsx:110` | Hardcoded. |
| `Sultanpur litigation heatmap` heading | `OfficerDashboard.tsx:130` | Hardcoded. |
| Bundled watchlist item with `has_update: true` | `client.ts:138-147` | Fake notification. |
| `Math.floor(Math.random()*1000)+10` subscribe id | `client.ts:162-166` | **Fabricated write receipt.** The UI reports a successful subscription that did not happen. |
| Entire `fallbackData.ts` (304 lines: 3 parcels, 3 litigation payloads, 2 case details, overview, heatmap, 5-village map) | `fallbackData.ts` | Legitimate tier-3 mechanism, **illegitimately hand-maintained**. Must be generated by `s7` so it cannot drift. |

### D.2 Backend

| Value | Location | Assessment |
|---|---|---|
| `user_ref = 'demo-user'` | `routers/watchlist.py:20` | Hardcoded single user. Blocks any multi-officer story. |
| `status or "GREEN"` | `routers/parcels.py:90` | Defensible defensive default; keep, but it means a NULL status reads as "clean". |
| `_bucket()`: NULL→GREEN, unknown→AMBER | `routers/dashboard.py:10-14` | Deliberate foreign-DB defence. Documented. Keep. |
| `density = (RED*2 + AMBER) / (parcels*2)` | `routers/dashboard.py:51` | Undocumented magic formula. It is at least rendered on screen next to the number. Move to a named constant with a docstring. |
| CORS origins `localhost:5173`, `127.0.0.1:5173` | `main.py:65` | Hardcoded. Should be env-driven for deployment. |

### D.3 Pipeline

| Value | Location | Assessment |
|---|---|---|
| `DISTRICT = "Sultanpur"` | `common.py:16` | Hardcoded single district. **Direct blocker for R29.** Must become a list. |
| `FLAGSHIP_CNR`, `SEED = 20260820` | `common.py:17-18` | Legitimate — reproducibility + demo anchors. Keep. |
| `WEIGHTS`, `HIGH=0.85`, `MEDIUM=0.60`, `CASE_TYPE_RELEVANCE` | `common.py:24-42` | Hardcoded but **correctly labelled** unvalidated. Keep as-is; the honesty is the point. |
| 8-table `SCHEMA` string | `s6_load_db.py:17-57` | Duplicates `backend/schema.sql`. Single-source it. |

### D.4 Demo seed data — the good news

`data/input/{cases,parcels}.parquet` are the committed contract, and **everything downstream is
real database records flowing through the real APIs**. The 38 cases are `source_label='real'`.
The dashboard's 135/38/12/62/61/8/43 are live `SELECT COUNT(*)` results — verified against the
DB. This is the correct pattern already, and the risk layer must extend it rather than
introduce a parallel one.

The one violation is `fallbackData.ts`, which is a hand-written JSON blob rather than a
build artifact.

---

## E. Database → UI data-flow audit

### E.1 Verified flows

```
GET /dashboard/overview
  Parcel.district, Parcel.status, COUNT(CourtCase),
  COUNT(CourtCase WHERE status='active'), COUNT(ParcelCaseLink WHERE band=…)
    -> routers/dashboard.py:overview  (aggregation in Python over 135 rows)
      -> api.getOverview()
        -> OfficerDashboard <Stat> x8         [REAL, except `district` label -> D.1]

GET /dashboard/heatmap
  Parcel.village, .village_canon, .status
    -> group by village_canon, density = (2R+A)/(2n)
      -> village cause list + ParcelMap circle radii   [REAL, formula = computed]

GET /dashboard/map
  Parcel.{id,survey_no,village,village_canon,status,confidence,geometry}
    -> GeoJSON FeatureCollection (135 features; rows with unparseable geometry skipped)
      -> ParcelMap polygons                   [REAL rows, SYNTHETIC geometry, unlabelled]

GET /parcels/search?survey_no&village
  norm_place(village) -> Parcel.village_canon = ?   (indexed)
  norm_survey(survey_no) compared in Python against survey_no|khasra_no|khata_no
      -> ParcelPicker / dashboard holdings list      [REAL]
  NOTE: survey filtering is a Python scan over the village result set, not SQL.
        Fine at 135 rows; O(n) at scale.

GET /parcels/{id}
  Parcel.* + Person (via owner_ref) + json.loads(geometry, land_events)
      -> Result header, timeline source events      [REAL]

GET /parcels/{id}/litigation
  Parcel.{status,confidence,note,closed_history}   <- PRECOMPUTED by s5
  JOIN ParcelCaseLink -> CourtCase, ORDER BY confidence_score DESC
      -> Result status banner, evidence table, timeline   [REAL]
      -> weights_used / features_absent drive the honest "feature absent" row

GET /cases/{cnr}
  CourtCase.* + CaseParty + CourtEvent(date asc) + ParcelCaseLink(conf desc)
      -> CaseDetailModal                       [REAL, except status colour -> D-6]

GET /watchlist  |  POST /watchlist
  Watchlist JOIN Parcel
      -> Watchlist page                        [REAL read/write, single hardcoded user]
```

### E.2 Unused database fields

| Field | Note |
|---|---|
| `Parcel.area`, `.taluk` | `area` selected in detail but never rendered; `taluk` rendered only in the picker. |
| `Person.name_normalized`, `.address` | never read by any route. |
| `CourtCase.raw_text_ref` | returned in the litigation payload, never rendered — yet it is the strongest credibility artifact available (the actual PDF path). Render it. |
| `CaseParty.person_id` | never read. |
| `ParcelCaseLink.identifier_match` | **deliberately** excluded from the link payload per spec, but `Result.tsx:307` calls `surveyMatchLabel(evidence.survey_match)` off the evidence JSON instead. Duplicate representation. |
| `Watchlist.last_notified_at` | never written, never read. |
| `SourceRecord` (all 4 rows) | never exposed by any route. Should back a provenance panel. |
| `evidence.taluk_match`, `.block`, `.location_unconfirmed` | present in the JSON, never rendered. `location_unconfirmed` is load-bearing for the AMBER-not-RED rule and should be surfaced. |

### E.3 Frontend assumptions that do not match the schema

| Assumption | Reality |
|---|---|
| `EvidenceDetail.father_name_similarity?: number` | pipeline emits `null` for 67/84 links → **D-2**, build failure |
| `case_type_relevance?: 'high'\|'medium'\|'low'\|number` | pipeline only ever emits a float. The string union is dead. |
| `LinkedCase.filing_date: string` (non-nullable) | `CourtCase.filing_date` is nullable in SQLite |
| `CaseDetail.status` implicitly active | 30 of 38 cases are `disposed` → **D-6** |
| `weights_used` always present | absent-feature links omit keys → UI substitutes hardcoded weights |

### E.4 Unused API surface

`GET /dashboard/map` is implemented and consumed but appears in **no** spec — the docs list
8 endpoints, the code has 9. Add it to the contract.

---

## F. Domain model assessment

### F.1 Can the current model represent the domain?

| Domain concept | Representable today? |
|---|---|
| Project | **No** |
| Acquisition stages | **No** |
| Parcels | Yes — `Parcel` |
| Owners / stakeholders | Partly — `Person` + `owner_ref` (1:1; real acquisitions are 1:N) |
| Notices / statutory events | **No** — `CourtEvent` is court-only; `Parcel.land_events` is JSON with `sale`/`mutation` types |
| Deadlines | **No** |
| Court cases / litigation | Yes — `CourtCase`, `CaseParty`, `CourtEvent`, `ParcelCaseLink` |
| Administrative delays | **No** |
| Risk prediction | **No** |
| Interventions / actions | **No** |

Seven of ten core concepts are absent. The two that exist are exactly the two the linkage
engine needed.

### F.2 Required schema changes — additive only, 8 → 13 tables

`s14_load_risk_db` must `CREATE` these and **never `DROP`** the original eight.

```sql
-- 9. one row per project
CREATE TABLE AcquisitionProject (
  id TEXT PRIMARY KEY,                  -- 'PRJ-<district>-<nn>'
  name TEXT NOT NULL,
  project_type TEXT,                    -- highway | railway | irrigation | industrial | transmission
  executing_agency TEXT,                -- NHAI | PWD | NHSRCL | ...
  act TEXT NOT NULL,                    -- 'NH_1956' | 'RFCTLARR_2013'
  state TEXT NOT NULL,
  district TEXT NOT NULL,
  block TEXT,
  nh_no TEXT,                           -- NULL unless act='NH_1956'
  gazette_ref TEXT,
  area_hectares REAL,
  affected_families INTEGER,
  budget_estimate_inr REAL,
  current_stage TEXT,                   -- denormalised for list queries
  stage_entered_on TEXT,                -- ISO date
  status TEXT NOT NULL,                 -- open | completed | lapsed
  source_label TEXT NOT NULL            -- real | synthetic | derived | cached
);

-- 10. one row per (project, stage)
CREATE TABLE ProjectStage (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES AcquisitionProject(id),
  stage TEXT NOT NULL,                  -- see F.3
  statutory_days INTEGER NOT NULL,
  clock_source TEXT NOT NULL,           -- 'statute' | 'administrative_target'
  clock_authority TEXT,                 -- 'NH Act 1956 s.3D(3)' | 'RFCTLARR 2013 s.19(7)' | ...
  started_on TEXT,                      -- NULL if not entered
  completed_on TEXT,                    -- NULL if open  -> right-censored
  deadline_on TEXT,                     -- started_on + statutory_days, materialised
  overdue_days INTEGER,                 -- NULL if open
  is_delayed INTEGER,                   -- 1 | 0 | NULL(censored). NEVER 0 for open stages.
  source_label TEXT NOT NULL,
  UNIQUE(project_id, stage)
);

-- 11. one row per (project, parcel)
CREATE TABLE ProjectParcel (
  project_id TEXT NOT NULL REFERENCES AcquisitionProject(id),
  parcel_id  TEXT NOT NULL REFERENCES Parcel(id),
  village_canon TEXT,
  binding_confidence REAL NOT NULL,
  binding_evidence TEXT NOT NULL,       -- JSON, same shape as ParcelCaseLink.evidence
  source_label TEXT NOT NULL,
  PRIMARY KEY (project_id, parcel_id)
);

-- 12. one row per (project, stage) scored
CREATE TABLE ProjectRisk (
  project_id TEXT NOT NULL REFERENCES AcquisitionProject(id),
  stage TEXT NOT NULL,
  delay_probability REAL NOT NULL,      -- calibrated [0,1]
  risk_band TEXT NOT NULL,              -- LOW | MEDIUM | HIGH
  predicted_overrun_days INTEGER,       -- empirical median, NOT a model
  lead_time_days INTEGER,               -- deadline_on - scored_at; the >=90d claim
  model_version TEXT NOT NULL REFERENCES ModelRun(model_version),
  scored_at TEXT NOT NULL,
  drivers TEXT NOT NULL,                -- JSON [{feature, shap, direction, value, label}] top-5
  recommendations TEXT NOT NULL,        -- JSON [{rule_id, driver, action}]
  source_label TEXT NOT NULL DEFAULT 'model_generated',
  PRIMARY KEY (project_id, stage)
);

-- 13. one row per training run
CREATE TABLE ModelRun (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_version TEXT NOT NULL UNIQUE,   -- 'mv-YYYYMMDD-NN'
  trained_at TEXT NOT NULL,
  stage TEXT NOT NULL,                  -- one run row per stage model
  algo TEXT NOT NULL,                   -- 'hgb_calibrated' | 'logreg' | 'base_rate'
  shipped INTEGER NOT NULL DEFAULT 0,   -- 1 for the model actually serving
  n_train INTEGER, n_test INTEGER,
  n_test_real INTEGER, n_test_synthetic INTEGER,   -- R16 enforcement
  cutoff_date TEXT,
  metrics TEXT,                         -- JSON roc_auc, pr_auc, brier, precision@t_high, ...
  feature_list TEXT,                    -- JSON, ordered
  thresholds TEXT,                      -- JSON {t_high, t_med} or {"high":"suppressed"}
  notes TEXT                            -- 'low_separation' etc.
);

-- 14. conditional (only if RBAC ships)
CREATE TABLE AuditLog (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, role TEXT, method TEXT, path TEXT, status INTEGER
);

-- additive column on an existing table
ALTER TABLE Watchlist ADD COLUMN project_id TEXT NULL REFERENCES AcquisitionProject(id);

CREATE INDEX ix_project_district   ON AcquisitionProject(district, status);
CREATE INDEX ix_stage_project      ON ProjectStage(project_id);
CREATE INDEX ix_stage_deadline     ON ProjectStage(deadline_on) WHERE completed_on IS NULL;
CREATE INDEX ix_pp_project         ON ProjectParcel(project_id);
CREATE INDEX ix_pp_parcel          ON ProjectParcel(parcel_id);
CREATE INDEX ix_risk_band          ON ProjectRisk(risk_band, delay_probability DESC);
```

`AuditLog` is not counted in the 13; the docs are internally inconsistent about whether it
ships (SPEC §1 says cut it first; ARCH §10 lists it as undecided). Resolution: build it — it
is ~15 lines and PS deliverable 12 depends on it.

### F.3 Statutory clocks — canonical table

| `stage` | Transition | `statutory_days` | `clock_source` | `clock_authority` | Consequence |
|---|---|---|---|---|---|
| `notification_3a_11` | §3A → §3D (NH) / §11 → §19 (RFCTLARR) | 365 | `statute` | NH Act 1956 s.3D(3) / RFCTLARR 2013 s.19(7) | notification **lapses** |
| `declaration_3d_19` | §3D/§19 → award (§3G/§23) | 365 | `statute` | RFCTLARR 2013 s.25 | proceedings **lapse** |
| `award_3g_23` | award declared → award passed | 365 | `statute` | RFCTLARR 2013 s.25 | proceedings **lapse** |
| `compensation_disbursed` | award → disbursement | 90 | `administrative_target` | project-chosen | none statutory |
| `possession` | award → possession | 90 | `administrative_target` | project-chosen | none statutory |
| `r_and_r` | (parallel, not sequential) | **undefined** | — | — | see Risk X-4 |

**Two of five clocks are targets we chose, not law.** Every screen and every slide showing a
deadline must render `clock_source`. This is the same rule already enforced for
`next_hearing_source='derived'`, and it must be enforced the same way — by tests.

### F.4 Model changes to existing tables

Only two, both additive:

1. `Watchlist.project_id` (nullable) — the whole of PS deliverable 8. No second notification
   subsystem.
2. `Person`/`Parcel` 1:1 ownership is wrong for real acquisitions (multiple co-owners per
   parcel). **Do not fix now.** It does not block any SIH26017 deliverable and touching
   `owner_ref` breaks `s6`, the parcel detail route and 4 tests. Record it as known debt.

---

## G. ML assessment

### G.1 What exists

Nothing that constitutes machine learning. `s4_score.py` is a hand-weighted linear combination
of five string-similarity features, thresholded at 0.85/0.60. It is a good *record-linkage*
scorer and it is honestly labelled as unvalidated. It is not, and does not claim to be, a
delay predictor.

### G.2 Gap against the stated requirement

The PDF says the system "predicts the probability of missing each statutory acquisition
deadline" rather than "producing one generic score". Today the system produces **one generic
litigation-status band per parcel** and no deadline probability at all. This is the largest
single requirement gap in the project.

### G.3 Minimum technically sound refactor

Eight new pipeline stages. No new matcher, no new frontend framework, no GPU.

| Stage | Responsibility | Output |
|---|---|---|
| `s8_acquisition_handoff` | Build the acquisition contract from `data/raw/bhoomirashi/` (real, dated) **plus** a synthetic generator seeded on real village names. Mirrors `s0`. | `data/input/{acquisitions,project_stages}.parquet` |
| `s9_acquisition_ingest` | Validate: columns, `source_label ∈ PROVENANCE`, resolvable clock per stage, `is_delayed` NULL iff `completed_on` NULL, flagship project present. **Raise on any violation.** Mirrors `s1`. | `acquisitions.json` |
| `s10_project_bind` | Bind project → parcels using `s2.norm_survey` / gazetteer and `s3` village blocking. `binding_confidence` = `s4._village` + `s4._identifier` reweighted over those two. **No new matcher.** | `project_parcels.json` |
| `s11_features` | Per-`(project, stage)` matrix, every value as of `stage.started_on`. Runs the leakage audit. Enforces the 20-feature cap. | `features.parquet` |
| `s12_train` | Per-stage: base-rate, LR, calibrated HGB. Time-based split. Threshold selection. Writes `ModelRun` rows. | `data/output/models/` (committed) |
| `s13_risk_score` | Score every open stage. SHAP top-5. Map drivers → retrieved recommendations. Compute `lead_time_days`, `predicted_overrun_days`. | `project_risk.json` |
| `s14_load_risk_db` | `CREATE` 5 tables, fill them. Never `DROP` the original 8. | `vivaad.db` (13 tables) |
| `s15_export_risk_fallback` | Render every new endpoint response to the fallback cache, **and regenerate `frontend/src/api/fallbackData.ts`** so tier 3 cannot drift. | `fallback/projects/*`, `fallback/dashboard_risk.json` |

Also implement the documented-but-missing `--risk-only` flag in `run_all.py`.

### G.4 Target definition

```python
is_delayed = (completed_on - started_on).days > statutory_days   # if completed_on is not None
is_delayed = None                                                 # if stage is open
```

Censored rows are **excluded from training** and **included in scoring**. `s12` writes both
counts into `ModelRun.metrics`. A test must assert no open stage carries `is_delayed = 0` —
that single coercion would silently label every in-flight project as on-time and inflate every
metric.

### G.5 Features — resolving the 20-cap conflict

**Conflict K-1:** `SPEC §5.6` caps features at 20. `SPEC §5.1-5.4` names **23**. `PPT §5` names
**21**. All three exceed the cap.

Proposed resolution — ship exactly 20 by dropping 3, with reasons:

- drop `project_type` — high-cardinality categorical, near-zero signal at small n
- drop `executing_agency` — same, and collinear with `act`
- drop `median_case_pendency_days` — ρ≈1 with `max_case_pendency_days` in this corpus

**A. Litigation (9) — the differentiator**
`share_parcels_red`, `share_parcels_amber`, `n_active_cases`, `max_case_pendency_days`,
`n_acquisition_compensation_cases`, `n_title_partition_cases`, `has_interim_order`,
`n_high_confidence_links`, **`litigation_coverage`** *(new — see X-2)*

**B. Project intrinsics (4)**
`area_hectares`, `n_parcels`, `n_villages`, `affected_families`

**C. Administrative (4)**
`days_in_current_stage`, `n_prior_stage_overruns`, `gazette_republication_count`,
`compensation_disbursed_share`

**D. District context (3) — training split only**
`district_median_3a_to_3d_days`, `district_active_land_cases`, `district_completed_projects`

= **20.** `act` folds into the stage identity (a stage model is already act-specific via its
clock), so it is not a separate feature.

### G.6 Model, calibration, thresholds

```python
HistGradientBoostingClassifier(max_depth=3, max_leaf_nodes=8,
                               l2_regularization=1.0, early_stopping=True,
                               random_state=SEED)
  -> CalibratedClassifierCV(method='isotonic' if n_closed >= 200 else 'sigmoid', cv=…)
```

Thresholds, chosen **on the holdout, per stage**:

- `t_high` = lowest `p` where holdout precision ≥ **0.70**. If unattainable → **HIGH band is
  not emitted at all**, and `ModelRun.thresholds` records `{"high": "suppressed"}`.
- `t_med` = lowest `p` where holdout recall ≥ **0.80**. If the cut points invert, clamp
  `t_med = t_high` and write `low_separation` to `ModelRun.notes`.

Baselines: base-rate and standardised L2 logistic regression, on the same holdout, in the same
table. **If LR wins on Brier score, LR ships and the slide says so.**

Metrics: ROC-AUC, PR-AUC, Brier, reliability curve — computed on `source_label='real'` rows
only, with `n_test_real` / `n_test_synthetic` persisted.

### G.7 Explainability and actions

SHAP `TreeExplainer` at **build time only**. Top-5 signed drivers per `(project, stage)`
persisted to `ProjectRisk.drivers`. Global mean-|SHAP| ranking to `ModelRun.metrics`.

`pipeline/recommendations.py` — a static `driver → action` table. Every recommendation carries
its triggering driver and a `rule_id`. Never generated. Seed rules from `SPEC §6.5`:

| Driver | Action |
|---|---|
| `has_interim_order` | Seek vacation of the interim order; list before the LARR Authority before the 3D clock expires |
| `share_parcels_red` high | Route litigated parcels to the district legal cell |
| `compensation_disbursed_share` low | Escalate disbursement to the CALA |
| `gazette_republication_count > 0` | 3A already extended once; a second extension risks lapse under s.3D(3) |

### G.8 Leakage discipline

Every feature row carries `computed_asof`. `s11` asserts no contributing row postdates
`stage.started_on`, and district aggregates are computed on the training split only. A
violation **raises at build time**, exactly like the `s1` contract check.

This is the most likely fatal flaw in a hackathon ML pipeline and the first thing a technical
judge will probe. It must be a test, not a comment. Create the
`tests/test_features.py::test_no_future_leakage` and `::test_district_context_train_only`
that three documents already claim exist.

### G.9 Explicitly do not build

No survival model. No regression head for overrun days (`predicted_overrun_days` is an
empirical median — `SPEC` dev. 7). No deep learning. No LLM in the request path. No retraining
at request time. No feature store.

---

## H. Government user workflow

Target: **District Land Acquisition Officer / CALA**, opening the tool before a monthly review.

```
 1. Login                     POST /auth/session   -> role + district scope
 2. Risk dashboard            GET /dashboard/risk
                              "6 projects HIGH, 11 MEDIUM in Sultanpur.
                               2 deadlines inside 90 days."
 3. Ranked portfolio          GET /projects?district=&risk_band=HIGH&sort=delay_probability
                              worst-first list, never alphabetical
 4. Why is it high risk?      GET /projects/{id}/risk
                              top-5 signed drivers, plain language, above the fold
 5. Which stage?              GET /projects/{id}
                              stage timeline, statutory deadline marker,
                              overshoot shading, clock_source badge
 6. Which parcels?            GET /projects/{id}/parcels?status=RED
                              12 of 84 parcels RED, sorted worst-first
 7. Which litigation?         GET /parcels/{id}/litigation      [ALREADY BUILT]
                              case no, court, pendency, next hearing (+source),
                              evidence table, raw_text_ref to the actual order PDF
 8. What is the deadline?     rendered at step 5 - date, days remaining,
                              lapse consequence, statute cited
 9. What do I do?             retrieved recommendations, each tied to its driver + rule_id
10. Record the action         POST /interventions   {project_id, stage, action, note}
11. Did risk change?          GET /projects/{id}/risk?history=1
                              band over model versions
```

### H.1 Does the current app support this?

**No.** Steps 1–6 and 8–11 have no implementation. Step 7 is fully built and is genuinely the
best screen in the app. The existing officer dashboard is a *litigation heatmap by village* —
useful, but it answers "where is land in court?", not "which project will miss its deadline?".

The navigational spine is inverted: today the entry point is a **parcel** and litigation is the
destination. The officer workflow needs **project** as the entry point and litigation as the
evidence at the bottom of a drill-down.

### H.2 Steps 10–11 require one new table

`Intervention` is not in the 13-table spec, and step 10 ("take/record an intervention") is an
explicit task requirement. It is also the only **write** path in the risk layer.

```sql
CREATE TABLE Intervention (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL REFERENCES AcquisitionProject(id),
  stage TEXT,
  rule_id TEXT,                    -- the recommendation acted upon, if any
  action TEXT NOT NULL,
  note TEXT,
  recorded_by TEXT NOT NULL,       -- user_ref
  recorded_at TEXT NOT NULL,
  risk_band_at_time TEXT,          -- snapshot, so "did risk change" is answerable
  model_version_at_time TEXT
);
```

This makes the count 14 tables + `AuditLog` = 15. **The "13 tables" figure in `ARCH §4` and
`PPT §3` becomes stale** — update both rather than contorting the design to preserve a number.

---

## I. UI/UX refactor plan, page by page

Global, before any page work:

- **G-1** Add `react-router-dom`. Replace the `App.tsx` view state machine with real routes.
  Officers must be able to bookmark and share `/projects/PRJ-SUL-01`. Without this there is no
  deep-linking, no back button, and no way to demo a specific screen reliably.
- **G-2** Rebrand: `index.html` title, `Header`, `package.json` name → Adhigrahan Radar.
  Subtitle must state the actual subject: *early detection of land-acquisition delays*.
- **G-3** `activeDistrict` and every district string from `/dashboard/overview`. Delete
  hardcoded `'Sultanpur'` defaults (5 sites).
- **G-4** Role-aware nav: officer sees their district; supervisor sees the state roll-up;
  admin sees `/models/history`.
- **G-5** Add a `<RiskBadge>` component. Mandatory disclaimer text baked in: *"predicted risk
  of missing a statutory deadline, not an administrative finding."* Reuse existing
  green/amber/red tokens; add LOW/MEDIUM/HIGH semantics distinct from the parcel
  GREEN/AMBER/RED. **These are two different scales and must not share labels.**
- **G-6** Add a `<ClockSourceBadge>`. Renders `statute` (with the section cited) vs
  `administrative target`. Required on every deadline, everywhere.
- **G-7** Accessibility pass — currently **zero** a11y attributes repo-wide. Minimum:
  `<label htmlFor>` on both search inputs; `role="dialog"` + `aria-modal` + focus trap +
  Escape on `CaseDetailModal`; `aria-label` on all 14 icon-only buttons; skip-to-content link;
  `aria-live="polite"` on async regions; verify contrast of `text-ink-muted` on `#FAF3E0`.
- **G-8** Standardise loading / empty / error states. `Result` currently has no error state.
- **G-9** Vendor map tiles locally or fall back to a schematic SVG. Fixes D-5 and restores the
  "never hits the network" guarantee. Label synthetic geometry on the map itself.

| Page | Verdict | Changes |
|---|---|---|
| `Search` | **Demote.** It is a citizen entry point; the officer product starts at risk. | Move to `/lookup`. Remove the hardcoded default query (`Search.tsx:17`). Drive quick-picks from an API. |
| `Processing` | **Delete.** 5.3 s of fake progress for work that ran offline. It is the single most credibility-damaging screen in the app — a judge who asks "what is it computing?" gets "nothing". | Replace with a real spinner bounded by actual fetch latency (~50 ms). If a transition beat is wanted, show the *offline build* stats (1,678 pairs scored, 84 links) as a static provenance card. |
| `ParcelPicker` | Keep. | Add pagination (unbounded today), keyboard nav, `aria-label`s. |
| `Result` | Keep — best screen in the app. | Fix D-7 (conditional disclaimer), D-8 (provenance on not-found), D-9 (village fallback). Remove hardcoded weight fallbacks (`?? 0.4` etc.) — render `—` if absent. Render `raw_text_ref` as a link. Surface `location_unconfirmed`. Add a "Part of project X" backlink from `ProjectParcel`. Add an error state. |
| `OfficerDashboard` | Keep, rename `/litigation-map`. | Un-hardcode district strings. Label geometry synthetic. Keep the density formula visible. |
| `Watchlist` | Keep, extend. | Support `project_id` subscriptions. Remove the fabricated random-id fallback (`client.ts:162`). Make `has_update` mean something or delete it. |
| **`RiskDashboard`** *(new, becomes `/`)* | — | KPI row: projects at risk by band, deadlines inside 30/60/90 days, median lead time, model version + trained-at. Top-N at-risk table. District roll-up. Every number from `GET /dashboard/risk`. |
| **`ProjectPortfolio`** *(new, `/projects`)* | — | Sortable/filterable ranked list: project, district, current stage, days in stage, deadline, days remaining, `delay_probability`, `RiskBadge`, top driver. Default sort `delay_probability DESC` — **never alphabetical**. Server-side pagination. |
| **`ProjectDetail`** *(new, `/projects/:id`)* | — | Header (name, act, agency, area, families). Stage timeline extending `resultModel.buildTimeline` — no charting library: 3A→3D→award→possession as CSS bars, vertical deadline marker, overshoot shading, `ClockSourceBadge` per stage. **Driver panel above the fold** (R14: <3 s to understand). Retrieved recommendations with rule ids. Bound-parcels table, RED-first, linking into the existing `Result` page. Intervention form + history. |
| **`ModelHistory`** *(new, `/models`, admin)* | — | `ModelRun` table: version, stage, algo, shipped flag, n_train, n_test_real/synthetic, ROC-AUC, PR-AUC, Brier vs base-rate, thresholds, notes. This screen is what converts "we trained a model" into "here is the evidence". |

---

## J. API refactor plan

### J.1 Existing 9 — preserve wire-compatibility

| Method | Path | Change |
|---|---|---|
| GET | `/parcels/search` | Add `?limit`/`?offset`. Push survey filtering into SQL. Add optional `?project_id`. |
| GET | `/parcels/{id}` | Add `projects: [{id, name, binding_confidence}]` from `ProjectParcel`. |
| GET | `/parcels/{id}/litigation` | **No change.** Wire-compatible. Its shape is the tier-2/3 contract. |
| GET | `/cases/{id}` | Add `affected_projects` via `ProjectParcel`. |
| GET | `/dashboard/overview` | No change. |
| GET | `/dashboard/heatmap` | No change. |
| GET | `/dashboard/map` | **Document it.** Implemented and consumed but in no spec. |
| POST | `/watchlist` | Accept `{parcel_id?, project_id?}`, exactly one required. Add a dedup guard (409). Take `user_ref` from the session, not a hardcoded literal. |
| GET | `/watchlist` | Scope to the authenticated user. Include project subscriptions. |

### J.2 New endpoints

| Method | Path | Response | Notes |
|---|---|---|---|
| GET | `/projects` | `{total, items:[{id,name,district,act,current_stage,days_in_stage,deadline_on,days_remaining,delay_probability,risk_band,clock_source,top_driver}]}` | filters `district`, `stage`, `risk_band`, `act`, `status`; `sort`, `limit`, `offset`. One `SELECT` + join. |
| GET | `/projects/{id}` | project + `stages[]` (with `statutory_days`, `clock_source`, `clock_authority`, `deadline_on`, `overdue_days`, `is_delayed`) + `parcel_summary{total,RED,AMBER,GREEN}` | |
| GET | `/projects/{id}/risk` | `{stages:[{stage,delay_probability,risk_band,predicted_overrun_days,lead_time_days,drivers[],recommendations[],model_version,scored_at}]}` | precomputed read. `?history=1` returns bands across `ModelRun` versions. |
| GET | `/projects/{id}/parcels` | `{total, items:[{parcel_id,survey_no,village,status,confidence,binding_confidence,n_active_cases}]}` | `?status=RED`, paginated. Bridges risk → the built litigation screen. |
| GET | `/dashboard/risk` | `{model_version,trained_at,bands:{HIGH,MEDIUM,LOW},deadlines:{d30,d60,d90},median_lead_time_days,districts:[…],top_at_risk:[…]}` | |
| GET | `/dashboard/risk-map` | GeoJSON of project corridors coloured by band | must declare `geometry_source: 'schematic'` in properties |
| GET | `/models/history` | `{runs:[ModelRun…]}` | |
| POST | `/interventions` | 201 `{id,…}` | **write.** Requires officer role. |
| GET | `/projects/{id}/interventions` | `{items:[…]}` | |
| POST | `/auth/session` | `{role, district_scope, user_ref}` | demo-grade; see J.4 |

### J.3 Invariants for every new route

1. One `SELECT` (plus joins) and JSON shaping. **No model load, no SHAP, no scoring.**
2. 404 body is flat: `{"error":"not_found","hint":"…"}` — matches the `main.py:49`
   structured handler.
3. Every response carries the `source_label` / `clock_source` / `model_version` needed for the
   UI to render provenance without inventing it.
4. `s15` must render a fallback file for every new route, in the naming convention
   `backend/fallback.py` already resolves.

### J.4 Auth — deliberately minimal

`X-Role` header + ~40 lines of middleware, gating response fields and write routes. Every
request appended to `AuditLog`. **It must be labelled "demo-grade access control, not
production authentication" on screen and in the README.** This satisfies PS deliverable 12
without pretending to security the project does not have. Do not add OAuth, JWT, or a password
store — `CONTRIBUTING.md` explicitly lists "real authentication" as out of scope.

---

## K. Testing strategy

Baseline: **47 tests, all green** (13 pipeline golden + 34 backend). That is the regression
gate. Target: **~95**.

### K.1 Unit — pipeline (`tests/`)

- `test_clocks.py` — every stage resolves a clock; the 3 statutory ones carry the right day
  count and section; the 2 targets are labelled `administrative_target`. **No stage may lack a
  `clock_source`.**
- `test_features.py::test_no_future_leakage` — no contributing row postdates `stage.started_on`.
  *(Three docs already claim this exists. Make it true.)*
- `test_features.py::test_district_context_train_only` — district aggregates never see holdout rows.
- `test_features.py::test_feature_cap` — exactly 20 features per stage model.
- `test_features.py::test_litigation_coverage_flag` — absent parcel coverage yields
  `litigation_coverage=0`, **not** `share_parcels_red=0`. Guards X-2.
- `test_labels.py::test_open_stages_are_censored` — no open stage has `is_delayed = 0`.
- `test_recommendations.py` — every driver the model can emit maps to ≥1 rule; every rule has a
  `rule_id`.

### K.2 Unit — ML (`tests/`)

- `test_model.py::test_beats_base_rate` — holdout Brier better than base rate, per stage.
- `test_model.py::test_metrics_are_real_only` — every reported holdout row is
  `source_label='real'`; `n_test_real + n_test_synthetic == n_test`.
- `test_model.py::test_high_band_precision` — either holdout precision at `t_high` ≥ 0.70, or
  the band is suppressed. **No third outcome.**
- `test_model.py::test_calibration_choice` — isotonic iff `n_closed ≥ 200`.
- `test_model.py::test_determinism` — same seed, same `data/input` → identical
  `delay_probability` to 6 dp. Protects demo reproducibility.
- `test_model.py::test_lr_wins_is_honoured` — if LR beats HGB on Brier, `ModelRun.shipped=1`
  sits on the LR row.

### K.3 Database / schema

- **Rewrite** `test_schema.py::test_schema_creates_exactly_eight_tables` (D-10) as
  `test_schema_creates_thirteen_core_tables` + `test_original_eight_tables_intact`. The second
  is load-bearing: `s14` must never `DROP` linkage tables.
- `test_schema_single_source` — `backend/schema.sql` and the `s14`/`s6` DDL agree. Best fixed
  structurally: have the pipeline `executescript` the `.sql` file rather than duplicating a
  Python string.
- `test_referential_integrity` — no orphan `ProjectStage.project_id`, `ProjectParcel.parcel_id`,
  `ProjectRisk.model_version`.
- `test_provenance_complete` — every row in all 13 tables has a non-null `source_label` drawn
  from `PROVENANCE`.

### K.4 Seed-data integrity — the section that protects the demo

The task requirement is explicit: the dashboard must not say "High Risk" while the records
imply low risk. Enforce it:

- `test_seed_consistency.py::test_high_risk_has_explaining_records` — every `risk_band='HIGH'`
  row has ≥1 driver with positive SHAP, and that driver's underlying record actually exists
  (e.g. `has_interim_order=1` ⇒ a `CourtEvent` of type `interim_order` on a linked case).
- `::test_litigated_parcel_references_a_real_case` — every RED parcel resolves through
  `ParcelCaseLink` to a `CourtCase` row.
- `::test_overdue_stage_dates_are_arithmetically_true` —
  `overdue_days == (today or completed_on) - deadline_on`, and `deadline_on == started_on +
  statutory_days`. No stage may *say* overdue while its dates disagree.
- `::test_every_band_is_represented` — the demo corpus contains ≥1 HIGH, MEDIUM, LOW project
  and ≥1 lapsed, ≥1 on-time completed project.
- `::test_flagship_project_binds_the_flagship_parcel` — the demo project contains `P-B01`, so
  the risk→litigation drill-down terminates in the already-built evidence screen.

### K.5 API (`backend/tests/`)

- Per new route: 200 shape, 404 shape, filter/sort/pagination correctness.
- `test_no_model_in_request_path` — assert no route module imports `sklearn`, `shap`, or
  anything under `pipeline/`. This is an architectural invariant worth a test.
- `test_response_latency` — every route < 200 ms against the real DB.
- `test_fallback_covers_every_route` — for each route in `app.routes`, `s15` produced a
  fallback file. Prevents a silent 503 half-way through a demo.

### K.6 Role / access

- Each role × each route → expected status.
- Officer cannot read another district's projects.
- Reviewer cannot `POST /interventions`.
- Every request produced exactly one `AuditLog` row.
- Missing `X-Role` → documented default, not a 500.

### K.7 Frontend (currently **0** tests)

Add Vitest + React Testing Library + MSW.

- `RiskBadge` renders its disclaimer — always.
- `ClockSourceBadge` renders `administrative target` for the 2 non-statutory stages.
- `Result` renders "Feature absent — weight redistributed" when
  `father_name_similarity == null` (regression guard for the honesty rule).
- `Result` does **not** render the GREEN disclaimer on a RED payload (guards D-7).
- `Result` does not claim "State Revenue Land Registry" on not-found (guards D-8).
- `ProjectDetail` renders drivers above the fold.
- Loading / empty / error state per page.
- `axe` assertion on each page — zero critical violations.
- `tsc --noEmit` in CI as a first-class gate (would have caught D-2).

### K.8 E2E (Playwright)

One spec, mirroring §L exactly: login → risk dashboard → portfolio → project detail →
stage → parcels → litigation → intervention. Plus: kill the DB mid-run and assert tier 2
still answers; run with `?demo=1` and network offline and assert tier 3 renders.

### K.9 CI changes

Add to `.github/workflows/ci.yml`:
- `ruff check .` (configured but never run)
- `tsc --noEmit` (would have caught D-2 before merge)
- `vitest run`
- `playwright test` against the built artifact
- a matrix over Python 3.11/3.12 to pin down D-3

---

## L. Demo plan (7 minutes)

Precondition: `make build && make test` green, API and web up, **network cable pulled** to
prove the offline claim.

| # | Time | Action | The point |
|---|---|---|---|
| 1 | 0:00 | Land on `/` — Risk Dashboard. "6 HIGH-risk projects in Sultanpur. Two statutory deadlines inside 90 days." Model version and trained-at visible in the corner. | The system leads with a prediction, not a search box. |
| 2 | 0:45 | `/projects` sorted by `delay_probability`. Worst first. | Officers get a ranked worklist, not an alphabetical table. |
| 3 | 1:30 | Open the top project. Driver panel above the fold: *"Interim order on 3 parcels (+0.21)", "Compensation disbursed on 12% of parcels (+0.14)"*. | **This is the ≤3-second claim.** Say the number out loud. |
| 4 | 2:15 | Stage timeline. 3A→3D bar, vertical deadline marker at day 365, red overshoot shading, `statute · NH Act 1956 s.3D(3)` badge. Point at the next stage: `administrative target` badge. | Per-stage, not one generic score — and we say which clocks are law and which we chose. |
| 5 | 3:15 | Bound parcels, RED first: "12 of 84 parcels under active litigation." | Litigation as a *predictive feature*, quantified per project. |
| 6 | 4:00 | Click `P-B01` → the existing `Result` screen. Evidence table: court wrote `1365/1`, land record says `1365-1`, village `Paniyar` vs `Panyar`, matched at **0.9105**. Open `raw_text_ref` — the actual High Court order PDF. | The link is *earned*, not assumed. This is the strongest credibility moment available. |
| 7 | 5:00 | Back to the project. Record an intervention against the interim-order recommendation (`rule_id` shown). Reload — it persists with the risk band snapshotted. | Closes the loop: predict → explain → act → track. |
| 8 | 5:45 | `/models` — ROC-AUC, PR-AUC, Brier vs base-rate and LR, `n_test_real` vs `n_test_synthetic`, thresholds. | Pre-empts every methodology question. If HIGH is suppressed, **show that** — it is stronger than a fabricated 0.72. |
| 9 | 6:30 | `rm data/output/vivaad.db` in a visible terminal. Reload. Same screens. Then `?demo=1` with the network still down. | Three-tier fallback, demonstrated rather than asserted. |

### L.1 Honesty beats to script explicitly

Say these out loud; each one is a question you would otherwise be asked:

- "38 real High Court cases. The land side is synthetic and labelled synthetic in the schema."
- "Longest real pendency in this corpus is **~2.6 years**, not the 6 years in our earlier PRD."
- "Zero of 38 cases carry a real next-hearing date. The ones you see are derived and stamped
  `derived`."
- "Two of five stage clocks are administrative targets we chose, not statute. The badge tells
  you which."
- "Synthetic rows trained the model. Only real rows appear in any reported metric."
- If HIGH cannot clear 0.70: **"We suppressed the HIGH band. It did not clear our precision
  floor."**

### L.2 Demo failure modes to pre-empt

| Risk | Mitigation |
|---|---|
| `make api` fails on the presenter's laptop | fix D-1 **first** |
| Frontend won't build | fix D-2 |
| Map shows "API KEY REQUIRED" | fix D-5 — vendor tiles |
| Wrong Python version on a fresh machine | fix D-3 + `make doctor` |
| A judge asks what `Processing` computes | delete it |

---

## M. Deployment / runbook

Requirement: a fresh developer runs the app locally **with no source edits**. Today that fails
three ways (D-1, D-3, D-4).

### M.1 Fixes

1. **D-1** — `Makefile`:
   ```make
   VENV := .venv
   BIN  := $(VENV)/bin
   ifeq ($(OS),Windows_NT)
   BIN  := $(VENV)/Scripts
   endif
   PY := $(BIN)/python
   PIP := $(BIN)/pip
   UVICORN := $(BIN)/uvicorn
   ```
   The existing `ifeq ($(OS),)` guard at `Makefile:13-16` is **correct** (verified: `$(OS)` is
   empty on Linux, so the POSIX branch is taken) — it is simply *incomplete*. It overrides only
   `PY` and `PIP`; `api` at line 44 bypasses both and hardcodes `$(VENV)/Scripts/uvicorn`.
   `lint` and `diagrams` use `$(PY)` and are unaffected. Routing every target through `$(BIN)`
   removes the whole class of defect.

2. **D-3** — `pyproject.toml`: `requires-python = ">=3.11,<3.14"`. Add a `constraints.txt` or
   move to `uv`. Add `make doctor`: assert interpreter in range, Node ≥ 20, then print
   actionable remediation.

3. **D-4** — rename to a single env var. Either change `.env.example` to `VITE_API_URL` or
   change `client.ts` to `VITE_API_BASE`. Document `VIVAAD_FALLBACK_DIR`. Add `PORT`,
   `CORS_ORIGINS`, `DEMO_MODE`.

4. **CORS** — `main.py:65` reads `CORS_ORIGINS` (comma-separated, default the two localhost
   origins).

### M.2 Add containerisation

- `Dockerfile.api` — python:3.12-slim, install requirements, run the build at image build time
  so the container ships with a populated `vivaad.db`, `CMD uvicorn`.
- `Dockerfile.web` — node:20 build stage → static serve.
- `docker-compose.yml` — both services, one healthcheck each, `VITE_API_URL` wired between
  them.
- `docker compose up` must be sufficient for a working demo on a machine with nothing but
  Docker.

### M.3 Runbook (target end state)

```bash
git clone … && cd adhigrahan-radar

# Path A — Docker, zero toolchain
docker compose up            # -> http://localhost:5173

# Path B — native
make doctor                  # verifies Python 3.11-3.13, Node >=20
make setup
make build                   # regenerate vivaad.db + fallback from committed contract
make test                    # ~95 tests
make api                     # :8000        (works on POSIX after D-1)
make web                     # :5173        (separate terminal)

# offline / degraded demo
make demo-offline            # tier 2: moves the DB aside, serves the fallback cache
open 'http://localhost:5173/?demo=1'   # tier 3: bundled payloads, no API at all
```

`make build-all` (including `s0`) stays optional and documented as requiring the external
High Court corpus.

---

## N. Implementation order

Nine phases. Every phase ends green: build passes, all tests pass, app runs. **No phase may
leave the repo un-runnable.**

### Phase 0 — Unbreak the build *(no features; do this first)*
D-1 `make api`; D-2 `father_name_similarity: number | null`; D-3 Python upper bound + `make
doctor`; D-4 env var reconciliation; add `ruff` + `tsc --noEmit` to CI.
**Verify:** `make setup && make build && make test && make api && make web` on a clean Linux
box; `npm run build` succeeds; CI green on both jobs.

### Phase 1 — Truth-in-UI *(cheap, high credibility, no schema change)*
D-5 vendor map tiles; D-6 status colour by case status; D-7 conditional disclaimer; D-8
not-found provenance; D-9 village fallback; delete `Processing`; remove hardcoded weight
fallbacks and `NOT_FOUND_CONFIDENCE`; G-2 rebrand; G-3 district from API; render
`raw_text_ref`.
**Verify:** each fix has a frontend test; demo the RED and not-found screens with the network
down.

### Phase 2 — Schema and contract
Single-source the schema (`backend/schema.sql` executed by the pipeline). Rewrite D-10
`test_schema` into 13-table + originals-intact. Write `s8_acquisition_handoff` /
`s9_acquisition_ingest` with full contract validation. Define the clock table in
`pipeline/clocks.py`. Add `s14_load_risk_db` creating empty tables. Add `--risk-only`.
**Verify:** 13 tables exist; original 8 untouched; all 47 legacy tests still pass; `s9` raises
on a deliberately malformed contract.

### Phase 3 — Seed data with integrity
Extend `s0`/`s8` to ≥8 UP districts. Generate projects, stages, and dates that are
*arithmetically* consistent with their claimed states. Write `s10_project_bind`. Land the §K.4
seed-integrity tests **before** any UI reads the data.
**Verify:** §K.4 suite green; every band represented; flagship project binds `P-B01`.

### Phase 4 — Features and leakage
`s11_features` — 20 features, `computed_asof` on each, leakage audit that raises. Add
`litigation_coverage`.
**Verify:** `test_no_future_leakage`, `test_district_context_train_only`, `test_feature_cap`,
`test_litigation_coverage_flag` green. Deliberately inject a future-dated row and confirm the
build fails.

### Phase 5 — Train, calibrate, threshold
`s12_train` — base-rate, LR, calibrated HGB per stage; time-based split; threshold selection;
`ModelRun` rows; commit models to `data/output/models/`.
**Verify:** §K.2 suite green. **If HIGH cannot clear 0.70, suppress it and record that** — do
not tune until it passes.

### Phase 6 — Score, explain, act
`s13_risk_score` — SHAP top-5, retrieved recommendations, `lead_time_days`,
`predicted_overrun_days`. `s15_export_risk_fallback`, including regenerating
`fallbackData.ts`.
**Verify:** every HIGH row has an explaining driver; median lead time measured and reported
(do not claim ≥90 days until measured).

### Phase 7 — API
6 read routes + `/interventions` + `/projects/{id}/parcels` + `Intervention` table + extend
`/watchlist`, `/parcels/{id}`, `/cases/{id}`. Document `/dashboard/map`.
**Verify:** §K.5 suite green including `test_no_model_in_request_path`,
`test_response_latency`, `test_fallback_covers_every_route`.

### Phase 8 — Frontend risk surface
`react-router-dom`; `RiskDashboard`, `ProjectPortfolio`, `ProjectDetail`, `ModelHistory`;
`RiskBadge`, `ClockSourceBadge`; extend the timeline component; intervention form.
**Verify:** Vitest + axe green; walk §L end to end in a browser.

### Phase 9 — RBAC, a11y, deployment, docs
`X-Role` middleware + `AuditLog` + `/auth/session`; G-7 accessibility pass; Dockerfiles +
compose; Playwright spec; reconcile every doc against reality (§O-6).
**Verify:** §K.6 and §K.8 green; `docker compose up` works on a Docker-only machine; no doc
claims an artifact that does not exist.

**Parallelisation:** Phases 0 and 1 are independent of 2–6 and can run alongside. Phase 7
needs 6. Phase 8 needs 7. Phase 9's a11y and Docker work is independent of everything after
Phase 1.

---

## O. Risks and under-specified areas

### X-1 — The real acquisition data does not exist in this repo *(critical)*

`data/raw/bhoomirashi/` contains only `.gitkeep`. `PPT §3.1` and `SPEC §1` both describe
"cached Bhoomi Rashi 3A/3D notifications across ≥8 UP districts (real, dated)" as the labelled
spine. **It is not present.**

Consequence: with no real acquisition rows, `is_delayed` labels are entirely synthetic. The
project's own hard rule — *"only real data may appear in a reported metric"* — then implies
**no metric may be reported at all**, and by R12 the HIGH band must be suppressed. That
collapses the ≥0.70-precision and ≥90-day-lead-time claims.

Options, in order of preference:

- **(a)** Acquire real 3A/3D notification dates from the e-Gazette / Bhoomi Rashi for N
  projects. Even N≈60–100 across 8 districts gives a genuine, if small, labelled spine.
  `PPT §6` cites ~1,467 NHAI projects with 3A/3D dates in the e-Gazette, so the data exists —
  it just has not been collected. **This is the highest-value pre-implementation task.**
- **(b)** Ship synthetic-only, report metrics explicitly labelled *synthetic holdout*, suppress
  the HIGH band per the project's own rule, and say so on the slide. Honest, and much weaker.
- **(c)** Hybrid: real dates where obtainable, synthetic to reach trainable volume, with
  `n_test_real` / `n_test_synthetic` reported per stage.

**This decision gates Phases 3–6 and must be made before Phase 3 starts.** Do not begin
feature engineering before it is resolved.

### X-2 — Litigation coverage exists in exactly one district

135 parcels, all Sultanpur. Expanding to 8 districts means 7 have **no parcel coverage**, so
every litigation feature is null there. A tree will read "no linked cases" as "no litigation
risk" — the differentiator feature family would actively mislead on 7/8 of the corpus.

Mitigation: add `litigation_coverage ∈ {0,1}` as a first-class feature (already folded into the
20 in §G.5), and extend the synthetic parcel generator to every target district while keeping
real cases Sultanpur-only. `test_litigation_coverage_flag` guards it. **Do not let absent
coverage be encoded as zero risk.**

### X-3 — Feature count contradiction

Cap is 20 (`SPEC §5.6`); 23 named in `SPEC §5.1-5.4`; 21 named in `PPT §5`. §G.5 proposes a
concrete 20 with reasons. **Needs sign-off** — this is a documented spec conflict, not an
implementation detail to be decided silently.

### X-4 — `r_and_r` has no clock

`SPEC §3` lists `r_and_r` as a `ProjectStage.stage` value "running in parallel, not sequence",
but the clock table defines only 5 clocks and none for it. Either define a clock (and its
source) or exclude `r_and_r` from `ProjectStage` and model it separately. Undefined today.

### X-5 — `cutoff_date` for the time-based split is unspecified

`SPEC §6.1` mandates the mechanism; no document gives a value. With filing dates spanning
2024-01-17 → 2025-12-10, a cutoff too late leaves no holdout and a cutoff too early leaves no
training data. Must be chosen from the acquisition-date distribution once X-1 resolves, and
recorded in `ModelRun.cutoff_date`.

### X-6 — Which 8 districts

`SPEC §1` commits to ≥8 UP districts; no document names the other 7. `ARCH §10` flags this as
open and notes it "sets `n_train` and therefore whether GBM or LR ships". Blocks Phase 3.

### X-7 — Cadastral geometry is not real

`PPT §6` concedes clean cadastral GeoJSON is not publicly available and says "corridors render
schematically, screen states this". The current parcel map renders synthetic squares generated
by `s0` and **does not state this on the map**. Fix in Phase 1; do not borrow another state's
polygons.

### X-8 — Table count in the docs will be wrong

`ARCH §4` and `PPT §3` both say **13 tables**. §F and §H.2 require `Intervention` +
`AuditLog` = **15**. Update the docs. Do not omit the intervention table to protect a slide
number — step 10 of the officer workflow is an explicit requirement.

### X-9 — No provenance for the Karnataka → UP pivot

`docs/research/*` specify Karnataka (Bhoomi RTC, `service22`) with detailed endpoints. The
build is Sultanpur, UP (Allahabad HC). No document explains the change. Either annotate the
research docs as superseded or record the reason; a judge reading `docs/` will hit this.

### X-10 — Documents claim artifacts that do not exist

`pipeline/README.md:203`, `SPEC §219`, `SPEC §404-405` cite `tests/test_features.py`;
`pipeline/README.md:9` cites `--risk-only`. Neither exists. This is the cheapest possible
credibility loss and the same failure mode `pipeline/README.md` itself warns about regarding
the "6 years" figure. Fix by making them true (Phases 2, 4), not by deleting the references.

### X-11 — Reconciliation table

| Requirement | Source | Current implementation | Gap | Resolution |
|---|---|---|---|---|
| Exactly 8 tables | `PRD §19` | 8 tables, test-enforced | conflicts with 13/15 | **Newer spec wins.** Rewrite the test as originals-intact. |
| 13 tables | `ARCH §4`, `PPT §3` | 8 | needs `Intervention` + `AuditLog` | **15.** Update the docs. |
| 20 features | `SPEC §5.6` | none | 23 / 21 also named | §G.5 list; needs sign-off |
| 0.91 confidence | `PDF p.4` | 0.9105 measured | rounding only | quote **0.9105**; it is verifiable |
| 6-year pendency | `PRD` illustrative | ~2.59 y measured | doc is stale | **Always say ~2.6 years.** Already flagged by the repo itself. |
| 42 tests | earlier PDF | 47 measured | stale | say 47; verified by `pytest` |
| 8 endpoints | `PRD §37` | **9** implemented | `/dashboard/map` undocumented | document it |
| ~1.3 s build | `PDF p.4` | 0.3 s measured (2.1 s wall incl. interpreter start) | claim is conservative | either figure is defensible; be consistent |
| Karnataka | `docs/research/*` | Sultanpur, UP | pivot unexplained | annotate as superseded |
| Buyer/lawyer roles | `PRD §8` | none | different product | Adhigrahan roles are officer / district officer / reviewer / admin |
| `RiskBadge`, `clock_source` tokens | `SPEC §8.2`, `ARCH §7.2` | absent from `DESIGN_GUIDELINES.md` | doc not updated | add both token tables in Phase 8 |

### X-12 — Scope discipline

`CONTRIBUTING.md` forbids: blockchain registry, OCR, legal chatbot, grievance portal,
land-record CRUD, payments, real authentication, cloud orchestration, graph database, PostGIS,
live scraping at demo time, retraining in the request path. **Nothing in this blueprint adds
any of them.** The largest temptation will be to "improve" the officer dashboard into a
land-record portal. Do not.

---

## Appendix — measured baseline (2026-09-09)

Reproduce with `make build && make test`.

```
python  3.12.13  (3.14.7 FAILS: pyarrow 17 has no cp314 wheel)
node    22.23.2

build   s1  cases=38 parcels=135 district=Sultanpur active=8 disposed=30
            max_pendency_years=2.59 next_hearing_derived=8 next_hearing_real=0
        s2  villages_surface=26 canonical=22 collapsed=4 cases_with_father=6
        s3  candidate_pairs=1678 (via_village=193, via_district_fallback=1485)
            full_cross_join_would_be=5130
        s4  pairs_scored=1678 surfaced=84 ident_exact=68 ident_subdivision=26
        s5  parcels=135 with_closed_history=31 P-B01=RED@0.9105 P-A01=GREEN
        s7  files_written=314
        complete in 0.3s

tests   47 passed in 0.26s

db      8 tables + sqlite_sequence
        Parcel 135 (100% synthetic) | Person 104 | CourtCase 38 (100% real)
        CaseParty 76 | CourtEvent 84 | ParcelCaseLink 84 | Watchlist 1 | SourceRecord 4
        links: HIGH 43, MEDIUM 41     cases: active 8, disposed 30
        districts: Sultanpur 135

api     200 on all 9 routes; /dashboard/map -> 135 GeoJSON features
        evidence: 67 of 84 links have father_name_similarity = null

fails   make api          -> .venv/Scripts/uvicorn: No such file or directory
        npm run build     -> TS2322 fallbackData.ts(149,9)
        map tiles         -> "API KEY REQUIRED" watermark (CARTO CDN)
        a11y attributes   -> 0
        frontend tests    -> 0
```
