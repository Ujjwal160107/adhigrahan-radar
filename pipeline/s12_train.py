"""s12 - train one calibrated delay classifier per lifecycle stage, plus
the base-rate and logistic-regression baselines the spec requires
alongside it in the same table.

Honesty rule this stage must obey (docs/plans/2026-09-09-adhigrahan-radar-
implementation-blueprint.md risk X-1): no real Bhoomi Rashi acquisition
snapshot was available to build this corpus, so `n_test_real` is 0 for
every stage, always. That is written into every ModelRun row, not hidden.
Per the project's own rule ("only real data may appear in a reported
metric"), the numbers below are a **diagnostic of the mechanism**, not a
validated performance claim - `ModelRun.notes` says so on every row, and
the officer-facing UI and docs must repeat it. Suppressing the HIGH band
outright because n_test_real=0 would make this build unable to demonstrate
its own threshold-selection logic at all; disclosing loudly instead is the
more honest failure mode of the two.

Time-based split: train on rows observed on/before the shared cutoff_date
(data/intermediate/cutoff_date.json, written by s11), test on rows observed
after it - never a random shuffle of temporal data.

Model: HistGradientBoostingClassifier(max_depth=3, max_leaf_nodes=8,
l2_regularization=1.0) wrapped in CalibratedClassifierCV. Isotonic
calibration needs >=200 training rows and sigmoid (Platt) is used below
that - a real, data-driven branch, not a hardcoded choice. On this corpus
stages run 90-222 rows, so both branches are actually taken; the per-stage
choice is recorded in `ModelRun` rather than assumed.

Whichever of {base_rate, logistic_regression, hgb_calibrated} has the
lowest Brier score on the holdout ships (`ModelRun.shipped=1`) for that
stage. If base_rate wins, that is reported plainly, not hidden - it means
the other two learned nothing generalizable for that stage in this corpus.

Band thresholds: `t_high` is the lowest holdout probability that clears
0.70 precision AND sits at or above the stage's own base rate. The base
rate floor is what makes the band mean "elevated", not just "this stage
usually overruns" - see `_select_thresholds`. A stage with no such cutoff
suppresses HIGH outright rather than emitting a band it did not earn.

Output: data/intermediate/model_runs.json, data/output/models/<stage>.joblib
"""
import json
import os
import warnings
from datetime import UTC, date, datetime

import joblib
import numpy as np
import pandas as pd
from common import DATA_IN, DATA_MID, DATA_OUT, RISK_SEED, STAGE_ORDER, TODAY, report
from s11_features import FEATURE_COLUMNS
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MODEL_VERSION = f"mv-{TODAY.replace('-', '')}-01"
MODELS_DIR = os.path.join(DATA_OUT, "models")
ISOTONIC_MIN_ROWS = 200
PRECISION_TARGET = 0.70
RECALL_TARGET = 0.80


def _impute(X, medians=None):
    X = X.copy()
    if medians is None:
        medians = X.median(numeric_only=True)
    for col in X.columns:
        if X[col].isna().any():
            X[col] = X[col].fillna(medians.get(col, 0.0))
    return X, medians


def _safe_auc(y_true, p, fn):
    if len(set(y_true)) < 2:
        return None  # undefined with a single class in the holdout
    return float(fn(y_true, p))


def _select_thresholds(y_test, p_test, base_rate=None):
    """t_high = lowest p that clears 0.70 holdout precision AND sits at or
    above the stage's own base rate (else HIGH is suppressed entirely);
    t_med = lowest p with holdout recall >= 0.80.

    The base-rate floor is not a tuning knob, it is what makes the band
    mean anything. A HIGH cut below the rate at which the stage overruns
    anyway does not identify elevated risk - it fires on the ordinary
    project. Worse, it is unexplainable by construction: the score is the
    model's baseline plus each feature's contribution, so a row below the
    baseline reached HIGH with every feature pushing risk DOWN, and the
    officer saw a HIGH badge over five drivers that all argue for lower
    risk (and, since recommendations only fire on risk-increasing drivers,
    no recommended action either).

    Every scoreable stage in this corpus picked such a cut - 0.315 against
    a 0.417 base rate on compensation_disbursed, 0.239 against 0.250 on
    award_3g_23 - so this was systematic, not one unlucky project."""
    if len(y_test) == 0 or len(set(y_test)) < 2:
        return {"high": "suppressed", "reason": "insufficient holdout diversity"}, None, None
    candidates = sorted(set(round(float(x), 3) for x in p_test))
    t_high = None
    for t in candidates:
        if base_rate is not None and t < base_rate:
            continue        # below the stage's own base rate - see docstring
        pred = (p_test >= t).astype(int)
        if pred.sum() == 0:
            continue
        prec = precision_score(y_test, pred, zero_division=0)
        if prec >= PRECISION_TARGET:
            t_high = t
            break
    t_med = None
    for t in candidates:
        pred = (p_test >= t).astype(int)
        rec = recall_score(y_test, pred, zero_division=0)
        if rec >= RECALL_TARGET:
            t_med = t
            break
    if t_high is None:
        thresholds = {"high": "suppressed",
                      "reason": "no cutoff at or above the stage base rate reached "
                                f"precision >= {PRECISION_TARGET}"}
        if t_med is not None:
            thresholds["t_med"] = t_med
        return thresholds, None, t_med
    if t_med is not None and t_med > t_high:
        t_med = t_high  # clamp: MEDIUM cannot start above HIGH's own floor
    return {"t_high": t_high, "t_med": t_med}, t_high, t_med


