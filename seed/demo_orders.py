"""The demo client's 20 test orders and their answer keys.

Writes seed/demo/orders/ (8 emails, 5 PDFs, 4 Excel files, 3 CSV files) and
seed/demo/answers/ (one JSON file per order in the standard internal order
shape, plus the expected initial status, flags and export rows). Between them
the orders cover every hard case in BRIEF.md. Everything is invented and the
same on every run.

An answer key holds the right final order: what should be exported once the
operator has dealt with anything flagged. Its lines also say how each line
should be matched first time and what status it should get.
"""

from __future__ import annotations

import csv
import io
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from openpyxl import Workbook

from pipeline.workdays import LONDON

# ---------------------------------------------------------------- what each order should give


@dataclass
class Line:
    marker: str  # a piece of the line's text; the full line in the extracted text becomes source_span
    product_code: str | None
    qty: str | None
    unit: str | None
    match_method: str | None  # how the pipeline should match it first time
    status: str  # the line's expected status first time
    conversion_note: str | None = None
    note: str | None = None


@dataclass
class Spec:
    key: str
    ext: str
    received: str  # London time, "2026-10-12 09:14"
    sender_email: str | None
    account: str | None  # the right customer, or None when there is no such customer
    match_method: str | None  # how the customer should be matched first time
    classification: str
    status: str  # expected initial status
    hard_cases: list[str]
    lines: list[Line] = field(default_factory=list)
    po_number: str | None = None
    delivery_raw: str | None = None
    delivery_date: str | None = None
    delivery_address: str | None = None
    delivery_notes: str | None = None
    flags: list[str] = field(default_factory=list)  # flag codes the pipeline should raise
    exportable: bool = True  # False when no export is possible (not an order, unknown customer, scan)
    note: str | None = None
    # content
    sender_name: str | None = None
    subject: str | None = None
    body: str = ""


def _london(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=LONDON)


