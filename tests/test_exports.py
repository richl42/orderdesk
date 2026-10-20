"""Exports: batches and single orders in the client's format, the history, and the demo answer keys."""

import csv
import io
import json

import pytest
from sqlmodel import select

from app import ai_client
from app.models import ExportBatch, Order, OrderLine
from app.orders import export, review, service
from eval.check_export import differences, expected_rows
from eval.run import FIXTURES_DIR
from pipeline.ai import AnthropicAI
from tests.helpers import add_client, add_order

HX = {"HX-Request": "true"}


def approved_order(session, client_id="demo", po="2291", code="CLN-101", qty=10, price="28.00", **extra):
    from datetime import datetime, timezone
    from decimal import Decimal

    from app.models import Customer

    customer = session.exec(select(Customer).where(Customer.client_id == client_id)).first()
    order = add_order(session, client_id, "approved", po_number=po, customer_id=customer.id if customer else None,
                      classification="new_order", **extra)
    session.add(OrderLine(client_id=client_id, order_id=order.id, line_no=1, product_code=code, qty=Decimal(qty),
                          unit="CASE", unit_price=Decimal(price), status="ready"))
    order.approved_at = datetime.now(timezone.utc)
    session.add(order)
    session.commit()
    return order


def test_batch_holds_only_approved_orders_and_marks_them_exported(logged_in, session, demo_client):
    first = approved_order(session, po="A-1")
    second = approved_order(session, po="A-2", code="GLV-202", qty=3, price="6.20")
    waiting = add_order(session, "demo", "check", po_number="NOT-YET")
    response = logged_in.post("/clients/demo/exports", follow_redirects=False)
    assert response.status_code == 303 and "/clients/demo/exports?new=" in response.headers["location"]

    batch = session.exec(select(ExportBatch)).one()
    assert batch.order_ids == [first.id, second.id] and batch.row_count == 2
    assert batch.file_name.startswith("DemoCleaning_orders_") and batch.file_name.endswith(".csv")
    data = open(batch.file_path, "rb").read()
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    assert rows[0] == ["AccountRef", "CustOrderNo", "OrderDate", "StockCode", "Qty", "UnitPrice", "DeliveryDate",
                       "Notes"]
    assert [r[1] for r in rows[1:]] == ["A-1", "A-2"] and rows[1][3:6] == ["CLN-101", "10", "28.00"]
    assert b"\r\n" in data and b"NOT-YET" not in data

    for order in (first, second):
        session.refresh(order)
        assert order.status == "exported" and order.export_batch_id == batch.id and order.exported_at
    session.refresh(waiting)
    assert waiting.status == "check"

    page = logged_in.get(response.headers["location"]).text
    assert f'url=/clients/demo/exports/{batch.id}/download' in page and batch.file_name in page
    download = logged_in.get(f"/clients/demo/exports/{batch.id}/download")
    assert download.content == data
    assert f'filename="{batch.file_name}"' in download.headers["content-disposition"]


def test_nothing_to_export_is_explained(logged_in, demo_client):
    response = logged_in.post("/clients/demo/exports")
    assert response.status_code == 422 and "no approved orders waiting" in response.text


def test_an_export_is_never_rebuilt(logged_in, session, demo_client):
    order = approved_order(session)
    made = export.export_batch(session, demo_client)
    line = session.exec(select(OrderLine).where(OrderLine.order_id == order.id)).one()
    line.qty = 99
    session.add(line)
    session.commit()
    assert logged_in.get(f"/clients/demo/exports/{made.batch.id}/download").content == made.data
    with pytest.raises(export.ExportRefused):
        export.export_batch(session, demo_client)  # exported orders never go in a second batch


def test_two_batches_in_the_same_minute_get_different_names(session, demo_client):
    approved_order(session, po="A-1")
    one = export.export_batch(session, demo_client)
    approved_order(session, po="A-2")
    two = export.export_batch(session, demo_client)
    assert one.batch.file_name != two.batch.file_name


def test_download_this_order_counts_as_exported(logged_in, session, demo_client):
    order = approved_order(session, po="PO/77 X")
    other = approved_order(session, po="B-2")
    response = logged_in.post(f"/clients/demo/orders/{order.id}/download", follow_redirects=False)
    assert response.status_code == 303
    batch = session.exec(select(ExportBatch)).one()
    assert batch.file_name == "DemoCleaning_PO77X.csv" and batch.order_ids == [order.id]
    session.refresh(order)
    assert order.status == "exported"
    page = logged_in.get(f"/clients/demo/orders/{order.id}").text
    assert "DemoCleaning_PO77X.csv" in page
    again = logged_in.post(f"/clients/demo/orders/{order.id}/download")
    assert again.status_code == 422 and "already been exported" in again.text
    made = export.export_batch(session, demo_client)
    assert made.batch.order_ids == [other.id]  # not in the next batch as well


