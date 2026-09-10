"""Gazette notification text -> a `Notification`.

Pure: text in, record out, no IO and no network. All the logic that can be
wrong about a document lives here, so all of it is testable against committed
fixtures.

Two rules the whole module obeys, both learned from real documents rather
than assumed:

**Read the operative clause, not the document.** A notification recites other
notifications, cites a ministry file number that contains a highway-shaped
token, and prints its own order number in a trailer that belongs to a
different order. Every field is therefore anchored to the sentence that
actually grants the power for *this* notification. Scanning the whole text
for a plausible-looking pattern gives confidently wrong answers.

**Strip the page furniture first.** Running headers are interleaved into the
text stream mid-sentence, so a phrase like "operation of Greenfield Highway
in the stretch of land" arrives as "operation of Greenfield 20 THE GAZETTE OF
INDIA : EXTRAORDINARY [PART II-SEC. 3(ii)] Highway in the stretch of land".
Anything matched before the furniture is removed will silently lose the
middle of sentences.
"""
import re
from datetime import date, datetime

from .records import Notification

# Which parser read a document is part of what the reading *means*, so every
# decision the harvest records carries this value.
#
# Bump it whenever a change here could alter what `parse` returns for a
# document it has already read. That is what makes a correction reach the
# corpus: `--reparse` re-reads every notification whose stored version is not
# this one, and reconsiders rejections made by an older parser instead of
# leaving them settled for ever. Every fix to this module so far has been
# prompted by a real document an earlier version read wrongly, so this will
# be bumped again.
PARSER_VERSION = "2026.09.1"

# Running headers and footers. Matched case-sensitively on the all-caps
# masthead: the body text says "published in the Gazette of India,
# Extraordinary" in ordinary case, and that sentence must survive.
_PAGE_FURNITURE = re.compile(
    r"^.*(?:THE GAZETTE OF INDIA\s*:\s*EXTRAORDINARY"
    r"|भारत का रा[िज]पत्र"          # both the correct and the mis-encoded form
    r"|Uploaded by Dte\. of Printing"
    r"|Digitally signed by).*$",
    re.M)

# "New Delhi, the 29th July, 2025". The same date appears earlier in
# Devanagari; only the English form is read, because the Devanagari is set in
# a legacy font encoding that pdftotext maps incorrectly (राष्ट्रीय comes out
# as िाष्ट्रीय), making it unsafe to parse.
_NOTIFIED_ON = re.compile(
    r"New\s+Delhi,\s*the\s*(\d{1,2})\s*(?:st|nd|rd|th)?\s*([A-Za-z]+),?\s*(\d{4})", re.I)

# This notification's own order number, anchored to the "S.O. N(E).—" form
# that opens the operative paragraph rather than to a bare "S.O. N(E)"
# anywhere in the text - the file-number trailer routinely recites a
# different order number, and matching that hands the notification someone
# else's identity.
_SO_NUMBER = re.compile(r"\bS\.O\.\s*(\d+)\s*\(E\)\s*\.?\s*[—–-]")

# A §3D recites the §3A it closes. The recital is bounded on the left by
# "Whereas by the notification" and on the right by "issued under sub-section
# (1) of section 3A". Bounding matters: a few sentences later the same
# paragraph gives the *newspaper* publication date under s.3A(3), which is a
# different date and would silently shorten every interval.
_PARENT_SPAN = re.compile(
    r"Whereas\s+by\s+the\s+notification\b(.*?)"
    r"issued\s+under\s+sub-section\s*\(1\)\s*of\s*section\s*3A", re.I)
# After "dated", in each of the three spellings the Press actually uses:
# "Dated:23/12/2024", ".Dated: 02.08.2024", " dated 08.08.2024".
_PARENT_DATE = re.compile(r"[Dd]ated\s*:?\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})")

# The clause that grants the power for *this* notification. A §3D recites the
# parent §3A at length, so 3D is tested first - otherwise every declaration
# would be misread as an intention to acquire.
_CONFERRED_BY = r"conferred\s+by\s+(?:the\s+)?"
_OPERATIVE_CLAUSE = (
    ("3D", re.compile(_CONFERRED_BY + r"sub-section\s*\(1\)\s*of\s*section\s*3D", re.I)),
    ("3A", re.compile(_CONFERRED_BY + r"sub-section\s*\(1\)\s*of\s*section\s*3A", re.I)),
    ("3", re.compile(_CONFERRED_BY + r"clause\s*\(a\)\s*of\s*section\s*3\b", re.I)),
)

# The purpose sentence names the highway and the chainage. Everything about
# the project's identity is read from the window it opens.
_PURPOSE = re.compile(r"management\s+and\s+operation\b", re.I)
_PURPOSE_WINDOW = 400

# "from Km. 417.000 to Km. 460.700", and also "from Km. 475.000 to 500.500"
# where the second "Km." is dropped.
_CHAINAGE = re.compile(r"from\s+Km\.?\s*([\d.]+)\s*to\s*(?:Km\.?\s*)?([\d.]+)", re.I)
# "NH66", "NH-552G", "NH 23", "National Highways No.17".
_HIGHWAY = re.compile(r"(?:National\s+Highways?\s*No\.?\s*|NH[-\s]?\.?\s*)(\d+[A-Z]{0,2})\b")

