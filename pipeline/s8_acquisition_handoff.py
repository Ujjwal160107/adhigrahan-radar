"""s8 - build the acquisition data contract in data/input/ (mirrors s0).

Two files, exactly as contracted:
  acquisitions.parquet    one row per project
  project_stages.parquet  one row per (project, stage) actually reached

No real Bhoomi Rashi 3A/3D snapshot is available in this environment
(data/raw/bhoomirashi/ is an empty placeholder - nothing has been scraped or
cached there). Rather than pretend an unavailable government dataset was
used, every row here is generated and stamped `source_label='synthetic'`.
This is disclosed, not hidden: docs/plans/2026-09-09-adhigrahan-radar-
implementation-blueprint.md risk X-1 records the decision and its
consequence - s12's honesty rule ("only real data may appear in a reported
metric") means no metric trained on this corpus may be reported as if it
came from real government records. It exists so the *mechanism* - per-stage
calibrated delay prediction with litigation as a feature - is real,
reproducible and testable end to end, on labels that are causally
consistent with the features (a litigation-heavy project really does run
long in this corpus, not by coincidence but because the generator makes it
so), even though the corpus itself is not.

The real litigation corpus (s0-s7) covers one district, so that is the only
district where `litigation_coverage=1` is possible; the other seven exist to
satisfy the >=8-district scope target and deliberately carry no litigation
signal at all. Which district that is, is read out of the already-built
vivaad.db rather than branched on by name: `_load_village_pools` returns a
pool per district and a district with no pool simply gets no villages, so
extending the linkage corpus to a second district is a data change here,
not a code change.

Determinism: everything is drawn from `random.Random(RISK_SEED)`, seeded
once, consumed in a fixed order, so a rebuild reproduces byte-identical
parquet output.
"""
import json
import os
import random
import sqlite3
from datetime import date, timedelta

import pandas as pd
from common import (
    ACQUISITION_DISTRICTS,
    DATA_IN,
    DATA_MID,
    DB,
    DISTRICT,
    DISTRICT_ABBR,
    FLAGSHIP_PARCEL_RED,
    FLAGSHIP_PROJECT_ID,
    RISK_SEED,
    STAGE_CLOCKS,
    STAGE_ORDER,
    STATUS_RANK,
    TODAY,
    clock_authority,
    parcel_status,
    report,
)

TODAY_D = date.fromisoformat(TODAY)

PROJECT_TYPES = {
    "highway": ("NHAI", "NH_1956"),
    "railway": ("Indian Railways", "RFCTLARR_2013"),
    "irrigation": ("UP Irrigation Department", "RFCTLARR_2013"),
    "industrial": ("UPSIDC", "RFCTLARR_2013"),
    "transmission": ("UPPTCL", "RFCTLARR_2013"),
}

# Deliberate archetypes so every contrasting demo scenario required by the
# spec (healthy, approaching-deadline, litigation-driven, admin-driven,
# overdue, lapsed, multi-parcel) is guaranteed to exist, not left to chance.
# Weights are the background mix; specific slots below force at least one
# of each regardless of the draw.
ARCHETYPE_WEIGHTS = {
    "healthy": 0.42, "litigation_risk": 0.14, "admin_risk": 0.20,
    "mixed_risk": 0.16, "lapsed_history": 0.08,
}

N_PER_DISTRICT = 12
BLOCK_SUFFIXES = ["Sadar", "North", "South", "East", "Rural"]

# A statutory stage that runs this far past its clock voids the
# notification/proceedings under the Act rather than limping to completion.
LAPSE_BREACH_MULTIPLE = 1.9


def _draw_archetype(rng, exclude=()):
    """Weighted draw over ARCHETYPE_WEIGHTS, minus `exclude`, renormalised
    by rng.choices itself.

    The excluded case used to carry a second, hand-written weight list that
    had to stay the same length as the filtered population - so adding an
    archetype raised ValueError inside the generator instead of just
    working, and the two sets of weights had already drifted apart."""
    pool = {a: w for a, w in ARCHETYPE_WEIGHTS.items() if a not in exclude}
    return rng.choices(list(pool), weights=list(pool.values()), k=1)[0]


