"""
MLS source router.

This app now understands listing sheets from two different MLSs -- MRED
(Illinois) and MichRIC (Michigan) -- which are completely different
physical documents, so each gets its own parser module (parser.py /
parser_michric.py) rather than one parser trying to branch on both
layouts internally. This module is the single place that decides which
one a given upload actually is and dispatches to it, so app.py doesn't
need to know or ask.

Unlike the sibling jlg-showing-packet app's mls_router.py (which only
ever hands back one Listing per upload), this app's entry point is the
plural, batch-aware `parse_listing_pdfs()` -- MRED lets an agent export a
"Full Report" for a whole search result set as one PDF with each
listing's sheet concatenated back-to-back, and parser.py's
`parse_listing_pdfs()` already detects and splits that apart (see
parser.py's own docstring/comments). MichRIC has no equivalent multi-
listing export format -- each upload there is always exactly one listing
(see parser_michric.py's module docstring) -- so the MichRIC branch below
always returns a single-item list rather than attempting any split.

Detection is a cheap text signature check (each MLS's own copyright/
disclaimer line names itself, e.g. "...Copyright 2026 MichRIC(R), LLC...")
rather than anything more elaborate -- both are consistently present on
every real export seen from either source. Defaults to the MRED parser
when neither signature is found, since that's the original/primary
format this app was built for.
"""
import io

import pdfplumber

from parser import parse_listing_pdfs as _parse_mred_batch
from parser_michric import parse_listing_pdf as _parse_michric, is_michric


def _sniff_text(file_bytes: bytes) -> str:
    # MichRIC's own "Copyright ... MichRIC(R), LLC" signature sits in the
    # compliance footer on page 2 (the listing data itself is entirely on
    # page 1), so this has to check more than just the first page or every
    # MichRIC upload would silently fall through to the MRED parser.
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = pdf.pages[:2]
            return "\n".join((p.extract_text() or "") for p in pages)
    except Exception:
        return ""


def parse_listing_pdfs(file_bytes: bytes, source_filename: str = ""):
    """Always returns a list, even for a single listing -- same contract as
    parser.py's own parse_listing_pdfs(), which is what app.py calls."""
    text = _sniff_text(file_bytes)
    if is_michric(text):
        return [_parse_michric(file_bytes, source_filename)]
    return _parse_mred_batch(file_bytes, source_filename)
