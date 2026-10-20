"""Build the fictional demo client, "Demo Cleaning Supplies".

Writes the raw files a real client would send (product list, customer list,
band B price list, an example import file) into seed/demo/, plus the 20 test
orders and their answer keys (see seed/demo_orders.py), then sets the client up
from those files through the same import code the setup screen uses.

Everything here is invented. Email addresses use the reserved .example domain.

Run with: make seed
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from sqlmodel import Session, select

from app.clientsetup import importers
from app.db import delete_client, get_engine
from app.models import Alias, Client, Customer
from pipeline import normalise
from pipeline.tables import guess_mapping, read_table
from pipeline.vocab import DEFAULT_RULES, DEFAULT_UNIT_SYNONYMS, ClientMode, MappingKind
from seed.demo_orders import STAFF_FILE, write_demo_orders

DEMO_ID = "demo"
SEED_DIR = Path(__file__).resolve().parent / "demo"

PRODUCTS_FILE = "demo_products.xlsx"
CUSTOMERS_FILE = "demo_customers.csv"
PRICES_FILE = "demo_prices.csv"
TEMPLATE_FILE = "SalesOrderImport_example.csv"
DEMO_CONTACT_EMAIL = "operations@demo-cleaning.example"

# (code, description, sell unit, pack size, inner unit, price, category, has barcode)
PRODUCTS: list[tuple[str, str, str, int | None, str | None, str, str, bool]] = [
    # Cleaning chemicals
    ("CLN-101", "Antibacterial surface spray 750ml, case of 12", "CASE", 12, "bottle", "28.00", "Chemicals", True),
    ("CLN-102", "Disinfectant spray 750ml, case of 12", "CASE", 12, "bottle", "31.50", "Chemicals", True),
    ("CLN-103", "Glass and mirror cleaner 750ml, case of 12", "CASE", 12, "bottle", "22.80", "Chemicals", True),
    ("CLN-104", "Multi-surface cleaner 750ml, case of 12", "CASE", 12, "bottle", "19.20", "Chemicals", True),
    ("CLN-105", "Multi-surface cleaner 5L, case of 2", "CASE", 2, "bottle", "17.40", "Chemicals", True),
    ("CLN-106", "Washing up liquid 5L, case of 2", "CASE", 2, "bottle", "14.60", "Chemicals", True),
    ("CLN-107", "Washing up liquid 1L, case of 6", "CASE", 6, "bottle", "11.20", "Chemicals", True),
    ("CLN-108", "Thick bleach 5L, case of 2", "CASE", 2, "bottle", "12.90", "Chemicals", True),
    ("CLN-109", "Thick bleach 750ml, case of 12", "CASE", 12, "bottle", "13.20", "Chemicals", True),
    ("CLN-110", "Toilet cleaner 750ml, case of 12", "CASE", 12, "bottle", "15.60", "Chemicals", True),
    ("CLN-111", "Limescale remover 1L, case of 6", "CASE", 6, "bottle", "18.30", "Chemicals", True),
    ("CLN-112", "Neutral floor cleaner 5L, case of 2", "CASE", 2, "bottle", "21.00", "Chemicals", True),
    ("CLN-113", "Kitchen degreaser 5L, case of 2", "CASE", 2, "bottle", "26.40", "Chemicals", True),
    ("CLN-114", "Oven cleaner gel 1L, case of 6", "CASE", 6, "bottle", "29.70", "Chemicals", True),
    ("CLN-115", "Pink pearl hand soap 5L, case of 2", "CASE", 2, "bottle", "16.80", "Chemicals", True),
    ("CLN-116", "Antibacterial hand soap 500ml pump, case of 6", "CASE", 6, "bottle", "13.50", "Chemicals", True),
    ("CLN-117", "Hand sanitiser gel 500ml pump, case of 6", "CASE", 6, "bottle", "19.80", "Chemicals", True),
    ("CLN-118", "Hand sanitiser gel 5L", "EACH", None, None, "24.00", "Chemicals", True),
    ("CLN-119", "Dishwasher detergent 5L, case of 2", "CASE", 2, "bottle", "27.90", "Chemicals", True),
    ("CLN-120", "Dishwasher rinse aid 5L, case of 2", "CASE", 2, "bottle", "24.60", "Chemicals", True),
    ("CLN-121", "Glasswasher detergent 5L, case of 2", "CASE", 2, "bottle", "25.20", "Chemicals", True),
    ("CLN-122", "Air freshener aerosol fresh linen 300ml, case of 12", "CASE", 12, "can", "16.20", "Chemicals", True),
    ("CLN-123", "Carpet shampoo 5L", "EACH", None, None, "14.50", "Chemicals", True),
    ("CLN-124", "Stainless steel polish aerosol 400ml, case of 6", "CASE", 6, "can", "17.10", "Chemicals", True),
    ("CLN-125", "Antibacterial surface spray 5L refill", "EACH", None, None, "11.40", "Chemicals", True),
    ("CLN-126", "Bio laundry liquid 5L, case of 2", "CASE", 2, "bottle", "23.40", "Chemicals", True),
    # Gloves
    ("GLV-201", "Blue nitrile gloves powder free S, box of 100", "BOX", 100, "glove", "6.20", "Gloves", True),
    ("GLV-202", "Blue nitrile gloves powder free M, box of 100", "BOX", 100, "glove", "6.20", "Gloves", True),
    ("GLV-203", "Blue nitrile gloves powder free L, box of 100", "BOX", 100, "glove", "6.20", "Gloves", True),
    ("GLV-204", "Blue nitrile gloves powder free XL, box of 100", "BOX", 100, "glove", "6.50", "Gloves", True),
    ("GLV-211", "Clear vinyl gloves powder free M, box of 100", "BOX", 100, "glove", "3.80", "Gloves", True),
    ("GLV-212", "Clear vinyl gloves powder free L, box of 100", "BOX", 100, "glove", "3.80", "Gloves", True),
    ("GLV-221", "Yellow household rubber gloves M, pack of 12 pairs", "PACK", 12, "pair", "9.60", "Gloves", True),
    ("GLV-222", "Yellow household rubber gloves L, pack of 12 pairs", "PACK", 12, "pair", "9.60", "Gloves", True),
    ("GLV-231", "Black heavy duty nitrile gloves L, box of 100", "BOX", 100, "glove", "8.40", "Gloves", True),
    ("GLV-241", "Clear polythene disposable gloves, pack of 100", "PACK", 100, "glove", "1.10", "Gloves", True),
    # Paper
    ("TOW-301", "Toilet roll 2ply 200 sheet, case of 36", "CASE", 36, "roll", "16.90", "Paper", True),
    ("TOW-302", "Mini jumbo toilet roll 2ply, case of 12", "CASE", 12, "roll", "18.40", "Paper", True),
    ("TOW-303", "Interfold hand towels white 2ply, case of 15 sleeves", "CASE", 15, "sleeve", "24.90", "Paper", True),
    ("TOW-304", "C-fold hand towels blue 1ply, case of 2880", "CASE", None, None, "17.60", "Paper", True),
    ("TOW-305", "Centrefeed roll blue 2ply, case of 6", "CASE", 6, "roll", "19.20", "Paper", True),
    ("TOW-306", "Centrefeed roll white 2ply, case of 6", "CASE", 6, "roll", "21.60", "Paper", True),
    ("TOW-310", "Blue roll 2ply, pack of 6", "PACK", 6, "roll", "14.40", "Paper", True),
    ("TOW-311", "Blue roll 2ply, single roll", "EACH", 1, "roll", "2.60", "Paper", True),
    ("TOW-312", "Kitchen roll white 2ply, case of 24", "CASE", 24, "roll", "15.80", "Paper", True),
    ("TOW-313", "Couch roll 2ply 50cm, case of 9", "CASE", 9, "roll", "22.50", "Paper", True),
    ("TOW-320", "White paper napkins 2ply 33cm, case of 2000", "CASE", 2000, "napkin", "18.00", "Paper", True),
    ("TOW-321", "Black paper napkins 3ply 40cm, case of 1000", "CASE", 1000, "napkin", "21.50", "Paper", True),
    ("TOW-330", "Facial tissues cube, case of 24", "CASE", 24, "cube", "19.90", "Paper", True),
    # Bags
    ("BAG-401", "Black refuse sacks heavy duty, box of 200", "BOX", 200, "sack", "12.90", "Bags", True),
    ("BAG-402", "Black refuse sacks light duty, box of 200", "BOX", 200, "sack", "8.70", "Bags", True),
    ("BAG-403", "Clear refuse sacks, box of 200", "BOX", 200, "sack", "11.40", "Bags", True),
    ("BAG-404", "White pedal bin liners 15x24, box of 1000", "BOX", 1000, "liner", "9.80", "Bags", True),
    ("BAG-405", "White swing bin liners, box of 500", "BOX", 500, "liner", "10.20", "Bags", True),
    ("BAG-406", "Compostable caddy liners 10L, box of 500", "BOX", 500, "liner", "14.60", "Bags", True),
    ("BAG-407", "Yellow clinical waste sacks, box of 250", "BOX", 250, "sack", "18.90", "Bags", True),
    ("BAG-408", "Wheelie bin liners extra heavy, box of 100", "BOX", 100, "liner", "19.50", "Bags", True),
    ("BAG-409", "Clear recycling sacks 90L, box of 200", "BOX", 200, "sack", "12.30", "Bags", True),
    ("BAG-410", "Food bags 7x9, box of 1000", "BOX", 1000, "bag", "6.30", "Bags", True),
    # Dispensers
    ("DSP-501", "Mini jumbo toilet roll dispenser white", "EACH", None, None, "18.50", "Dispensers", False),
    ("DSP-502", "Centrefeed roll dispenser white", "EACH", None, None, "21.00", "Dispensers", False),
    ("DSP-503", "C-fold hand towel dispenser white", "EACH", None, None, "19.40", "Dispensers", False),
    ("DSP-504", "Refillable soap dispenser 1L white", "EACH", None, None, "12.60", "Dispensers", False),
    ("DSP-505", "Touch free hand sanitiser dispenser", "EACH", None, None, "34.00", "Dispensers", False),
    ("DSP-506", "Blue roll wall bracket", "EACH", None, None, "15.20", "Dispensers", False),
    ("DSP-507", "Stainless steel interfold towel dispenser", "EACH", None, None, "39.00", "Dispensers", False),
    ("DSP-508", "Sanitary bin 23L", "EACH", None, None, "27.50", "Dispensers", False),
    ("DSP-509", "Empty trigger spray bottles 750ml, pack of 10", "PACK", 10, "bottle", "6.80", "Dispensers", True),
    # Cloths
    ("CLO-601", "Microfibre cloths blue 40cm, pack of 10", "PACK", 10, "cloth", "7.20", "Cloths", True),
    ("CLO-602", "Microfibre cloths red 40cm, pack of 10", "PACK", 10, "cloth", "7.20", "Cloths", True),
    ("CLO-603", "Microfibre cloths green 40cm, pack of 10", "PACK", 10, "cloth", "7.20", "Cloths", True),
    ("CLO-604", "Microfibre cloths yellow 40cm, pack of 10", "PACK", 10, "cloth", "7.20", "Cloths", True),
    ("CLO-611", "All purpose cloths blue, pack of 50", "PACK", 50, "cloth", "4.90", "Cloths", True),
    ("CLO-612", "All purpose cloths red, pack of 50", "PACK", 50, "cloth", "4.90", "Cloths", True),
    ("CLO-621", "Green scourer pads, pack of 10", "PACK", 10, "pad", "2.40", "Cloths", True),
    ("CLO-622", "Sponge scourers, pack of 10", "PACK", 10, "sponge", "2.90", "Cloths", True),
    ("CLO-631", "Stainless steel scourers, pack of 10", "PACK", 10, "scourer", "3.60", "Cloths", True),
    ("CLO-641", "Glass polishing cloths, pack of 5", "PACK", 5, "cloth", "6.40", "Cloths", True),
    ("CLO-651", "Yellow disposable dusters, pack of 50", "PACK", 50, "duster", "5.70", "Cloths", True),
    ("CLO-661", "Kentucky mop head 16oz", "EACH", None, None, "4.20", "Cloths", True),
    ("CLO-662", "Socket mop head 14oz", "EACH", None, None, "2.90", "Cloths", True),
]

# (account ref, name, order emails, delivery address, price band)
CUSTOMERS: list[tuple[str, str, str, str, str]] = [
    ("RIVERS01", "Riverside Hotel", "purchasing@riverside-hotel.example; kitchen@riverside-hotel.example",
     "14 River Walk, Kingsford, KF1 2AB", "A"),
    ("HARBVW01", "Harbour View Hotel", "orders@harbourhotels.example",
     "2 Quay Street, Kingsford, KF3 1QS", "B"),
    ("HARBPT01", "Harbour Point Hotel", "supplies@harbourhotels.example",
     "88 Marine Parade, Kingsford, KF3 4MP", "B"),
    ("MILLLN01", "Mill Lane Café", "hello@milllanecafe.example",
     "5 Mill Lane, Kingsford, KF2 6ML", "A"),
    ("STANNE01", "St Anne's Primary School", "bursar@stannes-school.example, site.manager@stannes-school.example",
     "Church Road, Kingsford, KF4 2CR", "B"),
    ("OAKFLD01", "Oakfield Academy", "facilities@oakfield-academy.example",
     "Oakfield Lane, Westbury, KF6 8OL", "B"),
    ("FITHUB01", "FitHub Gym Kingsford", "manager@fithub-gyms.example",
     "Unit 4, Riverside Retail Park, Kingsford, KF1 9RP", "A"),
    ("PULSE001", "Pulse Fitness Studio", "orders@pulse-studio.example",
     "21 High Street, Westbury, KF6 1HS", "A"),
    ("GREENW01", "Greenway Offices", "facilities@greenway-offices.example",
     "Greenway House, 9 Station Road, Kingsford, KF2 3SR", "A"),
    ("TOWERH01", "Tower House Business Centre", "ops@towerhouse-bc.example",
     "Tower House, Bridge Street, Kingsford, KF1 5BS", "B"),
    ("OLDMIL01", "The Old Mill Pub", "landlord@oldmillpub.example; bar@oldmillpub.example",
     "The Old Mill, Mill Lane, Kingsford, KF2 6MP", "A"),
    ("LARKSP01", "Larkspur Care Home", "housekeeping@larkspurcare.example",
     "Larkspur House, 40 Elm Avenue, Westbury, KF6 3EA", "B"),
    ("BLUEDR01", "Blue Door Bakery Café", "orders@bluedoorbakery.example",
     "12 Market Square, Kingsford, KF1 1MS", "A"),
    ("KINGSD01", "Kingsford Dental Practice", "practice.manager@kingsforddental.example",
     "3 Victoria Road, Kingsford, KF2 8VR", "A"),
    ("CEDARS01", "Cedars Day Nursery", "office@cedarsnursery.example",
     "The Cedars, 7 Park Road, Westbury, KF6 5PR", "A"),
]

# Band B prices: about 6% off list for 20 products bought by hotels, schools and care homes.
BAND_B_CODES = [
    "CLN-101", "CLN-102", "CLN-105", "CLN-106", "CLN-108", "CLN-110", "CLN-112", "CLN-115",
    "CLN-119", "CLN-120", "GLV-202", "GLV-203", "TOW-301", "TOW-302", "TOW-305", "TOW-310",
    "TOW-312", "BAG-401", "BAG-404", "CLO-611",
]

# How certain customers already phrase things, as if saved from earlier corrections.
# (account ref, how they write it, product code, unit hint)
ALIASES: list[tuple[str, str, str, str | None]] = [
    ("RIVERS01", "120 bottles of the antibac spray", "CLN-101", None),
    ("OLDMIL01", "3 boxes of the big black bags", "BAG-401", "BOX"),
    ("FITHUB01", "2 blue gloves medium", "GLV-202", "BOX"),
]

TRIAL_START = date(2026, 10, 12)


def ean13(seed: int) -> str:
    """A well-formed but invented EAN-13 barcode."""
    body = f"506999{seed:06d}"
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def band_b_price(code: str) -> Decimal:
    list_price = next(Decimal(p[5]) for p in PRODUCTS if p[0] == code)
    return (list_price * Decimal("0.94")).quantize(Decimal("0.01"))


def write_demo_files(out_dir: Path = SEED_DIR) -> dict[str, Path]:
    """Write the demo client's raw setup files. The same content every run."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "products": out_dir / PRODUCTS_FILE,
        "customers": out_dir / CUSTOMERS_FILE,
        "prices": out_dir / PRICES_FILE,
        "template": out_dir / TEMPLATE_FILE,
    }

    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(["Demo Cleaning Supplies product list"])
    ws.append([])
    ws.append(["Stock Code", "Description", "Sell Unit", "Pack Qty", "Inner Unit", "List Price", "Barcode",
               "Category", "VAT Code"])
    for i, (code, desc, unit, pack, inner, price, category, has_barcode) in enumerate(PRODUCTS, start=1):
        ws.append([code, desc, unit, pack, inner, float(price), ean13(i) if has_barcode else None, category, "S"])
    for col, width in zip("ABCDEFGHI", [12, 52, 10, 10, 11, 11, 16, 12, 9]):
        ws.column_dimensions[col].width = width
    fixed = datetime(2026, 10, 1, 9, 0, 0)
    wb.properties.creator = "Demo Cleaning Supplies"
    wb.properties.created = fixed
    wb.properties.modified = fixed
    wb.save(paths["products"])

    with paths["customers"].open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Account Ref", "Account Name", "Order Email", "Delivery Address", "Price Band"])
        writer.writerows(CUSTOMERS)

    with paths["prices"].open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Price Band", "Stock Code", "Price"])
        for code in BAND_B_CODES:
            writer.writerow(["B", code, f"{band_b_price(code):.2f}"])

    with paths["template"].open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["AccountRef", "CustOrderNo", "OrderDate", "StockCode", "Qty", "UnitPrice",
                         "DeliveryDate", "Notes"])
        writer.writerow(["SAMPLE01", "PO1234", "05/10/2026", "ABC-100", "2", "12.50", "07/10/2026", "Rear door"])
        writer.writerow(["SAMPLE01", "PO1234", "05/10/2026", "ABC-200", "1", "4.00", "07/10/2026", "Rear door"])

    products = {code: (desc, unit, price) for code, desc, unit, _pack, _inner, price, _cat, _bc in PRODUCTS}
    barcodes = {p[0]: ean13(i) for i, p in enumerate(PRODUCTS, start=1) if p[7]}
    customers = {account: {"account_code": account, "name": name, "price_band": band}
                 for account, name, _emails, _address, band in CUSTOMERS}
    band_prices = {("B", code): band_b_price(code) for code in BAND_B_CODES}
    paths["orders"], paths["answers"] = write_demo_orders(out_dir, products, customers, band_prices, barcodes,
                                                          DEMO_CONTACT_EMAIL)
    paths["staff_entries"] = out_dir / STAFF_FILE
    return paths


