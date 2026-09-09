"""s6 - load the seven linkage-engine tables into data/output/vivaad.db.

Relational + an explicit link table, not a graph DB. Geometry and
land_events are JSON text columns; no PostGIS.

Parcel carries derived `status`/`confidence` columns so the API is a plain
SELECT with no scoring at query time. land_events lives as JSON on Parcel
rather than as a separate table.

The schema itself lives in backend/schema.sql (shared with s14_load_risk_db
and the backend); this stage only DELETEs and refills the seven tables it
owns, so a linkage rebuild never disturbs the risk engine's tables.
"""
import json
import os
import sqlite3
from datetime import UTC, datetime

from common import DATA_MID, DATA_OUT, DB, SCHEMA_SQL, report

LINKAGE_TABLES = (
    "Parcel", "Person", "CourtCase", "CaseParty", "CourtEvent",
    "ParcelCaseLink", "SourceRecord",
)


def run():
    os.makedirs(DATA_OUT, exist_ok=True)
    norm = json.load(open(os.path.join(DATA_MID, "normalized.json"), encoding="utf-8"))
    status = json.load(open(os.path.join(DATA_MID, "parcel_status.json"), encoding="utf-8"))
    st = {s["parcel_id"]: s for s in status}
    now = datetime.now(UTC).isoformat(timespec="seconds")

    con = sqlite3.connect(DB)
    con.executescript(open(SCHEMA_SQL, encoding="utf-8").read())
    # DELETE, never DROP: s14 (risk engine) may already have created its
    # five tables against this same file, and a full linkage rebuild must
    # never touch them.
    for t in LINKAGE_TABLES:
        con.execute(f"DELETE FROM {t}")

    persons, parties = {}, []

    def person_id(name, norm_name, father=None):
        key = norm_name or name
        if key not in persons:
            persons[key] = {"id": f"PR-{len(persons) + 1:04d}", "name": name,
                            "name_normalized": norm_name, "father_name": father}
        elif father and not persons[key]["father_name"]:
            persons[key]["father_name"] = father
        return persons[key]["id"]

    # Parcel + owner Person
    for p in norm["parcels"]:
        s = st.get(p["parcel_id"], {})
        oid = person_id(p.get("owner_name"), p.get("owner_norm"),
                        p.get("owner_father_name"))
        con.execute(
            "INSERT INTO Parcel VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["parcel_id"], p.get("survey_no"), p.get("khasra_no"),
             p.get("khata_no"), p.get("village"), p.get("village_canon"),
             p.get("taluk"), p.get("district"), p.get("area"),
             p.get("geometry"), json.dumps(p.get("land_events") or []), oid,
             s.get("status", "GREEN"), s.get("confidence", 0.0), s.get("note"),
             1 if s.get("closed_history") else 0, p.get("source_label")))

    # CourtCase + CaseParty + CourtEvent
    for c in norm["cases"]:
        case_status = "unknown" if c.get("is_final") is None else (
            "disposed" if c["is_final"] else "active")
        con.execute("INSERT INTO CourtCase VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (c["cnr"], c.get("case_no"), c.get("court"), c.get("case_type"),
                     c.get("filing_date"), c.get("order_date"), case_status,
                     c.get("next_hearing_date"), c.get("raw_text_ref"),
                     c.get("source_label"), c.get("next_hearing_source")))
        for role, raw, nm in (("petitioner", c.get("petitioner_raw"), c.get("petitioner_norm")),
                              ("respondent", c.get("respondent_raw"), c.get("respondent_norm"))):
            if raw:
                parties.append((c["cnr"], person_id(raw, nm, c.get("father_norm")
                                                   if role == "petitioner" else None),
                                role, raw))
        if c.get("filing_date"):
            con.execute("INSERT INTO CourtEvent (case_id,event_type,date,note) VALUES (?,?,?,?)",
                        (c["cnr"], "filed", c["filing_date"], "Case filed"))
        if c.get("order_date"):
            con.execute("INSERT INTO CourtEvent (case_id,event_type,date,note) VALUES (?,?,?,?)",
                        (c["cnr"], "interim_order" if not c.get("is_final") else "judgment",
                         c["order_date"], "Latest order on record"))
        if c.get("next_hearing_date"):
            con.execute("INSERT INTO CourtEvent (case_id,event_type,date,note) VALUES (?,?,?,?)",
                        (c["cnr"], "next_hearing", c["next_hearing_date"], "Next hearing"))

    for p in persons.values():
        con.execute("INSERT INTO Person VALUES (?,?,?,?,?,?)",
                    (p["id"], p["name"], p["name_normalized"], p["father_name"],
                     None, "synthetic"))
    con.executemany("INSERT INTO CaseParty VALUES (?,?,?,?)", parties)

    for s in status:
        for l in s["links"]:
            con.execute(
                "INSERT INTO ParcelCaseLink (parcel_id,case_id,confidence_score,"
                "confidence_band,identifier_match,evidence,status,reason,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (s["parcel_id"], l["cnr"], l["confidence_score"], l["confidence_band"],
                 l["identifier_match"], json.dumps(l["evidence"]), l["status"],
                 l["reason"], now))

    con.executemany("INSERT INTO SourceRecord (source_type,origin,ingested_at,raw_ref)"
                    " VALUES (?,?,?,?)",
                    [("real", "Indian High Court judgments (Allahabad HC, Sultanpur)",
                      now, "data/input/cases.parquet"),
                     ("synthetic", "generated land records seeded from extracted ids",
                      now, "data/input/parcels.parquet"),
                     ("derived", "pipeline s4/s5 scored links and status", now,
                      "data/intermediate/parcel_status.json")])
    # Watchlist is never seeded here: it is a live user action
    # (POST /watchlist), backend-owned, and the pipeline must not fabricate
    # a subscription that no officer made.
    con.commit()

    counts = {t: con.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
              for t in LINKAGE_TABLES}
    con.close()
    report("s6", {"db": DB, "tables": counts})


if __name__ == "__main__":
    run()
