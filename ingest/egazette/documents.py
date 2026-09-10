"""Retrieving a gazette document and turning it into text.

The stable half of the source. `WriteReadData/<year>/<doc_id>.pdf` needs no
session, no cookies and no tokens - a plain GET with a repaired certificate
chain. When the search form is next rebuilt and `search.py` breaks, this
keeps working, and everything already in the store stays re-readable.

Text extraction goes through `pdfplumber`, which is pure Python, so a fresh
clone needs no system packages. The parser was developed against `pdftotext
-layout` output and verified to give identical results on `pdfplumber`
output for every fixture - it reads flattened sentences and line-anchored
schedule keys, neither of which depends on the extractor's column geometry.
"""
import warnings

from .. import config

# What `extract` says about bytes it could not turn into text. The two are
# not the same failure and must not be retried the same way: a PDF that opens
# cleanly and simply has no text layer is a scan, and no number of
# re-downloads will change that, while bytes that are not a PDF at all are
# usually a download this host truncated and will serve correctly next time.
NOT_A_PDF = "not a readable PDF"
NO_TEXT_LAYER = "no text layer"


def fetch(fetcher, doc_id, year):
    """The raw PDF bytes for one gazette document."""
    url = config.EGAZETTE_DOCUMENT.format(year=year, doc_id=doc_id)
    return fetcher.get(url).content


def extract(pdf_bytes):
    """`(text, None)`, or `(None, reason)` naming which failure this was.

    Collapsing both failures into a bare `None` is what let an image-only
    notification be re-downloaded on every harvest for ever: it was
    indistinguishable from a dropped connection, so it stayed eligible, and
    nothing counted how many times it had already cost a multi-megabyte
    fetch.

    Built on `to_text` rather than beside it, so there is still exactly one
    place that opens a PDF.
    """
    text = to_text(pdf_bytes)
    if text is None:
        return None, NOT_A_PDF
    if not text.strip():
        # pdfplumber opened it, so the bytes really are a PDF; there is
        # simply nothing in it to read.
        return None, NO_TEXT_LAYER
    return text, None


def to_text(pdf_bytes):
    """Extract text, or None if the bytes are not a readable PDF.

    A truncated download is an ordinary outcome on a slow government host,
    and it must be reported as "nothing to parse" rather than crashing a
    harvest that still has hundreds of documents to go.
    """
    import io

    import pdfplumber

    try:
        with warnings.catch_warnings():
            # pdfminer is noisy about malformed-but-readable government PDFs.
            warnings.simplefilter("ignore")
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                return "\n".join((page.extract_text(layout=True) or "") for page in pdf.pages)
    except Exception:
        return None
