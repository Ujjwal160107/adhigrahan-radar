import json

from fastapi import APIRouter

from backend.db import get_conn

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/history")
def history():
    """The model registry: which algo shipped per stage, how it compares
    to the base-rate/logistic-regression baselines (inside `metrics`), and
    the honesty notes s12 attaches - e.g. n_test_real=0 for every row in
    this build. Precomputed at train time; this is a SELECT, nothing is
    retrained on request."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT model_version, stage, algo, shipped, n_train, n_test,
                  n_test_real, n_test_synthetic, cutoff_date, metrics,
                  thresholds, notes, trained_at
           FROM ModelRun WHERE shipped=1 ORDER BY stage"""
    ).fetchall()
    runs = []
    for r in rows:
        d = dict(r)
        d["metrics"] = json.loads(d["metrics"])
        d["thresholds"] = json.loads(d["thresholds"])
        runs.append(d)
    return {"runs": runs}
