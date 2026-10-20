"""Milestone 8: everything the operator can do without the terminal, figures for the operator only,
filters and counts, and plain error messages."""

import html
import io
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import client_files_dir
from app.models import Alias, CatalogueItem, Client, Customer, Event, Order, OrderLine
from app.web import summaries
from app.web.summaries import delete_reminder, home_data
from tests.conftest import TEST_PASSWORD
from tests.helpers import add_client, add_order

# ---------------------------------------------------------------- delete a client


def test_delete_client_removes_every_row_and_file_and_nothing_of_another_client(logged_in, session):
    for cid, name in (("abc", "ABC Cleaning Supplies"), ("north", "Northside Plumbing Supply")):
        add_client(session, cid, name)
        session.add(CatalogueItem(client_id=cid, product_code="X-1", description="Thing", sell_unit="EACH"))
        session.add(Customer(client_id=cid, account_code="C1", name="Café"))
        order = add_order(session, cid, "check")
        session.add(OrderLine(client_id=cid, order_id=order.id, line_no=1, raw_text="2 x thing"))
        session.add(Event(client_id=cid, order_id=order.id, type="read"))
        session.commit()
        (client_files_dir(cid) / "orders").mkdir()
        (client_files_dir(cid) / "orders" / "1.eml").write_text("x")

    wrong = logged_in.post("/clients/abc/delete", data={"confirm": "ABC"}, follow_redirects=True)
    assert "Nothing was deleted. Type the client" in html.unescape(wrong.text)
    session.expire_all()
    assert session.get(Client, "abc") is not None

    done = logged_in.post("/clients/abc/delete", data={"confirm": "  abc cleaning  supplies "}, follow_redirects=True)
    assert done.url.path == "/"
    assert "ABC Cleaning Supplies and all of its data and files have been deleted." in done.text
    session.expire_all()
    assert session.get(Client, "abc") is None
    for model in (CatalogueItem, Customer, Order, OrderLine, Event):
        assert session.exec(select(model).where(model.client_id == "abc")).first() is None
        assert session.exec(select(model).where(model.client_id == "north")).first() is not None, model
    assert not (get_settings().clients_dir / "abc").exists()
    assert (get_settings().clients_dir / "north" / "orders" / "1.eml").exists()
    assert "Northside Plumbing Supply" in logged_in.get("/").text
    assert logged_in.get("/clients/abc/orders").status_code == 404


def test_settings_page_has_the_delete_section(logged_in, session):
    add_client(session, "abc", "ABC Cleaning Supplies")
    page = html.unescape(logged_in.get("/clients/abc/setup").text)
    assert "Delete this client" in page and "Type the client's name" in page
    assert "Start the demo again" not in page  # only on the demo client


# ---------------------------------------------------------------- the demo, without the terminal


@pytest.fixture
def seed_dir(tmp_path, monkeypatch):
    from seed import demo_data

    folder = tmp_path / "seed_written_by_the_app"
    monkeypatch.setattr(demo_data, "SEED_DIR", folder)
    return folder


def test_start_the_demo_again_needs_demo_typed_then_rebuilds_it(logged_in, session, demo_client, seed_dir):
    add_order(session, "demo", "check")
    session.add(Alias(client_id="demo", phrase="extra phrase", product_code="CLN-101",
                      created_at=datetime.now(timezone.utc)))
    session.commit()
    aliases_before = len(session.exec(select(Alias).where(Alias.client_id == "demo")).all())

    refused = logged_in.post("/demo/restart", data={"confirm": "yes", "back": "/"}, follow_redirects=True)
    assert "Nothing was changed. Type DEMO" in refused.text
    session.expire_all()
    assert session.exec(select(Order).where(Order.client_id == "demo")).first() is not None

    done = logged_in.post("/demo/restart", data={"confirm": "demo"}, follow_redirects=True)
    assert done.url.path == "/clients/demo/setup"
    assert "has been set up again from its files, with no orders" in done.text
    session.expire_all()
    assert session.exec(select(Order).where(Order.client_id == "demo")).first() is None
    assert len(session.exec(select(Alias).where(Alias.client_id == "demo")).all()) == aliases_before - 1
    assert len(session.exec(select(CatalogueItem).where(CatalogueItem.client_id == "demo")).all()) == 81
    assert (seed_dir / "staff_entries_demo.csv").exists() and len(list((seed_dir / "orders").iterdir())) == 20


