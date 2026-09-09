import json
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.auth import current_role, require_write_role
from backend.db import get_conn

router = APIRouter(prefix="/projects", tags=["projects"])

NOT_FOUND = {"error": "not_found", "hint": "check the project id"}
SORT_COLUMNS = {
    "delay_probability": "risk.delay_probability",
    "name": "ap.name",
    "district": "ap.district",
    "area_hectares": "ap.area_hectares",
    "deadline": "ps.deadline_on",
}


@router.get("")
def search(district: str = "", stage: str = "", risk_band: str = "", act: str = "",
          status: str = "", sort: str = "delay_probability", limit: int = 50, offset: int = 0):
    """Server-side filtered, sorted, paginated project list - never a
    client-side filter over an already-loaded array."""
    conn = get_conn()
    sort_col = SORT_COLUMNS.get(sort, SORT_COLUMNS["delay_probability"])
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    where, args = [], []
    if district:
        where.append("ap.district = ?")
        args.append(district)
    if act:
        where.append("ap.act = ?")
        args.append(act)
    if status:
        where.append("ap.status = ?")
        args.append(status)
    if stage:
        where.append("ap.current_stage = ?")
        args.append(stage)
    if risk_band:
        where.append("risk.risk_band = ?")
        args.append(risk_band)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # one open (unscored-yet-open or scored) stage per project: the
    # highest-probability scored stage, matching s15's fallback shape.
    base = f"""
        FROM AcquisitionProject ap
        LEFT JOIN (
            SELECT pr.* FROM ProjectRisk pr
            JOIN (SELECT project_id, MAX(delay_probability) mx FROM ProjectRisk
                  GROUP BY project_id) best
              ON best.project_id = pr.project_id AND best.mx = pr.delay_probability
        ) risk ON risk.project_id = ap.id
        LEFT JOIN ProjectStage ps ON ps.project_id = ap.id AND ps.stage = risk.stage
        {where_sql}
    """
    total = conn.execute(f"SELECT COUNT(DISTINCT ap.id) {base}", args).fetchone()[0]
    rows = conn.execute(
        f"""SELECT DISTINCT ap.id, ap.name, ap.district, ap.act, ap.current_stage,
                  ap.status, ap.area_hectares, ap.affected_families,
                  risk.risk_band, risk.delay_probability, risk.lead_time_days AS days_remaining,
                  risk.stage AS scored_stage
           {base}
           ORDER BY {sort_col} DESC NULLS LAST LIMIT ? OFFSET ?""",
        [*args, limit, offset]).fetchall()
    return {"total": total, "projects": [dict(r) for r in rows]}


@router.get("/{project_id}")
def detail(project_id: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM AcquisitionProject WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, NOT_FOUND)
    body = dict(row)
    body["stages"] = [dict(r) for r in conn.execute(
        "SELECT * FROM ProjectStage WHERE project_id=? ORDER BY stage_order", (project_id,))]
    counts = conn.execute(
        """SELECT p.status, COUNT(*) n FROM ProjectParcel pp
           JOIN Parcel p ON p.id = pp.parcel_id WHERE pp.project_id=?
           GROUP BY p.status""", (project_id,)).fetchall()
    by_status = {r["status"] or "GREEN": r["n"] for r in counts}
    total = sum(by_status.values())
    body["parcel_summary"] = {
        "total": total, "RED": by_status.get("RED", 0),
        "AMBER": by_status.get("AMBER", 0), "GREEN": by_status.get("GREEN", 0),
    }
    return body


@router.get("/{project_id}/risk")
def risk(project_id: str):
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM AcquisitionProject WHERE id=?", (project_id,)).fetchone()
    if exists is None:
        raise HTTPException(404, NOT_FOUND)
    rows = conn.execute(
        """SELECT stage, delay_probability, risk_band, predicted_overrun_days,
                  lead_time_days, model_version, scored_at, drivers, recommendations
           FROM ProjectRisk WHERE project_id=? ORDER BY delay_probability DESC""",
        (project_id,)).fetchall()
    stages = []
    for r in rows:
        d = dict(r)
        d["drivers"] = json.loads(d["drivers"])
        d["recommendations"] = json.loads(d["recommendations"])
        stages.append(d)
    return {"project_id": project_id, "stages": stages}


@router.get("/{project_id}/parcels")
def parcels(project_id: str, status: str = ""):
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM AcquisitionProject WHERE id=?", (project_id,)).fetchone()
    if exists is None:
        raise HTTPException(404, NOT_FOUND)
    where = "WHERE pp.project_id=?"
    args = [project_id]
    if status:
        where += " AND p.status=?"
        args.append(status)
    rows = conn.execute(
        f"""SELECT pp.parcel_id, p.survey_no, p.village, p.status, p.confidence,
                  pp.binding_confidence
           FROM ProjectParcel pp JOIN Parcel p ON p.id = pp.parcel_id
           {where}
           ORDER BY CASE p.status WHEN 'RED' THEN 0 WHEN 'AMBER' THEN 1 ELSE 2 END,
                    p.confidence DESC""", args).fetchall()
    return {"project_id": project_id, "parcels": [dict(r) for r in rows]}


@router.get("/{project_id}/interventions")
def list_interventions(project_id: str):
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM AcquisitionProject WHERE id=?", (project_id,)).fetchone()
    if exists is None:
        raise HTTPException(404, NOT_FOUND)
    rows = conn.execute(
        """SELECT id, stage, rule_id, action, note, recorded_by, recorded_at,
                  risk_band_at_time, model_version_at_time
           FROM Intervention WHERE project_id=? ORDER BY recorded_at DESC""",
        (project_id,)).fetchall()
    return {"project_id": project_id, "interventions": [dict(r) for r in rows]}


class InterventionCreate(BaseModel):
    stage: str | None = None
    rule_id: str | None = None
    action: str
    note: str | None = None


@router.post("/{project_id}/interventions", status_code=201)
def create_intervention(project_id: str, body: InterventionCreate,
                        x_role: str | None = Header(default=None)):
    role = current_role(x_role)
    require_write_role(role)
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM AcquisitionProject WHERE id=?", (project_id,)).fetchone()
    if exists is None:
        raise HTTPException(404, NOT_FOUND)

    risk_band_at_time = model_version_at_time = None
    if body.stage:
        r = conn.execute(
            "SELECT risk_band, model_version FROM ProjectRisk WHERE project_id=? AND stage=?",
            (project_id, body.stage)).fetchone()
        if r:
            risk_band_at_time, model_version_at_time = r["risk_band"], r["model_version"]

    now = datetime.now(UTC).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO Intervention (project_id, stage, rule_id, action, note, "
        "recorded_by, recorded_at, risk_band_at_time, model_version_at_time) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (project_id, body.stage, body.rule_id, body.action, body.note, role,
         now, risk_band_at_time, model_version_at_time),
    )
    conn.commit()
    return {
        "id": cur.lastrowid, "project_id": project_id, "stage": body.stage,
        "action": body.action, "recorded_by": role, "recorded_at": now,
        "risk_band_at_time": risk_band_at_time,
    }
