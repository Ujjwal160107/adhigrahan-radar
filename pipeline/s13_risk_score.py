"""s13 - score every open (project, stage) with its shipped s12 model,
explain each score with real SHAP values, and attach retrieved
recommendations. Everything here runs offline; the request path only ever
SELECTs the result (PRD 36/48).

Explainability: `shap.Explainer` wraps each shipped model's `predict_proba`
directly rather than assuming a tree model - the shipped algo differs by
stage (logistic_regression won most stages in this corpus's own holdout
comparison, one stage shipped base_rate), so a `TreeExplainer` alone would
not apply uniformly. Wrapping the callable is genuine SHAP (a real Shapley-
value approximation, not a hand-rolled substitute) and is correct for any
of the three algo types s12 can ship.

`predicted_overrun_days` is explicitly not a second model: for a stage's
OPEN row scored into risk_band B, it is the empirical median overrun
(actual duration - statutory_days) among that stage's CLOSED, delayed
training rows that were themselves scored into the same band B. Falls back
to the stage-wide median if a (stage, band) bucket has no delayed examples
- a small corpus will not populate every bucket, and that gap is reported,
not papered over with an invented number.

Output: data/intermediate/project_risk.json
"""
import json
import os
from datetime import UTC, date, datetime

import joblib
import numpy as np
import pandas as pd
import shap
from common import DATA_IN, DATA_MID, DATA_OUT, FLAGSHIP_PROJECT_ID, TODAY, report
from recommendations import recommend
from s11_features import FEATURE_COLUMNS

MODELS_DIR = os.path.join(DATA_OUT, "models")
TODAY_D = date.fromisoformat(TODAY)

FEATURE_LABELS = {
    "share_parcels_red": "Share of parcels under active litigation",
    "share_parcels_amber": "Share of parcels with a possible litigation link",
    "n_active_cases": "Active court cases touching this project",
    "max_case_pendency_days": "Longest-pending active case (days)",
    "n_acquisition_compensation_cases": "Compensation-dispute cases on this land",
    "n_title_partition_cases": "Title/partition disputes on this land",
    "has_interim_order": "Active interim court order",
    "n_high_confidence_links": "High-confidence litigation links",
    "litigation_coverage": "Litigation records available for this district",
    "area_hectares": "Project area",
    "n_parcels": "Number of parcels",
    "n_villages": "Number of villages",
    "affected_families": "Affected families",
    "days_in_current_stage": "Days already spent in this stage",
    "n_prior_stage_overruns": "Earlier stages that overran their clock",
    "gazette_republication_count": "3A notification republications",
    "compensation_disbursed_share": "Compensation disbursed so far",
    "district_median_3a_to_3d_days": "District median 3A-to-3D duration",
    "district_completed_projects": "District projects completed to date",
}

# Every driver s13 renders is looked up by name in FEATURE_LABELS, so a
# feature with no label is a KeyError in the middle of scoring - after
# training, with the build half done. Pin it at import instead. The reverse
# direction is checked too: `district_active_land_cases` sat here for a
# release after s11 stopped emitting it, describing a driver no officer
# could ever be shown.
assert set(FEATURE_LABELS) == set(FEATURE_COLUMNS), (
    "FEATURE_LABELS and s11.FEATURE_COLUMNS disagree: "
    f"unlabelled={sorted(set(FEATURE_COLUMNS) - set(FEATURE_LABELS))}, "
    f"orphaned={sorted(set(FEATURE_LABELS) - set(FEATURE_COLUMNS))}")


def _impute(X, medians):
    X = X.copy()
    for col in X.columns:
        if X[col].isna().any():
            X[col] = X[col].fillna(medians.get(col, 0.0))
    return X


def _risk_band(p, thresholds):
    t_high = thresholds.get("t_high")
    t_med = thresholds.get("t_med")
    if t_high is not None and p >= t_high:
        return "HIGH"
    if t_med is not None and p >= t_med:
        return "MEDIUM"
    return "LOW"


def _overrun_lookup(closed_df, bundle, thresholds):
    """(risk_band -> median overrun days) for one stage's delayed closed
    rows, scored with the same shipped model so the bucket a training row
    falls into matches how an open row would be scored."""
    if closed_df.empty:
        return {}, None
    X = _impute(closed_df[FEATURE_COLUMNS], bundle["medians"])
    p = bundle["model"].predict_proba(X)[:, 1]
    bands = [_risk_band(x, thresholds) for x in p]
    overrun = (pd.to_datetime(closed_df["completed_on"])
              - pd.to_datetime(closed_df["started_on"])).dt.days - closed_df["statutory_days"]
    df = pd.DataFrame({"band": bands, "overrun": overrun.values,
                       "delayed": closed_df["is_delayed"].values})
    delayed = df[(df.delayed == 1) & (df.overrun > 0)]
    by_band = delayed.groupby("band")["overrun"].median().to_dict()
    overall = float(delayed["overrun"].median()) if len(delayed) else None
    return {k: float(v) for k, v in by_band.items()}, overall


