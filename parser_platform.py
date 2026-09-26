"""
New Home Platform listing sheet parser ("single_listing_print" export).
--------------------------------------------------------------------------
Brian's brokerage is transitioning to a new listing platform (its
disclaimer footer names "Compass International Holdings" -- see
is_new_platform() below) whose print sheet is a completely different
physical document from both MRED's classic "Full Report" (parser.py) and
MichRIC's "New Full Detail Report" (parser_michric.py). It's also MLS-
agnostic: the same sheet layout is produced whether the underlying
listing lives in MRED (Illinois) or MichRIC (Michigan) -- confirmed
directly against real samples of both -- so this one module handles
both, rather than needing its own MRED/MichRIC split the way the classic
sheets do.

Two real differences from the classic sheets that matter for callers:

1. Each PDF export comes in a "Client" flavor and/or an "Agent" flavor
   (sometimes both concatenated into one multi-page file, sometimes just
   one). The Agent flavor adds a few extra Key Details rows (PRKG, FEES,
   bare Beds/Baths) and an internal-only Listing Contacts/Agent Remarks/
   Showing Instructions block -- none of which this flyer needs (the
   internal fields are already stripped from every other source's flyer
   too), and the extra Key Details rows just duplicate data the Client
   flavor already has elsewhere on the sheet (Property History/Details,
   the header banner). So Client-only input works fine and is preferred
   when present; Agent-only input parses just as well since the grid
   extraction below is driven by font weight, not a fixed field list --
   an unrecognized bold label is just a dict key nothing ever looks up.

2. This sheet comes in (at least) two export variants that carry
   noticeably different amounts of data, and callers can't tell which
   they got except by whether the extra sections come back empty. A
   SIMPLER variant really is meaningfully thinner than a MichRIC full-
   detail report: no room dimensions table, no categorized interior/
   exterior/construction feature grid, no water source/sewer, no heating
   type breakdown, no basement finish detail, no fireplace detail. But a
   RICHER variant (confirmed on a real sample, 6811 116th Avenue) adds a
   second "Property Information" page with exactly that detail --
   Taxes and HOA (incl. current taxable value), Parking, Building
   Features (incl. Foundation Details for basement, Construction
   Materials, Roof), Utilities (Water Source, Sewer), a nested Exterior
   Features grid (Patio And Porch Features, Private Pool) and Interior
   Features grid (Total Rooms, Total Fireplaces, Appliances, Cooling,
   Heating, Flooring, Laundry Features, Window Features, Security
   Features, Fireplace Features), plus a separate "Room Information" page
   of room-by-room Type/Level/Dimensions -- all read into
   `taxeshoa`/`parking`/`buildingfeat`/`utilities`/`exteriorfeat`/
   `interiorfeat`/`rooms_raw` below via `_bold_row_headers()`/
   `_all_headers()` (for the grid sections) and `_parse_room_dimensions()`
   (for the room-by-room page, which needs no header bounding at all --
   see its own docstring), since the "Property Information" page's
   subsection headings render at the same 7.0pt body size as ordinary
   label text and the plain size-based `_section_headers()` alone can't
   see them. Two
   things stay true regardless of variant: no County field at all
   (MRED/MichRIC's classic sheets both carry County directly), and every
   new lookup above degrades gracefully to blank on a simpler export that
   lacks that page -- so this is additive, not a replacement for the
   simpler-variant handling. The missing County field specifically means
   jlg-showing-packet's route-map geocoding (packet.py's _county_level()
   rural-address fallback) has nothing to fall back on for listings
   parsed from this source -- worth knowing if a showing packet stop
   sourced this way ever lands a mislocated pin the way 6456 104th Avenue
   did before that fix.

Detection and extraction approach
----------------------------------
Every Key Details / Property History / Property Details row on this
sheet renders as BOLD label word(s) immediately followed by REGULAR
value word(s) -- confirmed directly via pdfplumber's per-word font name
(bold rows use an "...-Bold" font, values use a "...-Regular" font) --
with two side-by-side (Key Details/Property Details) or four side-by-
side (Property History's first row) label:value pairs sharing one
visual text row. There's no colon delimiter, and the exact set of
fields present varies a lot by property/MLS (an MRED listing's Key
Details has Township/Ownership/Heat-Fuel rows a MichRIC listing's
doesn't, and vice versa for Architectural Style/Waterfront/Zoning), so
rather than hand-maintain an exhaustive label list, `_extract_kv_grid()`
below reads the bold/regular run pattern directly off each row's words
and builds a {label: value} dict from whatever's actually there. Callers
just look up the handful of labels they care about by name; anything
else present on the sheet (Ownership, Zoning, Subdivision Name, ...)
is harmlessly left in the dict, unused, same "generic label:value
scraping so it degrades gracefully" philosophy as parser.py.
"""
import io
import re

import fitz  # PyMuPDF
import pdfplumber

from parser import Listing, money, _is_nullish


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def is_new_platform(text: str) -> bool:
    """Signature check mirroring mls_router.py's is_michric()/MRED default
    pattern -- this platform's own compliance footer names its parent
    company, present on every real export seen from it."""
    return "Compass International Holdings" in (text or "")


# ---------------------------------------------------------------------------
# Font-weight-driven grid extraction
# ---------------------------------------------------------------------------

def _extract_kv_grid(words, top_min, top_max, row_tol=3):
    """Extract {label: value} pairs from a bold-label/regular-value grid
    section (Key Details, Property History, Property Details on this
    platform's sheet) bounded vertically by [top_min, top_max). Handles
    any number of side-by-side columns per row -- driven purely by each
    word's font weight and left-to-right order, not a fixed set of x
    positions, since Property History uses 4 columns on its first row
    and 2 on its second while Key Details/Property Details use 2
    throughout. See module docstring for why this beats a hardcoded
    label list."""
    picked = [w for w in words if top_min <= w["top"] < top_max]
    picked.sort(key=lambda w: (w["top"], w["x0"]))

    rows = []
    cur_top, cur = None, []
    for w in picked:
        if cur_top is None or abs(w["top"] - cur_top) <= row_tol:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append(cur)
            cur, cur_top = [w], w["top"]
    if cur:
        rows.append(cur)

    kv = {}
    # Remembers, per rough horizontal column (bucketed by the x0 where that
    # column's VALUE text starts), which label is currently "open" in that
    # column -- lets a value too long to fit before the next column's label
    # starts glue its wrapped continuation onto the SAME label on the next
    # row, instead of being silently dropped. Confirmed necessary on a real
    # sample: "Appliances" ran long enough to wrap mid-word, splitting
    # "Range" itself across two rows ("...Washer,R" / "ange,Oven,Electric
    # Water Heater") with literally zero space at the split -- pdfplumber's
    # own extract_text() shows the identical break, so this is a genuine
    # PDF layout wrap, not an artifact of word-boxing. Scoped to one
    # _extract_kv_grid() call (this dict is local, not module-level), so it
    # can never bleed a label from one section/call into another.
    open_label_by_col = {}
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        pairs = []  # (label_words, value_words, value_x0)
        label_words, value_words, value_x0 = [], [], None
        mode = None
        for w in row:
            is_bold = "Bold" in (w.get("fontname") or "")
            if is_bold:
                if mode == "value" and (label_words or value_words):
                    pairs.append((label_words, value_words, value_x0))
                    label_words, value_words, value_x0 = [], [], None
                mode = "label"
                label_words.append(w["text"])
            else:
                if value_x0 is None:
                    value_x0 = w["x0"]
                mode = "value"
                value_words.append(w["text"])
        if label_words or value_words:
            pairs.append((label_words, value_words, value_x0))
        for lw, vw, vx0 in pairs:
            label = " ".join(lw).strip()
            value = " ".join(vw).strip()
            if label:
                kv[label] = value
                if vx0 is not None:
                    open_label_by_col[round(vx0 / 30) * 30] = label
            elif value and vx0 is not None:
                # A value with no label on this row at all -- almost
                # certainly a wrapped continuation (see comment above), so
                # glue it onto whichever label is currently open in this
                # exact column, with NO separating space (the wrap can
                # split mid-word). If nothing's open in this column, this
                # is genuinely orphaned data (shouldn't happen on any real
                # sample seen) and is dropped, same as before this fix.
                open_label = open_label_by_col.get(round(vx0 / 30) * 30)
                if open_label is not None:
                    kv[open_label] = kv.get(open_label, "") + value
    return kv


