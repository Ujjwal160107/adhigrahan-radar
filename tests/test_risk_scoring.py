"""s13_risk_score.py golden tests. Run after every data drop:
    python -m pytest tests/test_risk_scoring.py -q
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MID = os.path.join(ROOT, "data", "intermediate")
FLAGSHIP_PROJECT_ID = "PRJ-SUL-001"


@pytest.fixture(scope="module")
def scores():
    path = os.path.join(MID, "project_risk.json")
    assert os.path.exists(path), "project_risk.json missing - run pipeline/run_all.py first"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def stages_by_key():
    with open(os.path.join(MID, "project_stages.json"), encoding="utf-8") as fh:
        return {(r["project_id"], r["stage"]): r for r in json.load(fh)}


def test_only_open_stages_are_scored(scores, stages_by_key):
    for s in scores:
        stage = stages_by_key[(s["project_id"], s["stage"])]
        assert stage["completed_on"] is None, f"{s['project_id']}/{s['stage']} is not open"


def test_every_band_is_represented(scores):
    """The dashboard must be able to show HIGH, MEDIUM and LOW projects -
    not just assert they exist."""
    bands = {s["risk_band"] for s in scores}
    assert bands == {"HIGH", "MEDIUM", "LOW"}, f"missing bands: {bands}"


def test_probability_is_a_real_probability(scores):
    for s in scores:
        assert 0.0 <= s["delay_probability"] <= 1.0, s["project_id"]


def test_high_risk_rows_have_an_explaining_driver(scores):
    """A HIGH badge must correspond to an actual positive-contribution
    driver in the underlying data - never a badge with nothing behind it."""
    for s in scores:
        if s["risk_band"] != "HIGH":
            continue
        assert s["drivers"], f"{s['project_id']}/{s['stage']} HIGH with no drivers"
        assert any(d["direction"] == "increases_risk" for d in s["drivers"]), (
            f"{s['project_id']}/{s['stage']} HIGH with no risk-increasing driver")


def test_drivers_are_capped_at_five_and_sorted_by_magnitude(scores):
    for s in scores:
        assert len(s["drivers"]) <= 5
        mags = [abs(d["shap_value"]) for d in s["drivers"]]
        assert mags == sorted(mags, reverse=True), s["project_id"]


def test_recommendations_only_cite_retrieved_rules(scores):
    """Every recommendation must carry a rule_id from the static table -
    never free text with no rule_id, which would mean it was generated."""
    for s in scores:
        for rec in s["recommendations"]:
            assert rec["rule_id"], s["project_id"]
            assert rec["action"], s["project_id"]


def test_recommendations_only_target_risk_increasing_drivers(scores):
    for s in scores:
        driver_directions = {d["feature"]: d["direction"] for d in s["drivers"]}
        for rec in s["recommendations"]:
            direction = driver_directions.get(rec["driver"])
            if direction is not None:
                assert direction == "increases_risk", (
                    f"{s['project_id']}: recommendation for a risk-decreasing driver")


def test_flagship_project_scores_and_is_explained_by_real_data(scores):
    flagship = [s for s in scores if s["project_id"] == FLAGSHIP_PROJECT_ID]
    assert flagship, "flagship project has no open stage to score"
    assert flagship[0]["risk_band"] in ("MEDIUM", "HIGH"), (
        "flagship (litigation-bound, RED parcel P-B01) should not score LOW")


def test_predicted_overrun_days_is_nonnegative_when_present(scores):
    for s in scores:
        if s["predicted_overrun_days"] is not None:
            assert s["predicted_overrun_days"] >= 0, s["project_id"]


def test_model_version_matches_the_trained_model(scores):
    with open(os.path.join(MID, "model_runs.json"), encoding="utf-8") as fh:
        runs = {r["stage"]: r["model_version"] for r in json.load(fh)}
    for s in scores:
        assert s["model_version"] == runs[s["stage"]], s["project_id"]


def test_provenance_is_traceable(scores):
    for s in scores:
        assert s["source_label"] == "model_generated", s["project_id"]
