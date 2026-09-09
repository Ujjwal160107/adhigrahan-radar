"""Static driver -> action rule table for s13_risk_score.py.

Recommendations are retrieved from this table, never generated: every rule
has a stable `rule_id` an officer can cite, and the mapping from a SHAP
driver to an action is decided once, here, by a human, not invented per
request. Matched by feature name and the sign of its SHAP contribution
(a feature only recommends an action when it is actually pushing risk up).
"""
from s11_features import FEATURE_COLUMNS

RULES = [
    {
        "rule_id": "R1-INTERIM-ORDER",
        "feature": "has_interim_order",
        "action": ("An interim order is active on a project parcel. Seek "
                   "vacation of the order and list the matter before the "
                   "LARR Authority before the current stage's statutory "
                   "clock expires."),
    },
    {
        "rule_id": "R2-LITIGATION-SHARE",
        "feature": "share_parcels_red",
        "action": ("A significant share of this project's parcels carry "
                   "active litigation. Route the litigated parcels to the "
                   "district legal cell for expedited resolution before "
                   "the next statutory milestone."),
    },
    {
        "rule_id": "R3-GAZETTE-REPUBLICATION",
        "feature": "gazette_republication_count",
        "action": ("The 3A notification has already been republished. A "
                   "further extension risks the notification lapsing "
                   "under s.3D(3) - escalate for a decision on proceeding "
                   "to declaration without delay."),
    },
    {
        "rule_id": "R4-COMPENSATION-LAG",
        "feature": "compensation_disbursed_share",
        "action": ("Compensation disbursement is lagging relative to the "
                   "stage's statutory window. Escalate disbursement to the "
                   "CALA before initiating possession - undisbursed "
                   "compensation is a common ground for possession delay "
                   "and litigation."),
    },
    {
        "rule_id": "R5-REPEAT-OVERRUNS",
        "feature": "n_prior_stage_overruns",
        "action": ("This project has already overrun one or more earlier "
                   "stages. Assign dedicated case management and a fixed "
                   "review cadence rather than routine monitoring."),
    },
    {
        "rule_id": "R6-ACTIVE-CASELOAD",
        "feature": "n_active_cases",
        "action": ("Multiple active court cases touch this project's land "
                   "bank. Coordinate with the district court liaison to "
                   "track hearing dates that could affect the acquisition "
                   "timeline."),
    },
    {
        "rule_id": "R7-STAGE-AGE",
        "feature": "days_in_current_stage",
        "action": ("This stage has been open longer than typical for a "
                   "project at this risk level. Confirm the file has not "
                   "stalled administratively and request a status update "
                   "from the executing agency."),
    },
    # ---- scope and load drivers. The first real gazette projects scored
    # HIGH on these alone (one spans 50 villages against a synthetic corpus
    # that tops out at three) and reached the officer with no action at
    # all, because every rule above is a litigation or process driver.
    {
        "rule_id": "R8-VILLAGE-FOOTPRINT",
        "feature": "n_villages",
        "action": ("This acquisition spans an unusually large number of "
                   "villages. Every village multiplies s.3C objection "
                   "hearings and title verification; split the file into "
                   "village clusters with separate CALA milestones so one "
                   "village's objections cannot hold the whole declaration "
                   "past the s.3D(3) clock."),
    },
    {
        "rule_id": "R9-AREA-SEGMENTATION",
        "feature": "area_hectares",
        "action": ("The notified area is large for this district. Sequence "
                   "the s.3D declaration by chainage segment so the statutory "
                   "clock is met for the segments that are ready, rather "
                   "than lapsing for all of them together."),
    },
    {
        "rule_id": "R10-PARCEL-COUNT",
        "feature": "n_parcels",
        "action": ("A large number of parcels is bound to this project. "
                   "Batch the survey and title checks by village and track "
                   "per-batch completion, so a handful of contested parcels "
                   "do not stall the whole schedule."),
    },
    {
        "rule_id": "R11-RR-LOAD",
        "feature": "affected_families",
        "action": ("Many families are affected. Begin the R&R entitlement "
                   "survey in parallel with the current stage rather than "
                   "after award - R&R disputes are a leading cause of "
                   "possession delay."),
    },
    # ---- litigation drivers not covered by R1/R2/R6
    {
        "rule_id": "R12-LONG-PENDENCY",
        "feature": "max_case_pendency_days",
        "action": ("A case touching this land has been pending for a long "
                   "time. Ask the district legal cell to move for an "
                   "expedited hearing; a long-pending title matter rarely "
                   "resolves inside a statutory window on its own."),
    },
    {
        "rule_id": "R13-CONFIRMED-LINKS",
        "feature": "n_high_confidence_links",
        "action": ("Several parcels carry confirmed court links. Verify "
                   "title and party status on each linked parcel before "
                   "award, and record the case reference on the parcel file."),
    },
    {
        "rule_id": "R14-TITLE-DISPUTES",
        "feature": "n_title_partition_cases",
        "action": ("Title or partition disputes are open on project land. "
                   "Compensation cannot be apportioned until title is "
                   "settled; refer the disputed parcels for reference under "
                   "the Act so apportionment does not block the award."),
    },
    {
        "rule_id": "R15-COMPENSATION-DISPUTES",
        "feature": "n_acquisition_compensation_cases",
        "action": ("Compensation disputes from earlier acquisitions touch "
                   "this land. Review the contested valuations before the "
                   "award to pre-empt a repeat challenge."),
    },
    {
        "rule_id": "R16-POSSIBLE-LINKS",
        "feature": "share_parcels_amber",
        "action": ("Some parcels have a possible but unconfirmed litigation "
                   "link. Confirm or clear each possible link before award "
                   "so an unverified dispute cannot surface at possession."),
    },
    {
        "rule_id": "R17-DISTRICT-PACE",
        "feature": "district_median_3a_to_3d_days",
        "action": ("Notifications in this district have historically taken "
                   "long to reach declaration. Front-load the survey and "
                   "objection hearings and set an internal milestone well "
                   "inside the statutory clock."),
    },
]

_BY_FEATURE = {r["feature"]: r for r in RULES}

# A rule keyed on a feature s11 does not emit can never fire, and nothing
# says so: it reads like a live recommendation to whoever maintains this
# table. R8-DISTRICT-CASELOAD was exactly that from the moment
# `district_active_land_cases` was dropped from the feature matrix, which is
# why this check exists rather than a note asking people to remember.
_orphans = sorted(set(_BY_FEATURE) - set(FEATURE_COLUMNS))
assert not _orphans, f"recommendation rules target features s11 does not emit: {_orphans}"

# Below this |SHAP| magnitude a feature is not treated as an actionable
# driver, only reported for transparency - retrieving a rule for noise
# would be worse than no recommendation.
MIN_ACTIONABLE_SHAP = 0.005


def recommend(drivers):
    """drivers: list of {feature, shap_value, ...} ordered by |shap_value|
    desc (as produced by s13). Returns retrieved recommendations for the
    drivers that (a) push risk UP (positive contribution) and (b) have a
    matching rule - never a generated action, never a rule for a feature
    pushing risk down."""
    out = []
    seen = set()
    for d in drivers:
        feature = d["feature"]
        if feature in seen or feature not in _BY_FEATURE:
            continue
        if d["shap_value"] <= MIN_ACTIONABLE_SHAP:
            continue  # not pushing risk up, or negligible
        rule = _BY_FEATURE[feature]
        out.append({"rule_id": rule["rule_id"], "driver": feature, "action": rule["action"]})
        seen.add(feature)
    return out