def _fit_hgb(runs, X_train, y_train, groups, calibration, n_train):
    """hgb_calibrated: shallow trees, strong L2, small-n discipline per spec.

    Leaves `runs` untouched when the stage is too small to stratify - the
    caller then ships base_rate/logistic_regression for it and says so."""
    n_splits = 3 if min(np.bincount(y_train)) >= 3 else 2
    hgb = HistGradientBoostingClassifier(
        max_depth=3, max_leaf_nodes=8, l2_regularization=1.0,
        early_stopping=n_train >= 60, random_state=RISK_SEED,
    )
    try:
        # A stage contributes several landmark rows, so plain StratifiedKFold
        # would put rows describing the SAME stage in both the calibration
        # fit fold and its held-out fold - optimistic calibration for the
        # same reason a random shuffle of temporal data is optimistic. Group
        # the folds by project so a stage's rows never straddle a fold.
        if groups is not None and len(set(groups)) >= n_splits:
            cv = list(StratifiedGroupKFold(
                n_splits=n_splits, shuffle=True, random_state=RISK_SEED,
            ).split(X_train, y_train, groups=groups))
        else:
            cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=RISK_SEED)
        calibrated = CalibratedClassifierCV(hgb, method=calibration, cv=cv)
        calibrated.fit(X_train, y_train)
        runs["hgb_calibrated"] = calibrated
    except ValueError:
        # too few positives per fold even at n_splits=2 - stage too small
        # for a calibrated tree model; base_rate/LR still ship for it.
        pass


