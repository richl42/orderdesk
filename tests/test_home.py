from datetime import datetime, timedelta, timezone

from app.models import Client
from app.web.summaries import home_data, summarise_client
from tests.helpers import add_client, add_order


def test_demo_card_shows_product_and_customer_counts(logged_in, demo_client):
    page = logged_in.get("/").text
    assert "Demo Cleaning Supplies" in page
    assert "81 products · 15 customers" in page
    assert ">Demo<" in page
    assert "Open demo" in page
    assert "Setup: 5 of 5 steps done" in page


def test_empty_home_has_plain_empty_states(logged_in):
    page = logged_in.get("/").text
    assert "Nothing needs you right now" in page
    assert "No clients yet" in page


def test_demo_orders_are_left_out_of_the_totals(session, demo_client):
    add_client(session, "abc", "ABC Cleaning Supplies")
    add_order(session, "demo", "check")
    add_order(session, "abc", "problem")
    tiles = {t.label: t for t in home_data(session).tiles}
    assert tiles["Orders in today"].value == "1"
    assert tiles["Waiting for your review"].value == "1"
    assert tiles["Orders in today"].note == "Across 1 active client"


def test_needs_attention_lists_check_problem_and_pass_on_oldest_first(session):
    add_client(session, "abc", "ABC Cleaning Supplies")
    now = datetime.now(timezone.utc)
    add_order(session, "abc", "ready", received_at=now - timedelta(hours=3))
    add_order(session, "abc", "check", received_at=now - timedelta(minutes=5), po_number="2295",
              flags=[{"code": "price", "message": "Price differs from price list", "severity": "check"}])
    add_order(session, "abc", "problem", received_at=now - timedelta(hours=1), po_number="2291",
              flags=[{"code": "dup", "message": "Possible duplicate", "severity": "check"},
                     {"code": "line_unmatched", "message": "1 unknown product", "severity": "problem"}])
    add_order(session, "abc", "pass_on", received_at=now - timedelta(minutes=30), classification="chase",
              classification_reason="Asking where yesterday's delivery is", sender_email="chef@cafe.example")
    items = home_data(session).attention
    assert [i.order.status for i in items] == ["problem", "pass_on", "check"]
    assert items[0].issue == "1 unknown product"
    assert items[0].title == "PO 2291 from Unknown sender"
    assert items[1].issue.startswith("Delivery chase: Asking where")
    assert items[1].title == "Order from chef@cafe.example"


def test_client_still_setting_up(logged_in, session):
    add_client(session, "north", "Northside Plumbing Supply", setup_done=["details", "catalogue", "customers"])
    page = logged_in.get("/").text
    assert "Setting up" in page
    assert "Setup: 3 of 5 steps done (output format missing)" in page
    assert "Continue setup" in page


def test_trial_day_on_card(session, logged_in):
    today = datetime.now(timezone.utc).date()
    add_client(session, "abc", "ABC Cleaning Supplies", trial_start_date=today + timedelta(days=30))
    assert "Trial starts" in logged_in.get("/").text


def test_client_orders_page(logged_in, demo_client):
    response = logged_in.get("/clients/demo/orders")
    assert response.status_code == 200
    assert "Demo Cleaning Supplies" in response.text
    assert "SalesOrderImport_example.csv" in response.text


def test_unknown_client_is_not_found(logged_in):
    assert logged_in.get("/clients/nobody/orders").status_code == 404


def test_nav_pages_load(logged_in, demo_client):
    for path in ("/orders", "/trials", "/clients/new", "/clients/demo/trial"):
        assert logged_in.get(path).status_code == 200, path


def test_orders_dated_later_today_or_in_the_future_are_not_received_today(session, logged_in):
    add_client(session, "abc", "ABC Cleaning Supplies")
    now = datetime.now(timezone.utc)
    add_order(session, "abc", "check", received_at=now)
    add_order(session, "abc", "check", received_at=now + timedelta(days=2))
    add_order(session, "abc", "check", received_at=now - timedelta(days=2))
    assert summarise_client(session, session.get(Client, "abc"), now).orders_today == 1
    assert "1 received today" in logged_in.get("/clients/abc/orders").text


def test_ready_orders_say_they_only_need_approving(session):
    from app.web.summaries import order_issue

    add_client(session, "abc", "ABC Cleaning Supplies")
    assert order_issue(add_order(session, "abc", "ready")).startswith("Everything matched and checked")
