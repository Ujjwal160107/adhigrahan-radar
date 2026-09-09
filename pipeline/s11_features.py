"""s11 - build the per-(project, stage) feature matrix (20 features, four
families) that s12 trains on and s13 scores.

Leakage discipline (the load-bearing rule of this stage):

  Every feature value is computed as of an `observation point`, never as of
  "the final, fully-known state of the world":

  - Open stages (completed_on is None) observe at TODAY - the real
    scoring moment. No leakage risk: nothing about an open stage's future
    is known yet, because it has none.
  - Closed (training) stages observe at a point strictly *before* they
    completed - `started_on + uniform(0.1, 0.95) * actual_duration`, seeded
    per (project_id, stage) so it is reproducible. This is a standard
    partial-observation simulation for training a duration/delay
    classifier on already-finished cases: it guarantees the model is never
    handed the completion date (or a value that deterministically implies
    it) for a row it is being asked to predict the completion of.
  - Litigation features are NOT read from `Parcel.status` (a static,
    fully-informed value computed once from the *final* case data by s5).
    They are recomputed here from CourtCase.filing_date/order_date/status
    restricted to "known as of the observation point", exactly the
    `computed_asof` discipline the spec requires: a case that was filed
    after a stage's observation point cannot possibly have influenced a
    decision made at that point, so it is excluded, not merely down-
    weighted.
  - District-context features (family D) are computed only from rows whose
    observation point falls before a single shared `cutoff_date` (written
    to data/intermediate/cutoff_date.json for s12 to reuse verbatim), so
    the train/test boundary and the feature computation boundary can never
    drift apart.

tests/test_features.py::test_no_future_leakage and
::test_district_context_train_only enforce this mechanically, not just by
convention.

Output: data/input/features.parquet (the ML-internal artifact; not part of
the DB contract - s6/s14 never read it back).
"""
import json
import os
import random
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from common import DATA_IN, DATA_MID, DB, RISK_SEED, TODAY, report

TODAY_D = date.fromisoformat(TODAY)
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
    # D. district context (3) - training-split only
    "district_median_3a_to_3d_days", "district_active_land_cases",
    "district_completed_projects",
]
assert len(FEATURE_COLUMNS) == 20, f"feature cap violated: {len(FEATURE_COLUMNS)}"


def _obs_date(stage_row, seed_key):
    """The leakage-safe observation point for one stage row (see module
    docstring). Returns a `date`."""
    started = date.fromisoformat(stage_row["started_on"])
    if stage_row["completed_on"] is None:
        return TODAY_D
    completed = date.fromisoformat(stage_row["completed_on"])
    duration = (completed - started).days
    rng = random.Random(f"{RISK_SEED}:{seed_key}")
    frac = rng.uniform(0.10, 0.95)
    obs = started + timedelta(days=max(1, round(duration * frac)))
    return min(obs, completed - timedelta(days=1))


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
    # district-context aggregates (spec 6.1/5.4): the date at the 75th
    # percentile of closed-stage completions, so training sees the
    # majority of the corpus's history and testing sees a genuine future
    # slice, not a random shuffle of it.
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
    # District-wide active-litigation count as of the cutoff (Sultanpur only
    # has any litigation corpus at all; every other district is 0 by
    # construction, never a fabricated estimate).
    sultanpur_active_asof_cutoff = len({
        link["case_id"]
        for links in litigation_by_parcel.values() for link in links
        if _case_known_asof(link, cutoff) == (True, True)
    })

    feature_rows = []
    for project_id, rows in stages_by_project.items():
        p = projects_by_id[project_id]
        parcel_ids = parcels_by_project.get(project_id, [])
        for idx, stage_row in enumerate(rows):
            obs = _obs_date(stage_row, seed_key=f"{project_id}:{stage_row['stage']}")
            lit = _litigation_features(parcel_ids, litigation_by_parcel, interim_dates, obs)

            prior_overruns = sum(1 for r in rows[:idx] if r["is_delayed"] == 1)
            days_in_stage = (obs - date.fromisoformat(stage_row["started_on"])).days

            comp_stage = next((r for r in rows if r["stage"] == "compensation_disbursed"), None)
            if comp_stage is None:
                comp_share = 0.0
            else:
                c_started = date.fromisoformat(comp_stage["started_on"])
                if obs < c_started:
                    comp_share = 0.0
                else:
                    comp_share = min(1.0, round(
                        (obs - c_started).days / comp_stage["statutory_days"], 4))

            n_villages = len({village_by_parcel.get(pid) for pid in parcel_ids} - {None}) \
                or len(p["villages"])

            row = {
                "project_id": project_id, "stage": stage_row["stage"],
                "computed_asof": obs.isoformat(),
                "is_delayed": stage_row["is_delayed"],  # label, carried for s12/s13 to split off
                "is_censored": stage_row["completed_on"] is None,
                "district": p["district"], "source_label": "model_generated",
                **lit,
                "litigation_coverage": 1 if p["district"] == "Sultanpur" else 0,
                "area_hectares": p["area_hectares"], "n_parcels": len(parcel_ids),
                "n_villages": n_villages, "affected_families": p["affected_families"],
                "days_in_current_stage": days_in_stage,
                "n_prior_stage_overruns": prior_overruns,
                "gazette_republication_count": p["gazette_republication_count"],
                "compensation_disbursed_share": comp_share,
                "district_median_3a_to_3d_days": district_median_days.get(p["district"]),
                "district_active_land_cases": (
                    sultanpur_active_asof_cutoff if p["district"] == "Sultanpur" else 0),
                "district_completed_projects": district_completed.get(p["district"], 0),
            }
            feature_rows.append(row)

    df = pd.DataFrame(feature_rows)
    ordered = (["project_id", "stage", "computed_asof", "is_delayed", "is_censored",
               "district", "source_label"] + FEATURE_COLUMNS)
    df = df[ordered]
    os.makedirs(DATA_IN, exist_ok=True)
    df.to_parquet(os.path.join(DATA_IN, "features.parquet"), index=False)

    report("s11", {
        "rows": len(df), "features": len(FEATURE_COLUMNS),
        "cutoff_date": cutoff.isoformat(),
        "closed_rows": int((~df.is_censored).sum()), "open_rows": int(df.is_censored.sum()),
        "rows_with_litigation_coverage": int((df.litigation_coverage == 1).sum()),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    })


if __name__ == "__main__":
    run()