def test_the_demo_can_be_set_up_from_home_when_there_is_none(logged_in, session, seed_dir):
    page = logged_in.get("/").text
    assert "Set up the demo client" in page
    logged_in.post("/demo/restart", data={})
    session.expire_all()
    assert session.get(Client, "demo").mode == "demo"
    home = logged_in.get("/").text
    assert "Set up the demo client" not in home and "Start the demo again" in home


def test_load_the_20_test_orders_after_starting_again(logged_in, session, seed_dir, fake_ai):
    logged_in.post("/demo/restart", data={})
    response = logged_in.post("/clients/demo/orders/demo", headers={"HX-Request": "true"})
    assert "20 orders added. Reading now." in response.text
    session.expire_all()
    assert len(session.exec(select(Order).where(Order.client_id == "demo")).all()) == 20


def test_any_demo_mode_client_can_load_the_test_orders(logged_in, session, seed_dir, fake_ai):
    from seed import demo_data

    demo_data.write_demo_files(seed_dir)
    add_client(session, "pitch", "Pitch Supplies", mode="demo")
    assert "Load the 20 test orders" in logged_in.get("/clients/pitch/orders").text
    response = logged_in.post("/clients/pitch/orders/demo", headers={"HX-Request": "true"})
    assert "20 orders added" in response.text
    add_client(session, "abc", "ABC Cleaning Supplies")
    assert "Load the 20 test orders" not in logged_in.get("/clients/abc/orders").text
    assert logged_in.post("/clients/abc/orders/demo").status_code == 404


# ---------------------------------------------------------------- backup


def test_backup_downloads_a_zip_of_the_data_folder(logged_in, demo_client):
    assert "Download a backup" in logged_in.get("/").text
    response = logged_in.post("/backup")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert "orderdesk-backup-" in response.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "orderdesk.db" in names
    assert any(n.startswith("clients/demo/") for n in names)
    kept = list(get_settings().backups_dir.glob("orderdesk-backup-*.zip"))
    assert len(kept) == 1
    logged_in.post("/backup")  # a second one straight after gets its own name
    assert len(list(get_settings().backups_dir.glob("orderdesk-backup-*.zip"))) == 2