# Schedule structure. "State:" and "District:" usually share a line, padded
# apart by a run of spaces - but not always: some schedules use a single
# space, and stopping only at the padding yields states named
# "HIMACHAL PRADESH District: SHIMLA", which then become their own row in
# every state-level aggregate. Stop at the padding *or* at the next label.
_STATE_LINE = re.compile(r"^\s*State\s*:\s*(\S.*?)(?:\s{2,}|\s*\bDistrict\s*:|$)", re.M)
_DISTRICT_LINE = re.compile(r"^.*?\bDistrict\s*:\s*(\S.*?)\s*$", re.M)
_VILLAGE_LINE = re.compile(r"^\s*Village\s*:\s*(\S.*?)\s*$", re.M)
# The operative clause states the state too, and is the fallback when a
# document's schedule uses a different table shape.
_STATE_IN_CLAUSE = re.compile(r"in\s+the\s+state\s+of\s+([A-Z][A-Za-z &]+?)\s*[.,]", re.I)

# A schedule data row: a serial number, then the plot and its description.
# Anchored on the leading serial so that headers, village markers and page
# furniture cannot be mistaken for land.
_SCHEDULE_ROW = re.compile(r"^\s{0,12}\d{1,5}\s{2,}\S.*$", re.M)
_DECIMAL = re.compile(r"\d+\.\d+")


def parse(text, doc_id):
    """Parse one gazette document.

    Returns None when the text is not a National Highways Act notification
    this parser understands - a malformed download, or a gazette item that
    the catalog's free-text subject line only made look like one.
    """
    cleaned = _PAGE_FURNITURE.sub("", text)
    # Gazette text wraps mid-sentence at the column edge, so every sentence
    # the parser reads is broken across lines. Flatten once, here; the
    # line-oriented schedule fields keep using `cleaned`.
    flat = re.sub(r"\s+", " ", cleaned)

    section = _section(flat)
    if section is None:
        return None

    purpose = _purpose_window(flat)
    km_from, km_to = _chainage(purpose)
    return Notification(
        doc_id=doc_id,
        section=section,
        so_number=_so_number(flat),
        notified_on=_notified_on(flat),
        parent_notified_on=_parent_notified_on(flat),
        nh_no=_highway(purpose),
        km_from=km_from,
        km_to=km_to,
        state=_state(cleaned, flat),
        districts=_unique(_DISTRICT_LINE.findall(cleaned)),
        villages=_unique(_VILLAGE_LINE.findall(cleaned)),
        area_hectares=_area_hectares(cleaned),
    )


def _section(flat):
    for section, pattern in _OPERATIVE_CLAUSE:
        if pattern.search(flat):
            return section
    return None


def _so_number(flat):
    m = _SO_NUMBER.search(flat)
    return f"S.O. {m.group(1)}(E)" if m else None


def _notified_on(flat):
    m = _NOTIFIED_ON.search(flat)
    if not m:
        return None
    day, month, year = m.groups()
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(f"{day} {month} {year}", fmt).date()
        except ValueError:
            continue
    return None


def _parent_notified_on(flat):
    """The date of the §3A this notification closes, or None on a §3A itself.

    The parent's *order number* is deliberately not extracted. It is written
    three incompatible ways across the corpus ("5527", "S.O.3105 (E)",
    "S.O.No. 3219(E)") and nothing consumes it: a §3D is matched to its §3A
    on this date plus state and chainage, all of which every notification
    states in a consistent form.
    """
    span = _PARENT_SPAN.search(flat)
    if not span:
        return None
    m = _PARENT_DATE.search(span.group(1))
    return _date(m) if m else None


def _purpose_window(flat):
    """The sentence naming the highway and the stretch being acquired."""
    m = _PURPOSE.search(flat)
    return flat[m.end():m.end() + _PURPOSE_WINDOW] if m else ""


def _chainage(purpose):
    m = _CHAINAGE.search(purpose)
    if not m:
        return None, None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None, None


def _highway(purpose):
    """The highway number, or None for a Greenfield alignment that has none.

    Only the text before the chainage phrase is considered, and the *last*
    match wins: a renumbered highway is written "National Highways No.17
    (New NH 66)", where the parenthetical is the current designation that
    every other source uses.
    """
    km = _CHAINAGE.search(purpose)
    matches = _HIGHWAY.findall(purpose[:km.start()] if km else purpose)
    return "NH" + matches[-1] if matches else None


def _area_hectares(cleaned):
    """Total land scheduled for acquisition, summed from the schedule.

    The rightmost decimal figure on a row is the hectares. Two layouts are in
    use - Madhya Pradesh prints "Area (in Local Unit)" and "Area (in
    Hectares)" as separate columns, Kerala prints one area column followed by
    the owner's name - and taking the rightmost decimal is correct for both,
    because the hectares column is the last one carrying a decimal in each.

    Returns None, not zero, when the notification schedules no land: a §3
    competent-authority appointment has no area, and zero would be a
    measurement where there is none.
    """
    total = 0.0
    for row in _SCHEDULE_ROW.findall(cleaned):
        figures = _DECIMAL.findall(row)
        if figures:
            total += float(figures[-1])
    return round(total, 5) if total > 0 else None


def _state(cleaned, flat):
    m = _STATE_LINE.search(cleaned) or _STATE_IN_CLAUSE.search(flat)
    return m.group(1).strip() if m else None


def _date(m):
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _unique(values):
    """De-duplicated, in first-seen order - a schedule repeats its district
    header on every page, and villages must stay in document order because
    that order is how they group under their district."""
    seen, out = set(), []
    for v in values:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return tuple(out)
