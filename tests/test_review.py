"""Screen 4: reviewing, correcting and approving an order, and learning from every correction."""

import html
from datetime import date
from email.message import EmailMessage

import pytest
from sqlmodel import select

from app.models import Alias, CatalogueItem, Customer, Event, Order, OrderLine
from tests.helpers import add_client, order_answer

HX = {"HX-Request": "true"}


def eml(body, sender="Lee <manager@fithub-gyms.example>", date_header="Wed, 14 Oct 2026 09:50:00 +0100"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "Weekly order"
    msg["Date"] = date_header
    msg.set_content(body)
    return bytes(msg)


BLUE_ROLL = "Can I get:\n6 blue roll\n2 blue gloves medium\n\nThanks\nLee\nFitHub Gym Kingsford"
BLUE_ROLL_READ = order_answer([("6 blue roll", None, "blue roll", "6", None, None),
                               ("2 blue gloves medium", None, "blue gloves medium", "2", None, None)],
                              customer_name="FitHub Gym Kingsford")
BLUE_ROLL_CHOICE = {"lines": [{"line_no": 1, "product_code": "TOW-310", "reason": "Pack or single?", "ambiguous": True}]}


def upload(browser, fake_ai, session, body=BLUE_ROLL, answers=(BLUE_ROLL_READ, BLUE_ROLL_CHOICE), **kwargs):
    fake_ai.answers = list(answers)
    browser.post("/clients/demo/orders/upload", files=[("files", ("o.eml", eml(body, **kwargs), "message/rfc822"))],
                 headers=HX)
    return session.exec(select(Order).where(Order.client_id == "demo").order_by(Order.id.desc())).first()


def lines(session, order):
    session.expire_all()
    return session.exec(select(OrderLine).where(OrderLine.order_id == order.id).order_by(OrderLine.line_no)).all()


class Page:
    """A response whose text is unescaped, so messages can be looked for as people read them."""

    def __init__(self, response):
        self.status_code, self.headers = response.status_code, response.headers
        self.text = html.unescape(response.text)


def post(browser, order, path, **data):
    return Page(browser.post(f"/clients/demo/orders/{order.id}/{path}", data=data, headers=HX))


@pytest.fixture
def blue_roll(logged_in, session, demo_client, fake_ai):
    order = upload(logged_in, fake_ai, session)
    assert order.status == "check"
    return order


def test_review_page_shows_both_sides_checks_and_export_preview(logged_in, session, blue_roll):
    page = logged_in.get(f"/clients/demo/orders/{blue_roll.id}").text
    assert "FitHub Gym Kingsford" in page and "Matched by sender email" in page
    assert '<mark class="hl-check" title="Line 1">' in page and '<mark class="hl-ready" title="Line 2">' in page
    assert "Remembered phrasing" in page  # line 2 came from a saved phrase
    assert "Pick the right product" not in page or "TOW-311" in page
    assert "Could be more than one product" in page
    assert "Order 1 of 1 waiting" in page
    assert "AccountRef,CustOrderNo,OrderDate,StockCode,Qty,UnitPrice,DeliveryDate,Notes" in page
    assert "FITHUB01,,14/10/2026,TOW-310,6,14.40" in page
    session.refresh(blue_roll)
    assert blue_roll.review_started_at is not None


def test_fixing_a_line_makes_it_ready_without_changing_the_initial_status(logged_in, session, blue_roll):
    one = lines(session, blue_roll)[0]
    response = post(logged_in, blue_roll, f"lines/{one.id}", product_code="TOW-311", qty="6", unit_choice="sell",
                    remember="on")
    assert response.status_code == 200
    one = lines(session, blue_roll)[0]
    assert (one.product_code, one.match_method, one.qty, one.unit, one.status) == ("TOW-311", "operator", 6, "EACH",
                                                                                  "ready")
    assert one.corrected_by_operator and one.flags == [] and one.remember
    session.refresh(blue_roll)
    assert blue_roll.status == "ready" and blue_roll.initial_status == "check"
    assert blue_roll.corrections_count == 1
    assert "Chosen by you" in response.text


def test_inner_units_are_converted_by_code(logged_in, session, blue_roll):
    one = lines(session, blue_roll)[0]
    post(logged_in, blue_roll, f"lines/{one.id}", product_code="TOW-310", qty="12", unit_choice="inner")
    one = lines(session, blue_roll)[0]
    assert (one.qty, one.unit, one.conversion_note) == (2, "PACK", "12 rolls / 6 = 2 packs")
    response = post(logged_in, blue_roll, f"lines/{one.id}", product_code="TOW-310", qty="10", unit_choice="inner")
    assert "10 rolls is not a whole number of packs of 6" in response.text
    assert lines(session, blue_roll)[0].qty == 2  # unchanged


def test_unknown_product_codes_are_refused(logged_in, session, blue_roll):
    one = lines(session, blue_roll)[0]
    response = post(logged_in, blue_roll, f"lines/{one.id}", product_code="NOPE-1", qty="1")
    assert "Choose a product from the catalogue." in response.text


def test_approve_saves_times_and_learns_the_phrasing(logged_in, session, blue_roll, fake_ai):
    one = lines(session, blue_roll)[0]
    post(logged_in, blue_roll, f"lines/{one.id}", product_code="TOW-311", qty="6", remember="on")
    response = post(logged_in, blue_roll, "approve", seconds="41")
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/clients/demo/orders?approved={blue_roll.id}"
    session.refresh(blue_roll)
    assert blue_roll.status == "approved" and blue_roll.approved_at is not None
    assert blue_roll.review_seconds == 41 and blue_roll.corrections_count == 1

    fithub = session.exec(select(Customer).where(Customer.account_code == "FITHUB01")).one()
    alias = session.exec(select(Alias).where(Alias.customer_id == fithub.id, Alias.phrase == "blue roll")).one()
    assert (alias.product_code, alias.unit_hint, alias.created_from_order_id) == ("TOW-311", "EACH", blue_roll.id)
    assert "approval" in [e.type for e in session.exec(select(Event).where(Event.order_id == blue_roll.id)).all()]

    # The same customer sends the same thing again: matched by the remembered phrase, no AI call 2.
    again = upload(logged_in, fake_ai, session, answers=(BLUE_ROLL_READ,))
    first = lines(session, again)[0]
    assert (first.product_code, first.match_method, first.qty, first.unit) == ("TOW-311", "alias", 6, "EACH")
    page = logged_in.get(f"/clients/demo/orders/{again.id}").text
    assert page.count("Remembered phrasing") >= 2
    assert again.status == "check"  # the identical text is flagged as a possible duplicate
    assert any(f["code"] == "duplicate" for f in again.flags)


def test_untick_remember_saves_no_phrase(logged_in, session, blue_roll):
    one = lines(session, blue_roll)[0]
    post(logged_in, blue_roll, f"lines/{one.id}", product_code="TOW-311", qty="6")
    post(logged_in, blue_roll, "approve")
    assert session.exec(select(Alias).where(Alias.phrase == "blue roll")).first() is None


def test_approve_is_refused_while_a_line_is_a_problem(logged_in, session, demo_client, fake_ai):
    order = upload(logged_in, fake_ai, session, body="Please send 6 yoga mats\nLee, FitHub Gym Kingsford",
                   answers=(order_answer([("6 yoga mats", None, "yoga mats", "6", None, None)]),
                            {"lines": [{"line_no": 1, "product_code": None, "reason": "None", "ambiguous": False}]}))
    assert order.status == "problem"
    page = logged_in.get(f"/clients/demo/orders/{order.id}").text
    assert 'id="approve" aria-keyshortcuts="A" disabled' in page and "Fix line 1 first." in page
    response = post(logged_in, order, "approve")
    assert "Fix line 1 first." in response.text
    session.refresh(order)
    assert order.status == "problem" and order.approved_at is None


def test_choosing_the_customer(logged_in, session, demo_client, fake_ai):
    order = upload(logged_in, fake_ai, session, sender="office@unknown.example")
    assert order.customer_match_method == "name" and order.status == "check"
    larkspur = session.exec(select(Customer).where(Customer.account_code == "LARKSP01")).one()
    post(logged_in, order, "customer", customer_id=str(larkspur.id))
    session.refresh(order)
    assert (order.customer_id, order.customer_match_method) == (larkspur.id, "operator")
    assert not any(f["code"] in ("unknown_customer", "customer_by_name") for f in order.flags)
    post(logged_in, order, "customer", customer_id="")
    session.refresh(order)
    assert order.customer_id is None and order.status == "problem"


def test_another_clients_customer_cannot_be_chosen(logged_in, session, blue_roll):
    add_client(session, "abc", "ABC Cleaning Supplies")
    other = Customer(client_id="abc", account_code="X", name="Elsewhere")
    session.add(other)
    session.commit()
    response = post(logged_in, blue_roll, "customer", customer_id=str(other.id))
    assert "not one of this client's customers" in response.text
    session.refresh(blue_roll)
    assert blue_roll.customer_match_method == "email"


def test_editing_the_header_reruns_the_checks(logged_in, session, blue_roll):
    post(logged_in, blue_roll, "header", field="requested_delivery_date", value="2026-10-01")
    session.refresh(blue_roll)
    assert blue_roll.status == "problem" and any(f["code"] == "delivery_past" for f in blue_roll.flags)
    post(logged_in, blue_roll, "header", field="requested_delivery_date", value="2026-10-20")
    session.refresh(blue_roll)
    assert blue_roll.requested_delivery_date == date(2026, 10, 20)
    assert not any(f["code"] == "delivery_past" for f in blue_roll.flags)
    post(logged_in, blue_roll, "header", field="po_number", value="FH-77")
    post(logged_in, blue_roll, "header", field="delivery_notes", value="Side door")
    session.refresh(blue_roll)
    assert (blue_roll.po_number, blue_roll.delivery_notes, blue_roll.corrections_count) == ("FH-77", "Side door", 4)
    response = post(logged_in, blue_roll, "header", field="status", value="approved")
    assert "can't be changed here" in response.text


def test_add_and_delete_lines(logged_in, session, blue_roll):
    post(logged_in, blue_roll, "lines/add")
    added = lines(session, blue_roll)[-1]
    assert added.line_no == 3 and added.status == "problem" and added.source_span is None
    post(logged_in, blue_roll, f"lines/{added.id}", product_code="CLN-101", qty="2")
    assert lines(session, blue_roll)[-1].product_code == "CLN-101"
    post(logged_in, blue_roll, f"lines/{added.id}/delete")
    assert len(lines(session, blue_roll)) == 2


def test_a_scanned_order_can_be_typed_in_and_approved(logged_in, session, demo_client, demo_files, fake_ai):
    path = demo_files["orders"] / "10_cedars_scanned.pdf"
    logged_in.post("/clients/demo/orders/upload", files=[("files", (path.name, path.read_bytes(), "application/pdf"))],
                   data={"sender_email": "office@cedarsnursery.example"}, headers=HX)
    order = session.exec(select(Order)).one()
    assert order.status == "problem" and order.customer_match_method == "email"
    assert "Add a line" in logged_in.get(f"/clients/demo/orders/{order.id}").text
    post(logged_in, order, "lines/add")
    line = lines(session, order)[0]
    post(logged_in, order, f"lines/{line.id}", product_code="TOW-301", qty="2")
    session.refresh(order)
    assert order.status == "ready", order.flags
    assert post(logged_in, order, "approve").status_code == 204


def test_not_an_order_and_back(logged_in, session, blue_roll):
    post(logged_in, blue_roll, "pass-on")
    session.refresh(blue_roll)
    assert blue_roll.status == "pass_on" and blue_roll.initial_status == "check"
    assert "It is an order" in logged_in.get(f"/clients/demo/orders/{blue_roll.id}").text
    post(logged_in, blue_roll, "back-to-review")
    session.refresh(blue_roll)
    assert blue_roll.status == "check"


def test_undo_approval(logged_in, session, blue_roll):
    one = lines(session, blue_roll)[0]
    post(logged_in, blue_roll, f"lines/{one.id}", product_code="TOW-311", qty="6")
    post(logged_in, blue_roll, "approve")
    response = post(logged_in, blue_roll, f"lines/{one.id}", product_code="TOW-310", qty="1")
    assert "Undo the approval to change it." in response.text
    post(logged_in, blue_roll, "undo-approval")
    session.refresh(blue_roll)
    assert blue_roll.status == "ready" and blue_roll.approved_at is None


def test_ask_client_contact_has_a_ready_written_question(logged_in, session, blue_roll):
    page = logged_in.get(f"/clients/demo/orders/{blue_roll.id}").text
    assert "To: operations@demo-cleaning.example" in page
    assert "Subject: Question about an order with no PO number from FitHub Gym Kingsford" in page
    assert "Line 1 ('6 blue roll'): Could be more than one product" in html.unescape(page)


def test_next_order_goes_through_the_queue_oldest_first(logged_in, session, demo_client, fake_ai):
    first = upload(logged_in, fake_ai, session, date_header="Mon, 12 Oct 2026 09:00:00 +0100")
    second = upload(logged_in, fake_ai, session, body=BLUE_ROLL + "\n", date_header="Tue, 13 Oct 2026 09:00:00 +0100")
    start = logged_in.get("/clients/demo/review", follow_redirects=False)
    assert start.headers["location"] == f"/clients/demo/orders/{first.id}"
    assert f'href="/clients/demo/orders/{second.id}" id="next-order"' in logged_in.get(
        f"/clients/demo/orders/{first.id}").text
    one = lines(session, first)[0]
    post(logged_in, first, f"lines/{one.id}", product_code="TOW-311", qty="6")
    assert post(logged_in, first, "approve").headers["HX-Redirect"] == f"/clients/demo/orders/{second.id}?approved=1"


def test_catalogue_search_only_finds_this_clients_products(logged_in, session, blue_roll):
    add_client(session, "abc", "ABC Cleaning Supplies")
    session.add(CatalogueItem(client_id="abc", product_code="ABC-ROLL", description="Blue roll deluxe"))
    session.commit()
    found = logged_in.get("/clients/demo/catalogue/search?q=blue+roll&line=1").text
    assert "TOW-310" in found and "ABC-ROLL" not in found
    assert "CLN-101" in logged_in.get("/clients/demo/catalogue/search?q=cln-10").text
    assert "Nothing in the catalogue" in logged_in.get("/clients/demo/catalogue/search?q=zzzzzz").text


def test_lines_and_orders_of_others_cannot_be_reached(logged_in, session, blue_roll, fake_ai):
    other = upload(logged_in, fake_ai, session, body=BLUE_ROLL + "\nsecond copy")
    foreign_line = lines(session, other)[0]
    response = post(logged_in, blue_roll, f"lines/{foreign_line.id}", product_code="TOW-311", qty="6")
    assert "That line is not on this order." in response.text
    add_client(session, "abc", "ABC Cleaning Supplies")
    assert logged_in.get(f"/clients/abc/orders/{blue_roll.id}").status_code == 404
    assert logged_in.post(f"/clients/abc/orders/{blue_roll.id}/approve", headers=HX).status_code == 404


def test_catalogue_search_is_tight(logged_in, session, demo_client):
    found = html.unescape(logged_in.get("/clients/demo/catalogue/search?q=towel").text)
    assert "TOW-303" in found and "CLN-110" not in found and "CLO-631" not in found
    assert "TOW-303" in logged_in.get("/clients/demo/catalogue/search?q=interfod+towels").text  # close spelling