def _section_headers(words):
    """Return [(header_text, top), ...] for this page's bold, >=7.5pt
    section/subsection headings (Key Details, Description, Property
    History, Property Details, Amenities, Schools, Transit, My Agent,
    Listing Contacts, ...), in top-to-bottom order. Body/value text on
    this sheet all sits at 7.0pt, so the size threshold alone reliably
    separates headings from content without needing to match specific
    heading strings."""
    hdr_words = [
        w for w in words
        if w.get("size", 0) >= 7.5 and "Bold" in (w.get("fontname") or "")
    ]
    hdr_words.sort(key=lambda w: (w["top"], w["x0"]))
    rows = []
    cur_top, cur = None, []
    for w in hdr_words:
        if cur_top is None or abs(w["top"] - cur_top) <= 3:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append(cur)
            cur, cur_top = [w], w["top"]
    if cur:
        rows.append(cur)
    out = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        out.append((" ".join(w["text"] for w in row), row[0]["top"]))
    return out


def _bold_row_headers(words, row_tol=3, gap_threshold=13.0):
    """Catches a SECOND tier of section headings that `_section_headers()`
    above misses entirely: on this platform's richer "Property Information"
    page (Location and General Information / Taxes and HOA / Parking /
    Building Features / Interior Features / etc. -- present on some exports
    of this format but not others, see module docstring update below), the
    subsection headings render bold at the SAME 7.0pt size as ordinary
    label words, so the size>=7.5 threshold alone can't tell a heading like
    "Parking" apart from a label like "Parcel Number". What DOES reliably
    tell them apart: a heading is the only bold-only content on its row (no
    regular-weight value word shares it) AND sits with extra vertical
    whitespace above it (confirmed ~17-19pt on real samples vs. the sheet's
    normal ~10.5pt body line-height) -- headings get breathing room, plain
    label:value rows don't.

    That gap check specifically is what keeps this from misfiring on a
    label whose bold text happens to wrap across two lines with nothing
    else on the second line -- confirmed on a real sample where "Previous
    sold price" (a 3-word label) wrapped as "Previous sold" + value on one
    row and a lone bold "price" on the next: that wrapped "price" row IS
    all-bold with no value, but its gap from the row above is the sheet's
    normal ~10.5pt body spacing, not the ~17-19pt a real heading gets, so
    the gap threshold correctly excludes it while still catching every
    genuine heading. Returned separately from _section_headers() rather
    than merged into it -- see call sites, which combine both lists -- so
    this stays purely additive and can't change what already works there."""
    rows = []
    cur_top, cur = None, []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if cur_top is None or abs(w["top"] - cur_top) <= row_tol:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append(cur)
            cur, cur_top = [w], w["top"]
    if cur:
        rows.append(cur)

    out = []
    prev_top = None
    for row in rows:
        row_sorted = sorted(row, key=lambda w: w["x0"])
        top = row_sorted[0]["top"]
        all_bold = all("Bold" in (w.get("fontname") or "") for w in row_sorted)
        gap = (top - prev_top) if prev_top is not None else 999
        if all_bold and gap >= gap_threshold:
            out.append((" ".join(w["text"] for w in row_sorted), top))
        prev_top = top
    return out


def _all_headers(words):
    """Merges both header tiers (see _bold_row_headers) into one top-sorted
    list for _section_bounds()/_grid_section()/_section_rows() to use."""
    merged = {top: text for text, top in _section_headers(words)}
    for text, top in _bold_row_headers(words):
        merged.setdefault(top, text)
    return sorted(((text, top) for top, text in merged.items()), key=lambda x: x[1])


def _tidy_list(s):
    """"Aluminum Siding,Vinyl Siding" -> "Aluminum Siding, Vinyl Siding" --
    every comma-separated multi-value field on this platform's "Property
    Information" page grid renders with NO space after the comma in the
    underlying PDF content (confirmed directly on real samples: this is
    how the source text actually is, not a word-extraction artifact), which
    reads as run-together on a printed flyer. Only touches a bare comma
    with no following space, so it's safe to run on values that already
    have proper spacing (nothing to change) or that never had commas at
    all (e.g. "Insulated Windows,Screens,Skylights" ->
    "Insulated Windows, Screens, Skylights")."""
    return re.sub(r",(?=\S)", ", ", s or "")


def _fmt_dim(s):
    """"9.0" -> "9", "10.5" -> "10.5" -- strips a trailing whole-number
    ".0" so room dimensions read the way an agent would actually write
    them, matching the "L x W" convention parser.py/parser_michric.py's
    own room tables already use."""
    s = (s or "").strip()
    if not s:
        return ""
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f == int(f) else str(f)


_ROOM_MARKER_RE = re.compile(r"^Room\s+(\d+)$")


