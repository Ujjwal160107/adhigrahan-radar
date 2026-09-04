# Adhigrahan Radar
#
# Fresh clone, in order:
#   make setup     install python + node dependencies
#   make build     regenerate data/output from the committed contract
#   make test      47 tests, all green
#   make api       serve on :8000
#   make web       serve on :5173  (separate terminal)

VENV := .venv
PY   := $(VENV)/Scripts/python
PIP  := $(VENV)/Scripts/pip
ifeq ($(OS),)
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
endif

.PHONY: setup build test api web diagrams clean lint

setup:
	python -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt
	cd frontend && npm install

## Regenerate vivaad.db + the fallback cache from data/input/*.parquet.
## --skip-handoff is the default: s0 rebuilds the synthetic land side from an
## external corpus that is not part of this repo. The committed parquets are
## the contract; everything downstream of them is reproducible.
build:
	$(PY) pipeline/run_all.py --skip-handoff

## Full build including s0. Requires the external land-cases corpus.
build-all:
	$(PY) pipeline/run_all.py

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

api:
	$(VENV)/Scripts/uvicorn backend.main:app --reload --port 8000

web:
	cd frontend && npm run dev

diagrams:
	$(PY) docs/architecture/make_diagrams.py

clean:
	rm -rf data/intermediate data/output/fallback data/output/vivaad.db