def _score_stage(stage, open_rows, closed_rows):
    path = os.path.join(MODELS_DIR, f"{stage}.joblib")
    if not os.path.exists(path) or open_rows.empty:
        return []
    bundle = joblib.load(path)
    thresholds = bundle["thresholds"]

    X_open = _impute(open_rows[FEATURE_COLUMNS], bundle["medians"])
    p_open = bundle["model"].predict_proba(X_open)[:, 1]

    overrun_by_band, overrun_overall = _overrun_lookup(closed_rows, bundle, thresholds)

    # Background for SHAP: up to 30 training/closed rows for this stage, or
    # the open rows themselves if the stage has no closed history at all.
    bg_source = closed_rows if not closed_rows.empty else open_rows
    background = _impute(bg_source[FEATURE_COLUMNS], bundle["medians"])
    background = background.sample(min(30, len(background)), random_state=0)
    explainer = shap.Explainer(lambda z: bundle["model"].predict_proba(z)[:, 1], background)
    shap_values = explainer(X_open).values

    out = []
    for i, (_, row) in enumerate(open_rows.iterrows()):
        p = float(p_open[i])
        band = _risk_band(p, thresholds)
        sv = shap_values[i]
        order = np.argsort(-np.abs(sv))[:5]
        drivers = [{
            "feature": FEATURE_COLUMNS[j],
            "label": FEATURE_LABELS[FEATURE_COLUMNS[j]],
            "shap_value": round(float(sv[j]), 5),
            "direction": "increases_risk" if sv[j] > 0 else "decreases_risk",
            "value": (float(row[FEATURE_COLUMNS[j]])
                     if pd.notna(row[FEATURE_COLUMNS[j]]) else None),
        } for j in order]

        overrun = overrun_by_band.get(band, overrun_overall)

        out.append({
            "project_id": row["project_id"], "stage": stage,
            "delay_probability": round(p, 4), "risk_band": band,
            "predicted_overrun_days": round(overrun) if overrun is not None else None,
            "model_version": bundle["model_version"],
            "scored_at": TODAY_D.isoformat(),
            "drivers": drivers,
            "recommendations": recommend(drivers),
            "source_label": "model_generated",
        })
    return out


def run():
    df = pd.read_parquet(os.path.join(DATA_IN, "features.parquet"))
    with open(os.path.join(DATA_MID, "project_stages.json"), encoding="utf-8") as fh:
        stage_list = json.load(fh)
    stage_rows = {(r["project_id"], r["stage"]): r for r in stage_list}
    stage_meta = pd.DataFrame(stage_list)[
        ["project_id", "stage", "started_on", "completed_on", "statutory_days"]]
    df = df.merge(stage_meta, on=["project_id", "stage"], how="left")

    # A closed stage now contributes one row per statutory landmark it
    # survived to. Those rows are not independent stages: keep only the
    # latest landmark of each, so the empirical overrun lookup and the SHAP
    # background weight every historical stage exactly once instead of
    # over-weighting the stages that happened to survive more landmarks.
    closed_latest = (df[~df.is_serving_row]
                     .sort_values("landmark_fraction")
                     .drop_duplicates(subset=["project_id", "stage"], keep="last"))

    scores = []
    for stage in df.stage.unique():
        open_rows = df[(df.stage == stage) & (df.is_serving_row)]
        closed_rows = closed_latest[closed_latest.stage == stage]
        scores.extend(_score_stage(stage, open_rows, closed_rows))

    # lead_time_days = deadline_on - scored_at, using the real ProjectStage
    # deadline (already computed by s9), not the feature row's obs date.
    for s in scores:
        stage_row = stage_rows[(s["project_id"], s["stage"])]
        deadline = date.fromisoformat(stage_row["deadline_on"])
        s["lead_time_days"] = (deadline - TODAY_D).days

    os.makedirs(DATA_MID, exist_ok=True)
    with open(os.path.join(DATA_MID, "project_risk.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh, indent=1, default=str)

    band_counts = {}
    for s in scores:
        band_counts[s["risk_band"]] = band_counts.get(s["risk_band"], 0) + 1
    flagship = next((s for s in scores if s["project_id"] == FLAGSHIP_PROJECT_ID), None)
    report("s13", {
        "scored_stages": len(scores), "band_counts": band_counts,
        "median_lead_time_days": (
            float(np.median([s["lead_time_days"] for s in scores])) if scores else None),
        "flagship_band": flagship["risk_band"] if flagship else None,
        "flagship_probability": flagship["delay_probability"] if flagship else None,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    })


if __name__ == "__main__":
    run()
