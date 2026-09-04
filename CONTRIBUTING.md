# Contributing

## Running it

```bash
make setup     # venv + pip + npm
make build     # regenerate data/output from data/input
make test      # 47 tests must stay green
```

`make test` is the gate. The linkage engine is finished and its behaviour is pinned by a
golden suite; if a change turns any of those 47 red, the change is wrong until proven
otherwise.

## Where things go

| Change | Goes in |
|---|---|
| A new pipeline stage | `pipeline/sN_name.py`, registered in `run_all.py` |
| A new endpoint | `backend/routers/`, plus a fallback export in `s7` or `s15` |
| A new screen | `frontend/src/pages/`, following `docs/architecture/DESIGN_GUIDELINES.md` |
| A design decision | A dated spec in `docs/specs/`, before the code |

## Rules that are not style preferences

1. **Nothing in `pipeline/` may be imported by request-time code.** The offline boundary is
   the reason the demo cannot fail live.
2. **No scoring, no model load, no SHAP in an endpoint.** Every route is a `SELECT` plus JSON
   shaping. If a route needs a computed value, precompute it in the pipeline.
3. **Every row carries a provenance label.** `real` / `synthetic` / `mocked` / `derived` /
   `model_generated` / `cached`.
4. **Synthetic data may train a model; only real data may score it.** No synthetic row may
   contribute to a reported metric.
5. **A fabricated value must say it is fabricated.** Derived court dates carry
   `next_hearing_source='derived'`; administrative deadlines carry
   `clock_source='administrative_target'`. Unlabelled fabrication is the cheapest possible
   credibility loss.
6. **Fail at build time, never at demo time.** Contract violations raise in `s1` / `s9`.

## What not to build

Blockchain registry · OCR / handwriting digitisation · legal chatbot · grievance portal ·
land-record CRUD · payments · real authentication · cloud orchestration · a graph database ·
PostGIS · live scraping at demo time · retraining in the request path.

If one of these appears in a pull request, the project has drifted. See
`docs/architecture/adhigrahan-radar-architecture.md` §9.

## Commits

Short imperative subject, then *why* rather than *what* - the diff already says what.
