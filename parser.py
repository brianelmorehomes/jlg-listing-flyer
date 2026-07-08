"""
MRED / MLS listing sheet parser.

Extracts structured, buyer-relevant fields plus the embedded property photo
from a standard MRED-style MLS listing sheet PDF (the kind produced by
MRED Connect / MLS "Full Report" export), so it can be rendered into a
branded, client-facing flyer.

This is built and tuned against the "Attached Single" (condo) template but
uses generic label:value scraping so it degrades gracefully on other
property types (detached single, multi-unit) rather than crashing.
"""
import io
import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF
import pdfplumber


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _grab(text, label, stop_labels):
    """Grab the value following `label:` up until the next of `stop_labels`,
    a newline, or end of string. Labels/stops are plain strings (already
    escaped) matched literally."""
    pattern = re.escape(label) + r":\s*(.*?)(?=" + "|".join(re.escape(s) for s in stop_labels) + r"|\n|$)"
    m = re.search(pattern, text)
    if not m:
        return ""
    return m.group(1).strip(" ,")


def _first_num(s):
    m = re.search(r"[\d,]+", s or "")
    return m.group(0) if m else ""


def _column_text(words, x_min, x_max, top_min, top_max, row_tol=3):
    """Reconstruct text for one column of a multi-column layout by selecting
    whole words whose *start* x-position falls inside [x_min, x_max) and whose
    top falls inside [top_min, top_max), grouping into rows by proximity.

    This is safer than pdfplumber's bbox-cropping for narrow columns because
    it assigns each *whole word* to a column (by where the word begins)
    instead of clipping glyphs at a hard pixel boundary, which can chop a
    word in half when it happens to straddle the column edge.
    """
    picked = [
        w for w in words
        if top_min <= w["top"] < top_max and x_min <= w["x0"] < x_max
    ]
    picked.sort(key=lambda w: (w["top"], w["x0"]))
    rows = []
    current_top = None
    current_words = []
    for w in picked:
        if current_top is None or abs(w["top"] - current_top) <= row_tol:
            current_words.append(w)
            current_top = w["top"] if current_top is None else current_top
        else:
            rows.append(current_words)
            current_words = [w]
            current_top = w["top"]
    if current_words:
        rows.append(current_words)
    lines = [" ".join(w["text"] for w in row) for row in rows]
    return "\n".join(lines)