def test_backup_needs_logging_in(browser):
    response = browser.post("/backup", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


# ---------------------------------------------------------------- trial ended: delete within 30 days


def test_delete_reminder_after_the_trial_ends():
    client = Client(id="abc", name="ABC", mode="trial", trial_start_date=date(2026, 10, 12), trial_working_days=10)
    assert delete_reminder(client, date(2026, 10, 23)) is None  # last day of the trial
    kind, text = delete_reminder(client, date(2026, 10, 26))
    assert kind == "check"
    assert text == "Trial ended Fri 23 Oct 2026: delete their data by Sun 22 Nov 2026 unless they continue."
    kind, text = delete_reminder(client, date(2026, 11, 23))
    assert kind == "problem" and "should have been deleted by Sun 22 Nov 2026" in text
    client.mode = "live"
    assert delete_reminder(client, date(2026, 11, 23)) is None


def test_reminder_shows_on_home_and_trials(logged_in, session):
    add_client(session, "abc", "ABC Cleaning Supplies", trial_start_date=date(2026, 1, 5), trial_working_days=10)
    expected = "Trial ended Fri 16 Jan 2026: their data should have been deleted by Sun 15 Feb 2026"
    assert expected in html.unescape(logged_in.get("/").text)
    assert expected in html.unescape(logged_in.get("/trials").text)


# ---------------------------------------------------------------- Needs your attention: filters and counts


def test_attention_filters_have_counts_and_show_how_long_orders_waited(logged_in, session):
    add_client(session, "abc", "ABC Cleaning Supplies")
    now = datetime.now(timezone.utc)
    add_order(session, "abc", "check", received_at=now - timedelta(hours=3))
    add_order(session, "abc", "check", received_at=now - timedelta(minutes=20))
    add_order(session, "abc", "problem", received_at=now - timedelta(days=2))
    add_order(session, "abc", "pass_on", received_at=now - timedelta(hours=1))
    add_order(session, "abc", "ready")

    data = home_data(session)
    assert data.attention_counts == {"all": 4, "check": 2, "problem": 1, "pass_on": 1}
    assert [i.waiting for i in data.attention] == ["Waiting 2 days", "Waiting 3 hours", "Waiting 1 hour",
                                                   "Waiting 20 minutes"]
    checks = home_data(session, attention_filter="check").attention
    assert [i.order.status for i in checks] == ["check", "check"]

    page = html.unescape(logged_in.get("/?attention=problem").text)
    assert 'href="/?attention=check#attention"' in page
    assert "Check <span class=\"count\">2</span>" in page
    assert page.count('class="attention-row"') == 1 and "Waiting 2 days" in page
    empty = home_data(session, attention_filter="nonsense")
    assert empty.attention_filter == "all" and len(empty.attention) == 4


def test_attention_says_when_there_are_more_than_it_lists(session, monkeypatch):
    add_client(session, "abc", "ABC Cleaning Supplies")
    for _ in range(4):
        add_order(session, "abc", "check")
    monkeypatch.setattr(summaries, "ATTENTION_LIMIT", 3)
    data = home_data(session)
    assert len(data.attention) == 3 and data.attention_more == 1


def test_filter_with_nothing_offers_to_show_all(logged_in, session):
    add_client(session, "abc", "ABC Cleaning Supplies")
    add_order(session, "abc", "check")
    assert "Nothing with this status. Show all 1" in logged_in.get("/?attention=pass_on").text.replace(
        '<a href="/?attention=all#attention">', "").replace("</a>", "")


def test_queue_has_an_exported_filter(logged_in, session):
    add_client(session, "abc", "ABC Cleaning Supplies")
    add_order(session, "abc", "exported")
    add_order(session, "abc", "approved")
    page = logged_in.get("/clients/abc/orders?status=exported").text
    assert 'Exported <span class="count">1</span>' in page
    assert page.count('<tr id="order-') == 1


# ---------------------------------------------------------------- cost and time, for the operator only


def test_queue_shows_tokens_and_a_note_when_ai_prices_are_not_set(logged_in, session):
    add_client(session, "abc", "ABC Cleaning Supplies")
    add_order(session, "abc", "check", ai_input_tokens=3000, ai_output_tokens=400, processing_ms=6200)
    page = logged_in.get("/clients/abc/orders").text
    assert "6.2s · 3,400 tokens" in page
    assert "Add AI prices to .env to see costs" in page
    home = logged_in.get("/").text
    assert "Per order: AI 3,400 tokens · read in 6.2s" in home
    assert "Add AI prices to .env to see costs" in home


def test_queue_card_and_trial_show_ai_cost_when_prices_are_set(logged_in, session, monkeypatch):
    monkeypatch.setenv("AI_PRICE_INPUT_PER_MTOK", "2.40")
    monkeypatch.setenv("AI_PRICE_OUTPUT_PER_MTOK", "12.00")
    get_settings.cache_clear()
    add_client(session, "abc", "ABC Cleaning Supplies")
    add_order(session, "abc", "check", ai_input_tokens=3000, ai_output_tokens=400, processing_ms=6200,
              ai_cost_gbp=Decimal("0.0120"))
    add_order(session, "abc", "ready", ai_input_tokens=1000, ai_output_tokens=200, processing_ms=3800,
              ai_cost_gbp=Decimal("0.0050"))
    page = logged_in.get("/clients/abc/orders").text
    assert "6.2s · 1.2p" in page and "3.8s · 0.5p" in page
    assert "Add AI prices to .env" not in page
    assert "Per order: AI 0.9p · read in 5.0s" in logged_in.get("/").text
    trial = html.unescape(logged_in.get("/clients/abc/trial").text)
    assert "Shadow trial results" in trial
    report = logged_in.get("/clients/abc/trial/report").text
    assert "0.9p" not in report and "AI" not in report.replace("MAIN", "")


def test_trial_details_for_you_has_average_cost_and_time(logged_in, session, demo_client, demo_files):
    from tests.test_exports import approved_order
    from tests.test_trial import upload_staff_file

    approved_order(session, po="2291")
    order = session.exec(select(Order).where(Order.client_id == "demo")).first()
    order.processing_ms, order.ai_input_tokens, order.ai_output_tokens = 4000, 1500, 300
    order.processing_state = "done"
    session.add(order)
    session.commit()
    upload_staff_file(logged_in, demo_files["staff_entries"])
    page = re.sub(r"<[^>]+>", "", html.unescape(logged_in.get("/clients/demo/trial").text))
    assert "Average AI cost: 1,800 tokens per order. Add AI prices to .env to see costs." in " ".join(page.split())
    assert "Average processing time: 4.0 seconds per order." in " ".join(page.split())
    assert "Average AI cost" not in logged_in.get("/clients/demo/trial/report").text


# ---------------------------------------------------------------- Trials page


def test_trials_page_shows_each_clients_trial(logged_in, session, monkeypatch):
    from app.web import pages

    monkeypatch.setattr(pages, "london_now", lambda: datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
    add_client(session, "abc", "ABC Cleaning Supplies", trial_start_date=date(2026, 10, 12), trial_working_days=10)
    add_client(session, "live", "Live Wholesale", mode="live")
    page = html.unescape(logged_in.get("/trials").text)
    assert "ABC Cleaning Supplies" in page and "Day 3 of 10" in page
    assert "Not loaded yet" in page
    assert "Live Wholesale" in page and "No trial dates set" in page


def test_trials_page_with_staff_entries_compared(logged_in, session, demo_client, demo_files):
    from tests.test_exports import approved_order
    from tests.test_trial import upload_staff_file

    approved_order(session, po="2291")
    upload_staff_file(logged_in, demo_files["staff_entries"])
    page = html.unescape(logged_in.get("/trials").text)
    assert "Loaded " in page
    assert "Demo Cleaning Supplies" in page


# ---------------------------------------------------------------- plain error messages


@pytest.fixture
def quiet_browser(app):
    """Logged in, and server errors come back as the error page instead of failing the test."""
    browser = TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False)
    browser.post("/login", data={"password": TEST_PASSWORD})
    return browser


def test_unexpected_error_shows_a_plain_page_and_keeps_the_details(quiet_browser, session, monkeypatch):
    from app.web import orders

    add_client(session, "abc", "ABC Cleaning Supplies")

    def broken(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(orders, "queue_data", broken)
    response = quiet_browser.get("/clients/abc/orders")
    assert response.status_code == 500
    assert "Something went wrong" in response.text and "Go back and try again" in response.text
    assert "secret internal detail" not in response.text and "Traceback" not in response.text
    log = (get_settings().data_dir / "logs" / "errors.log").read_text()
    assert "secret internal detail" in log and "/clients/abc/orders" in log
    session.expire_all()
    event = session.exec(select(Event).where(Event.client_id == "abc", Event.type == "error")).one()
    assert event.details["error"] == "RuntimeError: secret internal detail"


def test_error_outside_a_client_is_logged_to_the_file_only(quiet_browser, session, monkeypatch):
    from app.web import pages

    monkeypatch.setattr(pages, "home_data", lambda *a, **k: 1 / 0)
    response = quiet_browser.get("/")
    assert response.status_code == 500 and "Something went wrong" in response.text
    assert "ZeroDivisionError" in (get_settings().data_dir / "logs" / "errors.log").read_text()
    assert session.exec(select(Event).where(Event.type == "error")).first() is None


def test_every_page_has_the_notice_for_failed_updates(logged_in, demo_client):
    page = logged_in.get("/clients/demo/orders").text
    assert 'id="request-error"' in page and "/static/js/app.js" in page
    script = logged_in.get("/static/js/app.js").text
    assert "htmx:responseError" in script and "Nothing was saved" in script


def test_empty_states_on_a_new_client(logged_in, session):
    add_client(session, "abc", "ABC Cleaning Supplies")
    assert "No orders yet." in logged_in.get("/clients/abc/orders").text
    assert "Upload their file" in logged_in.get("/clients/abc/trial").text
    exports = logged_in.get("/clients/abc/exports").text
    assert "No exports yet" in exports

