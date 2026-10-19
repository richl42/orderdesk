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
