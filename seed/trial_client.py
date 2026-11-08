"""A second fake client, "Harbourside Foodservice", for practising a real client set-up before real data.

Unlike the demo client, nothing here is loaded for you: this writes the files a real client would send
(product list, customer list, price list, an example import file), 18 test orders, and an export of what
their staff "entered", into seed/trial_client/. You upload them through the screens, as you will with a real
client. Different business (food and catering), different column names, different units (kg, sacks, trays),
different import format (ISO dates). Everything is invented; email addresses use the reserved .example domain.

Run with: python -m seed.trial_client   (also writes seed/trial_client.zip)
"""

from __future__ import annotations

import csv
import io
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from openpyxl import Workbook

from pipeline.workdays import LONDON
from seed.demo_data import ean13

OUT_DIR = Path(__file__).resolve().parent / "trial_client"
CLIENT_NAME = "Harbourside Foodservice"
CLIENT_EMAIL = "orders@harbourside-foodservice.example"
TRIAL_START = "2026-11-09"
MIN_ORDER = "40.00"

# (code, description, sell unit, pack size, inner unit, list price, category)
PRODUCTS: list[tuple[str, str, str, int | None, str | None, str, str]] = [
    ("FV-101", "Maris Piper potatoes 25kg sack", "SACK", 25, "kg", "14.50", "Fresh veg"),
    ("FV-102", "Carrots 10kg bag", "BAG", 10, "kg", "5.20", "Fresh veg"),
    ("FV-103", "Brown onions 10kg bag", "BAG", 10, "kg", "6.80", "Fresh veg"),
    ("FV-104", "Vine tomatoes loose", "KG", None, None, "2.90", "Fresh veg"),
    ("FV-105", "Iceberg lettuce", "EACH", None, None, "0.95", "Fresh veg"),
    ("FV-106", "Chestnut mushrooms 2kg tray", "TRAY", 2, "kg", "9.40", "Fresh veg"),
    ("FV-107", "Button mushrooms 2kg tray", "TRAY", 2, "kg", "7.60", "Fresh veg"),
    ("FV-108", "Mixed peppers 5kg box", "BOX", 5, "kg", "13.50", "Fresh veg"),
    ("FV-109", "Broccoli loose", "KG", None, None, "2.20", "Fresh veg"),
    ("FV-110", "Cucumber", "EACH", None, None, "0.70", "Fresh veg"),
    ("FV-111", "Garlic bulbs 1kg bag", "BAG", None, None, "5.50", "Fresh veg"),
    ("FR-201", "Bananas loose", "KG", None, None, "1.45", "Fruit"),
    ("FR-202", "Lemons 3kg box", "BOX", 3, "kg", "6.90", "Fruit"),
    ("FR-203", "Gala apples 7kg box", "BOX", 7, "kg", "11.80", "Fruit"),
    ("FR-204", "Oranges 7kg box", "BOX", 7, "kg", "10.40", "Fruit"),
    ("DA-301", "Whole milk 2L, case of 6", "CASE", 6, "bottle", "8.40", "Dairy and eggs"),
    ("DA-302", "Semi-skimmed milk 2L, case of 6", "CASE", 6, "bottle", "8.40", "Dairy and eggs"),
    ("DA-303", "Free range eggs, tray of 30", "TRAY", 30, "egg", "6.90", "Dairy and eggs"),
    ("DA-304", "Large barn eggs, tray of 30", "TRAY", 30, "egg", "5.60", "Dairy and eggs"),
    ("DA-305", "Salted butter 250g, case of 20", "CASE", 20, "block", "38.00", "Dairy and eggs"),
    ("DA-306", "Mature cheddar 5kg block", "EACH", None, None, "29.50", "Dairy and eggs"),
    ("DA-307", "Double cream 1L, case of 12", "CASE", 12, "carton", "28.80", "Dairy and eggs"),
    ("DG-401", "Chopped tomatoes 400g tin, case of 24", "CASE", 24, "tin", "14.40", "Dry and tinned"),
    ("DG-402", "Baked beans 2.6kg tin, case of 6", "CASE", 6, "tin", "12.60", "Dry and tinned"),
    ("DG-403", "Basmati rice 10kg sack", "SACK", 10, "kg", "16.50", "Dry and tinned"),
    ("DG-404", "Plain flour 16kg sack", "SACK", 16, "kg", "13.20", "Dry and tinned"),
    ("DG-405", "Granulated sugar 15kg sack", "SACK", 15, "kg", "14.90", "Dry and tinned"),
    ("DG-406", "Penne pasta 3kg, case of 4", "CASE", 4, "bag", "17.60", "Dry and tinned"),
    ("DG-407", "Rapeseed oil 20L drum", "EACH", None, None, "24.50", "Dry and tinned"),
    ("DG-408", "Extra virgin olive oil 5L", "EACH", None, None, "22.00", "Dry and tinned"),
    ("DG-409", "Tea bags, box of 1100", "BOX", None, None, "9.80", "Dry and tinned"),
    ("FZ-501", "Chicken breast fillets 2kg bag, frozen", "BAG", 2, "kg", "11.80", "Frozen"),
    ("FZ-502", "Beef burgers 4oz, case of 40", "CASE", 40, "burger", "24.00", "Frozen"),
    ("FZ-503", "Straight cut chips 2.5kg, case of 4", "CASE", 4, "bag", "13.60", "Frozen"),
    ("DI-601", "Takeaway box 500ml, pack of 250", "PACK", 250, "box", "18.50", "Disposables"),
    ("DI-602", "Foil container large, pack of 100", "PACK", 100, "container", "14.20", "Disposables"),
    ("DI-603", "Napkins 2ply white, pack of 2000", "PACK", 2000, "napkin", "12.90", "Disposables"),
    ("DI-604", "Kitchen roll 2ply, case of 24", "CASE", 24, "roll", "21.60", "Disposables"),
    ("DI-605", "Cling film 450mm x 300m", "EACH", None, None, "6.40", "Disposables"),
]
PRICE = {p[0]: Decimal(p[5]) for p in PRODUCTS}
NAME = {p[0]: p[1] for p in PRODUCTS}
SELL_UNIT = {p[0]: p[2].lower() for p in PRODUCTS}

