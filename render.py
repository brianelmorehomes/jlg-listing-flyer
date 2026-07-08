"""
Renders a parsed Listing into the branded, print-ready 2-page PDF flyer.
"""
import base64
import dataclasses
import datetime
import os
import re
import tempfile

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
FONT_DIR = os.path.join(STATIC_DIR, "fonts")
LOGO_LOCKUP = os.path.join(STATIC_DIR, "logo", "jlg_atproperties_christies_lockup.png")
# Some printers/drivers render the brand's saturated red (@properties' "@"
# symbol) as near-black instead of red, no matter the print quality setting
# -- that's a printer color-management issue, not something fixable from the
# PDF side. Per @properties/Christie's own marketing guidelines: "If a piece
# is in black and white, the logo must either be all white or all black --
# no greyscale is permitted." This all-black lockup (red channel desaturated
# to match the surrounding black/white luminance, so anti-aliased edges stay
# smooth) is the compliant fallback for exactly that situation.
LOGO_LOCKUP_BW = os.path.join(STATIC_DIR, "logo", "jlg_atproperties_christies_lockup_blackonly.png")

STATUS_LABELS = {
    "NEW": "New Listing",
    "ACTV": "For Sale",
    "PCH": "Price Change",
    "BOM": "Back on Market",
}


def split_remarks(remarks: str):
    """Pull the first sentence out as an italic pull-quote lead-in, keep the
    rest as body copy. Falls back gracefully on odd punctuation."""
    if not remarks:
        return "", ""
    m = re.match(r"(.+?[.!?])\s+(.*)", remarks, re.S)
    if not m:
        return "", remarks
    lead, rest = m.group(1).strip(), m.group(2).strip()
    # Keep the lead-in short; if the first "sentence" is huge (e.g. no early
    # period), just skip the pull-quote treatment.
    if len(lead) > 160:
        return "", remarks
    return lead, rest


def friendly_property_type(listing):
    """MRED's raw property_type strings ('Attached Single', 'Detached Single')
    are MLS jargon; prefer a buyer-friendly label when we can infer one."""
    ownership = (listing.ownership or "").strip().lower()
    ptype = (listing.property_type or "").strip()
    if ownership == "condo":
        return "Condominium"
    if ownership == "co-op":
        return "Co-op"
    if "attached" in ptype.lower():
        return "Attached Home"
    if "detached" in ptype.lower():
        return "Single Family Home"
    return ptype or "Residential"


def lot_size_display(listing):
    """Format the MRED 'Dimensions' (lot size) field for buyer-facing display.

    MRED often stores this as shouted all-caps free text ("COMMON",
    "PER SURVEY") rather than a plain dimension string ("50 X 125"); those
    read as SHOUTING next to normally-cased labels, so sentence-case any
    value that's pure letters/spaces (leaving actual dimension strings,
    which mix in digits, untouched)."""
    val = (listing.lot_size or "").strip()
    if not val:
        return "TBD"
    if re.fullmatch(r"[A-Za-z ]+", val) and val.upper() == val:
        return val.title()
    return val


def parking_note(listing):
    """A short, explicit callout for whether parking is included in the list
    price -- this is a frequent point of buyer confusion, so it's worth
    surfacing plainly rather than leaving it buried in the raw MLS fields."""
    incl = (listing.parking_incl_in_price or "").strip().lower()
    if incl == "no":
        return "Not included in price"
    if incl == "yes":
        return "Included in price"
    return ""


def market_time_display(listing):
    """Combine list date and days-on-market into one compact header line --
    both signal how fresh a listing is, so they belong together rather than
    as two separate stray facts."""
    parts = []
    if listing.list_date:
        try:
            d = datetime.datetime.strptime(listing.list_date, "%m/%d/%Y")
            parts.append(f"Listed {d.strftime('%b')} {d.day}, {d.year}")
        except ValueError:
            parts.append(f"Listed {listing.list_date}")
    if listing.dom_total:
        if listing.dom_list_side and listing.dom_list_side != listing.dom_total:
            parts.append(f"{listing.dom_list_side} Days on Market ({listing.dom_total} Total)")
        else:
            unit = "Day" if listing.dom_total == "1" else "Days"
            parts.append(f"{listing.dom_total} {unit} on Market")
    return " · ".join(parts)


def mult_pins_display(listing):
    """Whether this listing's tax bill spans multiple PINs/parcels. MRED
    doesn't list the extra parcels here, just a pointer to the agent
    remarks, so translate that into a plain yes/no + pointer instead of
    showing the raw MLS phrasing verbatim."""
    val = (listing.mult_pins or "").strip()
    if not val:
        return ""
    if val.lower().startswith("no"):
        return "No"
    return "Yes (see agent remarks for parcel detail)"


def pets_display(listing):
    """Pet policy plus, when the MLS sheet specifies one, the max pet
    weight -- an easy detail to bury but one pet-owning buyers care about."""
    base = (listing.pets_allowed or "").strip()
    if not base:
        return ""
    if listing.max_pet_weight:
        return f"{base} (max {listing.max_pet_weight} lbs)"
    return base


def render_flyer(
    listing,
    output_path,
    agent_phone="",
    agent_email="brian@justinlucasgroup.com",
    agent_name="Brian Elmore",
    print_safe_logo=False,
):
    env = Environment(loader=FileSystemLoader(os.path.join(BASE_DIR, "templates")))
    template = env.get_template("flyer.html")

    photo_path = None
    tmp_photo = None
    if listing.photo_bytes:
        tmp_photo = tempfile.NamedTemporaryFile(
            suffix=f".{listing.photo_ext}", delete=False
        )
        tmp_photo.write(listing.photo_bytes)
        tmp_photo.close()
        photo_path = tmp_photo.name

    lead, rest = split_remarks(listing.remarks)

    html_str = template.render(
        l=listing,
        font_dir=FONT_DIR,
        logo_lockup=LOGO_LOCKUP_BW if print_safe_logo else LOGO_LOCKUP,
        photo_path=photo_path,
        status_label=STATUS_LABELS.get(listing.status, listing.status or "For Sale"),
        remarks_lead=lead,
        remarks_rest=rest,
        friendly_type=friendly_property_type(listing),
        agent_phone=agent_phone,
        agent_email=agent_email,
        agent_name=agent_name or "Brian Elmore",
        sqft_display=listing.approx_sf or "TBD",
        lot_size_display=lot_size_display(listing),
        parking_note=parking_note(listing),
        market_time_display=market_time_display(listing),
        mult_pins_display=mult_pins_display(listing),
        pets_display=pets_display(listing),
        prepared_date=datetime.date.today().strftime("%B %-d, %Y"),
    )

    HTML(string=html_str, base_url=BASE_DIR).write_pdf(output_path)

    if tmp_photo:
        os.unlink(tmp_photo.name)

    return output_path
