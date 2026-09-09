"""s11 - build the per-(project, stage, landmark) feature matrix that s12
trains on and s13 scores.

Leakage discipline (the load-bearing rule of this stage):

  Every feature value is computed as of an `observation point`, and that
  observation point is chosen WITHOUT reference to the outcome being
  predicted. This is the rule an earlier revision of this stage broke, and
  the reason the whole file is structured around landmarks now.

  The broken design: a closed stage was observed at
  `started_on + uniform(0.10, 0.95) * actual_duration`. That date is before
  completion, so it looked leakage-safe and the old test asserted exactly
  that. But it is a *function of the actual duration*, so the derived
  feature `days_in_current_stage` was literally `round(duration * frac)` -
  the label's own quantity scaled by noise, since `is_delayed` is
  `duration > statutory_days` and `statutory_days` is a per-stage constant.
  Measured on the real corpus, `days_in_current_stage` alone scored
  ROC-AUC 0.64-0.81 with no model at all. "Observed before completion" is
  not the same as "independent of completion".

  The landmark design (this file): a stage is observed at fixed offsets
  driven by the STATUTORY clock, never by what actually happened -
  `started_on + f * statutory_days` for f in LANDMARK_FRACTIONS. A row is
  emitted only if the stage was still open at that landmark, which is
  exactly the condition under which s13 scores a row in production. So:

  - the observation point cannot encode the outcome: `statutory_days` is a
    constant fixed by law/policy, identical for every project at a stage;
  - `days_in_current_stage` equals the landmark offset itself and is drawn
    from the same distribution at training time and at serving time, so
    the train/serve skew is gone too;
  - conditioning on "still open at the landmark" is the serving condition,
    not a filter on the outcome (survival/landmarking, the standard way to
    train a discrete-time overrun classifier).

  Landmarks are strictly BELOW 1.0: a stage still open at
  1.0 * statutory_days has already breached its deadline, so its label is
  1 by definition and the row would be tautological, not predictive.

  Two further as-of rules the earlier revision got wrong or omitted:

  - Litigation features are NOT read from `Parcel.status` (a static,
    fully-informed value computed once from the *final* case data by s5).
    They are recomputed here from CourtCase.filing_date/order_date/status
    restricted to "known as of the observation point": a case filed after
    a landmark cannot have influenced a decision made at that landmark, so
    it is excluded, not merely down-weighted.
  - `n_prior_stage_overruns` counts only prior stages that had actually
    CLOSED by the observation point. The earlier revision read every prior
    stage's final verdict regardless of when it landed - a verdict the
    officer could not have known at that moment.
  - District-context features (family D) are computed only from rows whose
    outcome was known before a single shared `cutoff_date` (written to
    data/intermediate/cutoff_date.json for s12 to reuse verbatim), so the
    train/test boundary and the feature computation boundary can never
    drift apart.

Coverage confound: the real litigation corpus (s0-s7) covers one district.
The former `district_active_land_cases` feature was
`<count> if district == 'Sultanpur' else 0` - a rescaled district dummy
carrying no information beyond `litigation_coverage`, which already says
the same thing. It is dropped rather than kept as a second copy of the
same indicator.

`litigation_coverage` itself was left as `1 if district == 'Sultanpur'
else 0`, i.e. the same hardcoded district literal the feature above was
deleted for. It is now derived from the districts that actually have
parcels in vivaad.db. That matters beyond tidiness: the flag exists to
say "no litigation signal is available here", and the moment a second
district is ingested the literal would keep asserting no-coverage over a
district whose `share_parcels_red` was non-zero - the model would be told
the evidence it is being shown does not exist.

`district_median_3a_to_3d_days` is left NULL (not 0) for a district with
no closed history, because unknown is not zero.

tests/test_features.py enforces all of the above mechanically, not just by
convention - in particular ::test_observation_point_is_independent_of_outcome,
the regression test for the leak described at the top.

Output: data/input/features.parquet (the ML-internal artifact; not part of
the DB contract - s6/s14 never read it back).
"""
import json
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from common import DATA_IN, DATA_MID, DB, STAGE_CLOCKS, TODAY, report

TODAY_D = date.fromisoformat(TODAY)

# Fractions of a stage's STATUTORY clock at which a still-open stage is
# observed. 0.0 is the day the stage opened - the moment a freshly gazetted
# notification first appears on the dashboard, and where every real open
# row in the first harvest sat (all within a week of TODAY): without it
# the model was scored at elapsed times it had never seen, since the
# earliest landmark was day 91. Elapsed time is zero for every row there,
# so it carries no label information and no survival selection - exactly
# the serving situation for a new notification. Strictly < 1.0 at the top
# - see the module docstring.
LANDMARK_FRACTIONS = (0.0, 0.25, 0.50, 0.75)

