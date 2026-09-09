"""§62 acceptance gate: the app must serve the REAL pipeline DB.

The real_client fixture copies the tracked data/output/vivaad.db into a temp
path and points VIVAAD_DB at that copy. The test module fails at collection
time if the tracked file is missing (run pipeline/run_all.py first).

Run: python -m pytest backend/tests/test_integration_real_db.py -v"""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REAL_DB = Path(__file__).resolve().parents[2] / "data" / "output" / "vivaad.db"
FLAGSHIP_CNR = "UPHC020611812025"


@pytest.fixture()
def real_client(tmp_path, monkeypatch):
    assert REAL_DB.exists(), (
        f"real DB missing at {REAL_DB} — run the pipeline (pipeline/run_all.py)")
    db_copy = tmp_path / "real.db"
    shutil.copy(REAL_DB, db_copy)
    monkeypatch.setenv("VIVAAD_DB", str(db_copy))
    from backend.main import app
    return TestClient(app)


def test_flagship_parcel_b_is_red_high_confidence(real_client):
    body = real_client.get("/parcels/P-B01/litigation").json()
    assert body["status"] == "RED"
    assert body["confidence"] >= 0.85
    assert body["links"], "flagship parcel must carry at least one link"
    link = next(l for l in body["links"] if l["case_id"] == FLAGSHIP_CNR)
    assert link["next_hearing_source"] == "derived"
    assert link["next_hearing"]


def test_flagship_parcel_a_is_green(real_client):
    assert real_client.get("/parcels/P-A01/litigation").json()["status"] == "GREEN"


def test_flagship_search_bridges_divergence(real_client):
    r = real_client.get("/parcels/search",
                        params={"survey_no": "1365/1", "village": "Madanpur Paniyar"})
    assert any(p["id"] == "P-B01" for p in r.json()["parcels"])


def test_every_endpoint_returns_200(real_client):
    checks = [
        "/parcels/search?survey_no=1365/1&village=Madanpur Paniyar",
        "/parcels/P-B01",
        "/parcels/P-B01/litigation",
        f"/cases/{FLAGSHIP_CNR}",
        "/dashboard/overview",
        "/dashboard/heatmap",
        "/dashboard/map",
        "/dashboard/risk",
        "/dashboard/risk-map",
        "/watchlist",
        "/projects",
        "/projects/PRJ-SUL-001",
        "/projects/PRJ-SUL-001/risk",
        "/projects/PRJ-SUL-001/parcels",
        "/projects/PRJ-SUL-001/interventions",
        "/models/history",
        "/auth/session",
    ]
    for path in checks:
        assert real_client.get(path).status_code == 200, path
    r = real_client.post("/watchlist", json={"parcel_id": "P-B01"})
    assert r.status_code == 201


def test_flagship_project_is_high_or_medium_risk(real_client):
    body = real_client.get("/projects/PRJ-SUL-001/risk").json()
    assert body["stages"], "flagship project has no scored stage"
    assert body["stages"][0]["risk_band"] in ("MEDIUM", "HIGH")
    assert body["stages"][0]["drivers"]


def test_flagship_project_binds_the_flagship_parcel(real_client):
    body = real_client.get("/projects/PRJ-SUL-001/parcels").json()
    parcel_ids = {p["parcel_id"] for p in body["parcels"]}
    assert "P-B01" in parcel_ids


def test_projects_search_sorted_worst_first_by_default(real_client):
    body = real_client.get("/projects?limit=10").json()
    probs = [p["delay_probability"] for p in body["projects"] if p["delay_probability"] is not None]
    assert probs == sorted(probs, reverse=True)


def test_projects_filter_by_risk_band(real_client):
    body = real_client.get("/projects?risk_band=HIGH&limit=100").json()
    assert body["projects"]
    assert all(p["risk_band"] == "HIGH" for p in body["projects"])


def test_models_history_discloses_synthetic_only_metrics(real_client):
    body = real_client.get("/models/history").json()
    assert len(body["runs"]) == 5
    for run in body["runs"]:
        assert run["n_test_real"] == 0
        assert "n_test_real=0" in run["notes"]


def test_auth_session_default_role(real_client):
    body = real_client.get("/auth/session").json()
    assert body["role"] == "officer"
    assert body["auth_mode"] == "demo_role_header"


def test_intervention_requires_write_role(real_client):
    r = real_client.post(
        "/projects/PRJ-SUL-001/interventions",
        json={"action": "test"}, headers={"X-Role": "reviewer"})
    assert r.status_code == 403


def test_intervention_create_and_list(real_client):
    r = real_client.post(
        "/projects/PRJ-SUL-001/interventions",
        json={"stage": "award_3g_23", "action": "Escalated to district legal cell"})
    assert r.status_code == 201
    body = r.json()
    assert body["risk_band_at_time"] in ("MEDIUM", "HIGH")
    listed = real_client.get("/projects/PRJ-SUL-001/interventions").json()
    assert any(i["action"] == "Escalated to district legal cell"
              for i in listed["interventions"])
