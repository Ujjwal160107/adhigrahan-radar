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


def fetch(fetcher, doc_id, year):
    """The raw PDF bytes for one gazette document."""
    url = config.EGAZETTE_DOCUMENT.format(year=year, doc_id=doc_id)
    return fetcher.get(url).content


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