FEATURE_COLUMNS = [
    # A. litigation (9) - the differentiator
    "share_parcels_red", "share_parcels_amber", "n_active_cases",
    "max_case_pendency_days", "n_acquisition_compensation_cases",
    "n_title_partition_cases", "has_interim_order", "n_high_confidence_links",
    "litigation_coverage",
    # B. project intrinsics (4)
    "area_hectares", "n_parcels", "n_villages", "affected_families",
    # C. administrative (4)
    "days_in_current_stage", "n_prior_stage_overruns",
    "gazette_republication_count", "compensation_disbursed_share",
    # D. district context (2) - training-split only
    "district_median_3a_to_3d_days", "district_completed_projects",
]
assert len(FEATURE_COLUMNS) == 19, f"feature cap violated: {len(FEATURE_COLUMNS)}"


def landmark_dates(stage_row):
    """The leakage-safe observation points for one stage row.

    Yields (landmark_fraction, obs_date) for every landmark at which the
    stage was still open - and, for a closed stage, strictly before it
    completed. Offsets come from the statutory clock only; `completed_on`
    is read solely to decide whether the stage had already ended by then
    (the serving condition), never to place the observation point.
    """
    started = date.fromisoformat(stage_row["started_on"])
    statutory = STAGE_CLOCKS[stage_row["stage"]]["statutory_days"]
    completed = (date.fromisoformat(stage_row["completed_on"])
                 if stage_row["completed_on"] else None)
    for frac in LANDMARK_FRACTIONS:
        obs = started + timedelta(days=int(round(statutory * frac)))
        if obs > TODAY_D:
            continue                      # the landmark is in the future
        if completed is not None and obs >= completed:
            continue                      # stage had already closed by then
        yield frac, obs


def _case_known_asof(case, obs):
    """A case exists in the record as of `obs` only once it has actually
    been filed - the hard leakage gate. 'Still active as of obs' is then
    reconstructed from the only history we have: it is active at `obs` if
    it is active today, or if it was disposed only *after* `obs`."""
    filing = case["filing_date"]
    if not filing or date.fromisoformat(filing) > obs:
        return False, False
    if case["status"] == "active":
        return True, True
    order = case["order_date"]
    active_asof = bool(order) and date.fromisoformat(order) > obs
    return True, active_asof


def _load_litigation_index(con):
    """One row per (parcel_id, case) with everything s11 needs to
    recompute a leakage-safe status as of an arbitrary earlier date."""
    rows = con.execute("""
        SELECT l.parcel_id, l.confidence_band, l.identifier_match,
               c.id AS case_id, c.case_type, c.filing_date, c.order_date, c.status
        FROM ParcelCaseLink l JOIN CourtCase c ON c.id = l.case_id
        WHERE l.confidence_band IN ('HIGH', 'MEDIUM')
    """).fetchall()
    by_parcel = {}
    for r in rows:
        by_parcel.setdefault(r["parcel_id"], []).append(dict(r))
    return by_parcel


def _interim_order_dates(con):
    out = {}
    for r in con.execute(
            "SELECT case_id, date FROM CourtEvent WHERE event_type='interim_order'"):
        out.setdefault(r["case_id"], []).append(r["date"])
    return out


def _litigation_features(parcel_ids, litigation_by_parcel, interim_dates, obs):
    """Recomputes RED/AMBER shares and the litigation feature block as of
    `obs`, using only cases known-and-active per `_case_known_asof`."""
    if not parcel_ids:
        return dict(share_parcels_red=0.0, share_parcels_amber=0.0,
                    n_active_cases=0, max_case_pendency_days=0,
                    n_acquisition_compensation_cases=0, n_title_partition_cases=0,
                    has_interim_order=0, n_high_confidence_links=0)
    red = amber = 0
    active_cases, pendencies = set(), []
    n_comp, n_title, n_high = 0, 0, 0
    has_interim = 0
    for pid in parcel_ids:
        best = "GREEN"
        for link in litigation_by_parcel.get(pid, []):
            known, active = _case_known_asof(link, obs)
            if not known or not active:
                continue
            active_cases.add(link["case_id"])
            pendencies.append((obs - date.fromisoformat(link["filing_date"])).days)
            if link["case_type"] == "acquisition_compensation":
                n_comp += 1
            if link["case_type"] in ("partition", "title_declaration"):
                n_title += 1
            if link["confidence_band"] == "HIGH":
                n_high += 1
                for d in interim_dates.get(link["case_id"], []):
                    if date.fromisoformat(d) <= obs:
                        has_interim = 1
            if link["confidence_band"] == "HIGH" and link["identifier_match"] != "none":
                best = "RED"
            elif best != "RED":
                best = "AMBER"
        if best == "RED":
            red += 1
        elif best == "AMBER":
            amber += 1
    n = len(parcel_ids)
    return dict(
        share_parcels_red=round(red / n, 4), share_parcels_amber=round(amber / n, 4),
        n_active_cases=len(active_cases),
        max_case_pendency_days=max(pendencies) if pendencies else 0,
        n_acquisition_compensation_cases=n_comp, n_title_partition_cases=n_title,
        has_interim_order=has_interim, n_high_confidence_links=n_high,
    )


