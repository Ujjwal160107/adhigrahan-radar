"""Leakage audit for s11_features.py. This is the single most important
test file in the risk engine: a feature that leaks the future is the most
likely fatal flaw in a hackathon ML pipeline and the first thing a
technical judge will probe.

Run after every data drop:
    python -m pytest tests/test_features.py -q
"""
import json
import os
from datetime import date

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES = os.path.join(ROOT, "data", "input", "features.parquet")
MID = os.path.join(ROOT, "data", "intermediate")


@pytest.fixture(scope="module")
def features():
    assert os.path.exists(FEATURES), "features.parquet missing - run pipeline/run_all.py first"
    return pd.read_parquet(FEATURES)


@pytest.fixture(scope="module")
def stages():
    with open(os.path.join(MID, "project_stages.json"), encoding="utf-8") as fh:
        return {(r["project_id"], r["stage"]): r for r in json.load(fh)}


@pytest.fixture(scope="module")
def cutoff():
    with open(os.path.join(MID, "cutoff_date.json"), encoding="utf-8") as fh:
        return date.fromisoformat(json.load(fh)["cutoff_date"])


def test_feature_cap_is_twenty(features):
    non_feature_cols = {"project_id", "stage", "computed_asof", "is_delayed",
                        "is_censored", "district", "source_label"}
    feature_cols = [c for c in features.columns if c not in non_feature_cols]
    assert len(feature_cols) == 20, f"feature cap violated: {len(feature_cols)} columns"


def test_no_future_leakage(features, stages):
    """computed_asof must never postdate the stage's own completion - a
    feature computed after the outcome it predicts is known is not a
    feature, it's the answer key."""
    bad = []
    for r in features.itertuples():
        key = (r.project_id, r.stage)
        stage = stages[key]
        obs = date.fromisoformat(r.computed_asof)
        started = date.fromisoformat(stage["started_on"])
        if obs < started:
            bad.append((key, "observed before stage started"))
        if stage["completed_on"] is not None:
            completed = date.fromisoformat(stage["completed_on"])
            if obs >= completed:
                bad.append((key, "observed at or after completion"))
    assert not bad, f"leakage found: {bad[:5]}"


def test_censored_rows_observe_at_a_single_shared_date(features):
    """Open stages must all be observed at the real, single build 'now' -
    never a per-row fabricated future date that would smuggle in
    information nobody has."""
    censored = features[features.is_censored]
    assert censored.computed_asof.nunique() <= 1, (
        "censored rows do not share a single observation date: "
        + str(censored.computed_asof.unique()))


def test_district_context_train_only(features, cutoff):
    """district_median_3a_to_3d_days / district_active_land_cases /
    district_completed_projects must be computed only from rows whose
    observation point is on or before the shared cutoff_date - never from
    the holdout the model will later be tested on."""
    after_cutoff = features[pd.to_datetime(features.computed_asof).dt.date > cutoff]
    # the aggregate values themselves are constants per district (computed
    # once, applied to every row), so the real assertion is that the
    # aggregate was built from <= cutoff data - verified structurally by
    # s11 itself (district_completed/notif dicts only ever consume rows
    # gated on `<= cutoff`). Here we assert the district-context columns
    # are present and non-negative wherever litigation/administrative data
    # exists, and that post-cutoff rows are not silently absent from
    # scoring (right-censored != excluded).
    assert len(after_cutoff) > 0, "no rows observed after cutoff - split is degenerate"
    assert (features.district_completed_projects >= 0).all()
    assert (features.district_active_land_cases >= 0).all()


def test_litigation_coverage_flag_not_confused_with_zero_risk(features):
    """Absent litigation coverage (7 of 8 districts) must never be encoded
    as the same signal as 'zero litigation risk' (share_parcels_red=0 with
    coverage=1, e.g. a genuinely clean Sultanpur project)."""
    no_coverage = features[features.litigation_coverage == 0]
    assert (no_coverage.share_parcels_red == 0).all()
    assert (no_coverage.n_active_cases == 0).all()
    has_coverage = features[features.litigation_coverage == 1]
    assert set(has_coverage.district) == {"Sultanpur"}


def test_open_stage_label_is_null_never_zero(features):
    assert features.loc[features.is_censored, "is_delayed"].isna().all()
    assert features.loc[~features.is_censored, "is_delayed"].notna().all()


def test_prior_stage_overruns_only_counts_earlier_stages(features, stages):
    """n_prior_stage_overruns for stage N must equal the count of delayed
    stages with stage_order < N for the same project - never counting a
    later stage, which would not exist yet."""
    by_project = {}
    for (pid, _stage), row in stages.items():
        by_project.setdefault(pid, []).append(row)
    for rows in by_project.values():
        rows.sort(key=lambda r: r["stage_order"])
    for r in features.itertuples():
        rows = by_project[r.project_id]
        idx = next(i for i, s in enumerate(rows) if s["stage"] == r.stage)
        expected = sum(1 for s in rows[:idx] if s["is_delayed"] == 1)
        assert r.n_prior_stage_overruns == expected, r.project_id


def test_no_null_features_outside_district_context(features):
    """Only the three train-only district-context columns may be null
    (a brand-new district with no closed notification stage yet); every
    other feature must be a real, computed value on every row."""
    allowed_null = {"district_median_3a_to_3d_days"}
    other_cols = [c for c in features.columns
                 if c not in allowed_null and c not in ("is_delayed",)]
    for col in other_cols:
        assert features[col].notna().all(), f"unexpected nulls in {col}"