def _parse_room_dimensions(words, page_bottom):
    """Room-by-room dimensions from this platform's "Room Information"
    page (Client page 3 of 4 on every real sample seen) -- confirmed via a
    real word-position dump that each "Room N" marker ("Room" + a bare
    number, e.g. "Room 1") sits alone on its own row, both words bold and
    nothing else sharing that row -- a signal unique enough on this sheet
    that no header/section bounding is needed to find them, unlike every
    other section this file reads. Each room's own body directly below its
    marker is laid out exactly like Key Details' grid (bold label +
    regular value, up to 2 pairs per row: Type/Length on one row, Level/
    Width on the next, Dimensions alone on a third), so this slices the
    page's words between consecutive markers and hands each slice to the
    same _extract_kv_grid() every other grid section already uses, rather
    than reinventing that parsing.

    Builds `size` from the Length/Width fields (clean floats, e.g. "9.0")
    instead of the sheet's own pre-formatted "Dimensions" text, which was
    confirmed inconsistently spaced from room to room on the one real
    sample checked ("9x9" for some rooms, "10 x 10" for others, purely an
    artifact of how pdfplumber tokenized the "x" at different widths) --
    Length x Width is unambiguous and always present whenever Dimensions
    is, so it's the more reliable source for the same information.

    That same real sample has one room (Room 7) missing "Type" entirely --
    a genuine gap in the source MLS data, not a parsing failure: Level/
    Width/Dimensions/Length are all present for it, just no room name.
    Falls back to a bare "Room" label rather than dropping the row
    outright, since its dimensions are still real data worth showing."""
    rows = []
    cur_top, cur = None, []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if cur_top is None or abs(w["top"] - cur_top) <= 3:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append((cur_top, cur))
            cur, cur_top = [w], w["top"]
    if cur:
        rows.append((cur_top, cur))

    markers = []
    for top, row in rows:
        row_sorted = sorted(row, key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in row_sorted)
        all_bold = all("Bold" in (w.get("fontname") or "") for w in row_sorted)
        if all_bold and _ROOM_MARKER_RE.match(text):
            markers.append(top)

    if not markers:
        return []

    rooms = []
    for idx, start in enumerate(markers):
        end = markers[idx + 1] if idx + 1 < len(markers) else page_bottom
        kv = _extract_kv_grid(words, start, end)
        name = kv.get("Type", "").strip() or "Room"
        # "Bedroom2" (no space) shows up alongside properly-spaced
        # "Bedroom 2" for other rooms on the same real sample -- a genuine
        # MLS data-entry inconsistency, not a parsing artifact (confirmed
        # via word-level dump: the source literally has one token
        # "Bedroom2"). Cosmetic fix so it doesn't look broken on a
        # client-facing flyer.
        name = re.sub(r"([A-Za-z])(\d)", r"\1 \2", name)
        length = _fmt_dim(kv.get("Length", ""))
        width = _fmt_dim(kv.get("Width", ""))
        size = f"{length} x {width}" if length and width else ""
        rooms.append({"name": name, "size": size, "level": kv.get("Level", "").strip(), "flooring": ""})
    return rooms


def _section_bounds(headers, name, page_bottom):
    """Find `name` in the (text, top) header list and return (top, next_top)
    -- the vertical band belonging to that section, up to whichever
    header comes next (any heading, not just ones this parser knows
    about) or the bottom of the page."""
    for i, (text, top) in enumerate(headers):
        if text == name:
            nxt = headers[i + 1][1] if i + 1 < len(headers) else page_bottom
            return top, nxt
    return None


def _grid_section(words, headers, name, page_bottom):
    bounds = _section_bounds(headers, name, page_bottom)
    if not bounds:
        return {}
    top, nxt = bounds
    # Skip the header's own row itself (its words are also >=7.5pt bold,
    # which would otherwise be picked up as a spurious label/value pair).
    body_words = [w for w in words if w["top"] > top + 2]
    return _extract_kv_grid(body_words, top, nxt)


def _section_rows(words, headers, name, page_bottom):
    """One reading-order line of text per visual row within a section --
    the building block both _text_section (Description/Amenities, which
    want one joined paragraph) and _parse_schools (which needs each
    school kept on its own line) are built from."""
    bounds = _section_bounds(headers, name, page_bottom)
    if not bounds:
        return []
    top, nxt = bounds
    body = [w for w in words if top + 2 < w["top"] < nxt]
    body.sort(key=lambda w: (w["top"], w["x0"]))
    rows = []
    cur_top, cur = None, []
    for w in body:
        if cur_top is None or abs(w["top"] - cur_top) <= 3:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append(cur)
            cur, cur_top = [w], w["top"]
    if cur:
        rows.append(cur)
    return [" ".join(w["text"] for w in sorted(r, key=lambda w: w["x0"])) for r in rows]


def _text_section(words, headers, name, page_bottom):
    """Plain prose/list body text of a section (Description, Amenities),
    reconstructed in reading order rather than as a label:value grid."""
    return " ".join(_section_rows(words, headers, name, page_bottom)).strip()


# ---------------------------------------------------------------------------
# Header banner ("Client - 123 Main St Chicago IL 60640 Active $500,000
# 3 BD * 2 BA * 1 1/2 BA * 1,500 SF * $333/SF Page 1/2")
# ---------------------------------------------------------------------------

_BANNER_RE = re.compile(
    r"(?:Client|Agent)\s*-\s*(?P<addrcity>.+?)\s+(?P<state>[A-Z]{2})\s+"
    r"(?:(?P<zip>\d{5})\s+)?"
    r"(?P<status>Active\s*\(\s*Private\s*\)|Active|Pending|Contingent|Sold|Closed|"
    r"Coming Soon|Withdrawn|Expired|New)\s+"
    r"\$(?P<price>[\d,]+)\s+"
    r"(?P<beds>\d+)\s*BD\s*[•*]\s*(?P<bfull>\d+)\s*BA"
    r"(?:\s*[•*]\s*(?P<bhalf>\d+)\s*1/2\s*BA)?\s*[•*]\s*"
    r"(?P<sqft>[\d,]+)\s*SF"
)
# The zip capture above is optional because a long enough street/unit/city
# ("420 East Waterside Drive Unit 2602 Chicago IL 60601") wraps onto a
# second visual row in Home Platform's own PDF export -- confirmed on a
# real sample where the zip alone got pushed onto that second row while
# price/beds/baths/page-marker stayed on the first row to its right.
# pdfplumber's extract_text() groups words into lines by y-position, so
# that wrapped zip comes out on its OWN line, positioned after the rest of
# row 1's content in the extracted text rather than between state and
# status where a non-wrapped banner has it -- which broke the regex
# entirely (no match at all, not just a missing zip) when the zip capture
# was mandatory. Falling back to this below when inline capture misses.
_WRAPPED_ZIP_RE = re.compile(r"(?m)^(\d{5})$")

# Common street-type suffixes, used to split the banner's glued-together
# "<street address><city>" text (there's no delimiter between them --
# confirmed on every real sample: "1619 West Summerdale Avenue Chicago",
# "3126 Red Oak Drive Saugatuck"). Not exhaustive -- a street name with no
# recognized suffix (e.g. a bare "Broadway") falls back to treating just
# the last word as the city, which is a reasonable but imperfect guess.
_STREET_SUFFIXES = {
    "avenue", "ave", "street", "st", "drive", "dr", "road", "rd", "lane", "ln",
    "boulevard", "blvd", "way", "court", "ct", "place", "pl", "circle", "cir",
    "terrace", "ter", "parkway", "pkwy", "trail", "trl", "highway", "hwy",
    "square", "sq", "loop", "path", "row", "crossing", "xing", "pass", "walk",
    "point", "pt", "crescent", "cres", "close", "commons",
}
_UNIT_WORDS = {"unit", "apt", "apartment", "ste", "suite", "#"}