def _duration_days(statutory_days, archetype, rng, stage_idx, force_breach=False):
    """Sampled real duration for one stage. Archetype shifts the
    *distribution* of delay, it does not determine it - ranges overlap
    across the statutory threshold on purpose, so is_delayed is a genuine
    probability the model has to learn, not a label that can be read off
    the archetype with 100% accuracy. A real calibrated classifier facing
    perfectly separable classes would be a red flag, not a good result.

    `force_breach` is the one exception: the guaranteed-lapse slot draws
    strictly past LAPSE_BREACH_MULTIPLE instead of rolling for it, so "at
    least one lapsed project exists" is a property of the generator rather
    than a 35% chance the golden suite happens to survive."""
    if force_breach:
        return round(statutory_days * rng.uniform(LAPSE_BREACH_MULTIPLE + 0.1,
                                                  LAPSE_BREACH_MULTIPLE + 0.7))
    if archetype == "healthy":
        mult = rng.uniform(0.55, 1.15)
    elif archetype == "litigation_risk":
        # bites hardest pre-award (notification/declaration/award), where a
        # title dispute actually blocks the file from moving.
        mult = rng.uniform(0.75, 1.85) if stage_idx < 3 else rng.uniform(0.6, 1.5)
    elif archetype == "admin_risk":
        mult = rng.uniform(0.65, 1.65)
    elif archetype == "lapsed_history":
        mult = rng.uniform(0.9, 2.3)
    else:  # mixed_risk
        mult = rng.uniform(0.6, 1.5)
    mult *= rng.uniform(0.92, 1.08)  # extra noise on top of the archetype spread
    return max(14, round(statutory_days * mult))


def _gazette_republications(archetype, rng):
    if archetype in ("admin_risk", "lapsed_history"):
        return rng.choice([1, 1, 2, 2, 3])
    if archetype == "mixed_risk":
        return rng.choice([0, 0, 1])
    return 0


def _load_village_pools():
    """Village pools PER DISTRICT, bucketed by the worst real litigation
    status found on any parcel in that village, read from the already-built
    data/output/vivaad.db (s6 has run by this point in run_all.py's stage
    order).

    This is what makes litigation a *causally* predictive feature rather
    than a cosmetic one: a `litigation_risk` project below is deliberately
    routed onto villages that really do carry RED/AMBER parcels, so the
    delay it goes on to simulate and the litigation features s11 later
    recomputes from those same real parcels are consistent with each
    other - a model trained on this corpus can genuinely learn "more
    litigation exposure -> more delay" instead of memorizing an unrelated
    archetype label.

    Keyed by district rather than hardcoded to the one district that has a
    corpus today: which districts the linkage engine covers is a property
    of the DB, and callers ask this mapping instead of testing a name. A
    district with no parcels simply has no entry.

    Soft-fails to an empty mapping if s6 has not run yet (e.g. s8 exercised
    standalone) - s10 then finds nothing to bind, which is a smaller corpus,
    not a contract violation."""
    if not os.path.exists(DB):
        return {}
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "SELECT district, village, status FROM Parcel "
            "WHERE village IS NOT NULL AND district IS NOT NULL").fetchall()
    finally:
        con.close()

    worst = {}  # (district, village) -> worst status seen on any of its parcels
    for district, village, raw_status in rows:
        # parcel_status(), not a bare lookup: a status this build did not
        # write (a foreign DB, a half-finished s5) used to raise KeyError
        # here and take the whole build down.
        status = parcel_status(raw_status)
        key = (district, village)
        if key not in worst or STATUS_RANK[status] > STATUS_RANK[worst[key]]:
            worst[key] = status

    bucket = {"RED": "red", "AMBER": "amber", "GREEN": "clean"}
    pools = {}
    for (district, village), status in sorted(worst.items()):
        pool = pools.setdefault(district, {"all": [], "red": [], "amber": [], "clean": []})
        pool["all"].append(village)
        pool[bucket[status]].append(village)
    for pool in pools.values():
        pool["clean"] = pool["clean"] or pool["all"]
    return pools


def _flagship_village(pools):
    """The village the flagship RED parcel actually sits in, read from the
    DB rather than repeated here as a literal: s10 binds the flagship
    project to that parcel purely by village catchment, so the name s8
    writes and the name s6 stored have to be the same one."""
    if not os.path.exists(DB):
        return None
    con = sqlite3.connect(DB)
    try:
        row = con.execute("SELECT village FROM Parcel WHERE id=?",
                          (FLAGSHIP_PARCEL_RED,)).fetchone()
    finally:
        con.close()
    return row[0] if row and row[0] and row[0] in pools.get(DISTRICT, {}).get("all", []) else None


