"""What to ingest, and from where. Pure data - no behaviour lives here.

Keeping the targets as data rather than as branches in the harvest means
adding a state or a year is an edit to a literal, not a new code path.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
CACHE = os.path.join(RAW, ".cache")

# ---- e-Gazette --------------------------------------------------------------

EGAZETTE_HOST = "egazette.gov.in"
EGAZETTE_BASE = f"https://{EGAZETTE_HOST}"
# WriteReadData is partitioned by publication year and needs no session at
# all, which is why retrieval survives search-flow changes.
EGAZETTE_DOCUMENT = EGAZETTE_BASE + "/WriteReadData/{year}/{doc_id}.pdf"

# "Search by Ministry" option values, read from the live form.
MINISTRIES = {
    "MoRTH": "133",
    "NHAI": "144",
}

# Land acquisition under the NH Act is always published here.
PART_SECTION = "Part II-Section 3-Sub-Section (ii)"

# The portal offers 2015 onwards. Bhoomi Rashi became mandatory for all
# project implementing agencies on 2018-04-01, and coverage before that is
# too patchy to build a corpus on.
EARLIEST_YEAR = 2018

# ---- Bhoomi Rashi -----------------------------------------------------------

BHOOMIRASHI_BASE = "https://bhoomirashi.gov.in/auth/revamp"
# Open dropdown endpoints - no CAPTCHA, no session. The project search itself
# is CAPTCHA-gated and is deliberately not automated; see the design doc.
BHOOMIRASHI_DISTRICTS = BHOOMIRASHI_BASE + "/filldist1.cshtml?val={state_id}&sid=0&EncHid="
BHOOMIRASHI_PLACES = BHOOMIRASHI_BASE + "/fillplace1.cshtml?val={district_id}&sid=0&EncHid="

# ---- targets ----------------------------------------------------------------

# Uttar Pradesh carries the existing corpus and the flagship project. Bihar
# is the second state because its khasra/thana-number land vocabulary is the
# closest of any state to UP's khasra/gata, so s2's existing survey
# normaliser stays valid without a new transliteration model; because NH
# acquisition volume there is high; and because Patna High Court shares
# Allahabad's CNR format, so extending litigation coverage later costs no new
# code. Madhya Pradesh is the fallback if Bihar's yield is thin.
TARGET_STATES = {
    "Uttar Pradesh": {"gazette_name": "UTTAR PRADESH", "bhoomirashi_state_id": 9},
    "Bihar": {"gazette_name": "BIHAR", "bhoomirashi_state_id": 10},
}

# Districts are chosen by the data, not declared here: the harvest ranks them
# by notification count and keeps the top N per state. Naming eight districts
# up front risks naming eight with no notifications in them.
DISTRICTS_PER_STATE = 8

# Sultanpur is kept whatever its rank. It is the only district with a real
# litigation corpus, and the flagship project binds to a parcel in it.
ALWAYS_INCLUDE_DISTRICTS = ("SULTANPUR",)

# ---- politeness -------------------------------------------------------------

# Seconds between requests to a single host. These are public services run on
# public money; a research harvest has no claim on their capacity.
MIN_REQUEST_INTERVAL = 1.5
MAX_RETRIES = 3