def _split_street_city(blob):
    words = (blob or "").split()
    if not words:
        return "", ""
    idx = None
    for i, w in enumerate(words):
        if w.strip(".,").lower() in _STREET_SUFFIXES:
            idx = i
    if idx is None:
        if len(words) < 2:
            return blob.strip(), ""
        return " ".join(words[:-1]).strip(), words[-1].strip()
    j = idx + 1
    if j < len(words) and words[j].strip(".,#").lower() in _UNIT_WORDS:
        j = min(j + 2, len(words))
    return " ".join(words[:j]).strip(), " ".join(words[j:]).strip()


_STATUS_MAP = {
    "active": "ACTV",
    "active ( private )": "PRIV-ACTV",
    "pending": "PEND",
    "contingent": "CTG",
    "sold": "SOLD",
    "closed": "CLSD",
    "coming soon": "NEW",
    "new": "NEW",
    "withdrawn": "EXP",
    "expired": "EXP",
}


def _parse_banner(page1_text):
    m = _BANNER_RE.search(page1_text)
    out = {}
    if not m:
        return out
    street, city = _split_street_city(m.group("addrcity"))
    out["address_line1"] = street
    out["city"] = city
    out["state"] = m.group("state")
    zip_code = m.group("zip")
    if not zip_code:
        # Wrapped-address case -- see _WRAPPED_ZIP_RE comment above. Scan
        # only a short prefix of the page so an unrelated standalone
        # 5-digit line further down the sheet (there aren't any this early
        # in practice, but bounding it costs nothing) can't get mistaken
        # for the zip.
        zm = _WRAPPED_ZIP_RE.search(page1_text[:800])
        zip_code = zm.group(1) if zm else ""
    out["zip_code"] = zip_code
    out["status"] = _STATUS_MAP.get(re.sub(r"\s+", " ", m.group("status").strip().lower()), "")
    out["list_price"] = f"${m.group('price')}"
    out["bedrooms"] = m.group("beds")
    out["bathrooms_full"] = m.group("bfull")
    out["bathrooms_half"] = m.group("bhalf") or "0"
    out["approx_sf"] = m.group("sqft")
    return out


# ---------------------------------------------------------------------------
# Schools (name + grade range + "Serves this home"/"Nearby school"/
# "Choice school" + rating -- richer than MRED/MichRIC's bare district
# code, but needs bucketing into the shared elementary/junior_high/
# high_school fields the flyer template already expects).
# ---------------------------------------------------------------------------

_SCHOOL_LINE_RE = re.compile(
    r"^(?P<name>.+?)\s+(?:Public|Charter|Private)\s*[••]\s*"
    r"(?P<grades>[A-Za-z0-9\-]+)\s*[••]\s*(?P<note>.+?)\s*"
    r"(?:Rating:\s*(?P<rating>\d+/10))?$",
    re.IGNORECASE,
)


def _parse_schools(lines):
    """Only schools flagged "Serves this home" are this property's actual
    assigned schools -- "Nearby school"/"Choice school" entries are other
    options in the area, not what the address is zoned for, so those are
    left out of the flyer's Elementary/Middle/High fields entirely rather
    than risk implying they're assigned."""
    elem = mid = high = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = _SCHOOL_LINE_RE.match(line)
        if not m or "serves this home" not in m.group("note").lower():
            continue
        name = m.group("name").strip()
        grades = m.group("grades").upper()
        name_low = name.lower()
        if not elem and ("elementary" in name_low or grades.startswith("PK") or grades.startswith("K-")):
            elem = name
        elif not high and ("high" in name_low or grades.endswith("-12")):
            high = name
        elif not mid and ("middle" in name_low or "junior" in name_low or "jr" in name_low):
            mid = name
    return elem, mid, high


# ---------------------------------------------------------------------------
# Transit (e.g. "Paulina Brown Line 3 min * 0.16 mi away", "Addison &
# Paulina 152 1 min * 0.07 mi away" -- a mix of train stops and bus routes
# with no consistent delimiter between the stop/route name and the walk
# time, so this only pulls out the walk time and treats everything before
# it as one description rather than trying to cleanly split stop name from
# line/route name.
# ---------------------------------------------------------------------------

_TRANSIT_LINE_RE = re.compile(
    r"^(?P<desc>.+?)\s+(?P<mins>\d+)\s*min\s*[••]\s*[\d.]+\s*mi",
    re.IGNORECASE,
)


def _parse_transit(lines, limit=2):
    """First `limit` parseable stops, in the sheet's own listed order (not
    re-sorted by walk time -- the sheet already lists Paulina/Addison-Brown/
    Southport/... nearest-train-first in every real sample seen, a bus
    route mixed in further down isn't worth the complexity of re-ranking
    across mode types for a 2-line flyer card)."""
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = _TRANSIT_LINE_RE.match(line)
        if not m:
            continue
        out.append(f"{m.group('desc').strip()} — {m.group('mins')} min walk")
        if len(out) >= limit:
            break
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Photo -- the property photo is always the LEFTMOST image in the header's
# photo row (top of page); the Google Maps thumbnail sits to its right and
# the agent headshot further right still, confirmed identical positioning
# across every real sample regardless of property. Picking by x-position
# rather than image size/order avoids ever grabbing the map or headshot.
# ---------------------------------------------------------------------------

def _extract_photo(file_bytes, listing):
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page = doc[0]
        candidates = []
        for img in page.get_images(full=True):
            xref = img[0]
            rects = page.get_image_rects(xref)
            for r in rects:
                if r.y0 < 150:  # header photo row only
                    candidates.append((r.x0, xref))
        if candidates:
            candidates.sort(key=lambda c: c[0])
            xref = candidates[0][1]
            base = doc.extract_image(xref)
            listing.photo_bytes = base["image"]
            listing.photo_ext = base.get("ext", "jpg")
        doc.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def _page_kind(text):
    stripped = (text or "").lstrip()
    if stripped.startswith("Client -") or stripped.startswith("Client-"):
        return "client"
    if stripped.startswith("Agent -") or stripped.startswith("Agent-"):
        return "agent"
    return None


def _lot_size_from(details):
    for key in ("Lot Acres", "Lot Sq. Ft", "Lot Dimensions"):
        val = (details.get(key) or "").strip()
        if val and val != "-":
            return val
    return ""


