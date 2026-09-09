# Adhigrahan Radar
#
# Fresh clone, in order:
#   make setup     install python + node dependencies
#   make build     regenerate data/output from the committed contract (s0-s15)
#   make test      108 backend tests + 22 frontend tests, all green
#   make api       serve on :8000
#   make web       serve on :5173  (separate terminal)

VENV := .venv
BIN  := $(VENV)/bin
ifeq ($(OS),Windows_NT)
BIN  := $(VENV)/Scripts
endif
PY   := $(BIN)/python
PIP  := $(BIN)/pip
UVICORN := $(BIN)/uvicorn

# pyarrow has no 3.14 wheel yet, so the venv must be built with 3.11-3.13.
# System `python`/`python3` may resolve to a newer interpreter (e.g. a
# rolling-release distro shipping 3.14 by default) - search a small set of
# common names for one that satisfies the range instead of hardcoding
# `python` and failing opaquely inside `pip install`.
BASE_PY := $(shell for c in python3.13 python3.12 python3.11 python3 python; do \
	  command -v $$c >/dev/null 2>&1 || continue; \
	  $$c -c "import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<(3,14) else 1)" 2>/dev/null \
	    && { echo $$c; break; }; \
	done)

.PHONY: setup build build-all test test-py test-web api web diagrams clean lint doctor

setup: doctor
	$(BASE_PY) -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt
	cd frontend && npm install

## Verify the toolchain before setup: Python 3.11-3.13 (pyarrow has no 3.14
## wheel yet), Node >=20. Fails loudly, with the exact fix, instead of
## letting `pip install` fail deep inside a pyarrow build.
doctor:
	@if [ -z "$(BASE_PY)" ]; then \
	  echo "FAIL no python 3.11-3.13 found on PATH (pyarrow has no 3.14 wheel yet)."; \
	  echo "     install one, e.g.: sudo pacman -S python311  |  apt install python3.11  |  pyenv install 3.12"; \
	  exit 1; \
	fi
	@echo "OK python via $(BASE_PY): $$($(BASE_PY) --version 2>&1)"
	@node -e "const v=process.versions.node.split('.').map(Number); \
	  const ok=v[0]>=20; console.log((ok?'OK ':'FAIL ')+'node '+process.versions.node); \
	  process.exit(ok?0:1)"

## Regenerate vivaad.db + the fallback cache from data/input/*.parquet.
## --skip-handoff is the default: s0 rebuilds the synthetic land side from an
## external corpus that is not part of this repo. The committed parquets are
## the contract; everything downstream of them is reproducible.
build:
	$(PY) pipeline/run_all.py --skip-handoff

## Full build including s0. Requires the external land-cases corpus.
build-all:
	$(PY) pipeline/run_all.py

test: test-py test-web

test-py:
	$(PY) -m pytest

test-web:
	cd frontend && npm test

lint:
	$(PY) -m ruff check .

api:
	$(UVICORN) backend.main:app --reload --port 8000

web:
	cd frontend && npm run dev

diagrams:
	$(PY) docs/architecture/make_diagrams.py

clean:
	rm -rf data/intermediate data/output/fallback data/output/vivaad.db data/output/models
