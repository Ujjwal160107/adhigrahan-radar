"""Generate the Adhigrahan Radar excalidraw diagrams.

    python docs/architecture/make_diagrams.py

Writes two files, both regenerable from scratch:

  vivaad-radar-architecture.excalidraw   full system: sources -> two engines -> serving
  adhigrahan-ml-pipeline.excalidraw      the ML lifecycle, with the leakage boundary drawn

The previous hand-authored file carried mojibake in its title (a UTF-8 em dash
decoded as cp1252). Everything here is written ASCII-only and dumped with
ensure_ascii=True, so that class of bug cannot come back.
"""
import json
import os

OUT = os.path.dirname(os.path.abspath(__file__))
STAMP = 1756512000000

# Palette, carried over from the 2026-08-20 diagram and extended.
SRC = ("#1971c2", "#e7f5ff")      # blue    - inputs
LINK = ("#2f9e44", "#ebfbee")     # green   - linkage engine (s0-s7, built)
RISK = ("#6741d9", "#f3f0ff")     # violet  - risk engine (s8-s15, new)
ART = ("#e8590c", "#fff4e6")      # orange  - build artifacts
API = ("#343a40", "#f8f9fa")      # dark    - serving
UI = ("#0c8599", "#e3fafc")       # teal    - screens
WARN = ("#c92a2a", "#fff5f5")     # red     - hard rules and boundaries

_n = [0]


def _seed():
    _n[0] += 1
    return 1000 + _n[0]


def _base(kind, x, y, w, h, stroke):
    s = _seed()
    return {
        "id": "e%d" % s, "type": kind, "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": stroke, "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": {"type": 3}, "seed": s, "version": 1, "versionNonce": s,
        "isDeleted": False, "boundElements": None, "updated": STAMP,
        "link": None, "locked": False,
    }


def box(x, y, w, h, colors):
    e = _base("rectangle", x, y, w, h, colors[0])
    e["backgroundColor"] = colors[1]
    return e


def text(x, y, body, size=12, color="#1e1e1e"):
    lines = body.split("\n")
    w = int(max(len(l) for l in lines) * size * 0.55) + 4
    h = int(len(lines) * size * 1.25) + 2
    e = _base("text", x, y, w, h, color)
    e["strokeWidth"] = 1
    e["roundness"] = None
    e.update({
        "text": body, "originalText": body, "fontSize": size, "fontFamily": 2,
        "textAlign": "left", "verticalAlign": "top", "containerId": None,
        "lineHeight": 1.25,
    })
    return e


def labelled(x, y, w, h, colors, body, size=11, pad=10):
    """A box with its caption drawn on top of it."""
    return [box(x, y, w, h, colors), text(x + pad, y + pad, body, size, colors[0])]


def arrow(x, y, pts, color="#495057", dashed=False, head=True):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    e = _base("arrow", x, y, max(xs) - min(xs), max(ys) - min(ys), color)
    e["strokeWidth"] = 1
    e["roundness"] = {"type": 2}
    if dashed:
        e["strokeStyle"] = "dashed"
    e.update({
        "points": [list(p) for p in pts], "lastCommittedPoint": None,
        "startBinding": None, "endBinding": None, "startArrowhead": None,
        "endArrowhead": "arrow" if head else None,
    })
    return e


def hop(x1, y, x2):
    """Horizontal connector between two boxes in a chain."""
    return arrow(x1, y, [(0, 0), (x2 - x1, 0)])


def write(name, elements):
    doc = {
        "type": "excalidraw", "version": 2, "source": "adhigrahan-radar",
        "elements": elements, "files": {},
        "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
    }
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=True, indent=1)
    print("%-42s %3d elements" % (name, len(elements)))


# ---------------------------------------------------------------- diagram 1