def _import_with_guess(session: Session, client: Client, path: Path, kind: str):
    table = read_table(path)
    mapping = guess_mapping(table.columns, kind).mapping
    importer = {
        "catalogue": importers.import_catalogue,
        "customers": importers.import_customers,
        "prices": importers.import_prices,
    }[kind]
    return importer(session, client, path, table, mapping)


def load_demo_client(session: Session, files: dict[str, Path]) -> Client:
    """Set up the demo client from its raw files, replacing any earlier copy."""
    delete_client(session, DEMO_ID)

    client = Client(
        id=DEMO_ID,
        name="Demo Cleaning Supplies",
        short_name="DemoCleaning",
        erp_system="Sage 50",
        mode=ClientMode.DEMO,
        contact_name="Operations manager",
        contact_email=DEMO_CONTACT_EMAIL,
        trial_start_date=TRIAL_START,
        trial_working_days=10,
        minutes_per_order=5,
        rules=dict(DEFAULT_RULES, min_order_value="25.00"),
        unit_synonyms=dict(DEFAULT_UNIT_SYNONYMS),
        setup_done=["details"],
    )
    session.add(client)
    session.commit()

    _import_with_guess(session, client, files["products"], MappingKind.CATALOGUE)
    _import_with_guess(session, client, files["customers"], MappingKind.CUSTOMERS)
    _import_with_guess(session, client, files["prices"], MappingKind.PRICES)
    importers.import_output_template(session, client, files["template"])
    importers.mark_setup_step(client, "rules")
    session.add(client)

    synonyms = client.unit_synonyms
    unit_words = {w for words in synonyms.values() for w in words}
    now = datetime.now(timezone.utc)
    for account, wording, code, unit_hint in ALIASES:
        customer = session.exec(
            select(Customer).where(Customer.client_id == DEMO_ID, Customer.account_code == account)
        ).one()
        session.add(Alias(
            client_id=DEMO_ID,
            customer_id=customer.id,
            phrase=normalise.alias_phrase(wording, unit_words=unit_words),
            product_code=code,
            unit_hint=unit_hint,
            created_at=now,
        ))
    session.commit()
    session.refresh(client)
    return client


def main() -> None:
    files = write_demo_files()
    with Session(get_engine()) as session:
        client = load_demo_client(session, files)
        print(f"Demo client ready: {client.name}. Files written to {SEED_DIR}.")


if __name__ == "__main__":
    main()