SPECS: list[Spec] = [
    Spec(
        key="01_riverside_thursday", ext="eml", received="2026-10-12 09:14",
        sender_email="purchasing@riverside-hotel.example", sender_name="Maria Lopez", subject="Order for Thursday",
        account="RIVERS01", match_method="email", classification="new_order", status="ready",
        hard_cases=["Bottles that divide exactly into cases", "\"Deliver Thursday\"", "Remembered phrasing"],
        po_number="2291", delivery_raw="Thursday", delivery_date="2026-10-15", delivery_notes="Kitchen entrance",
        body="""Hi team,

Please can we order the following for delivery Thursday:

120 bottles of the antibac spray
Toilet cleaner 750ml - 2 cases

Our PO is 2291. Kitchen entrance please, as usual.

Thanks,
Maria Lopez
Purchasing, Riverside Hotel
14 River Walk, Kingsford
""",
        lines=[
            Line("120 bottles of the antibac spray", "CLN-101", "10", "CASE", "alias", "ready",
                 conversion_note="120 bottles / 12 = 10 cases"),
            Line("Toilet cleaner 750ml - 2 cases", "CLN-110", "2", "CASE", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="02_oldmill_simple", ext="eml", received="2026-10-12 10:02",
        sender_email="landlord@oldmillpub.example", sender_name="Dave Hughes", subject="Order",
        account="OLDMIL01", match_method="email", classification="new_order", status="ready",
        hard_cases=["A simple, clean order"], po_number="OM-5521",
        body="""Morning,

3 boxes of the big black bags
Washing up liquid 5L x 2 cases

PO OM-5521

Cheers
Dave
The Old Mill Pub
""",
        lines=[
            Line("3 boxes of the big black bags", "BAG-401", "3", "BOX", "alias", "ready"),
            Line("Washing up liquid 5L x 2 cases", "CLN-106", "2", "CASE", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="03_oldmill_amendment", ext="eml", received="2026-10-13 08:40",
        sender_email="landlord@oldmillpub.example", sender_name="Dave Hughes", subject="Re: Order",
        account="OLDMIL01", match_method="email", classification="amendment", status="pass_on",
        hard_cases=["\"Can you add 2 boxes to yesterday's order\""], exportable=False,
        body="""Hi, can you add 2 boxes of the big black bags to yesterday's order?

Ta
Dave
""",
    ),
    Spec(
        key="04_larkspur_misspelt", ext="eml", received="2026-10-13 11:30",
        sender_email="jo.patel.home@mailbox.example", sender_name="Jo Patel", subject="Order from Larkspur",
        account="LARKSP01", match_method="name", classification="new_order", status="check",
        hard_cases=["Misspelt product (\"disinfectent spary\")", "Unknown sender, customer named in signature"],
        po_number="LCH-2207", flags=["customer_by_name"],
        body="""Hello,

I'm covering for our housekeeping manager this week, so I'm sending this from my own email. Please could we have:

disinfectent spary x 2 cases
Yellow household rubber gloves L - 1 pack

Delivery as normal please. PO LCH-2207

Many thanks
Jo Patel
Larkspur Care Home
""",
        lines=[
            Line("disinfectent spary x 2 cases", "CLN-102", "2", "CASE", "ai_suggested", "check"),
            Line("Yellow household rubber gloves L - 1 pack", "GLV-222", "1", "PACK", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="05_harbourview_letter", ext="pdf", received="2026-10-13 14:05",
        sender_email="orders@harbourhotels.example", account="HARBVW01", match_method="email",
        classification="new_order", status="problem",
        hard_cases=["Bottles that do not divide into cases", "Letter-style PDF"],
        po_number="HV-3390", delivery_raw="Friday 16 October", delivery_date="2026-10-16",
        delivery_notes="goods entrance at the rear of the hotel", flags=["conversion"],
        note="30 bottles is not a whole number of cases of 12. The customer confirmed 3 cases by phone.",
        lines=[
            Line("30 bottles of glass and mirror cleaner", "CLN-103", "3", "CASE", "exact_name", "problem",
                 note="30 bottles / 12 leaves 6 over"),
            Line("Black refuse sacks heavy duty - 2 boxes", "BAG-401", "2", "BOX", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="06_fithub_blue_roll", ext="eml", received="2026-10-14 09:50",
        sender_email="manager@fithub-gyms.example", sender_name="Lee Carter", subject="Weekly order",
        account="FITHUB01", match_method="email", classification="new_order", status="check",
        hard_cases=["Near twins (blue roll single vs pack) with no unit", "No PO number"],
        flags=["no_unit"],
        body="""Hi,

Can I get:
6 blue roll
2 blue gloves medium
Hand sanitiser gel 500ml pump x 1 case

Thanks
Lee
FitHub Gym Kingsford
""",
        lines=[
            Line("6 blue roll", "TOW-310", "6", "PACK", "ai_suggested", "check",
                 note="No unit: blue roll comes as a pack of 6 (TOW-310) or single (TOW-311)"),
            Line("2 blue gloves medium", "GLV-202", "2", "BOX", "alias", "ready"),
            Line("Hand sanitiser gel 500ml pump x 1 case", "CLN-117", "1", "CASE", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="07_unknown_sender", ext="eml", received="2026-10-14 13:12",
        sender_email="office@brightsidelettings.example", sender_name="Sam Rowe", subject="Cleaning supplies order",
        account=None, match_method=None, classification="new_order", status="problem",
        hard_cases=["Completely unknown sender"], flags=["unknown_customer"], exportable=False,
        note="Brightside Lettings is not one of the client's customers.",
        body="""Hello,

We'd like to order:
4 boxes black refuse sacks heavy duty
Toilet roll 2ply 200 sheet x 1 case

Please invoice Brightside Lettings, 3 Abbey Court, Kingsford.

Thanks,
Sam Rowe
Brightside Lettings
""",
        lines=[
            Line("4 boxes black refuse sacks heavy duty", "BAG-401", "4", "BOX", "exact_name", "ready"),
            Line("Toilet roll 2ply 200 sheet x 1 case", "TOW-301", "1", "CASE", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="08_greenway_chase", ext="eml", received="2026-10-15 10:15",
        sender_email="facilities@greenway-offices.example", sender_name="Priya Shah", subject="Where is my delivery?",
        account="GREENW01", match_method="email", classification="chase", status="pass_on",
        hard_cases=["\"Where is my delivery?\""], po_number="GW-1180", exportable=False,
        body="""Hi,

Where is my delivery? Our order GW-1180 was due yesterday and nothing has arrived yet. Can you let me know?

Priya
Greenway Offices
""",
    ),
    Spec(
        key="09_milllane_after_cutoff", ext="eml", received="2026-10-15 16:20",
        sender_email="hello@milllanecafe.example", sender_name="Hannah Bell", subject="Order for tomorrow",
        account="MILLLN01", match_method="email", classification="new_order", status="check",
        hard_cases=["Sent after the cutoff for next day delivery"], po_number="ML-0907",
        delivery_raw="tomorrow", delivery_date="2026-10-16", flags=["cutoff"],
        body="""Hi there,

Sorry it's late - could we get these delivered tomorrow please?

Blue nitrile gloves powder free M x 3 boxes
Interfold hand towels white 2ply - 2 cases
Thick bleach 750ml x 1 case

PO ML-0907

Thanks!
Hannah
Mill Lane Café
""",
        lines=[
            Line("Blue nitrile gloves powder free M x 3 boxes", "GLV-202", "3", "BOX", "exact_name", "ready"),
            Line("Interfold hand towels white 2ply - 2 cases", "TOW-303", "2", "CASE", "exact_name", "ready"),
            Line("Thick bleach 750ml x 1 case", "CLN-109", "1", "CASE", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="10_cedars_scanned", ext="pdf", received="2026-10-16 09:30",
        sender_email="office@cedarsnursery.example", account="CEDARS01", match_method="email",
        classification="new_order", status="problem", hard_cases=["A scanned PDF (image only)"],
        flags=["scanned_pdf"], exportable=False,
        note="An image of a handwritten order form with no text layer.",
    ),
    Spec(
        key="11_towerhouse_po_table", ext="pdf", received="2026-10-19 09:05",
        sender_email="ops@towerhouse-bc.example", account="TOWERH01", match_method="email",
        classification="new_order", status="check",
        hard_cases=["Price written differs from price band", "Table-style purchase order PDF"],
        po_number="TH-4410", delivery_raw="21/10/2026", delivery_date="2026-10-21",
        delivery_address="Tower House, Bridge Street, Kingsford, KF1 5BS", flags=["price_difference"],
        lines=[
            Line("Toilet roll 2ply 200 sheet", "TOW-301", "4", "CASE", "exact_code", "check",
                 note="They wrote 16.90; their band B price is 15.89"),
            Line("Multi-surface cleaner 750ml", "CLN-104", "2", "CASE", "exact_code", "ready"),
            Line("White swing bin liners", "BAG-405", "3", "BOX", "exact_code", "ready"),
            Line("All purpose cloths blue", "CLO-611", "5", "PACK", "exact_code", "ready"),
        ],
    ),
    Spec(
        key="12_oakfield_40_lines", ext="xlsx", received="2026-10-19 10:40",
        sender_email="facilities@oakfield-academy.example", account="OAKFLD01", match_method="email",
        classification="new_order", status="ready", hard_cases=["A 40 line Excel order"],
        po_number="OA-7781", delivery_raw="22/10/2026", delivery_date="2026-10-22",
    ),
    Spec(
        key="13_stannes_own_codes", ext="pdf", received="2026-10-20 11:00",
        sender_email="bursar@stannes-school.example", account="STANNE01", match_method="email",
        classification="new_order", status="check", hard_cases=["Customer uses their own product codes"],
        po_number="SAP-0412",
        lines=[
            Line("SA-0042", "TOW-303", "4", "CASE", "ai_suggested", "check"),
            Line("SA-0107", "BAG-404", "2", "BOX", "ai_suggested", "check"),
            Line("SA-0215", "CLN-107", "1", "CASE", "ai_suggested", "check"),
        ],
    ),
    Spec(
        key="14_dental_barcode", ext="pdf", received="2026-10-20 14:30",
        sender_email="practice.manager@kingsforddental.example", account="KINGSD01", match_method="email",
        classification="new_order", status="ready", hard_cases=["Manufacturer barcode instead of product code"],
        po_number="KDP-118",
        lines=[
            Line("{barcode:GLV-202}", "GLV-202", "2", "BOX", "exact_code", "ready"),
            Line("Hand sanitiser gel 5L", "CLN-118", "1", "EACH", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="15_pulse_not_in_catalogue", ext="xlsx", received="2026-10-21 09:20",
        sender_email="orders@pulse-studio.example", account="PULSE001", match_method="email",
        classification="new_order", status="problem", hard_cases=["Product not in the catalogue"],
        po_number="PFS-0331", flags=["line_unmatched"],
        note="Yoga mat cleaner is not in the catalogue; it is left off the export and the client is asked.",
        lines=[
            Line("Antibacterial surface spray 750ml", "CLN-101", "2", "CASE", "exact_name", "ready"),
            Line("Yoga mat cleaner spray 500ml", None, None, None, None, "problem"),
            Line("Blue nitrile gloves powder free L", "GLV-203", "2", "BOX", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="16_towerhouse_duplicate", ext="csv", received="2026-10-21 13:45",
        sender_email="ops@towerhouse-bc.example", account="TOWERH01", match_method="email",
        classification="new_order", status="check", hard_cases=["Same PO number sent twice"],
        po_number="TH-4410", flags=["duplicate"],
        note="TH-4410 already arrived as a PDF on Monday 19 October.",
        lines=[
            Line("TOW-301", "TOW-301", "4", "CASE", "exact_code", "ready"),
            Line("CLN-104", "CLN-104", "2", "CASE", "exact_code", "ready"),
        ],
    ),
    Spec(
        key="17_bluedoor_below_minimum", ext="xlsx", received="2026-10-22 10:10",
        sender_email="orders@bluedoorbakery.example", account="BLUEDR01", match_method="email",
        classification="new_order", status="check", hard_cases=["Order below minimum value"],
        po_number="BD-0045", flags=["minimum_order"],
        lines=[Line("Green scourer pads", "CLO-621", "2", "PACK", "exact_name", "ready")],
    ),
    Spec(
        key="18_harbourpoint_no_po", ext="xlsx", received="2026-10-22 12:00",
        sender_email="supplies@harbourhotels.example", account="HARBPT01", match_method="email",
        classification="new_order", status="ready", hard_cases=["No PO number"],
        delivery_raw="Monday 26 October", delivery_date="2026-10-26",
        lines=[
            Line("CLN-108", "CLN-108", "2", "CASE", "exact_code", "ready"),
            Line("TOW-312", "TOW-312", "3", "CASE", "exact_code", "ready"),
            Line("GLV-203", "GLV-203", "2", "BOX", "exact_code", "ready"),
        ],
    ),
    Spec(
        key="19_milllane_semicolons", ext="csv", received="2026-10-23 08:55",
        sender_email="hello@milllanecafe.example", account="MILLLN01", match_method="email",
        classification="new_order", status="ready",
        hard_cases=["Semicolon CSV saved by Excel on a UK PC (Windows-1252)"], po_number="ML-0912",
        lines=[
            Line("Kitchen roll white 2ply", "TOW-312", "1", "CASE", "exact_name", "ready"),
            Line("Microfibre cloths blue 40cm", "CLO-601", "2", "PACK", "exact_name", "ready"),
        ],
    ),
    Spec(
        key="20_greenway_clean_csv", ext="csv", received="2026-10-23 11:25",
        sender_email="facilities@greenway-offices.example", account="GREENW01", match_method="email",
        classification="new_order", status="ready", hard_cases=["A simple, clean order"],
        po_number="GW-1203", delivery_raw="27/10/2026", delivery_date="2026-10-27", delivery_notes="Reception",
        lines=[
            Line("CLN-104", "CLN-104", "3", "CASE", "exact_code", "ready"),
            Line("TOW-301", "TOW-301", "2", "CASE", "exact_code", "ready"),
            Line("BAG-402", "BAG-402", "2", "BOX", "exact_code", "ready"),
        ],
    ),
]

# The 40 products on the Oakfield order, with quantities (all in each product's sell unit).
OAKFIELD_CODES = [
    "CLN-101", "CLN-102", "CLN-103", "CLN-104", "CLN-105", "CLN-106", "CLN-108", "CLN-109", "CLN-110", "CLN-111",
    "CLN-112", "CLN-115", "CLN-116", "CLN-117", "CLN-122", "CLN-123", "GLV-201", "GLV-202", "GLV-203", "GLV-211",
    "GLV-212", "GLV-221", "TOW-301", "TOW-302", "TOW-303", "TOW-305", "TOW-312", "BAG-401", "BAG-402", "BAG-403",
    "BAG-404", "BAG-405", "BAG-406", "BAG-409", "CLO-601", "CLO-602", "CLO-603", "CLO-611", "CLO-621", "CLO-661",
]


# ---------------------------------------------------------------- writing the files


def _email(spec: Spec, client_email: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{spec.sender_name} <{spec.sender_email}>" if spec.sender_name else spec.sender_email
    msg["To"] = client_email
    msg["Subject"] = spec.subject or ""
    msg["Date"] = format_datetime(_london(spec.received))
    msg["Message-ID"] = f"<{spec.key}@mail.example>"
    msg.set_content(spec.body)
    return bytes(msg)


def _pdf_doc(path: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    return SimpleDocTemplate(str(path), pagesize=A4, invariant=1, title="", author="", creator="", subject="",
                             leftMargin=56, rightMargin=56, topMargin=56, bottomMargin=56)


def _styles():
    from reportlab.lib.styles import getSampleStyleSheet

    return getSampleStyleSheet()


def _letter_pdf(path: Path) -> None:
    from reportlab.platypus import Paragraph, Spacer

    s = _styles()
    story = [
        Paragraph("<b>Harbour View Hotel</b>", s["Title"]),
        Paragraph("2 Quay Street, Kingsford, KF3 1QS", s["Normal"]),
        Spacer(1, 18),
        Paragraph("13 October 2026", s["Normal"]),
        Spacer(1, 12),
        Paragraph("Dear Demo Cleaning Supplies,", s["Normal"]),
        Spacer(1, 8),
        Paragraph("<b>Purchase order HV-3390</b>", s["Normal"]),
        Spacer(1, 8),
        Paragraph("Please supply the following for delivery on Friday 16 October:", s["Normal"]),
        Spacer(1, 8),
        Paragraph("30 bottles of glass and mirror cleaner 750ml", s["Normal"]),
        Paragraph("Black refuse sacks heavy duty - 2 boxes", s["Normal"]),
        Spacer(1, 8),
        Paragraph("Please deliver to the goods entrance at the rear of the hotel.", s["Normal"]),
        Spacer(1, 18),
        Paragraph("Yours sincerely,", s["Normal"]),
        Spacer(1, 18),
        Paragraph("Peter Grant", s["Normal"]),
        Paragraph("Head of Housekeeping", s["Normal"]),
    ]
    _pdf_doc(path).build(story)


def _table_pdf(path: Path, products: dict) -> None:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    s = _styles()
    rows = [("TOW-301", "4", "CASE", "16.90"), ("CLN-104", "2", "CASE", "19.20"),
            ("BAG-405", "3", "BOX", "10.20"), ("CLO-611", "5", "PACK", "4.61")]
    names = {"TOW-301": "Toilet roll 2ply 200 sheet", "CLN-104": "Multi-surface cleaner 750ml",
             "BAG-405": "White swing bin liners", "CLO-611": "All purpose cloths blue"}
    data = [["Code", "Description", "Qty", "Unit", "Unit price", "Total"]]
    total = Decimal(0)
    for code, qty, unit, price in rows:
        line_total = Decimal(qty) * Decimal(price)
        total += line_total
        data.append([code, names[code], qty, unit, price, f"{line_total:.2f}"])
    table = Table(data, colWidths=[62, 190, 36, 44, 64, 56])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDEDEA")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story = [
        Paragraph("<b>Tower House Business Centre</b>", s["Title"]),
        Paragraph("PURCHASE ORDER", s["Heading2"]),
        Paragraph("PO number: TH-4410", s["Normal"]),
        Paragraph("Order date: 19/10/2026", s["Normal"]),
        Paragraph("Delivery required: 21/10/2026", s["Normal"]),
        Paragraph("Deliver to: Tower House, Bridge Street, Kingsford, KF1 5BS", s["Normal"]),
        Spacer(1, 14),
        table,
        Spacer(1, 10),
        Paragraph(f"Order total: £{total:.2f} (excluding VAT)", s["Normal"]),
        Paragraph("Authorised by: K. Osei, Facilities", s["Normal"]),
    ]
    _pdf_doc(path).build(story)


def _own_codes_pdf(path: Path) -> None:
    from reportlab.platypus import Paragraph, Preformatted, Spacer

    s = _styles()
    listing = (
        "Our code   Item                              Qty\n"
        "SA-0042    Hand towels interfold white       4 cases\n"
        "SA-0107    Pedal bin liners white            2 boxes\n"
        "SA-0215    Washing up liquid 1 litre         1 case\n"
    )
    story = [
        Paragraph("<b>St Anne's Primary School</b>", s["Title"]),
        Paragraph("Order form - site office", s["Heading2"]),
        Paragraph("Order no. SAP-0412", s["Normal"]),
        Spacer(1, 12),
        Preformatted(listing, s["Code"]),
        Spacer(1, 12),
        Paragraph("Signed: Mrs D. Okafor, School Business Manager", s["Normal"]),
    ]
    _pdf_doc(path).build(story)


def _barcode_pdf(path: Path, barcode: str) -> None:
    from reportlab.platypus import Paragraph, Spacer

    s = _styles()
    story = [
        Paragraph("<b>Kingsford Dental Practice</b>", s["Title"]),
        Paragraph("3 Victoria Road, Kingsford, KF2 8VR", s["Normal"]),
        Spacer(1, 14),
        Paragraph("Order 20 October 2026 - our ref KDP-118", s["Normal"]),
        Spacer(1, 8),
        Paragraph("Please send:", s["Normal"]),
        Paragraph(f"{barcode} - 2 boxes", s["Normal"]),
        Paragraph("Hand sanitiser gel 5L - 1 each", s["Normal"]),
        Spacer(1, 14),
        Paragraph("Thank you,", s["Normal"]),
        Paragraph("Practice manager", s["Normal"]),
    ]
    _pdf_doc(path).build(story)


def _scanned_pdf(path: Path) -> None:
    """An image of a handwritten-looking order form, with no text layer, like a phone scan."""
    from PIL import Image, ImageDraw, ImageFilter
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    rng = random.Random(10)
    image = Image.new("L", (1240, 1754), 236)
    draw = ImageDraw.Draw(image)
    lines = ["CEDARS DAY NURSERY - ORDER", "", "Toilet roll 2ply  x 2 cases", "Blue roll  x 3 packs",
             "Antibac spray  x 1 case", "", "Thanks - Office"]
    y = 160
    for text in lines:
        x = 120 + rng.randint(-6, 6)
        for ch in text:
            draw.text((x, y + rng.randint(-2, 2)), ch, fill=40 + rng.randint(0, 30))
            x += 9
        y += 70
    for _ in range(4000):  # scanner speckle
        draw.point((rng.randint(0, 1239), rng.randint(0, 1753)), fill=rng.randint(150, 220))
    image = image.rotate(1.2, fillcolor=236).filter(ImageFilter.GaussianBlur(0.6))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    width, height = A4
    pdf.drawImage(ImageReader(buffer), 0, 0, width=width, height=height)
    pdf.showPage()
    pdf.save()


def _workbook(rows: list[list], path: Path, title: str = "Order") -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    fixed = datetime(2026, 10, 1, 9, 0, 0)
    wb.properties.creator = "Customer"
    wb.properties.created = fixed
    wb.properties.modified = fixed
    wb.save(path)


def _csv_bytes(rows: list[list[str]], delimiter: str = ",", encoding: str = "utf-8") -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue().encode(encoding)


def write_order_files(orders_dir: Path, products: dict, barcodes: dict[str, str], client_email: str) -> dict[str, Path]:
    orders_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for spec in SPECS:
        path = orders_dir / f"{spec.key}.{spec.ext}"
        paths[spec.key] = path
        if spec.ext == "eml":
            path.write_bytes(_email(spec, client_email))
        elif spec.key == "05_harbourview_letter":
            _letter_pdf(path)
        elif spec.key == "10_cedars_scanned":
            _scanned_pdf(path)
        elif spec.key == "11_towerhouse_po_table":
            _table_pdf(path, products)
        elif spec.key == "13_stannes_own_codes":
            _own_codes_pdf(path)
        elif spec.key == "14_dental_barcode":
            _barcode_pdf(path, barcodes["GLV-202"])
        elif spec.key == "12_oakfield_40_lines":
            rows = [["Oakfield Academy - cleaning supplies order"], [], ["Order ref:", "OA-7781"],
                    ["Delivery date:", "22/10/2026"], ["Contact:", "Facilities office"], [],
                    ["Item code", "Item", "Qty", "Unit"]]
            for i, code in enumerate(OAKFIELD_CODES):
                rows.append([code, short_name(products[code][0]), 1 + (i * 7) % 6, products[code][1]])
            _workbook(rows, path)
        elif spec.key == "15_pulse_not_in_catalogue":
            _workbook([["Pulse Fitness Studio"], ["PO:", "PFS-0331"], [],
                       ["Product", "Quantity", "Unit"],
                       ["Antibacterial surface spray 750ml", 2, "case"],
                       ["Yoga mat cleaner spray 500ml", 6, "bottles"],
                       ["Blue nitrile gloves powder free L", 2, "box"]], path)
        elif spec.key == "17_bluedoor_below_minimum":
            _workbook([["Blue Door Bakery Café order"], ["Ref:", "BD-0045"], [],
                       ["Item", "Qty", "Unit"], ["Green scourer pads", 2, "pack"]], path)
        elif spec.key == "18_harbourpoint_no_po":
            _workbook([["Harbour Point Hotel"], ["Delivery:", "Monday 26 October"], [],
                       ["Code", "Description", "Qty", "Unit"],
                       ["CLN-108", "Thick bleach 5L", 2, "CASE"],
                       ["TOW-312", "Kitchen roll white 2ply", 3, "CASE"],
                       ["GLV-203", "Blue nitrile gloves L", 2, "BOX"]], path, title="Sheet1")
        elif spec.key == "16_towerhouse_duplicate":
            path.write_bytes(_csv_bytes([
                ["PO Number", "Stock Code", "Description", "Qty", "Unit"],
                ["TH-4410", "TOW-301", "Toilet roll 2ply 200 sheet", "4", "CASE"],
                ["TH-4410", "CLN-104", "Multi-surface cleaner 750ml", "2", "CASE"],
            ]))
        elif spec.key == "19_milllane_semicolons":
            path.write_bytes(_csv_bytes([
                ["Customer", "Mill Lane Café"], ["Order ref", "ML-0912"],
                ["Product", "Qty", "Unit"],
                ["Kitchen roll white 2ply", "1", "case"],
                ["Microfibre cloths blue 40cm", "2", "pack"],
            ], delimiter=";", encoding="cp1252"))
        elif spec.key == "20_greenway_clean_csv":
            names = {"CLN-104": "Multi-surface cleaner 750ml", "TOW-301": "Toilet roll 2ply 200 sheet",
                     "BAG-402": "Black refuse sacks light duty"}
            rows = [["Order Ref", "Delivery Date", "Product Code", "Product", "Quantity", "Unit", "Notes"]]
            for line in spec.lines:
                rows.append(["GW-1203", "27/10/2026", line.product_code, names[line.product_code], line.qty,
                             line.unit, "Reception"])
            path.write_bytes(_csv_bytes(rows))
        else:
            raise ValueError(f"No writer for {spec.key}")
    return paths


def short_name(description: str) -> str:
    """A catalogue description without its pack size ("..., case of 12"), the way customers write it."""
    return description.split(",")[0].strip()


# ---------------------------------------------------------------- answer keys


def _oakfield_lines(products: dict) -> list[Line]:
    return [Line(code, code, str(1 + (i * 7) % 6), products[code][1], "exact_code", "ready")
            for i, code in enumerate(OAKFIELD_CODES)]


def find_line(text: str, marker: str) -> str | None:
    """The whole line of extracted text that contains the marker."""
    for raw in text.split("\n"):
        if marker in raw:
            return raw.strip()
    return None


def _price(code: str, band: str | None, products: dict, band_prices: dict) -> str:
    if band and (band, code) in band_prices:
        return f"{band_prices[(band, code)]:.2f}"
    return f"{Decimal(products[code][2]):.2f}"


def build_answers(paths: dict[str, Path], products: dict, customers: dict, band_prices: dict,
                  barcodes: dict[str, str]) -> dict[str, dict]:
    from pipeline.ingest import ingest_file

    answers = {}
    for spec in SPECS:
        path = paths[spec.key]
        text = ingest_file(path.name, path.read_bytes()).text
        received = _london(spec.received).astimezone(timezone.utc)
        customer = customers.get(spec.account) if spec.account else None
        band = customer["price_band"] if customer else None
        lines = _oakfield_lines(products) if spec.key == "12_oakfield_40_lines" else spec.lines

        out_lines = []
        for n, line in enumerate(lines, start=1):
            marker = line.marker
            if marker.startswith("{barcode:"):
                marker = barcodes[marker[len("{barcode:"):-1]]
            span = find_line(text, marker) if text else None
            if text and span is None:
                raise ValueError(f"{spec.key}: line marker {marker!r} not found in the extracted text")
            code = line.product_code
            out_lines.append({
                "line_no": n,
                "source_span": span,
                "product_code": code,
                "description": products[code][0] if code else None,
                "qty": line.qty,
                "unit": line.unit,
                "conversion_note": line.conversion_note,
                "unit_price": _price(code, band, products, band_prices) if code else None,
                "match_method": line.match_method,
                "status": line.status,
                **({"note": line.note} if line.note else {}),
            })

        rows = []
        if spec.exportable and customer:
            for line in out_lines:
                if not line["product_code"]:
                    continue
                rows.append([
                    customer["account_code"],
                    spec.po_number or "",
                    received.astimezone(LONDON).strftime("%d/%m/%Y"),
                    line["product_code"],
                    line["qty"],
                    line["unit_price"],
                    date.fromisoformat(spec.delivery_date).strftime("%d/%m/%Y") if spec.delivery_date else "",
                    spec.delivery_notes or "",
                ])

        answers[spec.key] = {
            "order": spec.key,
            "file": path.name,
            "source_type": spec.ext,
            "sender_email": spec.sender_email,
            "received_at": received.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hard_cases": spec.hard_cases,
            "classification": spec.classification,
            "customer": {"account_code": customer["account_code"], "name": customer["name"],
                         "match_method": spec.match_method} if customer else None,
            "po_number": spec.po_number,
            "requested_delivery_date": spec.delivery_date,
            "requested_delivery_raw": spec.delivery_raw,
            "delivery_address": spec.delivery_address,
            "delivery_notes": spec.delivery_notes,
            "lines": out_lines,
            "status": spec.status,
            "expected_initial_status": spec.status,
            "expected_flag_codes": spec.flags,
            "expected_export_rows": rows,
            **({"note": spec.note} if spec.note else {}),
        }
    return answers


def write_demo_orders(out_dir: Path, products: dict, customers: dict, band_prices: dict,
                      barcodes: dict[str, str], client_email: str) -> tuple[Path, Path]:
    """Write the order files and answer keys. Returns (orders folder, answers folder)."""
    orders_dir, answers_dir = out_dir / "orders", out_dir / "answers"
    for folder in (orders_dir, answers_dir):
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.iterdir():
            if old.is_file():
                old.unlink()
    paths = write_order_files(orders_dir, products, barcodes, client_email)
    answers = build_answers(paths, products, customers, band_prices, barcodes)
    for key, answer in answers.items():
        (answers_dir / f"{key}.json").write_text(json.dumps(answer, indent=2, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    return orders_dir, answers_dir