def system_architecture():
    e = []
    e.append(text(40, 20, "Adhigrahan Radar - System Architecture", 26))
    e.append(text(40, 56,
                  "SIH26017 - Predictive Analytics for Early Detection of Land Acquisition Delays"
                  "   |   design 2026-08-30", 13, "#868e96"))

    X0, PITCH, BW, BH = 300, 126, 112, 70
    R1, R2 = 150, 372          # row 1 = linkage engine, row 2 = risk engine

    # ---- inputs
    e.append(text(40, 116, "SOURCES", 13, SRC[0]))
    e += labelled(40, 140, 220, 92, SRC,
                  "cases.parquet\nREAL - 38 Allahabad HC\ncases, Sultanpur\nextracted parcel spans")
    e += labelled(40, 244, 220, 72, SRC,
                  "parcels.parquet\nSYNTHETIC - 135 parcels\n22 villages + decoy set")
    e += labelled(40, 362, 220, 92, SRC,
                  "bhoomirashi/ (cached)\nREAL - 3A / 3D gazette\nnotifications, dated\n>= 8 UP districts")
    e += labelled(40, 466, 220, 72, SRC,
                  "acquisitions (synthetic)\nSYNTHETIC - projects\nbound to Sultanpur parcels")

    # ---- row 1: linkage engine
    e.append(text(X0, 116, "LINKAGE ENGINE  s0-s7   (Vivaad Radar - built, unchanged)", 13, LINK[0]))
    stages1 = [
        "s0 handoff\nbuild contract",
        "s1 ingest\nvalidate, fail loud",
        "s2 normalize\nsurvey + gazetteer",
        "s3 candidates\nvillage blocking",
        "s4 score\nRapidFuzz + evidence",
        "s5 status\nworst case wins",
        "s6 load_db\n8 tables",
        "s7 fallback\ncached JSON",
    ]
    for i, s in enumerate(stages1):
        x = X0 + i * PITCH
        e += labelled(x, R1, BW, BH, LINK, s, 10, 8)
        if i:
            e.append(hop(x - 14, R1 + BH / 2, x))
    e.append(arrow(262, R1 + BH / 2, [(0, 0), (38, 0)]))
    e.append(arrow(262, 280, [(0, 0), (19, 0), (19, -95), (38, -95)]))

    # ---- row 2: risk engine
    e.append(text(X0, 338, "RISK ENGINE  s8-s15   (Adhigrahan Radar - new)", 13, RISK[0]))
    stages2 = [
        "s8 handoff\nacquisition contract",
        "s9 ingest\nclocks + provenance",
        "s10 bind\nproject -> parcels",
        "s11 features\nas-of + leak audit",
        "s12 train\nGBM + baselines",
        "s13 score\nSHAP + actions",
        "s14 load_db\n+5 tables = 13",
        "s15 fallback\ncached JSON",
    ]
    for i, s in enumerate(stages2):
        x = X0 + i * PITCH
        e += labelled(x, R2, BW, BH, RISK, s, 10, 8)
        if i:
            e.append(hop(x - 14, R2 + BH / 2, x))
    e.append(arrow(262, R2 + BH / 2, [(0, 0), (38, 0)]))
    e.append(arrow(262, 502, [(0, 0), (19, 0), (19, -95), (38, -95)]))

    # ---- the feature-provider seam: s6 writes the db, s11 reads it
    s6x = X0 + 6 * PITCH + BW / 2
    s11x = X0 + 3 * PITCH + BW / 2
    e.append(arrow(s6x, R1 + BH + 4,
                   [(0, 0), (0, 60), (s11x - s6x, 60), (s11x - s6x, R2 - R1 - BH - 8)],
                   LINK[0]))
    e += labelled(300, 244, 400, 68, LINK,
                  "litigation features flow this way\n"
                  "share_parcels_red - n_active_cases - has_interim_order\n"
                  "the linkage engine becomes a FEATURE PROVIDER", 10, 8)

    # ---- artifacts
    ax = X0 + 8 * PITCH + 20
    e.append(text(ax, 116, "BUILD ARTIFACTS", 13, ART[0]))
    e += labelled(ax, 140, 250, 132, ART,
                  "vivaad.db (SQLite)\n\n8 linkage tables\n+ 5 risk tables\n= 13, all precomputed\n"
                  "name kept deliberately", 11)
    e += labelled(ax, 362, 250, 110, ART,
                  "fallback/\ncached JSON per endpoint\n+ flagship payloads\n(demo tier 2)", 11)
    e.append(hop(X0 + 7 * PITCH + BW, R1 + BH / 2, ax))
    e.append(hop(X0 + 7 * PITCH + BW, R2 + BH / 2, ax))

    # ---- offline boundary
    bx = ax + 290
    e.append(arrow(bx, 110, [(0, 0), (0, 470)], WARN[0], dashed=True, head=False))
    e.append(text(bx - 128, 586,
                  "OFFLINE BOUNDARY - everything left of this line runs before the demo.\n"
                  "No scraping, no scoring, no model load, no SHAP at request time.",
                  12, WARN[0]))

    # ---- serving
    sx = bx + 30
    e.append(text(sx, 116, "SERVING", 13, API[0]))
    e += labelled(sx, 140, 240, 216, API,
                  "BACKEND\nFastAPI, read-only\n\n8 linkage endpoints\n6 risk endpoints\n\n"
                  "every route = one SELECT\nno model in request path\nfallback middleware\n"
                  "watchlist = only write", 11)
    e.append(hop(ax + 250, 206, sx))
    e.append(arrow(ax + 250, 417, [(0, 0), (35, 0), (35, -117), (70, -117)]))

    # ---- ui
    ux = sx + 280
    e.append(text(ux, 116, "OFFICER + POLICYMAKER UI", 13, UI[0]))
    e += labelled(ux, 140, 260, 82, UI,
                  "RISK DASHBOARD\ndistrict + state trends\ncorridor map, top-N at risk", 11)
    e += labelled(ux, 240, 260, 82, UI,
                  "PROJECT PORTFOLIO\nranked by delay probability\nfilter district / stage / band", 11)
    e += labelled(ux, 340, 260, 96, UI,
                  "PROJECT DETAIL\nstage timeline with statutory\ndeadline marker + overshoot\n"
                  "driver panel + retrieved action", 11)
    e += labelled(ux, 454, 260, 84, UI,
                  "PARCEL EVIDENCE (Vivaad)\nreached from the driver panel\n"
                  "survey + name + village match", 11)
    for dy in (-67, 33, 140, 248):
        e.append(arrow(sx + 240, 248, [(0, 0), (20, 0), (20, dy), (40, dy)]))

    # ---- notes
    e += labelled(40, 640, 560, 92, WARN,
                  "HARD RULES\n"
                  "1  No synthetic row contributes to any reported metric. Synthetic may train; only real may score.\n"
                  "2  Every stage clock carries clock_source: three are statute, two are administrative targets.\n"
                  "3  Open stages are right-censored (is_delayed = NULL), never scored as on-time.\n"
                  "4  HIGH risk band is emitted only if holdout precision >= 0.70, else suppressed.", 11)
    e += labelled(640, 640, 480, 92, ART,
                  "DEMO RELIABILITY TIERS\n"
                  "1  live    frontend -> FastAPI -> SQLite\n"
                  "2  cached  fallback middleware serves s7 / s15 JSON at the same URLs\n"
                  "3  bundled flagship payloads compiled into api/client.ts (?demo=1)\n"
                  "No tier touches the network during a demo.", 11)
    e += labelled(1160, 640, 430, 92, LINK,
                  "WHY THE CHANGE IS SAFE\n"
                  "s0-s7 source files are not edited. s14 CREATEs, never DROPs.\n"
                  "The 47 existing tests stay green untouched - that green suite\n"
                  "is the operational definition of 'minimal change'.", 11)

    write("vivaad-radar-architecture.excalidraw", e)


