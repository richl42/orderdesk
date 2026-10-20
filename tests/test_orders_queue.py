"""Screen 3: uploading and pasting orders, reading them, and the orders table."""

import json
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlmodel import select

from app import ai_client
from app.models import Event, Order, OrderLine
from app.orders import service
from pipeline.ai import AIUnavailable
from tests.helpers import add_client, order_answer


def eml(body: str, sender="Maria <purchasing@riverside-hotel.example>", date="Mon, 12 Oct 2026 09:14:00 +0100",
        subject="Order") -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = date
    msg.set_content(body)
    return bytes(msg)


BODY = "Please send for Thursday:\n120 bottles of the antibac spray\n\nPO 2291\nMaria, Riverside Hotel"
ANSWER = order_answer([("120 bottles of the antibac spray", None, "antibac spray", "120", "bottles", None)],
                      customer_name="Riverside Hotel", po_number="2291", delivery_date_written="Thursday")


def upload(browser, client_id, files, **fields):
    return browser.post(f"/clients/{client_id}/orders/upload", files=[("files", f) for f in files],
                        data=fields, headers={"HX-Request": "true"})


def test_upload_an_email_and_read_it(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [ANSWER]
    response = upload(logged_in, "demo", [("order.eml", eml(BODY), "message/rfc822")])
    assert response.status_code == 200
    assert "1 order added" in response.text

    order = session.exec(select(Order).where(Order.client_id == "demo")).one()
    assert order.source_type == "eml" and order.original_filename == "order.eml"
    assert order.sender_email == "purchasing@riverside-hotel.example"
    assert order.received_at == datetime(2026, 10, 12, 8, 14, tzinfo=timezone.utc)  # the email's own date
    assert order.processing_state == "read" and order.status is None and order.initial_status is None
    assert order.po_number == "2291" and order.customer_name_raw == "Riverside Hotel"
    assert str(order.requested_delivery_date) == "2026-10-15"
    assert order.content_hash and order.raw_text.startswith("Please send")
    assert order.ai_input_tokens == 1000 and order.ai_output_tokens == 200
    assert order.processing_ms is not None

    lines = session.exec(select(OrderLine).where(OrderLine.order_id == order.id)).all()
    assert [(ln.source_span, ln.requested_qty, ln.requested_unit) for ln in lines] == \
        [("120 bottles of the antibac spray", "120", "bottles")]
    assert lines[0].client_id == "demo"

    events = [e.type for e in session.exec(select(Event).where(Event.order_id == order.id)).all()]
    assert events == ["upload", "ai_call", "order_read"]

    page = logged_in.get("/clients/demo/orders")
    assert "Riverside Hotel" in page.text and "Lines read, matching comes next" in page.text
    assert "2291" in page.text


def test_the_original_file_is_kept_as_received(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [ANSWER]
    data = eml(BODY)
    upload(logged_in, "demo", [("order.eml", data, "message/rfc822")])
    order = session.exec(select(Order)).one()
    assert "/clients/demo/orders/" in order.stored_path
    original = logged_in.get(f"/clients/demo/orders/{order.id}/original")
    assert original.status_code == 200 and original.content == data


def test_several_files_at_once_with_sender_and_time_for_non_email_files(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [order_answer([("CLN-101", "CLN-101", None, "2", None, None)])]
    files = [("a.csv", b"Code,Qty\nCLN-101,2\n", "text/csv"), ("b.txt", b"CLN-101 2", "text/plain")]
    response = upload(logged_in, "demo", files, sender_email="Orders@FitHub-gyms.example",
                      received_at="2026-10-14T09:50")
    assert "2 orders added" in response.text
    orders = session.exec(select(Order).order_by(Order.id)).all()
    assert [o.source_type for o in orders] == ["csv", "txt"]
    assert all(o.sender_email == "orders@fithub-gyms.example" for o in orders)
    assert all(o.received_at == datetime(2026, 10, 14, 8, 50, tzinfo=timezone.utc) for o in orders)


def test_unsupported_files_are_refused_in_plain_words_and_others_still_go_in(logged_in, session, demo_client,
                                                                             fake_ai):
    fake_ai.answers = [ANSWER]
    response = upload(logged_in, "demo", [("photo.jpg", b"jpeg", "image/jpeg"),
                                          ("order.eml", eml(BODY), "message/rfc822")])
    assert "photo.jpg: Photos of orders are not supported yet." in response.text
    assert "1 order added" in response.text
    assert len(session.exec(select(Order)).all()) == 1


def test_bad_sender_or_time_is_explained(logged_in, demo_client, fake_ai):
    response = upload(logged_in, "demo", [("a.txt", b"x", "text/plain")], sender_email="not an email")
    assert "one email address" in response.text
    response = upload(logged_in, "demo", [("a.txt", b"x", "text/plain")], received_at="yesterday")
    assert "received time was not understood" in response.text


def test_paste_an_email(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [ANSWER]
    response = logged_in.post("/clients/demo/orders/paste", data={
        "text": BODY, "sender_email": "purchasing@riverside-hotel.example", "received_at": "2026-10-12T09:14",
    }, headers={"HX-Request": "true"})
    assert "Pasted email added" in response.text
    order = session.exec(select(Order)).one()
    assert order.source_type == "paste" and order.processing_state == "read"
    assert order.received_at == datetime(2026, 10, 12, 8, 14, tzinfo=timezone.utc)
    assert "Pasted email" in logged_in.get("/clients/demo/orders").text


def test_empty_paste_is_explained(logged_in, demo_client, fake_ai):
    response = logged_in.post("/clients/demo/orders/paste", data={"text": "  "}, headers={"HX-Request": "true"})
    assert "Paste the email text first." in response.text


def test_rows_show_reading_then_poll_until_done(logged_in, session, demo_client, monkeypatch):
    queued = []
    monkeypatch.setattr(service, "run_later", lambda fn, *args: queued.append((fn, args)))
    response = upload(logged_in, "demo", [("order.eml", eml(BODY), "message/rfc822")])
    assert "Reading the file" in response.text
    assert 'hx-trigger="every 2s"' in response.text

    from tests.helpers import FakeAI
    monkeypatch.setattr(ai_client, "factory", lambda: FakeAI(ANSWER))
    for fn, args in queued:
        fn(*args)
    live = logged_in.get("/clients/demo/orders/live")
    assert "Lines read, matching comes next" in live.text
    assert 'hx-trigger="every 2s"' not in live.text


def test_scanned_pdf_is_a_problem_without_an_ai_call(logged_in, session, demo_client, demo_files, fake_ai):
    path = demo_files["orders"] / "10_cedars_scanned.pdf"
    upload(logged_in, "demo", [(path.name, path.read_bytes(), "application/pdf")])
    order = session.exec(select(Order)).one()
    assert order.status == "problem" and order.initial_status == "problem"
    assert order.flags[0]["message"] == "Scanned PDF, not supported yet"
    assert fake_ai.calls == []
    assert "Scanned PDF, not supported yet" in logged_in.get("/clients/demo/orders").text


def test_not_an_order_is_pass_on_with_the_reason(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [order_answer(classification="chase", classification_reason="Asks where the delivery is")]
    upload(logged_in, "demo", [("chase.eml", eml("Where is my delivery?"), "message/rfc822")])
    order = session.exec(select(Order)).one()
    assert order.status == "pass_on" and order.initial_status == "pass_on"
    page = logged_in.get("/clients/demo/orders?status=pass_on")
    assert "Delivery chase: Asks where the delivery is" in page.text


def test_a_line_that_is_not_in_the_text_makes_the_order_a_problem(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [order_answer([("120 bottles of antibacterial spray", None, "antibac", "120", "bottles",
                                      None)])]
    upload(logged_in, "demo", [("order.eml", eml(BODY), "message/rfc822")])
    order = session.exec(select(Order)).one()
    assert order.status == "problem"
    assert order.flags[0]["code"] == "line_problem"
    assert "could not be found in what the customer sent (line 1)" in order.flags[0]["message"]


def test_unreadable_ai_answers_make_a_problem(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [None]
    upload(logged_in, "demo", [("order.eml", eml(BODY), "message/rfc822")])
    order = session.exec(select(Order)).one()
    assert order.status == "problem" and order.flags[0]["message"] == "Could not read this order"
    assert len(fake_ai.calls) == 2


def test_ai_not_reachable_can_be_tried_again(logged_in, session, demo_client, fake_ai, monkeypatch):
    class Down:
        def call_tool(self, **kwargs):
            raise AIUnavailable("Could not reach the AI service. Check the internet connection.")

    monkeypatch.setattr(ai_client, "factory", lambda: Down())
    upload(logged_in, "demo", [("order.eml", eml(BODY), "message/rfc822")])
    order = session.exec(select(Order)).one()
    assert order.processing_state == "failed" and order.initial_status is None
    page = logged_in.get("/clients/demo/orders")
    assert "Could not reach the AI service" in page.text and "Try again" in page.text

    monkeypatch.setattr(ai_client, "factory", lambda: fake_ai)
    fake_ai.answers = [ANSWER]
    logged_in.post(f"/clients/demo/orders/{order.id}/retry", headers={"HX-Request": "true"})
    session.refresh(order)
    assert order.processing_state == "read" and order.flags == []


def test_unfinished_orders_are_picked_up_after_a_restart(session, demo_client, fake_ai):
    order = Order(client_id="demo", source_type="paste", processing_state="reading")
    session.add(order)
    session.commit()
    service._store(order, "pasted.txt", BODY.encode())
    session.add(order)
    session.commit()
    fake_ai.answers = [ANSWER]
    service.resume_unfinished()
    session.refresh(order)
    assert order.processing_state == "read"


def test_filters_and_counts(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [order_answer(classification="chase")]
    upload(logged_in, "demo", [("a.eml", eml("Where is it?"), "message/rfc822")])
    fake_ai.answers = [ANSWER]
    upload(logged_in, "demo", [("b.eml", eml(BODY), "message/rfc822")])
    page = logged_in.get("/clients/demo/orders")
    assert 'All <span class="count">2</span>' in page.text
    assert 'Pass on <span class="count">1</span>' in page.text
    only = logged_in.get("/clients/demo/orders/live?status=pass_on")
    assert "a.eml" in only.text and "b.eml" not in only.text


def test_order_page_highlights_each_line_where_it_came_from(logged_in, session, demo_client, fake_ai):
    fake_ai.answers = [ANSWER]
    upload(logged_in, "demo", [("order.eml", eml(BODY), "message/rfc822")])
    order = session.exec(select(Order)).one()
    page = logged_in.get(f"/clients/demo/orders/{order.id}")
    assert page.status_code == 200
    assert '<mark class="hl-read" title="Line 1"><span class="visually-hidden">[line 1] </span>' \
           '120 bottles of the antibac spray</mark>' in page.text
    assert "They wrote &#34;Thursday&#34;" in page.text or 'They wrote "Thursday"' in page.text


def test_load_the_demo_orders_uses_each_answer_keys_sender_and_time(logged_in, session, demo_client, demo_files,
                                                                    fake_ai, monkeypatch):
    seed_dir = demo_files["orders"].parent
    monkeypatch.setattr("seed.demo_data.SEED_DIR", seed_dir)
    fake_ai.answers = [order_answer(classification="other")]
    response = logged_in.post("/clients/demo/orders/demo", headers={"HX-Request": "true"})
    assert "20 orders added" in response.text

    orders = {o.original_filename: o for o in session.exec(select(Order)).all()}
    assert len(orders) == 20
    answer = json.loads((seed_dir / "answers" / "11_towerhouse_po_table.json").read_text())
    pdf = orders["11_towerhouse_po_table.pdf"]
    assert pdf.sender_email == answer["sender_email"]
    assert pdf.received_at.strftime("%Y-%m-%dT%H:%M:%SZ") == answer["received_at"]


def test_demo_orders_missing_says_run_make_seed(logged_in, demo_client, fake_ai, monkeypatch, tmp_path):
    monkeypatch.setattr("seed.demo_data.SEED_DIR", tmp_path / "nowhere")
    response = logged_in.post("/clients/demo/orders/demo", headers={"HX-Request": "true"})
    assert "Run make seed first." in response.text


def test_demo_button_only_for_demo_clients(logged_in, session, fake_ai):
    add_client(session, "abc", "ABC Cleaning Supplies")
    assert "Load the 20 test orders" not in logged_in.get("/clients/abc/orders").text
    assert logged_in.post("/clients/abc/orders/demo").status_code == 404


def test_another_clients_orders_never_appear(logged_in, session, demo_client, fake_ai):
    add_client(session, "abc", "ABC Cleaning Supplies")
    fake_ai.answers = [ANSWER]
    upload(logged_in, "abc", [("abc-order.eml", eml(BODY), "message/rfc822")])
    abc_order = session.exec(select(Order).where(Order.client_id == "abc")).one()

    demo_page = logged_in.get("/clients/demo/orders")
    assert "abc-order.eml" not in demo_page.text and "No orders yet" in demo_page.text
    assert "abc-order.eml" in logged_in.get("/clients/abc/orders").text
    assert logged_in.get(f"/clients/demo/orders/{abc_order.id}").status_code == 404
    assert logged_in.get(f"/clients/demo/orders/{abc_order.id}/original").status_code == 404
    assert logged_in.post(f"/clients/demo/orders/{abc_order.id}/retry").status_code == 404
    assert "/clients/abc/orders/" in abc_order.stored_path


def test_queue_needs_login(browser, demo_client):
    response = browser.get("/clients/demo/orders", follow_redirects=False)
    assert response.status_code in (302, 303)
    response = browser.post("/clients/demo/orders/paste", data={"text": "x"}, follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)
