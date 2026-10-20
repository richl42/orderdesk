from collections import Counter

from sqlmodel import func, select

from app.models import Alias, CatalogueItem, Client, Customer, OutputTemplate, PriceListEntry
from pipeline.tables import read_table
from seed.demo_data import DEMO_ID, load_demo_client, write_demo_files


def count(session, model):
    return session.exec(select(func.count()).select_from(model).where(model.client_id == DEMO_ID)).one()


def test_demo_client_counts(session, demo_client):
    assert 75 <= count(session, CatalogueItem) <= 85
    assert count(session, Customer) == 15
    assert count(session, PriceListEntry) == 20
    assert count(session, Alias) >= 3
    assert count(session, OutputTemplate) == 1
    assert demo_client.setup_done == ["details", "catalogue", "customers", "output", "rules"]


def test_demo_files_are_the_same_every_run(tmp_path):
    a = write_demo_files(tmp_path / "a")
    b = write_demo_files(tmp_path / "b")
    for key in ("customers", "prices", "template"):
        assert a[key].read_bytes() == b[key].read_bytes()
    assert read_table(a["products"]).rows == read_table(b["products"]).rows


def test_demo_catalogue_has_near_twins_and_unique_codes(session, demo_client):
    items = {i.product_code: i for i in session.exec(select(CatalogueItem)).all()}
    assert items["TOW-310"].sell_unit == "PACK" and items["TOW-310"].pack_size == 6
    assert items["TOW-311"].sell_unit == "EACH"
    assert items["CLN-101"].pack_size == 12 and items["CLN-101"].inner_unit == "bottle"
    assert all(item.barcode is None or len(item.barcode) == 13 for item in items.values())


def test_demo_customers_are_fictional_and_two_share_a_domain(session, demo_client):
    customers = session.exec(select(Customer)).all()
    emails = [e for c in customers for e in c.emails]
    assert emails and all(e.endswith(".example") for e in emails)
    domain_use = Counter(d for c in customers for d in c.email_domains)
    assert any(n == 2 for n in domain_use.values())
    assert sum(1 for c in customers if len(c.emails) == 2) >= 2


def test_demo_aliases_point_at_real_products_and_customers(session, demo_client):
    codes = set(session.exec(select(CatalogueItem.product_code)).all())
    for alias in session.exec(select(Alias)).all():
        assert alias.product_code in codes
        assert alias.customer_id is not None
    phrases = set(session.exec(select(Alias.phrase)).all())
    assert "antibac spray" in phrases


def test_band_b_prices_are_below_list(session, demo_client):
    list_prices = {i.product_code: i.price for i in session.exec(select(CatalogueItem)).all()}
    for entry in session.exec(select(PriceListEntry)).all():
        assert entry.price_band == "B"
        assert entry.price < list_prices[entry.product_code]


def test_seeding_twice_replaces_the_demo_client(session, demo_files, demo_client):
    load_demo_client(session, demo_files)
    assert count(session, CatalogueItem) == len(set(session.exec(select(CatalogueItem.product_code)).all()))
    assert session.exec(select(func.count()).select_from(Client)).one() == 1
    assert count(session, Alias) == 3


def test_demo_orders_cover_every_type_and_hard_case(demo_files):
    import json

    from pipeline.ingest import ingest_file

    orders = sorted(demo_files["orders"].iterdir())
    assert len(orders) == 20
    assert Counter(p.suffix for p in orders) == {".eml": 8, ".pdf": 5, ".xlsx": 4, ".csv": 3}

    answers = [json.loads(p.read_text()) for p in sorted(demo_files["answers"].iterdir())]
    assert [a["file"] for a in answers] == [p.name for p in orders]
    cases = {c for a in answers for c in a["hard_cases"]}
    for needed in ["Bottles that divide exactly into cases", "Bottles that do not divide into cases",
                   "Misspelt product (\"disinfectent spary\")", "Customer uses their own product codes",
                   "Manufacturer barcode instead of product code", "Product not in the catalogue",
                   "Near twins (blue roll single vs pack) with no unit", "Price written differs from price band",
                   "Same PO number sent twice", "No PO number", "\"Deliver Thursday\"",
                   "Sent after the cutoff for next day delivery", "Order below minimum value",
                   "Unknown sender, customer named in signature", "Completely unknown sender",
                   "\"Can you add 2 boxes to yesterday's order\"", "\"Where is my delivery?\"",
                   "A 40 line Excel order", "A simple, clean order", "A scanned PDF (image only)"]:
        assert needed in cases, needed

    statuses = Counter(a["expected_initial_status"] for a in answers)
    assert statuses["pass_on"] == 2 and statuses["ready"] >= 5
    assert any(len(a["lines"]) == 40 for a in answers)

    for path, answer in zip(orders, answers):
        text = ingest_file(path.name, path.read_bytes()).text
        for line in answer["lines"]:
            assert line["source_span"] in text, (path.name, line["source_span"])
            if line["product_code"]:
                assert line["unit_price"] is not None
        if answer["expected_export_rows"]:
            assert len(answer["expected_export_rows"]) == sum(1 for ln in answer["lines"] if ln["product_code"])
            assert all(len(row) == 8 for row in answer["expected_export_rows"])


def test_demo_order_answer_keys_use_real_customers_and_products(session, demo_client, demo_files):
    import json

    codes = set(session.exec(select(CatalogueItem.product_code)).all())
    customers = {c.account_code: c for c in session.exec(select(Customer)).all()}
    for path in demo_files["answers"].iterdir():
        answer = json.loads(path.read_text())
        if answer["customer"]:
            customer = customers[answer["customer"]["account_code"]]
            if answer["customer"]["match_method"] == "email":
                assert answer["sender_email"] in customer.emails, path.name
        else:
            assert not any(answer["sender_email"] in c.emails for c in customers.values())
        for line in answer["lines"]:
            assert line["product_code"] is None or line["product_code"] in codes