def test_only_approved_orders_can_be_downloaded(logged_in, session, demo_client):
    order = add_order(session, "demo", "check", classification="new_order")
    response = logged_in.post(f"/clients/demo/orders/{order.id}/download")
    assert response.status_code == 422 and "Approve this order before downloading it." in response.text
    assert session.exec(select(ExportBatch)).first() is None


def test_no_output_format_means_no_file(session):
    client = add_client(session, "abc", "ABC Cleaning Supplies", setup_done=["details"])
    add_order(session, "abc", "approved", classification="new_order")
    with pytest.raises(export.ExportRefused, match="output format"):
        export.export_batch(session, client)
    assert session.exec(select(ExportBatch)).first() is None
    assert session.exec(select(Order)).one().status == "approved"


def test_another_clients_exports_are_out_of_reach(logged_in, session, demo_client):
    add_client(session, "abc", "ABC Cleaning Supplies")
    approved_order(session)
    made = export.export_batch(session, demo_client)
    assert logged_in.get(f"/clients/abc/exports/{made.batch.id}/download").status_code == 404
    assert made.batch.file_name not in logged_in.get("/clients/abc/exports").text
    assert "/clients/demo/exports/" in made.batch.file_path or "clients/demo/exports" in made.batch.file_path


def test_queue_export_bar(logged_in, session, demo_client):
    page = logged_in.get("/clients/demo/orders").text
    assert "Export: 0 approved orders ready" in page and "Past exports" in page
    approved_order(session)
    assert "Export: 1 approved order ready" in logged_in.get("/clients/demo/orders").text


# ---------------------------------------------------------------- the demo: Milestone 6 "Done when"


def review_like_the_operator(session, client, order):
    """The corrections made on the review screen in Milestone 5, then approve or pass on."""
    name = order.original_filename
    lines = review.lines_of(session, order)
    if name.startswith("07_"):
        review.pass_on(session, client, order)  # not a customer: goes to the client contact
        return
    if order.status == "pass_on":
        return
    if name.startswith("05_"):
        review.save_line(session, client, order, lines[0].id, "CLN-103", "3")
    if name.startswith("10_"):
        for code, qty in (("TOW-301", "2"), ("TOW-310", "3"), ("CLN-101", "1")):
            line = review.add_line(session, client, order)
            review.save_line(session, client, order, line.id, code, qty)
    if name.startswith("15_"):
        yoga = next(ln for ln in lines if ln.product_code is None)
        review.delete_line(session, client, order, yoga.id)
    for line in review.lines_of(session, order):
        if line.status != "ready" and line.product_code and line.qty is not None:
            review.save_line(session, client, order, line.id, line.product_code, str(line.qty))  # confirm
    review.approve(session, client, order, 30)


def test_demo_batch_matches_the_answer_keys_exactly(session, demo_client, demo_files, monkeypatch):
    if not FIXTURES_DIR.is_dir():
        pytest.skip("No saved AI answers: run make eval ARGS=\"--save-test-answers\"")
    replay = AnthropicAI("", "claude-sonnet-5-5", cache_dir=FIXTURES_DIR)
    monkeypatch.setattr(ai_client, "factory", lambda: replay)
    monkeypatch.setattr(service, "run_later", lambda fn, *args: fn(*args))

    answers = sorted((json.loads(p.read_text()) for p in demo_files["answers"].glob("*.json")),
                     key=lambda a: a["received_at"])
    from datetime import datetime

    for answer in answers:
        path = demo_files["orders"] / answer["file"]
        received = datetime.fromisoformat(answer["received_at"].replace("Z", "+00:00"))
        service.receive_files(session, demo_client, [(path.name, path.read_bytes())],
                              sender_email=answer["sender_email"], received_at=received)
    session.expire_all()
    for order in session.exec(select(Order).order_by(Order.received_at)).all():
        review_like_the_operator(session, demo_client, order)

    statuses = [o.status for o in session.exec(select(Order)).all()]
    assert statuses.count("approved") == 17 and statuses.count("pass_on") == 3

    made = export.export_batch(session, demo_client)
    assert differences(made.data, demo_files["answers"]) == []
    assert made.batch.row_count == len(expected_rows(demo_files["answers"])) == 79