def money(s):
    if not s:
        return ""
    s = s.strip()
    if not s.startswith("$"):
        return s
    return s


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Listing:
    mls_number: str = ""
    property_type: str = ""
    status: str = ""
    list_date: str = ""
    dom_list_side: str = ""
    dom_total: str = ""
    list_price: str = ""
    address_line1: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    directions: str = ""

    bedrooms: str = ""
    bathrooms_full: str = ""
    bathrooms_half: str = ""
    rooms_total: str = ""
    approx_sf: str = ""
    year_built: str = ""
    age: str = ""
    ownership: str = ""

    parking_type: str = ""
    parking_spaces: str = ""
    garage_details: str = ""
    parking_incl_in_price: str = ""
    lot_size: str = ""

    total_units: str = ""
    total_stories: str = ""
    unit_floor_level: str = ""

    assessment_amount: str = ""
    assessment_frequency: str = ""
    assessment_includes: str = ""
    special_assessments: str = ""

    tax_amount: str = ""
    tax_year: str = ""
    tax_exemptions: str = ""
    mult_pins: str = ""

    elementary: str = ""
    junior_high: str = ""
    high_school: str = ""

    pets_allowed: str = ""
    max_pet_weight: str = ""

    remarks: str = ""

    interior_features: str = ""
    exterior_features: str = ""
    heating: str = ""
    cooling: str = ""
    kitchen_features: str = ""
    appliances: str = ""
    bath_amenities: str = ""
    amenities: str = ""
    laundry: str = ""

    rooms: list = field(default_factory=list)  # list of dicts: name/size/level/flooring

    list_broker_name: str = ""
    list_brokerage: str = ""
    list_broker_phone: str = ""

    photo_bytes: bytes = None
    photo_ext: str = "jpg"

    source_filename: str = ""

    @property
    def full_address(self):
        parts = [self.address_line1]
        loc = ", ".join(p for p in [self.city, self.state] if p)
        if loc:
            parts.append(loc + (f" {self.zip_code}" if self.zip_code else ""))
        return ", ".join(parts)

    @property
    def street_address(self):
        return self.address_line1

    @property
    def city_state_zip(self):
        loc = ", ".join(p for p in [self.city, self.state] if p)
        return (loc + (f" {self.zip_code}" if self.zip_code else "")).strip()

    @property
    def bathrooms_display(self):
        full = self.bathrooms_full or "0"
        half = self.bathrooms_half or "0"
        if half and half != "0":
            return f"{full}.{1 if half else 0}"
        return full

    @property
    def file_safe_name(self):
        base = self.address_line1 or self.mls_number or "listing"
        base = re.sub(r"[^A-Za-z0-9 _-]", "", base).strip().replace(" ", "_")
        return base or "listing"


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse_listing_pdf(file_bytes: bytes, source_filename: str = "") -> Listing:
    listing = Listing(source_filename=source_filename)

    # --- Text extraction (pdfplumber gives clean reading-order text) -------
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        page1_text = pdf.pages[0].extract_text() or ""
        page2_text = pdf.pages[1].extract_text() if len(pdf.pages) > 1 else ""

    full_text = page1_text + "\n" + page2_text

    # --- Property type / MLS# / price -------------------------------------
    # Some exports (e.g. browser "Print to PDF") prepend a timestamp/site
    # banner line before the real listing header, so this can't be anchored
    # to the very start of the page text -- search for it instead of
    # matching only at position 0.
    m = re.search(r"([A-Za-z ]+?)\s*MLS #:\s*(\d+)", page1_text)
    if m:
        listing.property_type = m.group(1).strip()
        listing.mls_number = m.group(2).strip()

    listing.list_price = money(_grab(page1_text, "List Price", ["Orig List Price", "\n"]))
    listing.status = _grab(page1_text, "Status", ["List Date"])
    listing.list_date = _grab(page1_text, "List Date", ["Orig List Price"])

    # "Mkt. Time (Lst./Tot.)" is MRED's days-on-market field: the first
    # number is time on the *current* listing period, the second is
    # cumulative across any relists -- they differ only if this property
    # has been relisted, so both are worth keeping.
    m = re.search(r"Mkt\.?\s*Time\s*\(Lst\.?/Tot\.?\):(\d+)\s*/\s*(\d+)", page1_text)
    if m:
        listing.dom_list_side, listing.dom_total = m.group(1), m.group(2)

    # --- Address ------------------------------------------------------------
    m = re.search(r"Address:(.*?),\s*([A-Za-z .]+),\s*([A-Z]{2})\s*(\d{5})", full_text)
    if m:
        listing.address_line1 = m.group(1).strip()
        listing.city = m.group(2).strip()
        listing.state = m.group(3).strip()
        listing.zip_code = m.group(4).strip()

    listing.directions = _grab(page1_text, "Directions", ["Sold by"])

    # --- Core facts ----------------------------------------------------------
    listing.year_built = _grab(page1_text, "Year Built", ["Blt Before 78"])
    listing.ownership = _grab(page1_text, "Ownership", ["Subdivision"])
    listing.rooms_total = _grab(page1_text, "Rooms", ["Bathrooms"])
    listing.bedrooms = _grab(page1_text, "Bedrooms", ["Master Bath"])

    # "Dimensions" is MRED's lot-size field. For most condos it just says
    # COMMON (shared lot), but rowhome-style/low-rise condos and any
    # detached listing can carry real lot dimensions here, which buyers do
    # care about.
    m = re.search(r"Dimensions:(.*?)Ownership:", full_text, re.S)
    if m:
        listing.lot_size = m.group(1).strip()

    # Building-level facts -- especially relevant for condos/co-ops so buyers
    # know building size and where in it this unit sits.
    m = re.search(r"Total Units:(\d+)", page1_text)
    if m:
        listing.total_units = m.group(1)
    m = re.search(r"#\s*Stories:(\d+)", page1_text)
    if m:
        listing.total_stories = m.group(1)
    m = re.search(r"Unit Floor Lvl\.:(\d+)", page1_text)
    if m:
        listing.unit_floor_level = m.group(1)

    m = re.search(r"Bathrooms(?:\s*\(Full/Half\))?:?\s*(\d+)\s*/\s*(\d+)", page1_text)
    if m:
        listing.bathrooms_full, listing.bathrooms_half = m.group(1), m.group(2)

    m = re.search(r"Appx SF:\s*([\d,]+)", page1_text)
    if m:
        sf = m.group(1)
        listing.approx_sf = sf if sf not in ("0", "") else ""

    listing.age = _grab(full_text, "Age", ["Laundry Features", "Type:"])

    # --- Parking ---------------------------------------------------------------
    listing.parking_type = _grab(page1_text, "Parking", ["# Spaces"])
    m = re.search(r"#\s*Spaces:Gar:(\d+)", page1_text)
    if m:
        listing.parking_spaces = m.group(1)
    listing.garage_details = _grab(full_text, "Garage Details", ["Parking Ownership", "\n"])

    # "Parking Incl. In Price" -- distinct from (and not to be confused with)
    # "SP Incl. Parking", which is a sold-price field for closed listings.
    # Buyers regularly get tripped up by parking being available but sold
    # separately, so this needs to be called out explicitly.
    m = re.search(r"(?<!SP )Parking Incl\.(Yes|No)", page1_text)
    if m:
        listing.parking_incl_in_price = m.group(1)

    # --- 4-column block: School Data / Assessments / Tax / Pet Info --------------
    # This section is laid out as 4 side-by-side columns in the source PDF; a
    # plain linear text read jumbles them together, so we re-open the page and
    # crop each column separately by its known x-range.
    school_col = assess_col = tax_col = pet_col = ""
    all_words = []
    page_width = 612
    left_margin = 14
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            p1 = pdf.pages[0]
            all_words = p1.extract_words()
            page_width = p1.width
            # The page's actual left margin varies by export source (native
            # MRED PDF vs. a browser "Print to PDF", different browsers, etc),
            # so anchor words are matched relative to the page's own margin
            # rather than an absolute pixel value -- a hardcoded cutoff like
            # "x0 < 20" is brittle and silently drops whole sections (school
            # district, assessments, tax, pets) when a differently-exported
            # sheet's margin is a few points wider.
            if all_words:
                left_margin = min(w["x0"] for w in all_words)
            margin_cutoff = left_margin + 30
            header_top = next((w["top"] for w in all_words if w["text"] == "School" and w["x0"] < margin_cutoff), None)
            footer_top = next((w["top"] for w in all_words if w["text"] == "Square" and w["x0"] < margin_cutoff), None)
            if header_top is not None and footer_top is not None:
                top, bottom = header_top - 2, footer_top - 2
                school_col = _column_text(all_words, 0, 165, top, bottom)
                assess_col = _column_text(all_words, 165, 295, top, bottom)
                tax_col = _column_text(all_words, 295, 450, top, bottom)
                pet_col = _column_text(all_words, 450, page_width, top, bottom)
    except Exception:
        pass

    listing.assessment_amount = money("$" + _first_num(_grab(assess_col, "Amount", ["\n"])))
    listing.assessment_frequency = _grab(assess_col, "Frequency", ["\n"])
    listing.special_assessments = _grab(assess_col, "Special Assessments", ["\n"])

    listing.tax_amount = money("$" + _first_num(_grab(tax_col, "Amount", ["\n"])))
    listing.tax_year = _grab(tax_col, "Tax Year", ["\n"])

    # "Mult PINs" and "Tax Exmps" values can wrap onto a second line within
    # this column (e.g. "Mult PINs: (See Agent\nRemarks)"), so flatten
    # newlines to spaces first rather than grabbing from the raw column
    # text, which would truncate at the wrap.
    tax_col_flat = re.sub(r"\s*\n\s*", " ", tax_col)
    listing.mult_pins = _grab(tax_col_flat, "Mult PINs", ["Tax Year", "$"])
    listing.tax_exemptions = _grab(tax_col_flat, "Tax Exmps", ["Coop Tax Deduction", "$"])

    listing.elementary = _grab(school_col, "Elementary", ["\n"])
    listing.junior_high = _grab(school_col, "Junior High", ["\n"])
    listing.high_school = _grab(school_col, "High School", ["\n"])

    # Stop at "Pet Weight:" (the field label, with its colon) as well as
    # "Max Pet Weight" -- on some sheets the word "Max" lands a hair's-width
    # inside the neighboring tax column (its x-position is right at the
    # column boundary), leaving an orphaned "Pet Weight:000" fragment in
    # this column that "Max Pet Weight" alone wouldn't catch as a stop
    # point. The colon is required so this doesn't also match the legit
    # pet-policy phrase "Pet Weight Limitation" (no colon) that can appear
    # earlier in this same value.
    listing.pets_allowed = _grab(re.sub(r"\s*\n\s*", " ", pet_col), "Pets Allowed", ["Max Pet Weight", "Pet Weight:", "$"])
    m = re.search(r"Max Pet Weight:(\d+)", page1_text)
    if m:
        # MRED zero-fills this field ("000") when no specific limit was
        # entered -- that's a null placeholder, not an actual 0 lb limit.
        weight = int(m.group(1))
        if weight > 0:
            listing.max_pet_weight = str(weight)

    listing.assessment_includes = _grab(full_text, "Asmt Incl", ["HERS Index Score", "\n"])

    # --- Remarks (long free text) -------------------------------------------------
    m = re.search(r"Remarks:\s*(.*?)\s*(?:School Data|Broker Private Remarks)", full_text, re.S)
    if m:
        remarks = m.group(1).strip()
        remarks = re.sub(r"\s*\n\s*", " ", remarks)
        remarks = re.sub(r"\s{2,}", " ", remarks)
        listing.remarks = remarks

    # --- Features: 3-column grid (Age/Type/... | Laundry/Garage/... | Sewer/...) --
    feat_col1 = feat_col2 = feat_col3 = ""
    rooms_left = rooms_right = ""
    try:
        words = all_words or []

        def word_top(text):
            w = next((w for w in words if w["text"] == text), None)
            return w["top"] if w else None

        grid_top = word_top("Age:")
        margin_cutoff = left_margin + 30
        brm_top = next((w["top"] for w in words if w["text"] == "Broker" and w["x0"] < margin_cutoff), None)
        if grid_top is not None and brm_top is not None:
            top, bottom = grid_top - 2, brm_top - 2
            # Flatten to single-line-per-column text: every field we pull out of
            # this grid is a short label:value pair, and MRED wraps long values
            # (e.g. "Garage Door Opener(s), Heated, Tandem") onto a second line
            # within the same cell, so newlines here are just wrapping, not
            # meaningful row breaks.
            feat_col1 = re.sub(r"\s*\n\s*", " ", _column_text(words, 0, 195, top, bottom))
            feat_col2 = re.sub(r"\s*\n\s*", " ", _column_text(words, 195, 395, top, bottom))
            feat_col3 = re.sub(r"\s*\n\s*", " ", _column_text(words, 395, page_width, top, bottom))

        room_hdr_top = next((w["top"] for w in words if w["text"] == "Room" and w["x0"] < margin_cutoff), None)
        interior_top = word_top("Interior")
        if room_hdr_top is not None and interior_top is not None:
            top, bottom = room_hdr_top - 2, interior_top - 2
            rooms_left = _column_text(words, 0, 306, top, bottom)
            rooms_right = _column_text(words, 306, page_width, top, bottom)
    except Exception:
        pass

    listing.interior_features = _grab(full_text, "Interior Property Features", ["Exterior Property Features"])
    listing.exterior_features = _grab(full_text, "Exterior Property Features", ["Age:"])
    listing.heating = _grab(feat_col1, "Heating", ["Kitchen:"])
    listing.cooling = _grab(feat_col1, "Air Cond", ["Heating:"])
    listing.kitchen_features = _grab(feat_col1, "Kitchen", ["Appliances:"])
    listing.appliances = _grab(feat_col1, "Appliances", ["Dining:"])
    listing.bath_amenities = _grab(feat_col1, "Bath Amn", ["Fireplace Details:"])
    listing.amenities = _grab(feat_col3, "Amenities", ["Asmt Incl:"])
    listing.laundry = _grab(feat_col2, "Laundry Features", ["Garage Ownership:"])
    listing.age = _grab(feat_col1, "Age", ["Type:"]) or listing.age
    m = re.search(r"\bParking:(Garage|None|Space/s|Assigned Spaces|Off Street|Driveway|N/A)", page1_text)
    listing.parking_type = m.group(1) if m else ""
    listing.garage_details = _grab(feat_col2, "Garage Details", ["Parking Ownership:"])

    # --- Room dimension tables (two side-by-side mini tables) ---------------------
    def parse_room_column(col_text):
        rooms = []
        for line in col_text.splitlines():
            line = line.strip()
            m = re.match(
                r"(Living Room|Dining Room|Kitchen|Family Room|Master Bedroom|2nd Bedroom|3rd Bedroom|4th Bedroom|Laundry Room)"
                r"\s*([\dX]+|COMBO)?\s*(Main Level|2nd Level|Lower Level|Basement)?\s*(Hardwood|Carpet|Ceramic Tile|Vinyl|Marble|Wood Laminate)?",
                line,
            )
            if not m:
                continue
            name, size, level, flooring = m.groups()
            if not size and not level and not flooring:
                continue
            rooms.append({"name": name, "size": size or "", "level": level or "", "flooring": flooring or ""})
        return rooms

    listing.rooms = parse_room_column(rooms_left) + parse_room_column(rooms_right)

    # --- Listing broker (MLS compliance credit) -----------------------------------
    m = re.search(r"List Broker:\s*(.*?)\s*\(\d+\)\s*(?:on behalf of\s*(.*?)\s*\(T?\d+\))?\s*/\s*(.*?)\s*/", full_text)
    if m:
        listing.list_broker_name = m.group(1).strip()
        listing.list_brokerage = (m.group(2) or "").strip()
        listing.list_broker_phone = m.group(3).strip()

    # --- Photo -------------------------------------------------------------------
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page = doc[0]
        images = page.get_images(full=True)
        if images:
            xref = images[0][0]
            base = doc.extract_image(xref)
            listing.photo_bytes = base["image"]
            listing.photo_ext = base.get("ext", "jpg")
        doc.close()
    except Exception:
        pass

    return listing
