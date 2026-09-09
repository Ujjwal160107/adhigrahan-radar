from datetime import date

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, model_validator

from backend.auth import current_role
from backend.db import get_conn

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class Subscribe(BaseModel):
    parcel_id: str | None = None
    project_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if bool(self.parcel_id) == bool(self.project_id):
            raise ValueError("exactly one of parcel_id or project_id is required")
        return self


@router.post("", status_code=201)
def subscribe(body: Subscribe, x_role: str | None = Header(default=None)):
    conn = get_conn()
    user_ref = current_role(x_role)

    if body.parcel_id:
        if conn.execute(
                "SELECT 1 FROM Parcel WHERE id=?", (body.parcel_id,)).fetchone() is None:
            raise HTTPException(400, {"error": "unknown_parcel"})
        dup = conn.execute(
            "SELECT id FROM Watchlist WHERE user_ref=? AND parcel_id=?",
            (user_ref, body.parcel_id)).fetchone()
    else:
        if conn.execute(
                "SELECT 1 FROM AcquisitionProject WHERE id=?",
                (body.project_id,)).fetchone() is None:
            raise HTTPException(400, {"error": "unknown_project"})
        dup = conn.execute(
            "SELECT id FROM Watchlist WHERE user_ref=? AND project_id=?",
            (user_ref, body.project_id)).fetchone()

    if dup is not None:
        raise HTTPException(409, {"error": "already_subscribed", "id": dup["id"]})

    today = date.today().isoformat()
    cur = conn.execute(
        "INSERT INTO Watchlist (user_ref, parcel_id, project_id, subscribed_at) "
        "VALUES (?,?,?,?)",
        (user_ref, body.parcel_id, body.project_id, today),
    )
    conn.commit()
    return {
        "id": cur.lastrowid, "parcel_id": body.parcel_id, "project_id": body.project_id,
        "subscribed_at": today,
    }


@router.get("")
def list_watchlist(x_role: str | None = Header(default=None)):
    conn = get_conn()
    user_ref = current_role(x_role)
    rows = conn.execute(
        """SELECT w.id, w.parcel_id, w.project_id, w.subscribed_at, w.has_update,
                  p.survey_no, p.village, ap.name AS project_name
           FROM Watchlist w
           LEFT JOIN Parcel p ON p.id = w.parcel_id
           LEFT JOIN AcquisitionProject ap ON ap.id = w.project_id
           WHERE w.user_ref = ?
           ORDER BY w.id""", (user_ref,)
    ).fetchall()
    return {"items": [
        {"id": r["id"], "parcel_id": r["parcel_id"], "project_id": r["project_id"],
         "survey_no": r["survey_no"], "village": r["village"],
         "project_name": r["project_name"], "subscribed_at": r["subscribed_at"],
         "has_update": bool(r["has_update"])} for r in rows]}