# (account, name, order emails, delivery address, price band)
CUSTOMERS: list[tuple[str, str, str, str, str]] = [
    ("ANCHOR01", "The Anchor Inn", "kitchen@anchorinn.example", "1 Harbour Road, Portmouth, PM1 1AA", "B"),
    ("RIVCARE1", "Riverside Care Home", "catering@riversidecare.example", "20 Mill Lane, Portmouth, PM2 3BC", "A"),
    ("ACORNS01", "Little Acorns Nursery", "office@littleacorns.example", "5 Church Street, Portmouth, PM3 4DE", "A"),
    ("SPICEG01", "Spice Garden Restaurant", "orders@spicegarden.example", "88 High Street, Portmouth, PM1 2FG", "B"),
    ("BAYVIEW1", "Bayview Café", "hello@bayviewcafe.example", "2 Seafront, Portmouth, PM4 5HJ", "A"),
    ("STMARYS1", "St Mary's Primary School", "kitchen@stmarys-school.example", "Church Lane, Portmouth, PM3 6KL", "A"),
    ("GRAND001", "The Grand Hotel", "purchasing@grandhotel.example; chef@grandhotel.example",
     "The Promenade, Portmouth, PM4 7MN", "B"),
    ("CHIPPY01", "Joe's Chippy", "joe@localmail.example", "14 Station Road, Portmouth, PM2 8PQ", "A"),
    ("PATEL001", "Mrs Patel's Kitchen", "patel@localmail.example", "9 Park Avenue, Portmouth, PM5 9RS", "A"),
    ("OAKS0001", "The Oaks Golf Club", "stewards@oaksgolf.example", "Oaks Drive, Portmouth, PM6 1TU", "B"),
]
BAND = {c[0]: c[4] for c in CUSTOMERS}


