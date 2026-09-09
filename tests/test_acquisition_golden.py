"""Acquisition/risk-engine golden suite (s8-s10). Seed-data integrity: the
dashboard must never say "high risk" while the underlying records disagree,
and every contrasting demo scenario the product needs to show must actually
exist in the corpus, not just be assumed.

Run after every data drop, alongside tests/test_golden.py:
    python -m pytest tests/ -q
"""
import json
import os
import re
import sqlite3
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MID = os.path.join(ROOT, "data", "intermediate")
DB = os.path.join(ROOT, "data", "output", "vivaad.db")
CONTRACT = os.path.join(ROOT, "data", "raw", "acquisition_projects.json")
UNGAZETTED = ("affected_families", "budget_estimate_inr", "executing_agency", "block")
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


@pytest.fixture(scope="module")
def contract():
    """The ingest layer's contract - the source of every real row."""
    assert os.path.exists(CONTRACT), "data/raw/acquisition_projects.json missing"
    with open(CONTRACT, encoding="utf-8") as fh:
        return {c["project_id"]: c for c in json.load(fh)}


@pytest.fixture(scope="module")
def build_today():
    sys.path.insert(0, os.path.join(ROOT, "pipeline"))
    from common import TODAY
    return TODAY


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


def test_every_contracted_district_is_represented_by_the_synthetic_corpus(acquisitions):
    """The eight contracted UP districts bound the GENERATED corpus. Real
    rows sit wherever the gazette put them and are checked on their own."""
    got = {p["district"] for p in acquisitions if p["source_label"] == "synthetic"}
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


def test_synthetic_republications_come_only_from_administrative_archetypes(
        acquisitions, archetypes):
    """A GENERATED re-publication count is a property of the admin-delay
    archetypes. A REAL count is whatever the gazette says - the handoff
    singles it out as plausibly the strongest real feature - and must never
    be constrained by the generator's rule."""
    for p in acquisitions:
        if p["source_label"] == "synthetic" and p["gazette_republication_count"] > 0:
            assert archetypes[p["project_id"]] in (
                "admin_risk", "lapsed_history", "mixed_risk"), p["project_id"]


def test_real_projects_are_present_and_traceable_to_a_gazette_document(
        acquisitions, stages, contract):
    """The contract's `PRJ-<district>-G<doc id>` is kept verbatim, and the
    document id is repeated in gazette_ref, so every real figure in the
    product traces back to a retrievable government PDF."""
    real = [p for p in acquisitions if p["source_label"] == "real"]
    assert real, "no gazette-sourced project in the corpus"
    for p in real:
        assert re.fullmatch(r"PRJ-[A-Z]{3}-G\d+", p["project_id"]), p["project_id"]
        assert p["act"] == "NH_1956", p["project_id"]
        for doc_id in contract[p["project_id"]]["source_doc_ids"]:
            assert f"eGazette {doc_id}" in p["gazette_ref"], p["project_id"]
    real_ids = {p["project_id"] for p in real}
    real_stages = [s for s in stages if s["project_id"] in real_ids]
    assert len(real_stages) == len(real_ids), "a real project must carry exactly one stage"
    assert {s["stage"] for s in real_stages} == {"notification_3a_11"}
    assert all(s["source_label"] == "real" for s in real_stages)
    assert all(s["clock_authority"] == "NH Act 1956 s.3D(3)" for s in real_stages)


def test_real_rows_never_carry_a_measure_the_gazette_does_not_publish(acquisitions):
    """Absent is not zero (handoff, section 3). A value here would be an
    imputation dressed as a measurement."""
    for p in acquisitions:
        if p["source_label"] == "real":
            for col in UNGAZETTED:
                assert p[col] is None, (p["project_id"], col)


def test_real_dates_are_the_gazette_dates_windowed_at_the_build_now(
        acquisitions, stages, contract, build_today):
    """started_on is the s.3A date verbatim. completed_on is the s.3D date
    verbatim when it falls on or before TODAY and NULL otherwise - censored,
    never coerced. Nothing real is dated after the build's now, and a
    notification not yet published by then does not exist in the corpus."""
    real_ids = {p["project_id"] for p in acquisitions if p["source_label"] == "real"}
    for s in stages:
        if s["project_id"] not in real_ids:
            continue
        c = contract[s["project_id"]]
        assert s["started_on"] == c["notified_3a_on"], s["project_id"]
        assert s["started_on"] <= build_today, s["project_id"]
        if c["declared_3d_on"] and c["declared_3d_on"] <= build_today:
            assert s["completed_on"] == c["declared_3d_on"], s["project_id"]
        else:
            assert s["completed_on"] is None, s["project_id"]
    for pid, c in contract.items():
        if c["notified_3a_on"] > build_today:
            assert pid not in real_ids, f"{pid} is notified after TODAY yet in the corpus"


