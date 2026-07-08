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
    """Format the MRED 'Dimensions' (lot size) field for buyer-facing display."""
    val = (listing.lot_size or "").strip()
    if not val:
        return "TBD"
    if val.upper() == "COMMON":
        return "Common"
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


def render_flyer(
    listing,
    output_path,
    agent_phone="",
    agent_email="brian@justinlucasgroup.com",
    agent_name="Brian Elmore",
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
        logo_lockup=LOGO_LOCKUP,
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
        prepared_date=datetime.date.today().strftime("%B %-d, %Y"),
    )

    HTML(string=html_str, base_url=BASE_DIR).write_pdf(output_path)

    if tmp_photo:
        os.unlink(tmp_photo.name)

    return output_path