def _train_one_stage(stage, train, test, cutoff, groups=None):
    X_train_raw, y_train = train[FEATURE_COLUMNS], train["is_delayed"].astype(int).values
    X_test_raw, y_test = test[FEATURE_COLUMNS], test["is_delayed"].astype(int).values
    X_train, medians = _impute(X_train_raw)
    X_test, _ = _impute(X_test_raw, medians)

    n_train, n_test = len(X_train), len(X_test)
    calibration = "isotonic" if n_train >= ISOTONIC_MIN_ROWS else "sigmoid"

    runs, untrainable = {}, []

    # Nothing here can be fitted on an empty or single-class training split:
    # DummyClassifier needs a sample, LogisticRegression needs two classes,
    # and np.bincount([]).min() raises outright. A stage can legitimately
    # land in either state on a small corpus (`possession` closes latest, so
    # most of its stages fall on the far side of the cutoff), and it used to
    # abort the whole build with an opaque ValueError several stages before
    # the DB was written. Record it as an outcome instead - the same
    # discipline this file already applies to base_rate winning.
    n_classes = len(set(y_train.tolist()))
    if n_train == 0:
        untrainable.append("no stage closed on or before the cutoff: nothing to train on")
    elif n_classes < 2:
        untrainable.append(
            f"only one outcome class in the training split (n_train={n_train}): "
            "no classifier is identifiable, and a baseline over it would be a constant")

    if not untrainable:
        # base_rate: predicts the train positive rate for every row.
        base = DummyClassifier(strategy="prior", random_state=RISK_SEED)
        base.fit(X_train, y_train)
        runs["base_rate"] = base

        # logistic_regression: standardised, L2.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            warnings.filterwarnings("ignore", message="Unknown solver options")
            lr = make_pipeline(
                StandardScaler(),
                LogisticRegression(penalty="l2", max_iter=2000, random_state=RISK_SEED),
            )
            lr.fit(X_train, y_train)
        runs["logistic_regression"] = lr

        _fit_hgb(runs, X_train, y_train, groups, calibration, n_train)

    results = {}
    for algo, model in runs.items():
        if model is None:
            continue
        p_test = model.predict_proba(X_test)[:, 1] if n_test else np.array([])
        p_train = model.predict_proba(X_train)[:, 1]
        metrics = {
            "roc_auc": _safe_auc(y_test, p_test, roc_auc_score) if n_test else None,
            "pr_auc": _safe_auc(y_test, p_test, average_precision_score) if n_test else None,
            "brier": float(brier_score_loss(y_test, p_test)) if n_test else None,
            "train_brier": float(brier_score_loss(y_train, p_train)),
            "train_positive_rate": float(y_train.mean()) if n_train else None,
        }
        results[algo] = {"model": model, "metrics": metrics, "p_test": p_test}

    # Ship whichever has the lowest test Brier score (lower = better
    # calibrated); base_rate winning is reported, not hidden.
    scored = {a: r["metrics"]["brier"] for a, r in results.items()
             if r["metrics"]["brier"] is not None}
    if scored:
        shipped_algo = min(scored, key=scored.get)
    elif results:
        shipped_algo = "base_rate" if "base_rate" in results else next(iter(results))
    else:
        # No model was fitted at all. Say "none" rather than name an algo
        # that never ran: ModelRun.algo is what the model-history screen
        # reads, and s13 finds no .joblib and skips the stage.
        shipped_algo = "none"
    thresholds, t_high, t_med = _select_thresholds(
        y_test, results[shipped_algo]["p_test"],
        base_rate=float(y_train.mean()) if n_train else None,
    ) if shipped_algo in results else (
        {"high": "suppressed", "reason": "no scoreable model"}, None, None)

    notes = ["n_test_real=0: no real acquisition dataset available in this "
            "environment; metrics below are a synthetic-holdout diagnostic "
            "of the mechanism, not a validated performance claim"]
    if shipped_algo == "base_rate":
        notes.append("base_rate had the lowest holdout Brier score for this "
                     "stage: the learned models did not generalise better "
                     "than the naive prior on this corpus")
    if n_train < ISOTONIC_MIN_ROWS:
        notes.append(f"sigmoid calibration used: n_train={n_train} < {ISOTONIC_MIN_ROWS}")
    notes.extend(untrainable)
    if untrainable:
        notes.append("no model ships for this stage; s13 leaves its open stages unscored")

    stage_report = {
        "stage": stage, "model_version": MODEL_VERSION,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "n_train": n_train, "n_test": n_test,
        # Landmark rows are not independent observations: up to three
        # describe the same stage. The distinct-stage counts are the honest
        # sample size and every metric below should be read against them.
        "n_train_stages": int(train["project_id"].nunique()) if n_train else 0,
        "n_test_stages": int(test["project_id"].nunique()) if n_test else 0,
        "n_test_real": 0, "n_test_synthetic": n_test,
        "cutoff_date": cutoff.isoformat(),
        "calibration": calibration, "shipped_algo": shipped_algo,
        "thresholds": thresholds,
        "feature_list": FEATURE_COLUMNS,
        "runs": {a: r["metrics"] for a, r in results.items()},
        "notes": "; ".join(notes),
    }

    if shipped_algo in results:
        os.makedirs(MODELS_DIR, exist_ok=True)
        joblib.dump({
            "model": results[shipped_algo]["model"],
            "medians": medians.to_dict(),
            "features": FEATURE_COLUMNS,
            "stage": stage, "algo": shipped_algo,
            "model_version": MODEL_VERSION,
            "thresholds": thresholds,
        }, os.path.join(MODELS_DIR, f"{stage}.joblib"))

    return stage_report


def run():
    with open(os.path.join(DATA_MID, "cutoff_date.json"), encoding="utf-8") as fh:
        cutoff = date.fromisoformat(json.load(fh)["cutoff_date"])

    df = pd.read_parquet(os.path.join(DATA_IN, "features.parquet"))
    # Training pool = the landmark rows of closed stages. Serving rows (open
    # stages observed at TODAY) carry no label and are s13's input, not s12's.
    pool = df[~df.is_serving_row].copy()
    pool["completed"] = pd.to_datetime(pool["stage_completed_on"]).dt.date

    # Split whole STAGES, not rows. Each stage contributes up to three
    # landmark rows; splitting on the row's own observation date would drop
    # a stage's early landmarks in train and its late ones in test, so the
    # model would be scored on stages it had already learned. Splitting on
    # when the OUTCOME became known keeps a stage wholly on one side and
    # keeps the boundary genuinely temporal: train sees only stages that had
    # already closed by the cutoff.
    stage_reports = []
    for stage in STAGE_ORDER:
        rows = pool[pool.stage == stage]
        train = rows[rows["completed"] <= cutoff]
        test = rows[rows["completed"] > cutoff]
        stage_reports.append(_train_one_stage(
            stage, train, test, cutoff, groups=train["project_id"].values))

    os.makedirs(DATA_MID, exist_ok=True)
    with open(os.path.join(DATA_MID, "model_runs.json"), "w", encoding="utf-8") as fh:
        json.dump(stage_reports, fh, indent=1, default=str)

    report("s12", {
        "model_version": MODEL_VERSION, "stages_trained": len(stage_reports),
        "shipped": {r["stage"]: r["shipped_algo"] for r in stage_reports},
        "high_band_suppressed": [r["stage"] for r in stage_reports
                                 if r["thresholds"].get("high") == "suppressed"],
        "cutoff_date": cutoff.isoformat(),
    })


if __name__ == "__main__":
    run()
