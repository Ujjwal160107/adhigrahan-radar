"""Reading the e-Gazette *Search by Ministry* result grid.

Pure: HTML in, records out. The network side of the search - establishing an
ASP.NET session and walking its ViewState postbacks - lives in `search.py`,
so the fragile part and the parseable part fail independently and only the
parseable part needs a fixture to test.

This is the cheap half of discovery. The grid already states each
notification's subject, section and document id, so the harvest decides what
is worth downloading *before* spending a multi-megabyte PDF fetch. One MoRTH
month is roughly 45 rows.

The grid is a stock ASP.NET `GridView` with a fixed column order, so it is
read with regular expressions rather than a parser dependency. That is a
deliberate trade: the shape is rigid, the alternative is a new runtime
dependency for one table, and layout drift is caught by the committed
fixture either way.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape

_GRID = re.compile(r'<table[^>]*id="gvGazetteList".*?</table>', re.S)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAGS = re.compile(r"<[^>]*>")
_PAGE_LINK = re.compile(r"Page\$(\d+)")

# "CG-DL-E-30082025-265818": document 265818, published 30-08-2025. The year
# is what partitions WriteReadData, so retrieval needs both halves.
_GAZETTE_ID = re.compile(r"CG-[A-Z]{2}-[A-Z]-(\d{2})(\d{2})(\d{4})-(\d+)")

# Fixed GridView column order.
_SUBJECT, _ISSUE_DATE, _PUBLISH_DATE, _GAZETTE = 4, 7, 8, 9
_MIN_CELLS = 10

# Subjects worth downloading. Deliberately loose: the column is free text
# typed by a human, and the same month carries "Publication of notification
# under Section 3D" and a bare "3D Notification" for the same kind of
# document. A false positive costs one fetch; a false negative loses a
# project from the corpus for good. The authoritative classification happens
# in `parse.py`, against the document's own operative clause.
_LAND_ACQUISITION = re.compile(r"\bsection\s*3\s*[AD]\b|\b3\s*[AD]\s*notification\b", re.I)


@dataclass(frozen=True)
class CatalogEntry:
    """One row of the search grid: enough to decide whether to download."""

    doc_id: int
    year: int
    subject: str
    issue_date: date | None = None
    publish_date: date | None = None


class CatalogPage(list):
    """The entries on one page, plus the pages still to walk.

    A list subclass so callers can iterate a page directly; `next_pages` is
    the pagination the caller must follow to see the rest of the month.
    Stopping at page one silently harvests a fraction of it.
    """

    def __init__(self, entries=(), next_pages=()):
        super().__init__(entries)
        self.next_pages = tuple(next_pages)


def read_page(html):
    """Read one search-result page. An unrecognisable page yields nothing
    rather than raising: a month with no notifications is an ordinary
    outcome, not an error."""
    grid = _GRID.search(html or "")
    if not grid:
        return CatalogPage()

    entries = [e for e in (_entry(row) for row in _ROW.findall(grid.group(0))) if e]
    pages = sorted({int(n) for n in _PAGE_LINK.findall(grid.group(0))})
    return CatalogPage(entries, pages)


def is_land_acquisition(subject):
    """Whether a grid row is worth downloading."""
    return bool(_LAND_ACQUISITION.search(subject or ""))


def fetch_priority(subject):
    """Sort key for the download queue: lower is fetched first.

    A full harvest is roughly twenty thousand documents at a couple of
    megabytes each. It will be interrupted and it will be run with a budget,
    so the order has to be chosen such that any prefix of it is the most
    useful harvest of that size.

    A §3D is worth most: it recites its parent §3A's date, so one document
    yields a complete labelled interval by itself. A §3A adds only an open,
    right-censored row. A lowercase "3a" is usually a competent-authority
    appointment with no clock at all - worth downloading, worth downloading
    last.
    """
    text = subject or ""
    if _DECLARATION.search(text):
        return 0
    if _INTENTION.search(text):
        return 1
    return 2


# Case matters here and nowhere else: "3A" and "3a" are different documents
# in the Press's own usage, even though `is_land_acquisition` accepts both.
_DECLARATION = re.compile(r"\b3\s*D\b")
_INTENTION = re.compile(r"\b3\s*A\b")


def _entry(row_html):
    cells = [_text(c) for c in _CELL.findall(row_html)]
    if len(cells) < _MIN_CELLS:
        return None
    gazette = _GAZETTE_ID.search(cells[_GAZETTE])
    if not gazette:
        return None  # the header row, and anything else that is not a result
    day, month, year, doc_id = gazette.groups()
    return CatalogEntry(
        doc_id=int(doc_id),
        year=int(year),
        subject=cells[_SUBJECT],
        issue_date=_date(cells[_ISSUE_DATE]),
        publish_date=_date(cells[_PUBLISH_DATE]) or _ymd(year, month, day),
    )


def _text(cell_html):
    return re.sub(r"\s+", " ", unescape(_TAGS.sub(" ", cell_html))).strip()


def _date(value):
    """"28-Aug-2025", as the grid writes it."""
    try:
        return datetime.strptime(value, "%d-%b-%Y").date()
    except (ValueError, TypeError):
        return None


def _ymd(year, month, day):
    try:
        return datetime(int(year), int(month), int(day)).date()
    except ValueError:
        return None
