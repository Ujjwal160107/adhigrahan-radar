"""s10 - bind acquisition projects to the parcels their land bank actually
touches, by village catchment.

Reuses s2's `norm_place` and its already-computed gazetteer mapping
(data/intermediate/normalized.json's `gazetteer.mapping`, built once by
s2's `build_gazetteer`) to resolve each project's raw village references to
the same canonical villages `Parcel.village_canon` uses - so "Madanpur
Panyar" (how a project names it) and "Madanpur Paniyar" (how the land
record spells it) collapse to the same key exactly as they already do for
court citations. No new matcher is written: project binding reuses s2's
output rather than recomputing or approximating it, and it is a
village-catchment join, not an entity-resolution problem the way
court-case-to-parcel linkage was - there is nothing here for s4's weighted
scorer to do.

Matched against `Parcel.village_canon` in the already-built `vivaad.db`
(s6 has run by this point in run_all.py's stage order, exactly the pattern
pipeline/README.md documents for s11: "reading a database an earlier stage
of the same offline build produced is legitimate").

Only Sultanpur has real parcels behind it (the linkage engine's corpus,
s0-s7): projects in the other seven districts resolve to zero bindings,
never a fabricated one. `n_parcels=0` there is the honest answer, not a gap
to be papered over.

Output: data/intermediate/project_parcels.json
"""
import json
import os
import sqlite3
from datetime import UTC, datetime

from common import DATA_MID, DB, report
from s2_normalize import norm_place


def _load_village_mapping():
    path = os.path.join(DATA_MID, "normalized.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["gazetteer"]["mapping"]


def run():
    with open(os.path.join(DATA_MID, "acquisitions.json"), encoding="utf-8") as fh:
        projects = json.load(fh)
    village_mapping = _load_village_mapping()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    parcels_by_canon = {}
    for row in con.execute("SELECT id, village_canon FROM Parcel"):
        if row["village_canon"]:
            parcels_by_canon.setdefault(row["village_canon"], []).append(row["id"])
    con.close()

    now = datetime.now(UTC).isoformat(timespec="seconds")
    bindings = []
    for p in projects:
        for raw_village in p["villages"]:
            norm = norm_place(raw_village)
            canon = village_mapping.get(norm, norm)
            for parcel_id in parcels_by_canon.get(canon, []):
                bindings.append({
                    "project_id": p["project_id"], "parcel_id": parcel_id,
                    "village_canon": canon,
                    "binding_confidence": 1.0,
                    "binding_evidence": {
                        "method": "village_canon gazetteer match (s2.build_gazetteer)",
                        "village_match": True,
                        "project_village_raw": raw_village,
                    },
                    "source_label": "derived",
                    "created_at": now,
                })

    os.makedirs(DATA_MID, exist_ok=True)
    with open(os.path.join(DATA_MID, "project_parcels.json"), "w", encoding="utf-8") as fh:
        json.dump(bindings, fh, ensure_ascii=False, indent=1)

    projects_with_parcels = len({b["project_id"] for b in bindings})
    parcels_bound = len({b["parcel_id"] for b in bindings})
    flagship_parcels = sorted({b["parcel_id"] for b in bindings
                               if b["project_id"] == "PRJ-SUL-001"})
    report("s10", {
        "bindings": len(bindings), "projects": len(projects),
        "projects_with_parcels": projects_with_parcels,
        "unique_parcels_bound": parcels_bound,
        "flagship_parcels": ",".join(flagship_parcels),
    })


if __name__ == "__main__":
    run()
