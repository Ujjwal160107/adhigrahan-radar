"""run_all - execute the offline build s0 -> s15.

Every stage is idempotent and independently re-runnable; this just runs them in
dependency order and stops at the first failure, because a half-built DB is
worse than no DB (design doc section 5).

    python pipeline/run_all.py
    python pipeline/run_all.py --skip-handoff   # skip s0, rebuild from the committed contract
    python pipeline/run_all.py --risk-only      # s8 -> s15 only, against an existing vivaad.db
"""
import sys
import time
import traceback

import s0_handoff
import s1_ingest
import s2_normalize
import s3_candidates
import s4_score
import s5_status
import s6_load_db
import s7_export_fallback
import s8_acquisition_handoff
import s9_acquisition_ingest
import s10_project_bind
import s11_features
import s12_train
import s13_risk_score
import s14_load_risk_db
import s15_export_risk_fallback

LINKAGE_STAGES = [
    ("s0 handoff", s0_handoff.build),
    ("s1 ingest", s1_ingest.run),
    ("s2 normalize", s2_normalize.run),
    ("s3 candidates", s3_candidates.run),
    ("s4 score", s4_score.run),
    ("s5 status", s5_status.run),
    ("s6 load_db", s6_load_db.run),
    ("s7 export_fallback", s7_export_fallback.run),
]
RISK_STAGES = [
    ("s8 acquisition_handoff", s8_acquisition_handoff.build),
    ("s9 acquisition_ingest", s9_acquisition_ingest.run),
    ("s10 project_bind", s10_project_bind.run),
    ("s11 features", s11_features.run),
    ("s12 train", s12_train.run),
    ("s13 risk_score", s13_risk_score.run),
    ("s14 load_risk_db", s14_load_risk_db.run),
    ("s15 export_risk_fallback", s15_export_risk_fallback.run),
]
STAGES = LINKAGE_STAGES + RISK_STAGES


def main(skip_s0=False, risk_only=False):
    t0 = time.time()
    stages = RISK_STAGES if risk_only else STAGES
    for name, fn in stages:
        if skip_s0 and name.startswith("s0"):
            print("[skip] " + name)
            continue
        try:
            fn()
        except Exception as exc:
            print("\nFAILED at " + name + ": " + type(exc).__name__ + ": " + str(exc))
            traceback.print_exc()
            return 1
    print("\nbuild complete in %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main(skip_s0="--skip-handoff" in sys.argv, risk_only="--risk-only" in sys.argv))