def price_for(account: str, code: str) -> Decimal:
    """Band B customers pay 5% less than the list price."""
    price = PRICE[code]
    return (price * Decimal("0.95")).quantize(Decimal("0.01"), ROUND_HALF_UP) if BAND[account] == "B" else price


# ---------------------------------------------------------------- the 18 test orders


@dataclass
class Order:
    key: str
    ext: str
    received: str  # London time
    sender: str
    sender_name: str
    account: str | None
    subject: str
    body: str
    po: str | None
    due: str | None  # delivery date asked for, ISO
    lines: list[tuple[str, int]]  # the right answer: (product code, quantity in the sell unit)
    in_staff_file: bool
    expect: str  # what you should see, in plain words
    needs_sender_typed: bool = False
    extra: dict = field(default_factory=dict)


ORDERS: list[Order] = [
    Order("01_anchor_codes", "eml", "2026-11-09 08:10", "kitchen@anchorinn.example", "Dave Hollis", "ANCHOR01",
          "Order AN-5501",
          "Morning,\n\nOrder for Wednesday please, PO AN-5501\n\nFV-101 x 2 sacks\nFV-103 x 1 bag\nDA-301 x 3 cases\n"
          "DA-303 x 2 trays\nDG-407 x 1 each\n\nThanks\nDave, The Anchor Inn\n",
          "AN-5501", "2026-11-11",
          [("FV-101", 2), ("FV-103", 1), ("DA-301", 3), ("DA-303", 2), ("DG-407", 1)], True,
          "The clean one. Customer by sender email, products by their codes, nothing flagged: Ready."),
    Order("02_spice_garden_names", "eml", "2026-11-09 10:42", "orders@spicegarden.example", "Anil Shah", "SPICEG01",
          "Weekly veg",
          "Hi,\n\nPO SG-0311, for Thursday:\n\n5kg vine tomatoes\n2 trays chestnut mushrooms\n3 bags carrots\n"
          "1 case chopped tomatoes\n4kg broccoli\n10 each iceberg lettuce\n\nThanks, Anil\nSpice Garden\n",
          "SG-0311", "2026-11-12",
          [("FV-104", 5), ("FV-106", 2), ("FV-102", 3), ("DG-401", 1), ("FV-109", 4), ("FV-105", 10)], True,
          "Products written as words, not codes, including two sold by the kilo. Expect them matched by name; "
          "check how many lines need a look."),
    Order("03_riverside_letter", "pdf", "2026-11-10 09:05", "catering@riversidecare.example", "Riverside Care Home",
          "RIVCARE1", "Purchase order RC-221",
          "", "RC-221", "2026-11-13",
          [("FV-101", 2), ("DA-302", 3), ("DA-303", 1), ("DG-402", 2), ("DG-404", 1)], True,
          "A PDF letter. Type the sender (catering@riversidecare.example) when you upload it. Delivery "
          "'Friday 13 November'.", needs_sender_typed=True),
    Order("04_grand_hotel_sheet", "xlsx", "2026-11-10 11:30", "purchasing@grandhotel.example", "The Grand Hotel",
          "GRAND001", "Purchase order GH-7781",
          "", "GH-7781", "2026-11-12",
          [("FZ-501", 6), ("FZ-502", 3), ("FZ-503", 4), ("DA-307", 2), ("DA-306", 1)], True,
          "An Excel sheet with the hotel's own column names and product names. Type the sender "
          "(purchasing@grandhotel.example). Delivery 'Thursday'.", needs_sender_typed=True),
    Order("05_bayview_csv", "csv", "2026-11-11 07:55", "hello@bayviewcafe.example", "Bayview Café", "BAYVIEW1",
          "Purchase order BV-1142",
          "", "BV-1142", "2026-11-13",
          [("FR-202", 2), ("DG-408", 1), ("DI-601", 3), ("DI-604", 2)], True,
          "A CSV with its own headings (Code, Description, Qty ordered, Unit, Your PO). Type the sender "
          "(hello@bayviewcafe.example).", needs_sender_typed=True),
    Order("06_st_marys_text", "txt", "2026-11-11 14:20", "kitchen@stmarys-school.example", "St Mary's Kitchen",
          "STMARYS1", "School kitchen order",
          "St Mary's Primary School kitchen order\nPO SM-0099\nDeliver Monday 16th\n\n2 sacks Maris Piper potatoes\n"
          "48 tins chopped tomatoes\n2 trays free range eggs\n1 sack basmati rice\n12 bottles whole milk 2L\n",
          "SM-0099", "2026-11-16",
          [("FV-101", 2), ("DG-401", 2), ("DA-303", 2), ("DG-403", 1), ("DA-301", 2)], True,
          "A plain text file. Type the sender (kitchen@stmarys-school.example). Unit conversion: 48 tins = "
          "2 cases, 12 bottles = 2 cases.", needs_sender_typed=True),
    Order("07_acorns_kilos", "eml", "2026-11-12 08:30", "office@littleacorns.example", "Sue Barker", "ACORNS01",
          "Nursery order LA-3310",
          "Hello,\n\nPO LA-3310, delivery Monday please.\n\n50kg Maris Piper potatoes\n30kg carrots\n"
          "12 bottles semi-skimmed milk (2L)\n2 trays of large eggs\n\nThank you\nSue, Little Acorns\n",
          "LA-3310", "2026-11-16",
          [("FV-101", 2), ("FV-102", 3), ("DA-302", 2), ("DA-304", 2)], True,
          "Kilos into sacks and bags (50kg = 2 sacks, 30kg = 3 bags) and bottles into cases (12 = 2 cases)."),
    Order("08_anchor_odd_kilos", "eml", "2026-11-12 09:40", "kitchen@anchorinn.example", "Dave Hollis", "ANCHOR01",
          "Order AN-5502",
          "Hi, PO AN-5502 for Friday.\n\n30kg Maris Piper potatoes\n2 bags brown onions\n\nDave\n",
          "AN-5502", "2026-11-13",
          [("FV-101", 1), ("FV-103", 2)], False,
          "PROBLEM expected: 30kg is not a whole number of 25kg sacks. Never guessed; you fix the quantity."),
    Order("09_not_a_customer", "eml", "2026-11-12 10:15", "orders@luckydragon.example", "Lucky Dragon Takeaway", None,
          "New order please",
          "Hello, we would like to start ordering from you. First order: 10kg onions, 2 cases of chicken "
          "fillets, 5 packs takeaway boxes. How do we set up an account?\n\nMr Chen\n",
          None, None, [], False,
          "Not one of your customers: it cannot be approved. Use Ask client contact, then Not an order."),
    Order("10_chippy_other_address", "eml", "2026-11-12 11:00", "orders.chippy@localmail.example", "Joe", "CHIPPY01",
          "Joe's Chippy order JC-0021",
          "Joe's Chippy here, PO JC-0021, deliver Tuesday.\n\n2 sacks Maris Piper potatoes\n1 each rapeseed oil 20L\n"
          "2 cases straight cut chips\n\nJoe\n",
          "JC-0021", "2026-11-17",
          [("FV-101", 2), ("DG-407", 1), ("FZ-503", 2)], True,
          "Sent from an address that is not on the customer list, on a mail domain two customers share. "
          "Expect the customer to be guessed from the name in the text and marked Check."),
    Order("11_riverside_chase", "eml", "2026-11-12 15:45", "catering@riversidecare.example", "Riverside Care Home",
          "RIVCARE1", "Where is our delivery?",
          "Hi, where is our delivery for PO RC-221? It was due yesterday and nothing has arrived.\n\nRiverside "
          "Care Home\n",
          None, None, [], False, "A delivery chase, not an order: Pass on."),
    Order("12_anchor_amendment", "eml", "2026-11-13 08:05", "kitchen@anchorinn.example", "Dave Hollis", "ANCHOR01",
          "Addition to Monday's order",
          "Hi Dave here. Please add 2 trays of free range eggs to the order I sent on Monday. Thanks\n",
          None, None, [], False,
          "An amendment to an earlier order (not processed in version one): Pass on."),
    Order("13_anchor_resend", "eml", "2026-11-13 08:20", "kitchen@anchorinn.example", "Dave Hollis", "ANCHOR01",
          "RESEND order AN-5501",
          "Morning,\n\nOrder for Wednesday please, PO AN-5501\n\nFV-101 x 2 sacks\nFV-103 x 1 bag\nDA-301 x 3 cases\n"
          "DA-303 x 2 trays\nDG-407 x 1 each\n\nThanks\nDave, The Anchor Inn\n",
          "AN-5501", "2026-11-11", [], False,
          "The same PO as order 01, sent again. Expect a possible duplicate: Check."),
    Order("14_spice_garden_wagyu", "eml", "2026-11-13 09:10", "orders@spicegarden.example", "Anil Shah", "SPICEG01",
          "Order SG-0318",
          "PO SG-0318 for Tuesday please.\n\n2kg wagyu beef fillet\n5kg vine tomatoes\n1 case double cream\n\nAnil\n",
          "SG-0318", "2026-11-17",
          [("FV-104", 5), ("DA-307", 1)], True,
          "One product you do not sell (wagyu beef fillet): that line is a Problem. Delete it, as their staff "
          "would not enter it either."),
    Order("15_bayview_mushrooms", "eml", "2026-11-13 09:50", "hello@bayviewcafe.example", "Bayview Café", "BAYVIEW1",
          "Order BV-1150",
          "Hi, PO BV-1150, for Monday.\n\n2 trays mushrooms\n3 each iceberg lettuce\n1 tray free range eggs\n"
          "2 cases kitchen roll\n\nBayview\n",
          "BV-1150", "2026-11-16",
          [("FV-106", 2), ("FV-105", 3), ("DA-303", 1), ("DI-604", 2)], True,
          "'Mushrooms' could be chestnut or button. Expect it to be flagged for you to choose; chestnut is the "
          "right answer."),
    Order("16_acorns_price_no_po", "eml", "2026-11-13 10:30", "office@littleacorns.example", "Sue Barker", "ACORNS01",
          "Milk and flour",
          "Hello, 4 cases semi-skimmed milk 2L at £7.20 per case please, and 1 sack plain flour. Tuesday "
          "delivery. Thanks, Sue\n",
          None, "2026-11-17", [("DA-302", 4), ("DG-404", 1)], True,
          "Two flags: the price she wrote (£7.20) differs from your price list (£8.40), and there is no PO "
          "number."),
    Order("17_bayview_small", "eml", "2026-11-13 11:05", "hello@bayviewcafe.example", "Bayview Café", "BAYVIEW1",
          "Order BV-1151",
          "PO BV-1151, Monday please: 4 each iceberg lettuce. Thanks\n",
          "BV-1151", "2026-11-16", [("FV-105", 4)], True,
          f"Below your minimum order value (£{MIN_ORDER}): Check."),
    Order("18_grand_vague", "eml", "2026-11-13 12:20", "purchasing@grandhotel.example", "Grand Hotel Purchasing",
          "GRAND001", "Takeaway supplies",
          "PO GH-7790. We need a few boxes of takeaway containers and some napkins by Friday.\n",
          "GH-7790", "2026-11-20", [], False,
          "No real quantities ('a few', 'some'): Problem. Never guessed; you call them or ask the contact."),
]
# What their staff "entered" differently, in the staff entries file: (order key) -> what changes.
PLANTED = {
    "02_spice_garden_names": "tomatoes entered as 4 kg, not 5 kg",
    "04_grand_hotel_sheet": "delivery date entered one day later (Fri 13 Nov, not Thu 12 Nov)",
    "07_acorns_kilos": "large barn eggs (DA-304) entered as free range eggs (DA-303)",
}


