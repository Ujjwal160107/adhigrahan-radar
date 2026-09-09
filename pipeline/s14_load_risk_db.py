"""s14 - load the five risk-engine tables into the same vivaad.db s6
already built. CREATEs via the shared schema.sql (idempotent,
IF NOT EXISTS) and DELETEs+refills only the five tables this stage owns -
never the original eight, never Watchlist or AuditLog (backend-owned).

Output: vivaad.db gains AcquisitionProject, ProjectStage, ProjectParcel,
ProjectRisk, ModelRun.
"""
import json
import os
import sqlite3
from datetime import UTC, datetime

from common import DATA_MID, DB, SCHEMA_SQL, ContractError, report

RISK_TABLES = ("AcquisitionProject", "ProjectStage", "ProjectParcel",
              "ProjectRisk", "ModelRun")

# Columns s14 writes by name. schema.sql's CREATE TABLE IF NOT EXISTS
# silently does nothing to a table that already exists, so a vivaad.db
# built before a column was added still has the old shape and the INSERT
# below dies with a bare "table ModelRun has no column named calibration".
# Checked up front instead, with the fix in the message.
NAMED_INSERT_COLUMNS = {
    "ModelRun": ("model_version", "stage", "trained_at", "algo", "shipped",
                 "n_train", "n_test", "n_test_real", "n_test_synthetic",
                 "cutoff_date", "calibration", "metrics", "feature_list",
                 "thresholds", "notes"),
}


def _require_columns(con):
    for table, columns in NAMED_INSERT_COLUMNS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        missing = sorted(set(columns) - have)
        if missing:
            raise ContractError(
                f"{table} in {DB} is missing {missing}. This database predates a "
                "schema.sql change, and CREATE TABLE IF NOT EXISTS cannot add a "
                "column to an existing table. data/output is regenerable, so "
                "rebuild it: make clean && make build")


def _load_json(name):
    with open(os.path.join(DATA_MID, name), encoding="utf-8") as fh:
        return json.load(fh)


def run():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = OFF")  # bulk load; re-enabled after
    con.executescript(open(SCHEMA_SQL, encoding="utf-8").read())
    _require_columns(con)
    for t in RISK_TABLES:
        con.execute(f"DELETE FROM {t}")

    projects = _load_json("acquisitions.json")
    stages = _load_json("project_stages.json")
    bindings = _load_json("project_parcels.json")
    risk = _load_json("project_risk.json")
    model_runs = _load_json("model_runs.json")

    con.executemany(
        "INSERT INTO AcquisitionProject VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(p["project_id"], p["name"], p["project_type"], p["executing_agency"],
          p["act"], p["state"], p["district"], p["block"], p["nh_no"],
          p["gazette_ref"], p["area_hectares"], p["affected_families"],
          p["budget_estimate_inr"], p["current_stage"], p["stage_entered_on"],
          p["status"], p["source_label"]) for p in projects],
    )

    con.executemany(
        "INSERT INTO ProjectStage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"{s['project_id']}:{s['stage']}", s["project_id"], s["stage"],
          s["stage_order"], s["statutory_days"], s["clock_source"],
          s["clock_authority"], s["started_on"], s["completed_on"],
          s["deadline_on"], s["overdue_days"], s["is_delayed"], s["source_label"])
         for s in stages],
    )

    con.executemany(
        "INSERT INTO ProjectParcel VALUES (?,?,?,?,?,?)",
        [(b["project_id"], b["parcel_id"], b["village_canon"],
          b["binding_confidence"], json.dumps(b["binding_evidence"]),
          b["source_label"]) for b in bindings],
    )

    con.executemany(
        "INSERT INTO ProjectRisk VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(r["project_id"], r["stage"], r["delay_probability"], r["risk_band"],
          r["predicted_overrun_days"], r["lead_time_days"], r["model_version"],
          r["scored_at"], json.dumps(r["drivers"]), json.dumps(r["recommendations"]),
          r["source_label"]) for r in risk],
    )

    # One row per (model_version, stage) - the shipped algo. The full
    # base_rate/logistic_regression/hgb_calibrated comparison table lives
    # inside `metrics` (s12 already writes all three there), so the
    # baseline-vs-shipped comparison is queryable without a second row per
    # algo, which would violate ModelRun's UNIQUE(model_version, stage).
    con.executemany(
        "INSERT INTO ModelRun (model_version,stage,trained_at,algo,shipped,"
        "n_train,n_test,n_test_real,n_test_synthetic,cutoff_date,calibration,"
        "metrics,feature_list,thresholds,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(m["model_version"], m["stage"], m["trained_at"], m["shipped_algo"], 1,
          m["n_train"], m["n_test"], m["n_test_real"], m["n_test_synthetic"],
          m["cutoff_date"], m["calibration"], json.dumps(m["runs"]),
          json.dumps(m["feature_list"]), json.dumps(m["thresholds"]), m["notes"])
         for m in model_runs],
    )

    con.commit()
    con.execute("PRAGMA foreign_keys = ON")

    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in RISK_TABLES}
    original_eight = ("Parcel", "Person", "CourtCase", "CaseParty", "CourtEvent",
                      "ParcelCaseLink", "Watchlist", "SourceRecord")
    intact = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in original_eight}
    con.close()

    report("s14", {
        "db": DB, "risk_tables": counts, "original_tables_intact": intact,
        "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    })


if __name__ == "__main__":
    run()
