"""s9 - read both acquisition parquets, validate the contract, compute the
per-stage derived fields (deadline_on, overdue_days, is_delayed). Fail
loudly, exactly like s1: a half-built DB the night before a demo is worse
than no DB.

Outputs: data/intermediate/acquisitions.json, project_stages.json
"""
import json
import os
from datetime import date, timedelta

import pandas as pd
from common import (
    ACQUISITION_DISTRICTS,
    DATA_IN,
    DATA_MID,
    FLAGSHIP_PROJECT_ID,
    PROVENANCE,
    STAGE_CLOCKS,
    TODAY,
    ContractError,
    report,
)

ACQ_COLS = ["project_id", "name", "project_type", "executing_agency", "act",
            "state", "district", "block", "villages", "nh_no", "gazette_ref",
            "area_hectares", "affected_families", "budget_estimate_inr",
            "status", "gazette_republication_count", "source_label"]
STAGE_COLS = ["project_id", "stage", "stage_order", "statutory_days",
              "clock_source", "clock_authority", "started_on", "completed_on",
              "source_label"]
VALID_ACTS = {"NH_1956", "RFCTLARR_2013"}
VALID_STATUS = {"open", "completed", "lapsed"}
TODAY_D = date.fromisoformat(TODAY)


def _load(fname, cols):
    path = os.path.join(DATA_IN, fname)
    if not os.path.exists(path):
        raise ContractError("missing contract file: " + fname)
    df = pd.read_parquet(path)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ContractError(fname + " missing columns: " + str(missing))
    if df.source_label.isna().any():
        raise ContractError(fname + " has rows with no provenance label")
    bad = set(df.source_label.dropna().unique()) - PROVENANCE
    if bad:
        raise ContractError(fname + " bad provenance labels: " + str(bad))
    return df


def run():
    acq = _load("acquisitions.parquet", ACQ_COLS)
    stages = _load("project_stages.parquet", STAGE_COLS)

    if acq.project_id.duplicated().any():
        raise ContractError("acquisitions.project_id is not unique - it is the primary key")
    dup_stage = stages.duplicated(["project_id", "stage"])
    if dup_stage.any():
        raise ContractError("project_stages has duplicate (project_id, stage) rows")

    stray_district = set(acq.district.dropna().unique()) - set(ACQUISITION_DISTRICTS)
    if stray_district:
        raise ContractError("acquisitions has districts outside the contracted set: "
                            + str(stray_district))
    stray_act = set(acq.act.dropna().unique()) - VALID_ACTS
    if stray_act:
        raise ContractError("acquisitions has unknown act values: " + str(stray_act))
    stray_status = set(acq.status.dropna().unique()) - VALID_STATUS
    if stray_status:
        raise ContractError("acquisitions has unknown status values: " + str(stray_status))

    unresolvable = set(stages.stage.dropna().unique()) - set(STAGE_CLOCKS)
    if unresolvable:
        raise ContractError("project_stages has stages with no resolvable statutory "
                            "clock: " + str(unresolvable))

    orphan = set(stages.project_id.unique()) - set(acq.project_id.unique())
    if orphan:
        raise ContractError("project_stages references unknown project_id(s): "
                            + str(orphan)[:200])

    if FLAGSHIP_PROJECT_ID not in set(acq.project_id):
        raise ContractError("flagship project absent: " + FLAGSHIP_PROJECT_ID)

    # A completed stage must actually finish after it started - the
    # equivalent of s1's "disposed case with a future hearing" tell.
    closed = stages[stages.completed_on.notna()]
    backwards = closed[pd.to_datetime(closed.completed_on) < pd.to_datetime(closed.started_on)]
    if len(backwards):
        raise ContractError("stages complete before they start: "
                            + str(list(backwards.project_id)[:3]))

    # ---- derive deadline_on / overdue_days / is_delayed (pure functions of
    # started_on, completed_on, statutory_days - computed here, once, so s10
    # onward never recomputes them differently) ----
    stage_rows = []
    for r in stages.itertuples():
        started = pd.Timestamp(r.started_on).date()
        clock_days = int(r.statutory_days)
        deadline = started + timedelta(days=clock_days)
        if pd.notna(r.completed_on):
            completed = pd.Timestamp(r.completed_on).date()
            is_delayed = 1 if completed > deadline else 0
            overdue_days = max(0, (completed - deadline).days)
        else:
            completed = None
            is_delayed = None  # right-censored - never coerced to 0
            overdue_days = max(0, (TODAY_D - deadline).days)
        stage_rows.append({
            "project_id": r.project_id, "stage": r.stage, "stage_order": int(r.stage_order),
            "statutory_days": clock_days, "clock_source": r.clock_source,
            "clock_authority": r.clock_authority, "started_on": r.started_on,
            "completed_on": r.completed_on, "deadline_on": deadline.isoformat(),
            "overdue_days": overdue_days, "is_delayed": is_delayed,
            "source_label": r.source_label,
        })

    by_project = {}
    for row in stage_rows:
        by_project.setdefault(row["project_id"], []).append(row)
    for rows in by_project.values():
        rows.sort(key=lambda r: r["stage_order"])

    project_rows = []
    for r in acq.itertuples():
        rows = by_project.get(r.project_id, [])
        last = rows[-1] if rows else None
        project_rows.append({
            "project_id": r.project_id, "name": r.name, "project_type": r.project_type,
            "executing_agency": r.executing_agency, "act": r.act, "state": r.state,
            "district": r.district, "block": r.block,
            "villages": [v for v in (r.villages or "").split(",") if v],
            "nh_no": r.nh_no,
            "gazette_ref": r.gazette_ref, "area_hectares": float(r.area_hectares),
            "affected_families": int(r.affected_families),
            "budget_estimate_inr": float(r.budget_estimate_inr),
            "status": r.status,
            "gazette_republication_count": int(r.gazette_republication_count),
            "current_stage": last["stage"] if last else None,
            "stage_entered_on": last["started_on"] if last else None,
            "source_label": r.source_label,
        })

    # is_delayed must never silently read as "on time" for a stage that has
    # not finished (handoff rule, mirrors PRD 21's next_hearing discipline).
    censored = [r for r in stage_rows if r["completed_on"] is None]
    if any(r["is_delayed"] is not None for r in censored):
        raise ContractError("an open stage carries a non-null is_delayed")

    os.makedirs(DATA_MID, exist_ok=True)
    with open(os.path.join(DATA_MID, "acquisitions.json"), "w", encoding="utf-8") as fh:
        json.dump(project_rows, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(DATA_MID, "project_stages.json"), "w", encoding="utf-8") as fh:
        json.dump(stage_rows, fh, ensure_ascii=False, indent=1)

    report("s9", {
        "projects": len(project_rows), "stage_rows": len(stage_rows),
        "districts": len(set(p["district"] for p in project_rows)),
        "open_stages": sum(1 for r in stage_rows if r["completed_on"] is None),
        "closed_stages": sum(1 for r in stage_rows if r["completed_on"] is not None),
        "delayed_closed_stages": sum(1 for r in stage_rows if r["is_delayed"] == 1),
        "contract": "ok",
    })


if __name__ == "__main__":
    run()