def run():
    with open(os.path.join(DATA_MID, "acquisitions.json"), encoding="utf-8") as fh:
        projects = json.load(fh)
    with open(os.path.join(DATA_MID, "project_stages.json"), encoding="utf-8") as fh:
        stages = json.load(fh)
    with open(os.path.join(DATA_MID, "project_parcels.json"), encoding="utf-8") as fh:
        bindings = json.load(fh)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    litigation_by_parcel = _load_litigation_index(con)
    interim_dates = _interim_order_dates(con)
    village_by_parcel = {r["id"]: r["village_canon"]
                        for r in con.execute("SELECT id, village_canon FROM Parcel")}
    # Which districts the linkage engine actually covers, read from the
    # corpus rather than named in code - see the module docstring.
    litigation_districts = {
        r["district"] for r in con.execute(
            "SELECT DISTINCT district FROM Parcel WHERE district IS NOT NULL")}
    con.close()

    projects_by_id = {p["project_id"]: p for p in projects}
    parcels_by_project = {}
    for b in bindings:
        parcels_by_project.setdefault(b["project_id"], []).append(b["parcel_id"])
    stages_by_project = {}
    for s in stages:
        stages_by_project.setdefault(s["project_id"], []).append(s)
    for rows in stages_by_project.values():
        rows.sort(key=lambda r: r["stage_order"])

    # ---- shared cutoff for the time-based split and the train-only
    # district-context aggregates: the date at the 75th percentile of
    # closed-stage completions, so training sees the majority of the
    # corpus's history and testing sees a genuine future slice, not a
    # random shuffle of it. s12 splits whole STAGES on this date (a stage's
    # landmark rows must never straddle the boundary), so it is defined
    # here in terms of when an outcome became known.
    closed_dates = sorted(date.fromisoformat(s["completed_on"]) for s in stages
                          if s["completed_on"])
    cutoff = closed_dates[int(len(closed_dates) * 0.75)] if closed_dates else TODAY_D
    with open(os.path.join(DATA_MID, "cutoff_date.json"), "w", encoding="utf-8") as fh:
        json.dump({"cutoff_date": cutoff.isoformat()}, fh)

    # District context source rows, restricted to the training window only.
    district_notif_days = {}
    district_completed = {}
    for p in projects:
        rows = stages_by_project.get(p["project_id"], [])
        notif = next((r for r in rows if r["stage"] == "notification_3a_11"), None)
        if notif and notif["completed_on"] and date.fromisoformat(notif["completed_on"]) <= cutoff:
            d = (date.fromisoformat(notif["completed_on"])
                - date.fromisoformat(notif["started_on"])).days
            district_notif_days.setdefault(p["district"], []).append(d)
        last = rows[-1] if rows else None
        if (p["status"] == "completed" and last and last["completed_on"]
                and date.fromisoformat(last["completed_on"]) <= cutoff):
            district_completed[p["district"]] = district_completed.get(p["district"], 0) + 1

    def median(xs):
        xs = sorted(xs)
        n = len(xs)
        if not n:
            return None
        mid = n // 2
        return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2

    district_median_days = {d: median(v) for d, v in district_notif_days.items()}

    def build_row(p, project_id, stage_row, rows, idx, parcel_ids,
                  frac, obs, is_serving):
        """One feature row for one (project, stage) at one observation
        point. `is_serving` marks the single row s13 scores for an open
        stage; training rows are the landmark rows of closed stages."""
        lit = _litigation_features(parcel_ids, litigation_by_parcel, interim_dates, obs)

        # Only prior stages that had actually CLOSED by `obs` have a delay
        # verdict the officer could have known at that moment.
        prior_overruns = sum(
            1 for r in rows[:idx]
            if r["is_delayed"] == 1 and r["completed_on"]
            and date.fromisoformat(r["completed_on"]) <= obs)

        days_in_stage = (obs - date.fromisoformat(stage_row["started_on"])).days

        # Progress through the compensation stage. Deliberately 0.0 when
        # the row being built IS that stage: elapsed-time-in-this-stage is
        # already `days_in_current_stage`, and dividing it by the same
        # stage's statutory clock would just be that feature rescaled by a
        # constant - perfectly collinear inside the per-stage model.
        comp_stage = next((r for r in rows if r["stage"] == "compensation_disbursed"), None)
        if comp_stage is None or comp_stage["stage"] == stage_row["stage"]:
            comp_share = 0.0
        else:
            c_started = date.fromisoformat(comp_stage["started_on"])
            if obs < c_started:
                comp_share = 0.0
            else:
                # Capped at the point the stage actually closed, so this can
                # never imply progress past an end date that `obs` predates.
                c_end = (date.fromisoformat(comp_stage["completed_on"])
                         if comp_stage["completed_on"] else obs)
                elapsed = (min(obs, c_end) - c_started).days
                comp_share = min(1.0, round(elapsed / comp_stage["statutory_days"], 4))

        n_villages = len({village_by_parcel.get(pid) for pid in parcel_ids} - {None}) \
            or len(p["villages"])

        return {
            "project_id": project_id, "stage": stage_row["stage"],
            "landmark_fraction": frac, "computed_asof": obs.isoformat(),
            "is_delayed": stage_row["is_delayed"],  # label, split off by s12/s13
            "is_censored": stage_row["completed_on"] is None,
            "is_serving_row": is_serving,
            "stage_completed_on": stage_row["completed_on"],
            # Where the STAGE came from (real gazette record vs generated),
            # carried so s12 can count and score the real slice of the
            # holdout on its own. The feature row itself is model_generated.
            "stage_source_label": stage_row["source_label"],
            "district": p["district"], "source_label": "model_generated",
            **lit,
            "litigation_coverage": 1 if p["district"] in litigation_districts else 0,
            "area_hectares": p["area_hectares"], "n_parcels": len(parcel_ids),
            "n_villages": n_villages, "affected_families": p["affected_families"],
            "days_in_current_stage": days_in_stage,
            "n_prior_stage_overruns": prior_overruns,
            "gazette_republication_count": p["gazette_republication_count"],
            "compensation_disbursed_share": comp_share,
            "district_median_3a_to_3d_days": district_median_days.get(p["district"]),
            "district_completed_projects": district_completed.get(p["district"], 0),
        }

    feature_rows = []
    for project_id, rows in stages_by_project.items():
        p = projects_by_id[project_id]
        parcel_ids = parcels_by_project.get(project_id, [])
        for idx, stage_row in enumerate(rows):
            if stage_row["completed_on"] is None:
                # Serving row: an open stage is scored once, at the real
                # build "now" - exactly what s13 does in production.
                feature_rows.append(build_row(
                    p, project_id, stage_row, rows, idx, parcel_ids,
                    frac=None, obs=TODAY_D, is_serving=True))
                continue
            # Training rows: every statutory landmark this stage survived to.
            for frac, obs in landmark_dates(stage_row):
                feature_rows.append(build_row(
                    p, project_id, stage_row, rows, idx, parcel_ids,
                    frac=frac, obs=obs, is_serving=False))

    df = pd.DataFrame(feature_rows)
    ordered = (["project_id", "stage", "landmark_fraction", "computed_asof",
               "is_delayed", "is_censored", "is_serving_row", "stage_completed_on",
               "stage_source_label", "district", "source_label"] + FEATURE_COLUMNS)
    df = df[ordered]
    os.makedirs(DATA_IN, exist_ok=True)
    df.to_parquet(os.path.join(DATA_IN, "features.parquet"), index=False)

    train_rows = df[~df.is_serving_row]
    stages_covered = train_rows.groupby(["project_id", "stage"]).ngroups
    report("s11", {
        "rows": len(df), "features": len(FEATURE_COLUMNS),
        "cutoff_date": cutoff.isoformat(),
        "landmark_fractions": list(LANDMARK_FRACTIONS),
        "training_rows": len(train_rows),
        "closed_stages_covered": int(stages_covered),
        "rows_per_covered_stage": (round(len(train_rows) / stages_covered, 2)
                                   if stages_covered else 0),
        "serving_rows": int(df.is_serving_row.sum()),
        "real_training_rows": int(((~df.is_serving_row)
                                   & (df.stage_source_label == "real")).sum()),
        "real_serving_rows": int((df.is_serving_row
                                  & (df.stage_source_label == "real")).sum()),
        "litigation_districts": ",".join(sorted(litigation_districts)),
        "rows_with_litigation_coverage": int((df.litigation_coverage == 1).sum()),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    })


if __name__ == "__main__":
    run()
