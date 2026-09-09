"""Shared paths, provenance labels and IO helpers for the offline build.

Everything in this package runs BEFORE the demo (Excalidraw offline boundary,
PRD 36/48/49). Nothing here may be imported by request-time code.
"""
import json
import os
import re

# Small-data build. Every fit in s12 and every predict in s13 runs on a few
# hundred rows, where OpenMP fan-out buys nothing and can cost everything:
# on a loaded machine sklearn's HistGradientBoosting spun for minutes in
# its thread barrier on a 50-row fit that takes 20 ms single-threaded, and
# the build looked hung at s12. One thread is also what makes the
# histogram sums order-stable, so a rebuild is byte-identical. This module
# is the first import of every stage, so the runtime reads it before any
# OpenMP library loads; `setdefault` leaves an operator's explicit choice
# alone. s12/s13 repeat the limit at runtime via threadpoolctl in case a
# caller imported sklearn first.
os.environ.setdefault("OMP_NUM_THREADS", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_IN = os.path.join(ROOT, "data", "input")
DATA_MID = os.path.join(ROOT, "data", "intermediate")
DATA_OUT = os.path.join(ROOT, "data", "output")
FALLBACK = os.path.join(DATA_OUT, "fallback")
DB = os.path.join(DATA_OUT, "vivaad.db")

# The source layer's mirror of real government records (`make ingest`,
# ingest/README.md). The build only ever READS this directory and never
# opens a socket; the two files below are the contract the risk engine
# consumes, specified in docs/specs/2026-09-10-acquisition-contract-handoff.md.
RAW_DIR = os.path.join(ROOT, "data", "raw")
ACQUISITION_CONTRACT = os.path.join(RAW_DIR, "acquisition_projects.json")
TARGET_DISTRICTS = os.path.join(RAW_DIR, "target_districts.json")

# The single source of truth for every table. backend/db.py:init_schema
# loads the same file, so the pipeline and the backend can never define the
# schema twice and drift apart again.
SCHEMA_SQL = os.path.join(ROOT, "backend", "schema.sql")

DISTRICT = "Sultanpur"                      # linkage engine (s0-s7) scope - unchanged
FLAGSHIP_CNR = "UPHC020611812025"          # WRIB/784/2025, active, gata 153 + 1365/1
FLAGSHIP_PARCEL_RED = "P-B01"              # the litigated parcel the flagship case cites
FLAGSHIP_PARCEL_CLEAN = "P-A01"            # its clean control, same village
SEED = 20260820

# The build's fictional "now", and the only place it is written down. s0
# places the flagship sale relative to it, s1 validates that placement
# against it, s8/s9/s11/s13 resolve every statutory clock against it. It
# used to be spelled three times - here, in s0 as a Timestamp, in s1 as a
# literal - and two of the three had drifted a day apart, which silently
# widened the contract check s1 exists to enforce.
#
# Pinned to the date of the first Gazette harvest (ingest design, "verified
# live on 2026-09-10"). A "now" that predates the real records the build
# consumes would make most of them invisible: s8 windows the contract at
# this date, so a notification published after it does not exist yet and
# a declaration published after it has not happened yet. Moving this
# earlier is legitimate - it is exactly how a historical replay would be
# run - but it censors real rows accordingly, and s8 reports how many.
TODAY = "2026-09-10"

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


def _district_abbreviations(districts):
    """Shortest prefix length that keeps every district distinct, so the
    PRJ-<abbr>-<seq> id space is collision-free by construction.

    A fixed 3-letter prefix is not. Real UP district pairs collide on it
    (Ballia/Balrampur both give BAL); the two districts then mint the same
    project_id, and the only symptom is s9 failing the build four stages
    later with "acquisitions.project_id is not unique". Widening the
    prefix until it separates keeps today's ids (SUL, AME, ...) unchanged
    and makes the next district a data edit rather than a debugging
    session."""
    for n in range(3, max(len(d) for d in districts) + 1):
        abbr = {d: d[:n].upper() for d in districts}
        if len(set(abbr.values())) == len(districts):
            return abbr
    raise ValueError("districts are not distinguishable by prefix: " + str(districts))


DISTRICT_ABBR = _district_abbreviations(ACQUISITION_DISTRICTS)


def canon_district(name):
    """One spelling per place, across the two corpora.

    The gazette prints `SULTANPUR`, `KAIMUR (BHABUA )`, `TUTICORIN( Thoothukudi)`;
    the synthetic corpus and the litigation corpus use `Sultanpur`. Left
    alone they are two districts for one place and every district-level
    feature and filter splits in half (handoff spec, section 5). A name that
    matches a contracted district case-insensitively takes that exact
    spelling; anything else is title-cased word by word with the gazette's
    stray whitespace collapsed. Mixed-case input (`East Jaintia Hills`,
    `Ri-Bhoi`) is already a spelling and is left alone."""
    if not name:
        return name
    cleaned = re.sub(r"\s+", " ", name).replace("( ", "(").replace(" )", ")").strip()
    for district in ACQUISITION_DISTRICTS:
        if district.lower() == cleaned.lower():
            return district
    return re.sub(r"[A-Za-z]+",
                  lambda m: m.group(0).capitalize()
                  if m.group(0).isupper() or m.group(0).islower() else m.group(0),
                  cleaned)

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

# Parcel status vocabulary. s5 decides it, s6 stores it, s8 and s11 read it
# back out of the DB - so the ranking and the "what if the DB says something
# else" rule live here once instead of being re-spelled by each reader.
PARCEL_STATUSES = ("GREEN", "AMBER", "RED")
STATUS_RANK = {s: i for i, s in enumerate(PARCEL_STATUSES)}


def parcel_status(raw):
    """NULL -> GREEN, unrecognised -> AMBER, never RED. The same rule the
    serving layer applies (backend/routers/dashboard.py:_bucket), so a
    foreign or half-built DB degrades identically on both sides instead of
    taking the build down with a KeyError on an unexpected status."""
    if raw is None:
        return "GREEN"
    return raw if raw in STATUS_RANK else "AMBER"

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
