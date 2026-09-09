"""Shared paths, provenance labels and IO helpers for the offline build.

Everything in this package runs BEFORE the demo (Excalidraw offline boundary,
PRD 36/48/49). Nothing here may be imported by request-time code.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_IN = os.path.join(ROOT, "data", "input")
DATA_MID = os.path.join(ROOT, "data", "intermediate")
DATA_OUT = os.path.join(ROOT, "data", "output")
FALLBACK = os.path.join(DATA_OUT, "fallback")
DB = os.path.join(DATA_OUT, "vivaad.db")

# The single source of truth for every table. backend/db.py:init_schema
# loads the same file, so the pipeline and the backend can never define the
# schema twice and drift apart again.
SCHEMA_SQL = os.path.join(ROOT, "backend", "schema.sql")

DISTRICT = "Sultanpur"                      # linkage engine (s0-s7) scope - unchanged
FLAGSHIP_CNR = "UPHC020611812025"          # WRIB/784/2025, active, gata 153 + 1365/1
SEED = 20260820
TODAY = "2026-08-21"                       # the build's fictional "now" (matches s1_ingest)

# ---- risk engine (s8-s15) --------------------------------------------------

# The real litigation corpus (s0-s7) is Sultanpur-only. The acquisition side
# spans multiple UP districts, per the SIH26017 design brief's >=8-district
# target; Sultanpur is included so its real litigation history is usable as
# a feature, and the other 7 are real UP district names with no litigation
# corpus behind them (litigation_coverage=0 there - s11 must not read that
# as "no risk", only as "no litigation signal available").
ACQUISITION_DISTRICTS = [
    "Sultanpur", "Amethi", "Pratapgarh", "Raebareli",
    "Ayodhya", "Barabanki", "Gonda", "Basti",
]
FLAGSHIP_PROJECT_ID = "PRJ-SUL-001"          # binds P-B01's parcels; the RED-risk demo anchor
RISK_SEED = 20260909

# Five lifecycle stages with a resolvable statutory clock. A sixth,
# r_and_r (rehabilitation & resettlement), runs in parallel rather than in
# this sequence and no source document defines a clock for it, so it is
# deliberately excluded rather than assigned an invented deadline.
STAGE_CLOCKS = {
    "notification_3a_11": {
        "order": 1, "statutory_days": 365, "clock_source": "statute"},
    "declaration_3d_19": {
        "order": 2, "statutory_days": 365, "clock_source": "statute"},
    "award_3g_23": {
        "order": 3, "statutory_days": 365, "clock_source": "statute"},
    "compensation_disbursed": {
        "order": 4, "statutory_days": 90, "clock_source": "administrative_target"},
    "possession": {
        "order": 5, "statutory_days": 90, "clock_source": "administrative_target"},
}
STAGE_ORDER = sorted(STAGE_CLOCKS, key=lambda s: STAGE_CLOCKS[s]["order"])


def clock_authority(stage, act):
    """The statute section a stage's clock is drawn from, or None for the
    two administrative targets (PRD/spec: 'every row carries clock_source
    and every screen renders the distinction')."""
    if stage == "notification_3a_11":
        return "NH Act 1956 s.3D(3)" if act == "NH_1956" else "RFCTLARR 2013 s.19(7)"
    if stage in ("declaration_3d_19", "award_3g_23"):
        return "RFCTLARR 2013 s.25"
    return None

# PRD 21 - every row must be traceable to exactly one of these.
PROVENANCE = {"real", "synthetic", "mocked", "derived", "model_generated", "cached"}

# PRD 27 - hackathon-initial weights, explicitly unvalidated starting points.
WEIGHTS = {
    "identifier": 0.40,
    "name": 0.25,
    "father_name": 0.15,
    "village": 0.10,
    "case_type": 0.10,
}

# PRD 28 bands.
HIGH, MEDIUM = 0.85, 0.60

# PRD 29 - dispute types weighted for matching relevance.
CASE_TYPE_RELEVANCE = {
    "partition": 1.0, "title_declaration": 1.0, "boundary_demarcation": 1.0,
    "encroachment": 1.0, "specific_performance": 0.9, "succession_inheritance": 0.9,
    "mutation_revenue_record": 0.9, "consolidation_chak": 0.8, "possession": 0.8,
    "injunction": 0.7, "sale_deed_transfer": 0.7, "tenancy": 0.6,
    "acquisition_compensation": 0.6,
}


def report(stage, payload):
    """Write a per-stage report; these feed the PRD 53 technical story."""
    os.makedirs(DATA_MID, exist_ok=True)
    path = os.path.join(DATA_MID, f"{stage}_report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    bits = ", ".join(f"{k}={v}" for k, v in payload.items()
                     if isinstance(v, int | float | str))
    print(f"[{stage}] {bits}")


class ContractError(Exception):
    """Raised by s1 when the handoff contract is violated. Fail loudly."""