# ---------------------------------------------------------------- writing the files


def _london(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=LONDON)


def _eml(order: Order) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{order.sender_name} <{order.sender}>"
    msg["To"] = CLIENT_EMAIL
    msg["Subject"] = order.subject
    msg["Date"] = format_datetime(_london(order.received))
    msg["Message-ID"] = f"<{order.key}@mail.example>"
    msg.set_content(order.body)
    return bytes(msg)


def _letter_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    s = getSampleStyleSheet()
    lines = [
        ("<b>Riverside Care Home</b>", "Title"), ("20 Mill Lane, Portmouth, PM2 3BC", "Normal"), (None, None),
        ("10 November 2026", "Normal"), (None, None), ("Dear Harbourside Foodservice,", "Normal"), (None, None),
        ("<b>Purchase order RC-221</b>", "Normal"), (None, None),
        ("Please deliver the following on Friday 13 November:", "Normal"), (None, None),
        ("2 sacks Maris Piper potatoes", "Normal"), ("3 cases semi-skimmed milk 2 litre", "Normal"),
        ("1 tray free range eggs", "Normal"), ("2 cases baked beans (large tins)", "Normal"),
        ("1 sack plain flour", "Normal"), (None, None),
        ("Delivery to the kitchen door at the side of the building before 10am.", "Normal"), (None, None),
        ("Yours sincerely,", "Normal"), (None, None), ("Margaret Doyle", "Normal"), ("Catering Manager", "Normal"),
    ]
    story = [Spacer(1, 12) if text is None else Paragraph(text, s[style]) for text, style in lines]
    SimpleDocTemplate(str(path), pagesize=A4, invariant=1, title="", author="", creator="", subject="",
                      leftMargin=56, rightMargin=56, topMargin=56, bottomMargin=56).build(story)


