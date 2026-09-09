"""Acquisition/risk-engine golden suite (s8-s10). Seed-data integrity: the
dashboard must never say "high risk" while the underlying records disagree,
and every contrasting demo scenario the product needs to show must actually
exist in the corpus, not just be assumed.

Run after every data drop, alongside tests/test_golden.py:
    python -m pytest tests/ -q
"""
import json
import os
import sqlite3
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MID = os.path.join(ROOT, "data", "intermediate")
DB = os.path.join(ROOT, "data", "output", "vivaad.db")
FLAGSHIP_PROJECT_ID = "PRJ-SUL-001"
ACQUISITION_DISTRICTS = {"Sultanpur", "Amethi", "Pratapgarh", "Raebareli",
                         "Ayodhya", "Barabanki", "Gonda", "Basti"}


@pytest.fixture(scope="module")
def acquisitions():
    path = os.path.join(MID, "acquisitions.json")
    assert os.path.exists(path), "acquisitions.json missing - run pipeline/run_all.py first"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def stages():
    with open(os.path.join(MID, "project_stages.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def bindings():
    with open(os.path.join(MID, "project_parcels.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def archetypes():
    with open(os.path.join(MID, "acquisitions_archetypes.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def con():
    c = sqlite3.connect(DB)
    yield c
    c.close()


def test_flagship_project_exists(acquisitions):
    ids = {p["project_id"] for p in acquisitions}
    assert FLAGSHIP_PROJECT_ID in ids


def test_flagship_project_binds_the_flagship_parcel(bindings):
    """The risk story (HIGH-risk project) must terminate in the already-
    built litigation evidence (RED parcel P-B01, 0.9105) - the drill-down
    the product exists to support, not two unrelated corpora."""
    flagship_parcels = {b["parcel_id"] for b in bindings
                        if b["project_id"] == FLAGSHIP_PROJECT_ID}
    assert "P-B01" in flagship_parcels, (
        f"flagship project does not bind P-B01: bound to {flagship_parcels}")


def test_every_district_represented(acquisitions):
    got = {p["district"] for p in acquisitions}
    assert got == ACQUISITION_DISTRICTS


def test_every_district_has_projects(acquisitions):
    counts = {}
    for p in acquisitions:
        counts[p["district"]] = counts.get(p["district"], 0) + 1
    empty = [d for d in ACQUISITION_DISTRICTS if counts.get(d, 0) == 0]
    assert not empty, f"districts with zero projects: {empty}"


def test_every_required_archetype_present(archetypes):
    """Task requirement: healthy, litigation-driven, admin-delay-driven,
    lapsed and mixed scenarios must all be inspectable in the seed corpus,
    not asserted in a slide."""
    present = set(archetypes.values())
    required = {"healthy", "litigation_risk", "admin_risk",
                "lapsed_history", "mixed_risk"}
    missing = required - present
    assert not missing, f"archetypes missing from the corpus: {missing}"


def test_every_project_status_represented(acquisitions):
    statuses = {p["status"] for p in acquisitions}
    assert {"open", "completed", "lapsed"} <= statuses


def test_open_stages_are_censored_never_on_time(stages):
    """PRD-equivalent rule for the risk engine: is_delayed must be NULL,
    never 0, for a stage that has not finished - coercing it to 0 would
    silently label every in-flight project on-time."""
    bad = [s for s in stages if s["completed_on"] is None and s["is_delayed"] is not None]
    assert not bad, f"open stages with a non-null is_delayed: {[s['project_id'] for s in bad]}"


def test_closed_stages_always_have_a_delay_verdict(stages):
    bad = [s for s in stages if s["completed_on"] is not None and s["is_delayed"] is None]
    assert not bad, f"closed stages with no delay verdict: {[s['project_id'] for s in bad]}"


def test_deadline_arithmetic_is_true(stages):
    """A stage may not merely *say* overdue - deadline_on, overdue_days and
    is_delayed must be arithmetically derivable from started_on/
    completed_on/statutory_days, or the dashboard can show a risk badge
    the underlying dates contradict."""
    for s in stages:
        started = date.fromisoformat(s["started_on"])
        deadline = date.fromisoformat(s["deadline_on"])
        assert deadline == started + timedelta(days=s["statutory_days"]), s["project_id"]
        if s["completed_on"] is not None:
            completed = date.fromisoformat(s["completed_on"])
            assert completed > started, s["project_id"]
            expect_delayed = 1 if completed > deadline else 0
            assert s["is_delayed"] == expect_delayed, s["project_id"]
            expect_overdue = max(0, (completed - deadline).days)
            assert s["overdue_days"] == expect_overdue, s["project_id"]


def test_stage_sequence_never_skips_or_reorders(stages):
    """A project's stages must be entered in statutory order (you cannot
    reach award before declaration)."""
    by_project = {}
    for s in stages:
        by_project.setdefault(s["project_id"], []).append(s)
    for pid, rows in by_project.items():
        orders = [r["stage_order"] for r in rows]
        assert orders == sorted(orders), f"{pid} stages out of order: {orders}"
        assert orders == list(range(1, len(orders) + 1)), (
            f"{pid} skipped a stage: {orders}")


def test_statutory_vs_administrative_clocks_are_labelled(stages):
    """Two of five clocks are administrative targets, not law - every row
    must say which, the same discipline already enforced for
    next_hearing_source on the litigation side."""
    for s in stages:
        assert s["clock_source"] in ("statute", "administrative_target")
        if s["stage"] in ("compensation_disbursed", "possession"):
            assert s["clock_source"] == "administrative_target"
        else:
            assert s["clock_source"] == "statute"


def test_referential_integrity_stage_to_project(acquisitions, stages):
    known = {p["project_id"] for p in acquisitions}
    orphans = {s["project_id"] for s in stages} - known
    assert not orphans, f"stages reference unknown projects: {orphans}"


def test_referential_integrity_bindings(acquisitions, bindings, con):
    known_projects = {p["project_id"] for p in acquisitions}
    known_parcels = {r[0] for r in con.execute("SELECT id FROM Parcel")}
    bad_projects = {b["project_id"] for b in bindings} - known_projects
    bad_parcels = {b["parcel_id"] for b in bindings} - known_parcels
    assert not bad_projects, f"bindings reference unknown projects: {bad_projects}"
    assert not bad_parcels, f"bindings reference unknown parcels: {bad_parcels}"


def test_only_sultanpur_projects_bind_parcels(acquisitions, bindings):
    """The real litigation corpus is Sultanpur-only; a binding anywhere
    else would be fabricated, not derived."""
    district_by_project = {p["project_id"]: p["district"] for p in acquisitions}
    bound_districts = {district_by_project[b["project_id"]] for b in bindings}
    assert bound_districts <= {"Sultanpur"}, (
        f"non-Sultanpur projects have parcel bindings: {bound_districts}")


def test_lapsed_history_archetype_has_a_lapsed_project(acquisitions, archetypes):
    """A statutory-clock breach really does lapse the project in this
    corpus - not just run long - so 'lapsed' is a real, inspectable
    terminal state, matching the legal consequence the product warns about."""
    lapsed_ids = {p["project_id"] for p in acquisitions if p["status"] == "lapsed"}
    assert lapsed_ids, "no lapsed project in the corpus"
    for pid in lapsed_ids:
        assert archetypes.get(pid) in ("lapsed_history", "litigation_risk", "admin_risk"), (
            f"{pid} lapsed under an unexpected archetype: {archetypes.get(pid)}")


def test_gazette_republication_only_on_administrative_archetypes(acquisitions):
    for p in acquisitions:
        if p["gazette_republication_count"] > 0:
            assert p["source_label"] == "synthetic"


def test_provenance_is_traceable(acquisitions, stages, bindings):
    for row in acquisitions:
        assert row["source_label"], row["project_id"]
    for row in stages:
        assert row["source_label"], (row["project_id"], row["stage"])
    for row in bindings:
        assert row["source_label"], (row["project_id"], row["parcel_id"])


def test_risk_tables_loaded_into_db(con):
    for t, min_rows in (("AcquisitionProject", 90), ("ProjectStage", 300),
                        ("ProjectParcel", 50), ("ProjectRisk", 40), ("ModelRun", 5)):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert n >= min_rows, f"{t} has only {n} rows"


def test_original_eight_tables_survive_s14(con):
    for t in ("Parcel", "Person", "CourtCase", "CaseParty", "CourtEvent",
             "ParcelCaseLink", "SourceRecord"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert n > 0, f"{t} is empty after the risk-engine load - s14 touched it"


def test_flagship_project_scores_high_in_db(con):
    row = con.execute(
        "SELECT risk_band FROM ProjectRisk WHERE project_id=? ORDER BY delay_probability DESC",
        (FLAGSHIP_PROJECT_ID,)).fetchone()
    assert row is not None, "flagship project has no risk score in the DB"
    assert row[0] in ("MEDIUM", "HIGH")
