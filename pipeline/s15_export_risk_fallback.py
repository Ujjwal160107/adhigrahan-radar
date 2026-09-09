"""s15 - render every new risk-engine endpoint response to
data/output/fallback/, exactly like s7 does for the linkage engine. Tier 2
of the reliability ladder: if the DB is missing or a query fails, the
backend middleware serves these files at the same URLs.

Filenames mirror the request path: per-resource routes nest
(`/projects/PRJ-SUL-001/risk` -> `projects/PRJ-SUL-001/risk.json`),
dashboard/list routes are flat (`/dashboard/risk` -> `dashboard_risk.json`).

The response-shape functions here are intentionally a second, independent
implementation of the same shapes backend/routers/projects.py and
backend/routers/risk.py produce (not an import - pipeline/ code may never
run in the request path). backend/tests asserts the two stay byte-
compatible, the same discipline test_golden_case.py already enforces for
the linkage engine's s7 vs backend/routers/parcels.py.
"""
import json
import os
import sqlite3

from common import DB, FALLBACK, report


def _rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _write(name, payload):
    path = os.path.join(FALLBACK, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)


def _project_list_row(con, p):
    risk = con.execute(
        """SELECT stage, delay_probability, risk_band, lead_time_days
           FROM ProjectRisk WHERE project_id=?
           ORDER BY delay_probability DESC LIMIT 1""", (p["id"],)).fetchone()
    return {
        "id": p["id"], "name": p["name"], "district": p["district"],
        "act": p["act"], "current_stage": p["current_stage"], "status": p["status"],
        "area_hectares": p["area_hectares"], "affected_families": p["affected_families"],
        "risk_band": risk["risk_band"] if risk else None,
        "delay_probability": risk["delay_probability"] if risk else None,
        "days_remaining": risk["lead_time_days"] if risk else None,
        "scored_stage": risk["stage"] if risk else None,
    }


def projects_list_payload(con):
    projects = _rows(con, "SELECT * FROM AcquisitionProject ORDER BY id")
    return {"projects": [_project_list_row(con, p) for p in projects]}


def project_detail_payload(con, pid):
    p = _rows(con, "SELECT * FROM AcquisitionProject WHERE id=?", (pid,))
    if not p:
        return None
    p = p[0]
    stages = _rows(con,
        "SELECT * FROM ProjectStage WHERE project_id=? ORDER BY stage_order", (pid,))
    counts = _rows(con,
        """SELECT p.status, COUNT(*) n FROM ProjectParcel pp
           JOIN Parcel p ON p.id = pp.parcel_id WHERE pp.project_id=?
           GROUP BY p.status""", (pid,))
    total = sum(c["n"] for c in counts)
    by_status = {c["status"] or "GREEN": c["n"] for c in counts}
    p["stages"] = stages
    p["parcel_summary"] = {
        "total": total, "RED": by_status.get("RED", 0),
        "AMBER": by_status.get("AMBER", 0), "GREEN": by_status.get("GREEN", 0),
    }
    return p


def project_risk_payload(con, pid):
    rows = _rows(con,
        """SELECT stage, delay_probability, risk_band, predicted_overrun_days,
                  lead_time_days, model_version, scored_at, drivers, recommendations
           FROM ProjectRisk WHERE project_id=? ORDER BY delay_probability DESC""", (pid,))
    for r in rows:
        r["drivers"] = json.loads(r["drivers"])
        r["recommendations"] = json.loads(r["recommendations"])
    return {"project_id": pid, "stages": rows}


def project_parcels_payload(con, pid):
    rows = _rows(con,
        """SELECT pp.parcel_id, p.survey_no, p.village, p.status, p.confidence,
                  pp.binding_confidence
           FROM ProjectParcel pp JOIN Parcel p ON p.id = pp.parcel_id
           WHERE pp.project_id=?
           ORDER BY CASE p.status WHEN 'RED' THEN 0 WHEN 'AMBER' THEN 1 ELSE 2 END,
                    p.confidence DESC""", (pid,))
    return {"project_id": pid, "parcels": rows}


def project_interventions_payload(con, pid):
    rows = _rows(con,
        """SELECT id, stage, rule_id, action, note, recorded_by, recorded_at,
                  risk_band_at_time, model_version_at_time
           FROM Intervention WHERE project_id=? ORDER BY recorded_at DESC""", (pid,))
    return {"project_id": pid, "interventions": rows}