def _grand_hotel_sheet(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Order"
    ws.append(["The Grand Hotel: purchase order GH-7781"])
    ws.append(["Required: Thursday 12 November"])
    ws.append([])
    ws.append(["Product", "Qty", "Pack", "Notes"])
    for row in [["Chicken breast fillets 2kg (frozen)", 6, "bag", ""], ["Beef burgers 4oz", 3, "case", ""],
                ["Chips straight cut 2.5kg", 4, "case", "Rear loading bay"], ["Double cream 1L", 2, "case", ""],
                ["Mature cheddar 5kg block", 1, "each", ""]]:
        ws.append(row)
    fixed = datetime(2026, 11, 1, 9, 0, 0)
    wb.properties.creator = "The Grand Hotel"
    wb.properties.created = fixed
    wb.properties.modified = fixed
    wb.save(path)


def _csv_text(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue()


def write_files(out_dir: Path = OUT_DIR) -> dict[str, Path]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    setup, orders = out_dir / "1_setup_files", out_dir / "2_orders"
    setup.mkdir(parents=True)
    orders.mkdir()
    paths: dict[str, Path] = {}

    # Product list: title rows above the headings, and column names that are not the demo's.
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock list"
    ws.append([f"{CLIENT_NAME} stock list, November 2026"])
    ws.append([])
    ws.append(["Item No", "Item Description", "UOM", "Units Per UOM", "Inner UOM", "Sell Price", "EAN", "Category"])
    for i, (code, desc, unit, pack, inner, price, category) in enumerate(PRODUCTS, start=1):
        ws.append([code, desc, unit, pack, inner, float(price), ean13(500 + i), category])
    for col, width in zip("ABCDEFGH", [10, 42, 8, 14, 11, 11, 16, 16]):
        ws.column_dimensions[col].width = width
    fixed = datetime(2026, 11, 1, 9, 0, 0)
    wb.properties.creator = CLIENT_NAME
    wb.properties.created = fixed
    wb.properties.modified = fixed
    paths["products"] = setup / "harbourside_stock_list.xlsx"
    wb.save(paths["products"])

    paths["customers"] = setup / "harbourside_customers.csv"
    paths["customers"].write_text(_csv_text(
        [["Account No", "Customer Name", "Order Email", "Delivery Address", "Price Band"], *map(list, CUSTOMERS)]),
        encoding="utf-8")

    paths["prices"] = setup / "harbourside_band_b_prices.csv"
    paths["prices"].write_text(_csv_text(
        [["Price Band", "Item No", "Price"],
         *[["B", code, f"{price_for('ANCHOR01', code):.2f}"] for code, *_ in PRODUCTS]]), encoding="utf-8")

    paths["template"] = setup / "Unleashed_SalesOrderImport_example.csv"
    paths["template"].write_text(_csv_text(
        [["Customer Code", "Order Number", "Order Date", "Required Date", "Product Code", "Order Quantity",
          "Unit Price", "Comments"],
         ["EXAMPLE01", "PO-1001", "2026-11-02", "2026-11-04", "ABC-100", "3", "12.50", "Back door"],
         ["EXAMPLE01", "PO-1001", "2026-11-02", "2026-11-04", "ABC-200", "1", "4.20", "Back door"]]),
        encoding="utf-8")

    for order in ORDERS:
        path = orders / f"{order.key}.{order.ext}"
        if order.ext == "eml":
            path.write_bytes(_eml(order))
        elif order.ext == "txt":
            path.write_text(order.body, encoding="utf-8")
        elif order.ext == "pdf":
            _letter_pdf(path)
        elif order.key.startswith("04"):
            _grand_hotel_sheet(path)
        else:  # 05: Bayview's CSV
            path.write_text(_csv_text([
                ["Code", "Description", "Qty ordered", "Unit", "Your PO"],
                ["FR-202", "Lemons 3kg box", "2", "box", "BV-1142"],
                ["DG-408", "Extra virgin olive oil 5L", "1", "each", "BV-1142"],
                ["DI-601", "Takeaway box 500ml", "3", "pack", "BV-1142"],
                ["DI-604", "Kitchen roll 2ply", "2", "case", "BV-1142"]]),
                encoding="utf-8")
        paths[order.key] = path

    paths["staff"] = out_dir / "3_after_the_trial" / "staff_entries_harbourside.csv"
    paths["staff"].parent.mkdir()
    write_staff_entries(paths["staff"])
    paths["guide"] = out_dir / "READ_ME_FIRST.md"
    paths["guide"].write_text(guide_text(), encoding="utf-8")
    return paths


def _staff_unit(code: str) -> str:
    """Their system exports the unit as it names it."""
    return SELL_UNIT[code].upper()


def write_staff_entries(path: Path) -> None:
    """What their staff entered for the same orders, with the planted differences."""
    rows = [["Order Ref", "Account", "Entered On", "Item", "Qty", "UOM", "Price", "Due Date"]]
    for order in ORDERS:
        if not order.in_staff_file:
            continue
        entered = _london(order.received).strftime("%d/%m/%Y")
        due = datetime.strptime(order.due, "%Y-%m-%d") if order.due else None
        for code, qty in order.lines:
            this_due, this_qty, this_code = due, qty, code
            if order.key == "02_spice_garden_names" and code == "FV-104":
                this_qty = 4
            if order.key == "07_acorns_kilos" and code == "DA-304":
                this_code = "DA-303"
            if order.key == "04_grand_hotel_sheet" and this_due is not None:
                this_due = this_due.replace(day=this_due.day + 1)
            rows.append([order.po or "", order.account, entered, this_code, str(this_qty), _staff_unit(this_code),
                         f"{price_for(order.account, this_code):.2f}",
                         this_due.strftime("%d/%m/%Y") if this_due else ""])
    path.write_text(_csv_text(rows), encoding="utf-8")


def guide_text() -> str:
    sender_rows = "\n".join(f"| {o.key}.{o.ext} | {o.sender} | {_london(o.received):%a %d %b %H:%M} |"
                            for o in ORDERS if o.needs_sender_typed)
    order_rows = "\n".join(f"| {o.key} | {o.expect} |" for o in ORDERS)
    planted = "\n".join(f"- {k.split('_', 1)[0]}: {v}" for k, v in PLANTED.items())
    return f"""# Harbourside Foodservice: a fake client to practise on

A food and catering wholesaler. Nothing in it is real. Use it to run a whole client set-up and shadow trial
before real data, exactly as you would with a real client.

## 1. Add the client (Add client)

- Name: {CLIENT_NAME}. ERP system: Unleashed. Mode: Shadow trial.
- Contact: Operations, {CLIENT_EMAIL}
- Trial start date: Mon 9 Nov 2026, 10 working days.

## 2. Upload the setup files (folder 1_setup_files)

| Section | File | What to look for |
|---|---|---|
| Product catalogue | harbourside_stock_list.xlsx | Heading rows above the column names. Check "UOM" is the unit they sell in, "Units Per UOM" the pack size, "Inner UOM" the inner unit. |
| Prices by band | harbourside_band_b_prices.csv | Band B pays 5% less than the list price |
| Customer list | harbourside_customers.csv | The Grand Hotel has two order emails in one cell. Two customers share the localmail.example domain. |
| Output format | Unleashed_SalesOrderImport_example.csv | Check the preview: dates should come out as 2026-11-11, not 11/11/2026 |

Then Rules: cutoff 15:00, minimum order value {MIN_ORDER}, and tick "Flag orders with no PO number". Minutes per
order: 6. Look at the unit words: kg, sack, bag, tray, case, tin, bottle and so on should all be there.

## 3. Upload the 18 test orders (folder 2_orders), all at once

The .eml files carry their own sender and arrival time. For the four other files, open "Add the sender or
arrival time" before uploading each one and type the sender (upload them one at a time):

| File | Sender to type | Arrival time to type (UK) |
|---|---|---|
{sender_rows}

## 4. What you should see

The system never guesses: anything it is not sure of is Check or Problem. These are the intentions:

| Order | What it tests |
|---|---|
{order_rows}

Words in an order that the system matched by name are always Check ("suggested match, please confirm"), however
sure it is: a guess never reaches Ready. Only lines written with a product code (orders 01 and 05) come out
Ready. Open an order and press 1 to confirm each suggestion, then A to approve.

Review them all (press A to approve, 1-5 to pick a product), then export. Things to do on the way:
delete the wagyu line on 14, pick chestnut on 15, use Ask client contact then Not an order on 09, and Pass on
the resend (13) rather than approving it. Leave 08 and 18 alone: they stay Problems until you call the customer.

## 5. After the trial (folder 3_after_the_trial)

On Trial results, upload staff_entries_harbourside.csv and check the columns. It holds what their staff
"entered" for the orders that were real sales orders, with 3 differences planted:

{planted}

You should see exactly these 3 differences and nothing else. If you also approved the resend (13), it shows
as "not entered by their team": that is the duplicate you should have passed on.

Tag each difference with a cause and open the client report. Delete the client from its Settings when done.

## What I got when I ran all of this through the real app

I set the client up through the screens, read the 18 orders with the AI, and reviewed them like this: approve
everything that could be approved, delete the wagyu line, pick chestnut mushrooms, pass on 09. 13 orders were
approved, 3 passed on, and 2 (08 and 18) left as Problems. Then with the staff entries file: 12 orders compared,
9 exactly right (75%), 0 confidently wrong, and the 3 planted differences. First pass, 10 of the 12 were
flagged for review (the system was unsure about 83% of them, which is the right way round for a start).
"""


def make_zip(out_dir: Path = OUT_DIR) -> Path:
    archive = out_dir.parent / "trial_client.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                zf.write(path, Path("harbourside_fake_client") / path.relative_to(out_dir))
    return archive


def main() -> None:
    paths = write_files()
    print(f"Wrote {len(ORDERS)} orders and the setup files to {OUT_DIR}")
    print(f"Zip: {make_zip()}")
    print(f"Read first: {paths['guide']}")


if __name__ == "__main__":
    main()
