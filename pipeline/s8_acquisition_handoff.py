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

The real litigation corpus (s0-s7) is Sultanpur-only, so Sultanpur is the
only district where `litigation_coverage=1` is possible; the other seven
districts exist to satisfy the >=8-district scope target and deliberately
carry no litigation signal at all.

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
    FLAGSHIP_PROJECT_ID,
    RISK_SEED,
    STAGE_CLOCKS,
    STAGE_ORDER,
    TODAY,
    clock_authority,
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
ARCHETYPES = ["healthy", "litigation_risk", "admin_risk", "mixed_risk", "lapsed_history"]
ARCHETYPE_WEIGHTS = [0.42, 0.14, 0.20, 0.16, 0.08]

N_PER_DISTRICT = 12
BLOCK_SUFFIXES = ["Sadar", "North", "South", "East", "Rural"]


def _district_abbr(district):
    return district[:3].upper()


def _duration_days(statutory_days, archetype, rng, stage_idx):
    """Sampled real duration for one stage. Archetype shifts the
    *distribution* of delay, it does not determine it - ranges overlap
    across the statutory threshold on purpose, so is_delayed is a genuine
    probability the model has to learn, not a label that can be read off
    the archetype with 100% accuracy. A real calibrated classifier facing
    perfectly separable classes would be a red flag, not a good result."""
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


def _load_sultanpur_villages():
    """Real village names from s2's gazetteer output (data/intermediate/
    normalized.json), bucketed by the worst real litigation status found on
    any parcel in that village (data/output/vivaad.db, already built by s6
    at this point in run_all.py's stage order).

    This is what makes litigation a *causally* predictive feature rather
    than a cosmetic one: a `litigation_risk` project below is deliberately
    routed onto villages that really do carry RED/AMBER parcels, so the
    delay it goes on to simulate and the litigation features s11 later
    recomputes from those same real parcels are consistent with each
    other - a model trained on this corpus can genuinely learn "more
    litigation exposure -> more delay" instead of memorizing an
    unrelated archetype label.

    Soft-fails to empty structures if s2/s6 have not run yet (e.g. s8
    exercised standalone) - s10 then simply finds nothing to bind, not a
    contract violation."""
    norm_path = os.path.join(DATA_MID, "normalized.json")
    if not os.path.exists(norm_path) or not os.path.exists(DB):
        return {"all": [], "red": [], "amber": [], "clean": []}
    with open(norm_path, encoding="utf-8") as fh:
        norm = json.load(fh)
    all_villages = sorted({p["village"] for p in norm["parcels"] if p.get("village")})

    con = sqlite3.connect(DB)
    worst = {}  # village (raw) -> worst status seen
    rank = {"GREEN": 0, "AMBER": 1, "RED": 2}
    for p in norm["parcels"]:
        village = p.get("village")
        if not village:
            continue
        row = con.execute("SELECT status FROM Parcel WHERE id=?", (p["parcel_id"],)).fetchone()
        status = (row[0] if row and row[0] else "GREEN")
        if village not in worst or rank[status] > rank[worst[village]]:
            worst[village] = status
    con.close()

    return {
        "all": all_villages,
        "red": sorted(v for v, s in worst.items() if s == "RED"),
        "amber": sorted(v for v, s in worst.items() if s == "AMBER"),
        "clean": sorted(v for v, s in worst.items() if s == "GREEN") or all_villages,
    }


def _build_project(pid, district, seq, archetype, rng, sultanpur_villages,
                    force_stall_stage=None, forced_villages=None):
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
    if forced_villages is not None:
        villages = forced_villages
    elif district == "Sultanpur" and sultanpur_villages.get("all"):
        n = 1 if area < 20 else (2 if area < 60 else rng.choice([2, 3]))
        if archetype == "litigation_risk":
            pool = (sultanpur_villages["red"] or sultanpur_villages["amber"]
                   or sultanpur_villages["all"])
        elif archetype == "healthy":
            pool = sultanpur_villages["clean"]
        else:
            pool = sultanpur_villages["all"]
        villages = rng.sample(pool, k=min(n, len(pool)))
    else:
        villages = []

    if archetype == "litigation_risk":
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
        duration = _duration_days(clock["statutory_days"], archetype, rng, idx)
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
        if (archetype == "lapsed_history" and clock["clock_source"] == "statute"
                and duration > clock["statutory_days"] * 1.9 and rng.random() < 0.35):
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
            "gazette_ref": f"UP/LA/{district[:3].upper()}/{2021 + seq % 5}/{100 + seq}",
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
    sultanpur_villages = _load_sultanpur_villages()

    seq_by_district = {d: 0 for d in ACQUISITION_DISTRICTS}

    def next_id(district):
        seq_by_district[district] += 1
        seq = seq_by_district[district]
        return f"PRJ-{_district_abbr(district)}-{seq:03d}", seq

    # ---- guaranteed flagship: HIGH risk, litigation-driven, active statute
    # clock running over. Forced onto Madanpur Panyar so s10 binds it to
    # the real RED flagship parcel (P-B01, 0.9105), tying the whole product
    # story (RED parcel -> HIGH-risk project) to one inspectable project.
    pid, seq = next_id("Sultanpur")
    assert pid == FLAGSHIP_PROJECT_ID, f"flagship id drifted: {pid}"
    rec = _build_project(pid, "Sultanpur", seq, "litigation_risk", rng,
                         sultanpur_villages, forced_villages=["Madanpur Panyar"])
    projects.append(rec["acquisition"])
    stage_rows.extend(rec["stages"])

    # ---- guaranteed scenarios, one per required demo contrast ----
    forced = [
        ("Sultanpur", "healthy", None),
        ("Sultanpur", "litigation_risk", None),
        ("Amethi", "admin_risk", None),
        ("Pratapgarh", "lapsed_history", None),
        ("Raebareli", "mixed_risk", None),
        # currently overdue with nothing completed yet on the open stage
        ("Ayodhya", "admin_risk", "notification_3a_11"),
        ("Barabanki", "healthy", None),
    ]
    for district, archetype, stall in forced:
        pid, seq = next_id(district)
        rec = _build_project(pid, district, seq, archetype, rng, sultanpur_villages,
                             force_stall_stage=stall)
        projects.append(rec["acquisition"])
        stage_rows.extend(rec["stages"])

    # ---- background corpus, statistical bulk for the model to train on ----
    for district in ACQUISITION_DISTRICTS:
        while seq_by_district[district] < N_PER_DISTRICT:
            pid, seq = next_id(district)
            archetype = (rng.choices(ARCHETYPES, weights=ARCHETYPE_WEIGHTS, k=1)[0]
                         if district == "Sultanpur"
                         # litigation_risk requires a real litigated parcel to
                         # bind to in s10, which only exists in Sultanpur.
                         else rng.choices(
                             [a for a in ARCHETYPES if a != "litigation_risk"],
                             weights=[0.46, 0.24, 0.20, 0.10], k=1)[0])
            rec = _build_project(pid, district, seq, archetype, rng, sultanpur_villages)
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