def _build_project(pid, district, seq, archetype, rng, village_pools,
                    force_stall_stage=None, force_lapse_stage=None,
                    forced_villages=None):
    ptype = rng.choice(list(PROJECT_TYPES))
    agency, act = PROJECT_TYPES[ptype]
    name = {
        "highway": f"NH widening, {district} bypass section {seq}",
        "railway": f"Rail corridor land acquisition, {district} section {seq}",
        "irrigation": f"{district} canal command area acquisition, phase {seq}",
        "industrial": f"{district} industrial corridor land pooling, block {seq}",
        "transmission": f"{district} transmission line ROW acquisition, line {seq}",
    }[ptype]
    area = round(rng.uniform(4.5, 140.0), 2)
    families = max(1, round(area * rng.uniform(3.5, 9.0)))
    budget = round(area * rng.uniform(0.8, 2.4) * 1e7, 2)  # ~INR crore/hectare scale
    block = f"{district} {rng.choice(BLOCK_SUFFIXES)}"
    # A district with no entry in village_pools has no real parcel corpus
    # behind it, so it gets no villages and s10 will bind it to nothing -
    # the honest answer. This is a lookup, not a branch on a district name:
    # ingesting a second district into the linkage engine gives it a pool
    # and routes projects onto it with no edit here.
    pools = village_pools.get(district)
    if forced_villages is not None:
        villages = forced_villages
    elif pools and pools["all"]:
        n = 1 if area < 20 else (2 if area < 60 else rng.choice([2, 3]))
        if archetype == "litigation_risk":
            pool = pools["red"] or pools["amber"] or pools["all"]
        elif archetype == "healthy":
            pool = pools["clean"]
        else:
            pool = pools["all"]
        villages = rng.sample(pool, k=min(n, len(pool)))
    else:
        villages = []

    if force_lapse_stage:
        # Early enough that the deliberately over-long stage still closes
        # before TODAY: a lapse that has not happened yet is just an open
        # stage, and the corpus would again have no lapsed project in it.
        project_start = date(2021, 1, 1) + timedelta(days=rng.randint(0, 400))
    elif archetype == "litigation_risk":
        # anchored so at least the pre-award stages' observation points
        # fall inside/after the real corpus's filing window (2024-01 to
        # 2025-12, per s1_report.json) - otherwise the leakage gate in s11
        # correctly (but uselessly, for this archetype's purpose) finds no
        # case yet filed and share_parcels_red comes out 0 regardless of
        # which village was picked.
        project_start = date(2023, 6, 1) + timedelta(days=rng.randint(0, 500))
    else:
        project_start = date(2021, 1, 1) + timedelta(days=rng.randint(0, 1500))
    republications = _gazette_republications(archetype, rng)

    stages = []
    started_on = project_start
    project_status = "open"
    for idx, stage in enumerate(STAGE_ORDER):
        clock = STAGE_CLOCKS[stage]
        lapse_here = force_lapse_stage == stage
        duration = _duration_days(clock["statutory_days"], archetype, rng, idx,
                                  force_breach=lapse_here)
        completed_on = started_on + timedelta(days=duration)
        # A forced stall (used for the guaranteed "currently overdue,
        # nothing completed" demo scenario) never completes, however long
        # it has been running.
        stalled = force_stall_stage == stage
        if stalled or completed_on > TODAY_D:
            stages.append({
                "project_id": pid, "stage": stage, "stage_order": clock["order"],
                "statutory_days": clock["statutory_days"],
                "clock_source": clock["clock_source"],
                "clock_authority": clock_authority(stage, act),
                "started_on": started_on.isoformat(), "completed_on": None,
                "source_label": "synthetic",
            })
            break
        stages.append({
            "project_id": pid, "stage": stage, "stage_order": clock["order"],
            "statutory_days": clock["statutory_days"],
            "clock_source": clock["clock_source"],
            "clock_authority": clock_authority(stage, act),
            "started_on": started_on.isoformat(), "completed_on": completed_on.isoformat(),
            "source_label": "synthetic",
        })
        # A statute-clock breach (3A-3D, 11-19, or the award clock) voids
        # the notification/proceedings under the Act - a small, deliberate
        # share of the lapsed_history archetype's *statutory* stages lapse
        # outright rather than limping to completion, so "lapsed" is a real,
        # inspectable outcome in the corpus and not just a very late one.
        # The forced slot takes the same branch without the dice: one
        # lapsed project is a demo requirement, and it used to rest on a
        # 35% roll that had produced exactly one in 96.
        if (clock["clock_source"] == "statute"
                and duration > clock["statutory_days"] * LAPSE_BREACH_MULTIPLE
                and (lapse_here or (archetype == "lapsed_history"
                                    and rng.random() < 0.35))):
            project_status = "lapsed"
            break
        started_on = completed_on
    else:
        project_status = "completed"

    return {
        "acquisition": {
            "project_id": pid, "name": name, "project_type": ptype,
            "executing_agency": agency, "act": act, "state": "Uttar Pradesh",
            "district": district, "block": block,
            "villages": ",".join(villages),
            "nh_no": f"NH-{100 + seq}" if act == "NH_1956" else None,
            "gazette_ref": f"UP/LA/{DISTRICT_ABBR[district]}/{2021 + seq % 5}/{100 + seq}",
            "area_hectares": area, "affected_families": families,
            "budget_estimate_inr": budget, "status": project_status,
            "gazette_republication_count": republications,
            "archetype": archetype,  # kept in the intermediate JSON for the
                                      # golden tests below, never loaded to the DB
            "source_label": "synthetic",
        },
        "stages": stages,
    }


