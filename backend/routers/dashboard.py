import json

from fastapi import APIRouter

from backend.db import get_conn

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

STATUSES = ("RED", "AMBER", "GREEN")


def _bucket(status):
    """Foreign-DB defense: NULL -> GREEN, unrecognized -> AMBER (never RED)."""
    if status is None:
        return "GREEN"
    return status if status in STATUSES else "AMBER"


@router.get("/overview")
def overview():
    conn = get_conn()
    parcels = conn.execute("SELECT district, status FROM Parcel").fetchall()
    counts = {s: 0 for s in STATUSES}
    for p in parcels:
        counts[_bucket(p["status"])] += 1

    def one(sql):
        return conn.execute(sql).fetchone()["n"]
    return {
        "district": parcels[0]["district"] if parcels else None,
        "parcels": len(parcels),
        "cases": one("SELECT COUNT(*) n FROM CourtCase"),
        "status_counts": counts,
        "active_cases": one("SELECT COUNT(*) n FROM CourtCase WHERE status='active'"),
        "high_confidence_links": one(
            "SELECT COUNT(*) n FROM ParcelCaseLink WHERE confidence_band='HIGH'"),
        "possible_matches": one(
            "SELECT COUNT(*) n FROM ParcelCaseLink WHERE confidence_band='MEDIUM'"),
    }


@router.get("/heatmap")
def heatmap():
    conn = get_conn()
    rows = conn.execute(
        "SELECT village, village_canon, status FROM Parcel").fetchall()
    agg: dict[str, dict] = {}
    for r in rows:
        v = r["village_canon"] or "unknown"
        a = agg.setdefault(v, {"village": r["village"], "village_canon": v,
                               "parcels": 0, "RED": 0, "AMBER": 0, "GREEN": 0})
        a["parcels"] += 1
        a[_bucket(r["status"])] += 1
    for a in agg.values():
        a["density"] = round((a["RED"] * 2 + a["AMBER"]) / (a["parcels"] * 2), 3)
    return {"villages": sorted(agg.values(), key=lambda x: -x["density"])}


@router.get("/map")
def parcel_map():
    """GeoJSON FeatureCollection of parcel polygons for the officer map."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, survey_no, village, village_canon, status, confidence, geometry "
        "FROM Parcel"
    ).fetchall()
    features = []
    for r in rows:
        try:
            geom = json.loads(r["geometry"]) if r["geometry"] else None
        except (TypeError, ValueError):
            geom = None
        if not geom or not isinstance(geom, dict) or not geom.get("coordinates"):
            continue
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": r["id"],
                "survey_no": r["survey_no"],
                "village": r["village"],
                "village_canon": r["village_canon"] or "unknown",
                "status": _bucket(r["status"]),
                "confidence": r["confidence"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


@router.get("/risk")
def risk_overview():
    conn = get_conn()
    bands = {r["risk_band"]: r["n"] for r in conn.execute(
        "SELECT risk_band, COUNT(*) n FROM ProjectRisk GROUP BY risk_band")}
    deadlines = {}
    for label, days in (("d30", 30), ("d60", 60), ("d90", 90)):
        deadlines[label] = conn.execute(
            "SELECT COUNT(*) FROM ProjectRisk WHERE lead_time_days <= ?", (days,)
        ).fetchone()[0]
    lead_times = [r[0] for r in conn.execute(
        "SELECT lead_time_days FROM ProjectRisk ORDER BY lead_time_days")]
    median_lead = lead_times[len(lead_times) // 2] if lead_times else None
    districts = [dict(r) for r in conn.execute(
        """SELECT ap.district,
                  COUNT(DISTINCT ap.id) projects,
                  SUM(CASE WHEN pr.risk_band='HIGH' THEN 1 ELSE 0 END) high,
                  SUM(CASE WHEN pr.risk_band='MEDIUM' THEN 1 ELSE 0 END) medium,
                  SUM(CASE WHEN pr.risk_band='LOW' THEN 1 ELSE 0 END) low
           FROM AcquisitionProject ap LEFT JOIN ProjectRisk pr ON pr.project_id = ap.id
           GROUP BY ap.district ORDER BY ap.district""")]
    top = [dict(r) for r in conn.execute(
        """SELECT ap.id AS project_id, ap.name, ap.district, pr.stage,
                  pr.risk_band, pr.delay_probability, ps.deadline_on
           FROM ProjectRisk pr
           JOIN AcquisitionProject ap ON ap.id = pr.project_id
           JOIN ProjectStage ps ON ps.project_id = pr.project_id AND ps.stage = pr.stage
           ORDER BY pr.delay_probability DESC LIMIT 10""")]
    model_run = conn.execute(
        "SELECT model_version, trained_at FROM ModelRun WHERE shipped=1 LIMIT 1").fetchone()
    return {
        "model_version": model_run["model_version"] if model_run else None,
        "trained_at": model_run["trained_at"] if model_run else None,
        "bands": {"HIGH": bands.get("HIGH", 0), "MEDIUM": bands.get("MEDIUM", 0),
                 "LOW": bands.get("LOW", 0)},
        "deadlines": deadlines, "median_lead_time_days": median_lead,
        "districts": districts, "top_at_risk": top,
    }


@router.get("/risk-map")
def risk_map():
    """GeoJSON of scored projects. geometry_source is always 'schematic':
    no real cadastral corridor geometry exists for any project in this
    build - a project is plotted at the centroid of its bound parcels
    where any exist (Sultanpur only), never at a fabricated point."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT ap.id, ap.name, ap.district, pr.risk_band, pr.delay_probability,
                  p.geometry
           FROM ProjectRisk pr
           JOIN AcquisitionProject ap ON ap.id = pr.project_id
           LEFT JOIN ProjectParcel pp ON pp.project_id = ap.id
           LEFT JOIN Parcel p ON p.id = pp.parcel_id
           GROUP BY ap.id""").fetchall()
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
            continue
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