def parse_listing_pdf(file_bytes: bytes, source_filename: str = "") -> Listing:
    listing = Listing(source_filename=source_filename)

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        page_texts = [p.extract_text() or "" for p in pdf.pages]
        kinds = [_page_kind(t) for t in page_texts]

        # Prefer the Client flavor when present (simpler, and everything
        # this flyer needs is already there -- see module docstring);
        # fall back to Agent-only input if that's all that was uploaded.
        if "client" in kinds:
            use_idx = [i for i, k in enumerate(kinds) if k == "client"]
        elif "agent" in kinds:
            use_idx = [i for i, k in enumerate(kinds) if k == "agent"]
        else:
            use_idx = []
        # Agent pages specifically -- a few fields (Num Of Rooms in
        # particular, see the rooms_total_raw scan below) only ever appear
        # on the Agent flavor, confirmed absent from every Client page on
        # every real sample checked. When a combined Client+Agent upload
        # prefers Client above, those Agent-only pages are still worth a
        # second, narrower pass for just that handful of fields rather
        # than losing them entirely.
        agent_idx = [i for i, k in enumerate(kinds) if k == "agent"]

        if not use_idx:
            # Not actually this platform's format (shouldn't happen --
            # mls_router.py only dispatches here on a positive signature
            # match) -- return the mostly-empty listing rather than
            # guessing at a page layout with no recognizable banner.
            return listing

        banner = _parse_banner(page_texts[use_idx[0]])
        for field_name, val in banner.items():
            setattr(listing, field_name, val)

        details = {}      # Key Details
        history = {}      # Property History
        propdetails = {}  # Property Details / Building Details -- see the
                           # total_stories/total_units/unit_floor_level
                           # dispatch below for why these two differently-
                           # named sections are folded into one dict.
        pubrecords = {}   # Public Records
        # These four only exist on the richer "Property Information" page
        # some exports of this platform's sheet include (see
        # _bold_row_headers() docstring) -- a simpler export without that
        # page just leaves these empty, same as before this was added.
        taxeshoa = {}      # Taxes and HOA
        parking = {}       # Parking
        buildingfeat = {}  # Building Features
        interiorfeat = {}  # Interior Features
        utilities = {}     # Utilities (Water Source/Sewer/Electric/fuel)
        exteriorfeat = {}  # Exterior Features (nested under "Interior and
                            # Exterior Features", a sibling of Interior
                            # Features -- see the Interior Features comment
                            # below for why these are two separate nested
                            # sub-headings rather than one combined section)
        rooms_total_raw = ""
        garage_cost_raw = ""
        rooms_raw = []     # Room Information page -- see
                            # _parse_room_dimensions()
        description = ""
        amenities_text = ""
        schools_lines = []
        transit_lines = []

        for i in use_idx:
            page = pdf.pages[i]
            words = page.extract_words(extra_attrs=["fontname", "size"])
            # Drop fine-print words (the compliance disclaimer paragraph
            # renders at ~5.0pt, vs. ~7.0pt for every real label/value/
            # description/amenities word and ~7.5-8.0pt for headers,
            # confirmed directly on real samples -- see module docstring's
            # "7.0pt body" convention). Without this, a section with
            # nothing else below it on the page (Amenities in particular,
            # which sits last before the disclaimer block on every real
            # sample seen) has no next-header boundary to stop at and
            # _text_section() swallows the entire multi-hundred-word
            # disclaimer paragraph into that section's value.
            words = [w for w in words if w.get("size", 0) >= 6.0]
            headers = _all_headers(words)
            bottom = page.height

            d = _grid_section(words, headers, "Key Details", bottom)
            if d:
                details.update(d)
            h = _grid_section(words, headers, "Property History", bottom)
            if h:
                history.update(h)
            # Newer exports rename this section "Building Details" -- same
            # content, confirmed on a real sample (420 E Waterside Dr, a
            # high-rise condo) where "Property Details" doesn't appear
            # anywhere on the sheet at all, but "Building Details" carries
            # the exact same Building/Complex, Total Units, Total Stories,
            # Unit Floor, Lot Sq. Ft/Dimensions fields the older-template
            # samples (Paulina, Red Oak) filed under "Property Details".
            pd = (_grid_section(words, headers, "Property Details", bottom)
                  or _grid_section(words, headers, "Building Details", bottom))
            if pd:
                propdetails.update(pd)
            # Public Records -- a county-assessor data block also present
            # on this sheet (Client page 2, Agent's equivalent page), that
            # carries a County field this platform otherwise doesn't have
            # anywhere (see module docstring's "no County field at all").
            # Also carries the current owner's name and mailing address --
            # deliberately only County gets read out of this dict below;
            # never add a wholesale dump of this section to the flyer.
            pr = _grid_section(words, headers, "Public Records", bottom)
            if pr:
                pubrecords.update(pr)
            th = _grid_section(words, headers, "Taxes and HOA", bottom)
            if th:
                taxeshoa.update(th)
            pk = _grid_section(words, headers, "Parking", bottom)
            if pk:
                parking.update(pk)
            bf = _grid_section(words, headers, "Building Features", bottom)
            if bf:
                buildingfeat.update(bf)
            ut = _grid_section(words, headers, "Utilities", bottom)
            if ut:
                utilities.update(ut)
            # NOT "Interior and Exterior Features" -- that's the parent
            # heading; the actual Total Rooms/Total Fireplaces/Basement/
            # Bathrooms grid sits under its own nested "Interior Features"
            # sub-heading further down (confirmed on a real sample: "Interior
            # and Exterior Features" is immediately followed by an "Exterior
            # Features" sub-section, THEN "Interior Features"). "Exterior
            # Features" (Patio And Porch Features, Private Pool) is that
            # sibling sub-section, read out separately here for the same
            # reason.
            extf = _grid_section(words, headers, "Exterior Features", bottom)
            if extf:
                exteriorfeat.update(extf)
            intf = _grid_section(words, headers, "Interior Features", bottom)
            if intf:
                interiorfeat.update(intf)
            # Room Information -- a dedicated page (Client page 3 of 4 on
            # every real sample seen) of room-by-room Type/Level/Dimensions
            # data. No header/section bounding needed to find it -- see
            # _parse_room_dimensions()'s own docstring -- so this just
            # tries every page in use_idx and naturally comes back empty on
            # every page that isn't the Room Information page.
            if not rooms_raw:
                rooms_raw = _parse_room_dimensions(words, bottom)
            if not description:
                description = _text_section(words, headers, "Description", bottom)
            if not amenities_text:
                amenities_text = _text_section(words, headers, "Amenities", bottom)
            if not schools_lines:
                schools_lines = _section_rows(words, headers, "Schools", bottom)
            if not transit_lines:
                transit_lines = _section_rows(words, headers, "Transit", bottom)

        # A handful of fields (Num Of Rooms, Deeded Garage Cost) live
        # inside subsections ("Interior Features", "Addtl Parking
        # Information") of the page-spanning "Property Information"
        # section. First assumed that whole section was Agent-only (true
        # of every sample checked at the time), but a later real sample --
        # same listing, 420 E Waterside Dr, re-exported -- turned up a
        # 4-page ALL-CLIENT export that includes this exact section
        # (Confidential Data/Showing Info/Interior Features/Num Of Rooms/
        # Addtl Parking Information and all) under "Client -" banners
        # throughout, no Agent pages in the file at all. So Client-vs-
        # Agent doesn't reliably predict whether this section is present
        # -- it depends on which export variant Home Platform generated,
        # not (as first assumed) which flavor. Scanning the union of
        # `use_idx` (whichever flavor is actually in use) and `agent_idx`
        # (in case Agent pages exist separately and weren't otherwise
        # selected) covers every variant seen so far without needing to
        # special-case any of them. Also handles a field landing on a
        # headerless continuation page: these subsection headings are
        # themselves bold 7.0pt text, one visual tier below the >=7.5pt
        # threshold _section_headers() requires to count as a real section
        # header -- confirmed on the original Agent-only sample where
        # "Property Information" starts on one page and "Interior
        # Features"/"Num Of Rooms" only show up on the NEXT page, which
        # has no >=7.5pt bold text anywhere on it, so
        # _grid_section("Property Information", ...) can't find a
        # bounding header there and comes back empty. Scanning each page's
        # whole grid unbounded by any section sidesteps reconstructing
        # cross-page section continuity -- both labels here are unique and
        # don't collide with anything else on this sheet, so this is safe
        # even though it ignores section boundaries entirely.
        for i in sorted(set(use_idx) | set(agent_idx)):
            if rooms_total_raw and garage_cost_raw:
                break
            page = pdf.pages[i]
            words = page.extract_words(extra_attrs=["fontname", "size"])
            words = [w for w in words if w.get("size", 0) >= 6.0]
            raw = _extract_kv_grid(words, 0, page.height)
            if not rooms_total_raw:
                val = raw.get("Num Of Rooms", "")
                if val and not _is_nullish(val):
                    rooms_total_raw = val
            if not garage_cost_raw:
                val = raw.get("Deeded Garage Cost", "")
                if val and not _is_nullish(val):
                    garage_cost_raw = val

    # --- MLS / property basics ---------------------------------------------
    listing.mls_number = details.get("MLS ID", "")
    ptype = details.get("Property Type", "")
    if ptype:
        listing.property_type = ptype
    style = details.get("Architectural Style", "")
    if style:
        listing.architectural_style = style
    if not listing.year_built:
        listing.year_built = details.get("Year Built", "")
    ownership = details.get("Ownership", "")
    if ownership:
        listing.ownership = ownership

    # County -- not present anywhere in Key Details/Property Details on
    # this sheet (see module docstring), but IS present in the separate
    # Public Records section, which otherwise only carries county-assessor
    # data (owner name/mailing address, assessed value breakdown) this
    # flyer has no business showing -- County is the one field from that
    # section worth reading out. Fixes the geocoding fallback gap noted in
    # jlg-showing-packet's packet.py (_county_level()) for listings from
    # this source.
    county = pubrecords.get("County", "")
    if county and not _is_nullish(county):
        listing.county = county.title()

    if rooms_total_raw:
        listing.rooms_total = rooms_total_raw

    # "Deeded Garage Cost" ($35,000.00) -- the field's own name already
    # tells us the ownership type (deeded), so this is built into the same
    # "<Ownership> (<$cost>)" shape classic MRED's "Garage Ownership:"
    # field uses ("Deeded Sold Separately ($25,000)") -- garage_ownership
    # is a shared Listing field, and both parking_note() and
    # feature_groups() in render.py already know how to surface it (they
    # only care that a "$" is in the string), so no template/render.py
    # changes are needed here. Cents are stripped ("$35,000.00" ->
    # "$35,000") to match that same MRED convention, which never carries
    # cents. This platform's sheet has no separate non-deeded/leased
    # parking-cost field seen on any real sample yet -- only add one here
    # once an actual sample surfaces it, per the "verified against real
    # samples" rule the rest of this parser follows.
    if garage_cost_raw:
        cost = re.sub(r"\.00$", "", garage_cost_raw)
        listing.garage_ownership = f"Deeded ({cost})"

    # Interior fireplace count -- shares the same `fireplaces` field the
    # classic MRED parser populates from "# Fireplaces:" (see parser.py),
    # already wired into the facts strip there; this sheet just labels it
    # differently ("Num of Interior Fireplaces").
    fireplaces = details.get("Num of Interior Fireplaces", "")
    if fireplaces and not _is_nullish(fireplaces):
        listing.fireplaces = fireplaces
    elif not listing.fireplaces:
        # Key Details doesn't carry this label on the richer "Property
        # Information" page variant (see _bold_row_headers()) -- there it
        # only shows up nested under Interior Features as "Total
        # Fireplaces". Only used as a fallback since some sheets carry
        # both and the Key Details label above is the one already proven
        # against real samples.
        tf = interiorfeat.get("Total Fireplaces", "")
        if tf and not _is_nullish(tf):
            listing.fireplaces = tf

    # Room count -- available two ways on this platform, from two different
    # real samples: the "Num Of Rooms" Agent/Confidential-Data scan above
    # (rooms_total_raw, already applied to listing.rooms_total before this
    # point) and, on the richer "Property Information" page variant, a
    # nested Interior Features "Total Rooms" field. Only seen one or the
    # other present on any single real sample so far, never both, so
    # there's no real-sample evidence for which should win if a future
    # export somehow carries both -- guarding here just means "don't
    # clobber a value the other path already found" rather than asserting
    # a precedence this file can't yet back up.
    if not listing.rooms_total:
        rooms_total = interiorfeat.get("Total Rooms", "")
        if rooms_total and not _is_nullish(rooms_total):
            listing.rooms_total = rooms_total

    # Open house -- Key Details' compact one-line version ("Sat, Sep 19th
    # 11:00 AM - 1:00 PM"), already sitting in `details` from the same grid
    # grab as everything else above. There's a richer, separate "Open
    # Houses" section with Date/Time/Type/Contact broken into its own
    # fields, but the compact form is all the flyer's badge needs.
    open_house = details.get("Open House", "")
    if open_house and not _is_nullish(open_house):
        listing.open_house = open_house

    # --- Parking/garage ------------------------------------------------------
    garage_spaces = details.get("Num Of Garage Spaces", "")
    parking_spaces = details.get("Num Of Parking Spaces", "")
    if garage_spaces and not _is_nullish(garage_spaces):
        n = garage_spaces.split(".")[0]
        listing.parking_type = "Garage"
        listing.parking_spaces = n
    elif parking_spaces and not _is_nullish(parking_spaces):
        n = parking_spaces.split(".")[0]
        listing.parking_type = "Space/s"
        listing.parking_spaces = n
    elif not listing.parking_spaces:
        # Key Details' "Num Of Garage Spaces"/"Num Of Parking Spaces"
        # labels don't exist on the richer "Property Information" page
        # variant -- there the count lives in its own "Parking" section
        # as "Garage Spaces" instead (e.g. "2.5"). Only used as a
        # fallback since the Key Details labels above are the ones
        # already proven against real samples.
        garage_spaces2 = parking.get("Garage Spaces", "")
        if garage_spaces2 and not _is_nullish(garage_spaces2):
            listing.parking_type = "Garage"
            listing.parking_spaces = garage_spaces2.split(".")[0]
    incl = details.get("Parking Included in Price", "")
    if incl:
        listing.parking_incl_in_price = incl

    # --- Heating/cooling (MRED-sourced listings only carry these here) -----
    heat = details.get("Heat/Fuel", "")
    if heat:
        listing.heating = heat
    elif not listing.heating:
        # Key Details doesn't carry Heat/Fuel on the richer "Property
        # Information" page variant -- there it's a plain "Heating" field
        # nested under Interior Features instead (e.g. "Forced Air,
        # Propane"). _tidy_list() adds a space after each comma -- this
        # sheet's own grid values have none ("Forced Air,Propane"), which
        # reads as run-together on a printed flyer.
        heat2 = interiorfeat.get("Heating", "")
        if heat2 and not _is_nullish(heat2):
            listing.heating = _tidy_list(heat2)
    cooling = details.get("Air Conditioning Type", "")
    if cooling:
        listing.cooling = cooling
    elif not listing.cooling:
        cooling2 = interiorfeat.get("Cooling", "")
        if cooling2 and not _is_nullish(cooling2):
            listing.cooling = _tidy_list(cooling2)

    # --- Appliances / laundry / fireplace type (richer page variant only) --
    appliances = interiorfeat.get("Appliances", "")
    if appliances and not _is_nullish(appliances):
        listing.appliances = _tidy_list(appliances)
    laundry = interiorfeat.get("Laundry Features", "")
    if laundry and not _is_nullish(laundry):
        listing.laundry = _tidy_list(laundry)
    fireplace_features = interiorfeat.get("Fireplace Features", "")
    if fireplace_features and not _is_nullish(fireplace_features):
        listing.fireplace_details = _tidy_list(fireplace_features)

    # --- Interior features (base value + Flooring/Window/Security folded
    # in, same "; Label: value" convention parser.py/parser_michric.py use
    # for extra bits that don't warrant their own card) --------------------
    interior_parts = []
    interior_base = interiorfeat.get("Interior Features", "")
    if interior_base and not _is_nullish(interior_base):
        interior_parts.append(_tidy_list(interior_base))
    flooring = interiorfeat.get("Flooring", "")
    if flooring and not _is_nullish(flooring):
        interior_parts.append(f"Flooring: {_tidy_list(flooring)}")
    window_features = interiorfeat.get("Window Features", "")
    if window_features and not _is_nullish(window_features):
        interior_parts.append(f"Windows: {_tidy_list(window_features)}")
    security_features = interiorfeat.get("Security Features", "")
    if security_features and not _is_nullish(security_features):
        interior_parts.append(f"Security: {_tidy_list(security_features)}")
    if interior_parts:
        listing.interior_features = "; ".join(interior_parts)

    # --- Exterior features (Building Features' Construction Materials/Roof
    # + the nested Exterior Features sub-section's Patio/Porch + Pool) -----
    exterior_parts = []
    construction = buildingfeat.get("Construction Materials", "")
    if construction and not _is_nullish(construction):
        exterior_parts.append(_tidy_list(construction))
    roof = buildingfeat.get("Roof", "")
    if roof and not _is_nullish(roof):
        exterior_parts.append(f"Roof: {_tidy_list(roof)}")
    patio_porch = exteriorfeat.get("Patio And Porch Features", "")
    if patio_porch and not _is_nullish(patio_porch):
        exterior_parts.append(f"Patio/Porch: {_tidy_list(patio_porch)}")
    if exterior_parts:
        listing.exterior_features = "; ".join(exterior_parts)
    pool = exteriorfeat.get("Private Pool", "")
    if pool and not _is_nullish(pool):
        listing.pool = pool

    # --- Water source / sewer (Utilities section, richer page variant only)
    # Brian's guidance (see render.py's water_utilities_display()): for
    # Michigan buyers specifically, well-vs-municipal water and septic-vs-
    # public sewer are real cost/maintenance facts worth their own card,
    # not a minor detail to bury elsewhere. Previously only ever populated
    # for MichRIC-sourced listings -- this is the Home Platform equivalent.
    water_source = utilities.get("Water Source", "")
    if water_source and not _is_nullish(water_source):
        listing.water_source = water_source
    sewer = utilities.get("Sewer", "")
    if sewer and not _is_nullish(sewer):
        listing.sewer_type = _tidy_list(sewer)

    # --- Garage attached/detached (Parking Features, richer page variant
    # only) -- parking_type/parking_spaces above only say "Garage" + a
    # count, not whether it's attached or detached, which is the specific
    # fact buyers actually ask about (see render.py's feature_groups()).
    parking_features = parking.get("Parking Features", "")
    if parking_features and not listing.garage_type:
        pf_low = parking_features.lower()
        if "attached" in pf_low and "detached" not in pf_low:
            listing.garage_type = "Attached"
        elif "detached" in pf_low:
            listing.garage_type = "Detached"

    # --- Room-by-room dimensions (Room Information page, richer page
    # variant only) -- see _parse_room_dimensions(). render.py/flyer.html
    # already know how to lay this out as a Room Dimensions table; this
    # platform just never fed it one before.
    if rooms_raw:
        listing.rooms = rooms_raw

    # --- Waterfront (MichRIC-sourced listings only carry these here) -------
    if details.get("Has Waterfront", "").strip().lower() == "yes":
        listing.waterfront = "Yes"
    wf = details.get("Waterfront Features", "")
    if wf and not _is_nullish(wf):
        listing.water_features = wf

    # --- Taxes / HOA ---------------------------------------------------------
    taxes = details.get("Taxes", "")
    if taxes and not _is_nullish(taxes):
        listing.tax_amount = money(taxes.split("/")[0].strip())
    hoa = details.get("HOA Fees", "")
    if hoa and not _is_nullish(hoa):
        amt, _, freq = hoa.partition("/")
        listing.assessment_amount = money(amt.strip())
        listing.assessment_frequency = freq.strip().capitalize() or "mo"

    # Current taxable value (Michigan Prop A) -- only available on the
    # richer "Property Information" page variant, in its own "Taxes and
    # HOA" section as "Tax Assessed Value". Confirmed via mill-rate math
    # on a real sample (annual tax / this figure * 1000 landed on ~48.6
    # mills, a plausible MI rate) that this is the CURRENT taxable value
    # the shown tax bill is actually based on, NOT the SEV (which uncaps
    # to match this the year after a sale) -- so it maps to
    # tax_taxable_value, which render.py's tax_uncap_note() actively uses
    # for a precise homestead/non-homestead post-sale $/yr estimate,
    # rather than tax_sev, which nothing reads for display.
    taxable_value = taxeshoa.get("Tax Assessed Value", "")
    if taxable_value and not _is_nullish(taxable_value):
        listing.tax_taxable_value = money(taxable_value)

    # --- Property History: dates/DOM/price history --------------------------
    list_date = history.get("List date", "")
    if list_date and not _is_nullish(list_date):
        listing.list_date = list_date
    cur_price = history.get("Current price", "")
    if cur_price and not _is_nullish(cur_price):
        listing.list_price = money(cur_price)
    orig_price = history.get("List price", "")
    if orig_price and not _is_nullish(orig_price):
        listing.orig_list_price = money(orig_price)
    dom = history.get("DOM / CDOM", "")
    if dom and not _is_nullish(dom):
        parts = [p.strip() for p in dom.split("/")]
        if len(parts) == 2 and all(p.replace(",", "").isdigit() for p in parts):
            listing.dom_list_side, listing.dom_total = parts[0], parts[1]

    # --- Property Details: lot/stories ---------------------------------------
    lot = _lot_size_from(propdetails)
    if lot:
        listing.lot_size = lot
    # `listing.stories` (stories in the home itself, paired with Basement/
    # Fireplaces on the facts strip) vs. `listing.total_stories` (a condo
    # BUILDING's floor count, paired with Total Units/Unit Floor Level) is
    # a real distinction the shared Listing model and flyer.html template
    # already draw for classic MRED (see parser.py's own "Type
    # Detached/Attached: 2 Stories" -> listing.stories vs. "# Stories:" ->
    # listing.total_stories, with the latter's own comment noting it's
    # "especially relevant for condos/co-ops"). This sheet's "Total
    # Stories" field means one or the other depending on what ELSE is in
    # the same section: on a single-family Ranch (Red Oak Dr) it's alone,
    # with no Total Units/Unit Floor Level fields anywhere on the sheet --
    # that's the home's own story count. On a high-rise condo (420 E
    # Waterside Dr) it sits right alongside Total Units/Unit Floor in the
    # same grid -- that's the BUILDING's floor count, and Total Units/
    # Unit Floor themselves were never being captured into the Listing
    # model at all before this fix. flyer.html's facts-strip-secondary
    # picks its whole second row based on whether ANY of total_units/
    # total_stories/unit_floor_level is set, so getting this dispatch
    # wrong either swaps in a Total Units/Unit Floor row that's blank for
    # a house, or (the bug Brian actually hit) leaves a condo's real
    # building data uncaptured and falls back to a Basement/Fireplaces/
    # Stories row where Stories is also empty.
    total_units = propdetails.get("Total Units", "")
    unit_floor = propdetails.get("Unit Floor", "")
    stories = propdetails.get("Total Stories", "")
    is_building_info = (
        (total_units and not _is_nullish(total_units))
        or (unit_floor and not _is_nullish(unit_floor))
    )
    if is_building_info:
        if total_units and not _is_nullish(total_units):
            listing.total_units = total_units
        if unit_floor and not _is_nullish(unit_floor):
            listing.unit_floor_level = unit_floor
        if stories and not _is_nullish(stories):
            listing.total_stories = stories
    elif stories and not _is_nullish(stories):
        listing.stories = stories
    else:
        # Property Details' own "Total Stories" comes back blank ("-") on
        # every MRED-sourced sample seen so far -- but MRED-sourced
        # listings put the story count in Key Details' "MLS Prop Type 2"
        # instead, as free text like "2 Stories" (this mirrors the
        # classic MRED sheet's own "Type Detached/Attached: 2 Stories"
        # field, which is exactly what this fallback is patterned after).
        # MichRIC-sourced listings do the reverse: Total Stories is
        # populated directly (the branch above), and MLS Prop Type 2
        # holds a property-type string instead ("Single Family
        # Residence") that this regex simply won't match -- confirmed on
        # real samples of both, so this fallback only ever fires when
        # it's actually needed. The optional "+" before "Stor" handles a
        # condo's own "High Rise (7+ Stories)" phrasing (that listing
        # normally hits the is_building_info branch above instead, but a
        # condo sheet with Total Units/Unit Floor genuinely blank would
        # otherwise silently fail to match here since the literal "+"
        # sits between the digit and "Stories").
        m = re.search(r"(\d+(?:\.\d+)?)\+?\s*Stor", details.get("MLS Prop Type 2", ""), re.IGNORECASE)
        if m:
            listing.stories = m.group(1)

    # --- Basement (Building Features, richer page variant only) --------------
    # Preferred over the Amenities substring fallback below -- direct field,
    # not a comma-split guess, and correctly handles values like "Crawl
    # Space" that don't contain the word "basement" at all (so the fallback
    # below would never have caught them, and shouldn't have to guess a
    # basement-vs-crawlspace distinction from a token that doesn't say
    # either). basement_display() in render.py accepts any string here and
    # renders it as-is (plus an optional "(bath included)" suffix), so this
    # doesn't need any Yes/No normalization the way the amenities fallback
    # does.
    foundation = buildingfeat.get("Foundation Details", "")
    if foundation and not _is_nullish(foundation):
        listing.basement = foundation

    # --- Amenities / Description / Schools -----------------------------------
    if amenities_text:
        listing.amenities = amenities_text
        # This sheet has no dedicated Basement field -- it only ever shows
        # up as a token inside the Amenities list, either bare
        # ("Basement") or with a finish/size qualifier ("Full Basement"),
        # confirmed on real samples of both -- so a substring match (not
        # exact) is needed to catch the qualified form. Some listings
        # (3541 N Paulina) carry BOTH tokens in the same list ("...
        # Fireplace, Basement, Forced Air, Range, Full Basement, Park,
        # ...") -- MRED appears to emit a bare "Basement" amenity flag
        # alongside a separately-sourced finish/size qualifier rather than
        # one or the other. Taking the first match blindly picked the bare
        # "Basement" and threw away the more useful "Full Basement" sitting
        # right next to it, so this explicitly prefers any qualified token
        # over the bare one when both are present, and only falls back to
        # bare "Basement" -> "Yes" when that's genuinely all the sheet
        # gives us.
        if not listing.basement:
            basement_tokens = [t.strip() for t in amenities_text.split(",") if "basement" in t.lower()]
            if basement_tokens:
                basement_item = next((t for t in basement_tokens if t.lower() != "basement"), basement_tokens[0])
                listing.basement = "Yes" if basement_item.lower() == "basement" else basement_item
    if description:
        listing.remarks = description
    if schools_lines:
        elem, mid, high = _parse_schools(schools_lines)
        listing.elementary = elem
        listing.junior_high = mid
        listing.high_school = high
    if transit_lines:
        nearby_transit = _parse_transit(transit_lines)
        if nearby_transit:
            listing.nearby_transit = nearby_transit

    _extract_photo(file_bytes, listing)

    return listing
