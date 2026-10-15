"""Every MLS source must render through the same card-style flyer.

Run from the jlg-listing-flyer folder:  python3 tests/test_unified_flyer.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import fitz  # noqa: E402

from mls_router import parse_listing_pdfs  # noqa: E402
from render import render_flyer  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
CARD_MARKERS = ("PRICE & COSTS", "ABOUT THIS HOME")   # card layout headings
LEGACY_MARKERS = ("ROOM DIMENSIONS", "PROPERTY FEATURES", "ASSESSMENT, TAX & SCHOOLS")


def _render(name):
    data = open(os.path.join(FIX, name), "rb").read()
    listings = parse_listing_pdfs(data, name)
    assert listings, f"{name}: nothing parsed"
    out = os.path.join(tempfile.mkdtemp(), "flyer.pdf")
    render_flyer(listings[0], out, agent_phone="773.220.0419", agent_email="brianelmore@atproperties.com")
    doc = fitz.open(out)
    return listings[0], doc.page_count, "\n".join(p.get_text() for p in doc).upper()


def test_same_layout_for_every_source():
    for name in ("homeplatform_234_francis.pdf", "michric_234_francis.pdf",
                 "michric_2840_lake_breeze.pdf", "mred_private_5406_lakewood.pdf"):
        l, pages, text = _render(name)
        assert l.hp, f"{name}: no card record (would fall back to legacy layout)"
        assert pages == 2, f"{name}: {pages} pages"
        for m in CARD_MARKERS:
            assert m in text, f"{name}: missing card heading {m}"
        for m in LEGACY_MARKERS:
            assert m not in text, f"{name}: legacy heading {m} present"


def test_michric_facts_land_in_cards():
    l, _, text = _render("michric_234_francis.pdf")
    hp = l.hp
    assert hp["address"]["line1"] == "234 Francis Street" and hp["list_price"] == "$825,000"
    assert hp["beds"] == "5" and hp["sqft"] == "1,971"
    rows = {b["key"]: dict(map(tuple, b["rows"])) for b in hp["buckets"]}
    assert rows["price"]["Property tax"] == "$15,931 (2025)"
    assert rows["building"]["Roof"] == "Shingle" and rows["building"]["Construction"] == "Wood Siding"
    assert rows["interior"]["Flooring"] == "Tile, Wood"
    assert rows["outdoors"]["Fencing"] == "Other"
    assert rows["public"]["Parcel (APN/PIN)"] == "57-300-039-00"
    assert len(hp["rooms"]) >= 12
    # Sheet says "Garage Y/N: No": must not be presented as a garage.
    assert "garage" not in (hp["facts"]["parking_type"] or "").lower()


def test_no_agent_side_text_leaks_into_client_flyer():
    _, _, text = _render("michric_2840_lake_breeze.pdf")
    for bad in ("SHOWING INSTRUCTIONS", "LISTING AGREEMENT", "EXCLUSIVE RIGHT TO SELL", "AGENT FOR OWNER"):
        assert bad not in text, f"agent-side text leaked: {bad}"


def test_private_mred_sheet_and_blank_baths():
    l, _, text = _render("mred_private_5406_lakewood.pdf")
    assert l.hp["baths_full"] == ""          # sheet has no bath count; tile is omitted, not "0"
    assert "PRIVATE LISTING" in text


def test_waterfront_surfaces_on_page_one():
    # Home Platform record with a Water bucket: the chip, subtitle and Location
    # row must all appear on page 1, not just the page-2 Water card.
    l = parse_listing_pdfs(open(os.path.join(FIX, "homeplatform_234_francis.pdf"), "rb").read(), "x")[0]
    bs = [b for b in l.hp["buckets"] if b["key"] != "water"]
    bs.append({"key": "water", "title": "Water", "icon": "w", "agent_only": False,
               "rows": [["Waterfront", "Yes"], ["Body of water", "Kalamazoo River"], ["Features", "Dock"]]})
    l.hp["buckets"] = bs
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "f.pdf")
        render_flyer(l, out)
        doc = fitz.open(out)
        text = doc[0].get_text()
        assert len(doc) == 2
    assert "WATERFRONT" in text.upper() and "Kalamazoo River" in text


if __name__ == "__main__":
    fails = 0
    for fn_name, fn in sorted(globals().items()):
        if fn_name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", fn_name)
            except AssertionError as e:
                fails += 1
                print("FAIL", fn_name, "-", e)
    sys.exit(1 if fails else 0)