def dashboard_risk_payload(con):
    bands = {r["risk_band"]: r["n"] for r in _rows(con,
        "SELECT risk_band, COUNT(*) n FROM ProjectRisk GROUP BY risk_band")}
    deadlines = {}
    for label, days in (("d30", 30), ("d60", 60), ("d90", 90)):
        n = con.execute(
            "SELECT COUNT(*) FROM ProjectRisk WHERE lead_time_days <= ?", (days,)
        ).fetchone()[0]
        deadlines[label] = n
    median_row = con.execute(
        "SELECT lead_time_days FROM ProjectRisk ORDER BY lead_time_days").fetchall()
    lead_times = [r[0] for r in median_row]
    median_lead = (lead_times[len(lead_times) // 2] if lead_times else None)
    districts = _rows(con,
        """SELECT ap.district,
                  COUNT(DISTINCT ap.id) projects,
                  SUM(CASE WHEN pr.risk_band='HIGH' THEN 1 ELSE 0 END) high,
                  SUM(CASE WHEN pr.risk_band='MEDIUM' THEN 1 ELSE 0 END) medium,
                  SUM(CASE WHEN pr.risk_band='LOW' THEN 1 ELSE 0 END) low
           FROM AcquisitionProject ap LEFT JOIN ProjectRisk pr ON pr.project_id = ap.id
           GROUP BY ap.district ORDER BY ap.district""")
    top = _rows(con,
        """SELECT ap.id AS project_id, ap.name, ap.district, pr.stage,
                  pr.risk_band, pr.delay_probability, ps.deadline_on
           FROM ProjectRisk pr
           JOIN AcquisitionProject ap ON ap.id = pr.project_id
           JOIN ProjectStage ps ON ps.project_id = pr.project_id AND ps.stage = pr.stage
           ORDER BY pr.delay_probability DESC LIMIT 10""")
    model_run = _rows(con,
        "SELECT model_version, trained_at FROM ModelRun WHERE shipped=1 LIMIT 1")
    return {
        "model_version": model_run[0]["model_version"] if model_run else None,
        "trained_at": model_run[0]["trained_at"] if model_run else None,
        "bands": {"HIGH": bands.get("HIGH", 0), "MEDIUM": bands.get("MEDIUM", 0),
                 "LOW": bands.get("LOW", 0)},
        "deadlines": deadlines, "median_lead_time_days": median_lead,
        "districts": districts, "top_at_risk": top,
    }


def dashboard_risk_map_payload(con):
    """GeoJSON of scored projects, centred on their bound parcels' centroid
    where real geometry exists (Sultanpur only). geometry_source is always
    'schematic': no real cadastral corridor geometry exists for any
    project in this build."""
    rows = _rows(con,
        """SELECT ap.id, ap.name, ap.district, pr.risk_band, pr.delay_probability,
                  p.geometry
           FROM ProjectRisk pr
           JOIN AcquisitionProject ap ON ap.id = pr.project_id
           LEFT JOIN ProjectParcel pp ON pp.project_id = ap.id
           LEFT JOIN Parcel p ON p.id = pp.parcel_id
           GROUP BY ap.id""")
    features = []
    for r in rows:
        lng = lat = None
        if r["geometry"]:
            try:
                geom = json.loads(r["geometry"])
                coords = geom["coordinates"][0]
                lng = sum(c[0] for c in coords) / len(coords)
                lat = sum(c[1] for c in coords) / len(coords)
            except (TypeError, ValueError, KeyError, IndexError):
                pass
        if lng is None:
            continue  # no bound geometry - never plot a fabricated point
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "id": r["id"], "name": r["name"], "district": r["district"],
                "risk_band": r["risk_band"], "delay_probability": r["delay_probability"],
                "geometry_source": "schematic",
            },
        })
    return {"type": "FeatureCollection", "features": features}


def models_history_payload(con):
    rows = _rows(con,
        """SELECT model_version, stage, algo, shipped, n_train, n_test,
                  n_test_real, n_test_synthetic, cutoff_date, metrics,
                  thresholds, notes, trained_at
           FROM ModelRun WHERE shipped=1 ORDER BY stage""")
    for r in rows:
        r["metrics"] = json.loads(r["metrics"])
        r["thresholds"] = json.loads(r["thresholds"])
    return {"runs": rows}


def run():
    os.makedirs(FALLBACK, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    _write("projects_search.json", projects_list_payload(con))

    project_ids = [r[0] for r in con.execute("SELECT id FROM AcquisitionProject")]
    for pid in project_ids:
        _write(f"projects/{pid}.json", project_detail_payload(con, pid))
        _write(f"projects/{pid}/risk.json", project_risk_payload(con, pid))
        _write(f"projects/{pid}/parcels.json", project_parcels_payload(con, pid))
        _write(f"projects/{pid}/interventions.json",
               project_interventions_payload(con, pid))

    _write("dashboard_risk.json", dashboard_risk_payload(con))
    _write("dashboard_risk_map.json", dashboard_risk_map_payload(con))
    _write("models_history.json", models_history_payload(con))

    n_files = 3 + len(project_ids) * 4
    con.close()
    report("s15", {"projects": len(project_ids), "files_written": n_files, "dir": FALLBACK})


if __name__ == "__main__":
    run()