def build():
    rng = random.Random(RISK_SEED)
    projects, stage_rows = [], []
    village_pools = _load_village_pools()

    seq_by_district = {d: 0 for d in ACQUISITION_DISTRICTS}

    def next_id(district):
        seq_by_district[district] += 1
        seq = seq_by_district[district]
        return f"PRJ-{DISTRICT_ABBR[district]}-{seq:03d}", seq

    # ---- guaranteed flagship: HIGH risk, litigation-driven, active statute
    # clock running over. Forced onto the flagship RED parcel's own village
    # so s10 binds it to that parcel, tying the whole product story (RED
    # parcel -> HIGH-risk project) to one inspectable project. The village
    # name is read from the DB, not repeated here.
    pid, seq = next_id(DISTRICT)
    assert pid == FLAGSHIP_PROJECT_ID, f"flagship id drifted: {pid}"
    flagship_village = _flagship_village(village_pools)
    rec = _build_project(pid, DISTRICT, seq, "litigation_risk", rng, village_pools,
                         forced_villages=[flagship_village] if flagship_village else None)
    projects.append(rec["acquisition"])
    stage_rows.extend(rec["stages"])

    # ---- guaranteed scenarios, one per required demo contrast ----
    forced = [
        (DISTRICT, "healthy", None, None),
        (DISTRICT, "litigation_risk", None, None),
        ("Amethi", "admin_risk", None, None),
        # a statutory breach that really does void the notification, so
        # "lapsed" is guaranteed present rather than rolled for
        ("Pratapgarh", "lapsed_history", None, "notification_3a_11"),
        ("Raebareli", "mixed_risk", None, None),
        # currently overdue with nothing completed yet on the open stage
        ("Ayodhya", "admin_risk", "notification_3a_11", None),
        ("Barabanki", "healthy", None, None),
    ]
    for district, archetype, stall, lapse in forced:
        pid, seq = next_id(district)
        rec = _build_project(pid, district, seq, archetype, rng, village_pools,
                             force_stall_stage=stall, force_lapse_stage=lapse)
        projects.append(rec["acquisition"])
        stage_rows.extend(rec["stages"])

    # ---- background corpus, statistical bulk for the model to train on ----
    for district in ACQUISITION_DISTRICTS:
        while seq_by_district[district] < N_PER_DISTRICT:
            pid, seq = next_id(district)
            # litigation_risk requires a real litigated parcel to bind to
            # in s10, which only exists where the linkage corpus has
            # coverage - a property of the DB, not of a district's name.
            archetype = _draw_archetype(
                rng, exclude=() if district in village_pools else ("litigation_risk",))
            rec = _build_project(pid, district, seq, archetype, rng, village_pools)
            projects.append(rec["acquisition"])
            stage_rows.extend(rec["stages"])

    acq_df = pd.DataFrame(projects)
    stage_df = pd.DataFrame(stage_rows)

    os.makedirs(DATA_IN, exist_ok=True)
    acq_df.to_parquet(os.path.join(DATA_IN, "acquisitions.parquet"), index=False)
    stage_df.to_parquet(os.path.join(DATA_IN, "project_stages.parquet"), index=False)

    os.makedirs(DATA_MID, exist_ok=True)
    with open(os.path.join(DATA_MID, "acquisitions_archetypes.json"), "w", encoding="utf-8") as fh:
        json.dump({p["project_id"]: p["archetype"] for p in projects}, fh, indent=1)

    report("s8", {
        "projects": len(acq_df), "stage_rows": len(stage_df),
        "districts": acq_df["district"].nunique(),
        "status_counts": acq_df["status"].value_counts().to_dict(),
        "archetype_counts": acq_df["archetype"].value_counts().to_dict(),
        "flagship": FLAGSHIP_PROJECT_ID,
    })


if __name__ == "__main__":
    build()
