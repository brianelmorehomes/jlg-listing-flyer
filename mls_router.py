"""
MLS source router.

This app now understands listing sheets from three different sources --
MRED (Illinois), MichRIC (Michigan), and the newer cross-MLS "Home
Platform" print sheet Brian's brokerage is transitioning to -- which are
three completely different physical documents, so each gets its own
parser module (parser.py / parser_michric.py / parser_platform.py)
rather than one parser trying to branch on all three layouts internally.
This module is the single place that decides which one a given upload
actually is and dispatches to it, so app.py doesn't need to know or ask.

Unlike the sibling jlg-showing-packet app's mls_router.py (which only
ever hands back one Listing per upload), this app's entry point is the
plural, batch-aware `parse_listing_pdfs()` -- MRED lets an agent export a
"Full Report" for a whole search result set as one PDF with each
listing's sheet concatenated back-to-back, and parser.py's
`parse_listing_pdfs()` already detects and splits that apart (see
parser.py's own docstring/comments). MichRIC has no equivalent
multi-listing export, so that branch always returns a single-item list.
The Home Platform export CAN hold several listings (a buyer-tour print),
so that branch splits the file per listing via the shared hp_core parser.

Detection is a cheap text signature check (each source's own copyright/
disclaimer line names itself, e.g. "...Copyright 2026 MichRIC(R), LLC..."
or "...Compass International Holdings..." for the new platform) rather
than anything more elaborate -- all three are consistently present on
every real export seen from their respective source. Defaults to the
MRED parser when none of the signatures are found, since that's the
original/primary format this app was built for.
"""
import io

import pdfplumber

from parser import parse_listing_pdfs as _parse_mred_batch
from parser_michric import parse_listing_pdf as _parse_michric, is_michric
from parser_platform import parse_listing_pdf as _parse_platform, is_new_platform


def _sniff_text(file_bytes: bytes) -> str:
    # MichRIC's own "Copyright ... MichRIC(R), LLC" signature sits in the
    # compliance footer on page 2 (the listing data itself is entirely on
    # page 1), so this has to check more than just the first page or every
    # MichRIC upload would silently fall through to the MRED parser. The
    # new Home Platform sheet's "Compass International Holdings" signature
    # is on every page (it's in each page's own disclaimer block), so it
    # doesn't strictly need this, but scanning the same 2 pages costs
    # nothing extra and keeps this one shared sniff for all three checks.
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = pdf.pages[:2]
            return "\n".join((p.extract_text() or "") for p in pages)
    except Exception:
        return ""


def _split_platform_listings(file_bytes: bytes):
    """A Home Platform export can hold several listings back-to-back (each
    starts at "Page 1/N"). hp_core knows which pages belong to which listing;
    this cuts the file into one PDF per listing so the classic parser (which
    expects exactly one) runs on each. Returns [file_bytes] when there is only
    one listing or anything goes wrong."""
    try:
        import fitz
        import hp_core
        recs = hp_core.parse_file(file_bytes, prefer="client", client_safe=True)
        if len(recs) <= 1:
            return [file_bytes]
        src = fitz.open(stream=file_bytes, filetype="pdf")
        parts = []
        for r in recs:
            out = fitz.open()
            for pg in r["source_pages"]:
                out.insert_pdf(src, from_page=pg, to_page=pg)
            parts.append(out.tobytes())
            out.close()
        src.close()
        return parts
    except Exception:
        return [file_bytes]


def parse_listing_pdfs(file_bytes: bytes, source_filename: str = ""):
    """Always returns a list, even for a single listing -- same contract as
    parser.py's own parse_listing_pdfs(), which is what app.py calls."""
    text = _sniff_text(file_bytes)
    if is_new_platform(text):
        return [_parse_platform(part, source_filename) for part in _split_platform_listings(file_bytes)]
    if is_michric(text):
        return [_parse_michric(file_bytes, source_filename)]
    return _parse_mred_batch(file_bytes, source_filename)