# ---------------------------------------------------------------- diagram 2

def ml_pipeline():
    e = []
    e.append(text(40, 20, "Adhigrahan Radar - ML Pipeline", 26))
    e.append(text(40, 56,
                  "one model per lifecycle stage   |   label from statute   |   "
                  "everything below runs offline", 13, "#868e96"))

    # ---- feature families
    e.append(text(40, 110, "FEATURE FAMILIES  (20 features per (project, stage) row)", 13, SRC[0]))
    fams = [
        (LINK, "A  LITIGATION  (9)   <- from the linkage engine\n"
               "share_parcels_red - share_parcels_amber - n_active_cases\n"
               "max_case_pendency_days - median_case_pendency_days\n"
               "n_acquisition_compensation_cases - n_title_partition_cases\n"
               "has_interim_order - n_high_confidence_links"),
        (SRC, "B  PROJECT INTRINSICS  (5)\n"
              "area_hectares - n_parcels - n_villages\n"
              "affected_families - act {NH_1956 | RFCTLARR_2013}"),
        (SRC, "C  ADMINISTRATIVE  (4)\n"
              "days_in_current_stage - n_prior_stage_overruns\n"
              "gazette_republication_count - compensation_disbursed_share"),
        (WARN, "D  DISTRICT CONTEXT  (3)   train-split only\n"
               "district_median_3a_to_3d_days - district_active_land_cases\n"
               "district_completed_projects"),
    ]
    y = 138
    for colors, body in fams:
        h = 22 + 14 * len(body.split("\n"))
        e += labelled(40, y, 430, h, colors, body, 10, 8)
        y += h + 12

    # ---- the chain
    e.append(text(510, 110, "PIPELINE", 13, RISK[0]))

    e += labelled(510, 138, 210, 116, RISK,
                  "features.parquet\n\none row per\n(project_id, stage)\n\n"
                  "every value computed\nAS OF stage.started_on", 11)

    e += labelled(510, 300, 210, 128, WARN,
                  "LEAKAGE AUDIT\n\nassert no contributing\nrow post-dates\nstage.started_on\n\n"
                  "raises at build time,\nexactly like s1", 11)
    e.append(arrow(615, 258, [(0, 0), (0, 38)], WARN[0]))

    e += labelled(770, 190, 220, 200, RISK,
                  "TIME SPLIT\n\ntrain  stages closed\n       before cutoff\n\n"
                  "test   stages closed\n       after cutoff\n\n"
                  "censored  is_delayed IS\n          NULL -> excluded\n\n"
                  "never a random shuffle", 11)
    e.append(arrow(724, 364, [(0, 0), (42, -74)], RISK[0]))

    e += labelled(1040, 138, 230, 128, RISK,
                  "TRAIN  (per stage)\n\nHistGradientBoosting\n  max_depth = 3\n"
                  "  max_leaf_nodes = 8\n  l2_regularization = 1.0\n  early_stopping = True", 11)
    e += labelled(1040, 300, 230, 100, ART,
                  "BASELINES\nreported in the SAME table\n\n"
                  "1  base rate\n2  L2 logistic regression\n\nif LR wins, LR ships", 11)
    e.append(arrow(994, 260, [(0, 0), (42, -50)], RISK[0]))
    e.append(arrow(994, 320, [(0, 0), (42, 20)], ART[0]))

    e += labelled(1320, 138, 220, 110, RISK,
                  "CALIBRATE\n\nCalibratedClassifierCV\n  isotonic if n >= 200\n  sigmoid otherwise\n\n"
                  "reported by Brier score", 11)
    e.append(hop(1274, 200, 1320))

    e += labelled(1320, 290, 220, 130, WARN,
                  "THRESHOLD  (on holdout)\n\nt_high  lowest p with\n        precision >= 0.70\n"
                  "t_med   lowest p with\n        recall >= 0.80\n\n"
                  "no t_high -> band suppressed", 11)
    e.append(arrow(1430, 252, [(0, 0), (0, 34)], RISK[0]))

    e += labelled(1600, 138, 220, 112, RISK,
                  "EXPLAIN\n\nSHAP TreeExplainer\ntop-5 signed drivers\nper (project, stage)\n\n"
                  "computed at BUILD time", 11)
    e.append(hop(1544, 200, 1600))

    e += labelled(1600, 290, 220, 130, ART,
                  "ACT\n\ndriver -> action table\nwith a rule id\n\n"
                  "retrieved, NEVER generated\nno model writes an\nadministrative instruction", 11)
    e.append(arrow(1710, 254, [(0, 0), (0, 32)], RISK[0]))

    e += labelled(1880, 138, 230, 132, ART,
                  "ProjectRisk\n\ndelay_probability\nrisk_band\npredicted_overrun_days\n"
                  "drivers (JSON)\nrecommendations (JSON)\nmodel_version", 11)
    e += labelled(1880, 300, 230, 120, ART,
                  "ModelRun  (registry)\n\nalgo - cutoff_date\nn_train - n_test\n"
                  "n_test_real / _synthetic\nmetrics - thresholds\nfeature_list", 11)
    e.append(hop(1824, 200, 1880))
    e.append(hop(1824, 356, 1880))

    # ---- serving boundary
    e.append(arrow(2150, 110, [(0, 0), (0, 340)], WARN[0], dashed=True, head=False))
    e.append(text(2170, 200,
                  "OFFLINE BOUNDARY\n\nGET /projects/{id}/risk\nis a SELECT.\n\n"
                  "No model is loaded and\nno SHAP value is computed\nin the request path.",
                  12, WARN[0]))

    # ---- notes
    e += labelled(40, 640, 700, 106, WARN,
                  "THE LABEL COMES FROM STATUTE, NOT FROM US\n\n"
                  "is_delayed = (completed_on - started_on) > statutory_days\n\n"
                  "3A -> 3D            365 d   NH Act 1956 s.3D(3), 3A lapses      clock_source = statute\n"
                  "s.11 -> s.19        365 d   RFCTLARR 2013 s.19(7)               clock_source = statute\n"
                  "s.19 -> s.23 award  365 d   RFCTLARR 2013 s.25                  clock_source = statute\n"
                  "award -> disbursed   90 d   project chosen        clock_source = administrative_target\n"
                  "award -> possession  90 d   project chosen        clock_source = administrative_target", 11)

    e += labelled(780, 640, 640, 106, ART,
                  "HONESTY RULES ENFORCED BY TESTS\n\n"
                  "test_no_future_leakage           no feature post-dates stage entry\n"
                  "test_district_context_train_only aggregates never see holdout rows\n"
                  "test_beats_base_rate             holdout Brier beats the base rate\n"
                  "test_high_band_precision         >= 0.70 or the band is not emitted\n"
                  "test_metrics_are_real_only       every reported row is source_label = 'real'\n"
                  "test_censored_stages_excluded    open stages are NULL, not 0", 11)

    e += labelled(1460, 640, 560, 106, RISK,
                  "NOT A SECOND MODEL\n\n"
                  "predicted_overrun_days is the empirical median overrun among\n"
                  "delayed holdout stages of the same stage and risk band,\n"
                  "rendered as 'typically N days late when this happens'.\n\n"
                  "A regression head is not justified at this corpus size, and an\n"
                  "unlabelled point estimate would be worse than an honest median.", 11)

    write("adhigrahan-ml-pipeline.excalidraw", e)


if __name__ == "__main__":
    system_architecture()
    ml_pipeline()