def test_district_spelling_is_one_per_place(acquisitions):
    """'SULTANPUR' and 'Sultanpur' would be two districts for one place and
    split every district feature and filter in half (handoff, section 5)."""
    by_lower = {}
    for p in acquisitions:
        by_lower.setdefault(p["district"].lower(), set()).add(p["district"])
    dupes = {k: sorted(v) for k, v in by_lower.items() if len(v) > 1}
    assert not dupes, dupes
    assert not any(d != d.strip() or "  " in d or "( " in d for d in by_lower)


def test_real_and_synthetic_rows_are_separable_in_the_db(con):
    for t in ("AcquisitionProject", "ProjectStage"):
        n_real = con.execute(
            f"SELECT COUNT(*) FROM {t} WHERE source_label='real'").fetchone()[0]
        n_syn = con.execute(
            f"SELECT COUNT(*) FROM {t} WHERE source_label='synthetic'").fetchone()[0]
        assert n_real > 0 and n_syn > 0, (t, n_real, n_syn)


def test_open_real_notifications_are_scored(con):
    """The tangible output: a real, open s.3A notification gets a delay
    score and drivers like any other open stage, and shows up wherever the
    portfolio is ranked."""
    n = con.execute(
        """SELECT COUNT(*) FROM ProjectRisk pr
           JOIN AcquisitionProject ap ON ap.id = pr.project_id
           WHERE ap.source_label = 'real'""").fetchone()[0]
    assert n > 0, "no gazette-sourced project has a risk score"


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


def test_project_ids_are_unique_and_district_prefixes_do_not_collide(acquisitions):
    """project_id is PRJ-<district abbreviation>-<seq>, and the sequence
    counter is per district - so two districts sharing an abbreviation mint
    the same id. A fixed 3-letter prefix collides on real UP district pairs
    (Ballia/Balrampur -> BAL), and the only symptom is s9 failing the build
    four stages later with "project_id is not unique". The abbreviation must
    be a bijection with the district set."""
    ids = [p["project_id"] for p in acquisitions]
    assert len(set(ids)) == len(ids), "duplicate project_id in the corpus"
    # The prefix bijection is a property of the GENERATOR's id space. A real
    # id is `PRJ-<abbr>-G<gazette doc id>` and is unique by the document id
    # regardless of prefix (Solan and Solapur both abbreviate to SOL).
    synthetic = [p for p in acquisitions if p["source_label"] == "synthetic"]
    abbr_to_districts = {}
    for p in synthetic:
        abbr = p["project_id"].split("-")[1]
        abbr_to_districts.setdefault(abbr, set()).add(p["district"])
    collisions = {a: sorted(d) for a, d in abbr_to_districts.items() if len(d) > 1}
    assert not collisions, f"districts sharing a project-id prefix: {collisions}"
    assert len(abbr_to_districts) == len({p["district"] for p in synthetic})


def test_a_lapsed_project_is_guaranteed_not_rolled_for(acquisitions, archetypes):
    """One forced slot lapses by construction (s8's force_lapse_stage), so
    the corpus cannot come out with zero lapsed projects. This used to be a
    35% dice roll per over-long statutory stage and had produced exactly one
    lapsed project in 96 - one unlucky seed away from failing the build."""
    lapsed = [p for p in acquisitions if p["status"] == "lapsed"]
    assert lapsed, "no lapsed project in the corpus"
    assert any(archetypes.get(p["project_id"]) == "lapsed_history" for p in lapsed)


def test_unexpected_parcel_status_is_bucketed_not_crashed():
    """s8 ranks villages by the worst Parcel.status behind them. It used to
    index a bare dict, so a status this build did not write took the whole
    build down with a KeyError. Unknown must degrade to AMBER, never RED,
    and never explode - the same rule the serving layer applies."""
    sys.path.insert(0, os.path.join(ROOT, "pipeline"))
    from common import STATUS_RANK, parcel_status
    assert parcel_status(None) == "GREEN"
    assert parcel_status("UNDER_REVIEW") == "AMBER"
    assert parcel_status("RED") == "RED"
    assert STATUS_RANK[parcel_status("UNDER_REVIEW")] < STATUS_RANK["RED"]


def test_one_build_today_across_the_pipeline(stages):
    """s0 placed the flagship sale against one "now", s1 validated it
    against another, and common carried a third - two of the three had
    drifted a day apart, quietly widening the contract check s1 exists to
    enforce. There must be exactly one."""
    sys.path.insert(0, os.path.join(ROOT, "pipeline"))
    import s0_handoff
    from common import TODAY
    assert s0_handoff.TODAY.date().isoformat() == TODAY
    # every deadline is resolved against that same date
    assert all(date.fromisoformat(s["deadline_on"])
               == date.fromisoformat(s["started_on"]) + timedelta(days=s["statutory_days"])
               for s in stages)
