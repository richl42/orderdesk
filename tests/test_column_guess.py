"""Setup: the AI is asked only about columns whose headers say nothing, and its answer is checked."""

from sqlmodel import select

from app.clientsetup import pending, service
from app.models import Event
from pipeline.column_guess import build_user_message, suggest_columns
from pipeline.tables import Table
from tests.helpers import FakeAI, add_client

TABLE = Table(
    columns=["Stock Code", "Col B", "Col C", "Col D"],
    rows=[{"Stock Code": f"CLN-10{i}", "Col B": f"Thick bleach {i}L", "Col C": "CASE", "Col D": "S"}
          for i in range(1, 6)],
)


def test_only_unclear_columns_and_three_examples_are_sent():
    message = build_user_message("catalogue", TABLE, ["Col B", "Col D"])
    assert 'Column 2: header "Col B", examples: "Thick bleach 1L", "Thick bleach 2L", "Thick bleach 3L"' in message
    assert "Thick bleach 4L" not in message
    assert "Stock Code" not in message and "CLN-101" not in message


def test_suggestions_are_checked():
    ai = FakeAI({"columns": [
        {"column_number": 2, "meaning": "description"},
        {"column_number": 3, "meaning": "product_code"},  # already taken by a clear header
        {"column_number": 4, "meaning": "ignore"},
        {"column_number": 9, "meaning": "price"},  # no such column
        {"column_number": 1, "meaning": "price"},  # not an unclear column
    ]})
    suggestions, call = suggest_columns(ai, "catalogue", TABLE, ["Col B", "Col C", "Col D"], {"product_code"})
    assert suggestions == {"Col B": "description", "Col D": "ignore"}
    assert call.input is not None


def test_upload_with_unclear_headers_uses_the_ai_and_logs_the_call(session, fake_ai):
    client = add_client(session, "abc", "ABC Cleaning Supplies", setup_done=["details"])
    fake_ai.answers = [{"columns": [{"column_number": 2, "meaning": "description"},
                                    {"column_number": 3, "meaning": "sell_unit"},
                                    {"column_number": 4, "meaning": "ignore"}]}]
    data = "Stock Code,Col B,Col C,Col D\n" + "".join(f"CLN-10{i},Thick bleach {i}L,CASE,S\n" for i in range(1, 6))
    assert service.receive_table_upload(client, "catalogue", "products.csv", data.encode(), session=session) is None
    assert pending.load("abc", "catalogue").mapping == {
        "Stock Code": "product_code", "Col B": "description", "Col C": "sell_unit", "Col D": "ignore"}
    event = session.exec(select(Event).where(Event.client_id == "abc", Event.type == "ai_call")).one()
    assert event.details["purpose"] == "column_guess" and event.details["columns"] == 3
    section = service.table_section(session, client, "catalogue")
    assert {c.column for c in section.columns if c.unsure} == {"Col B", "Col C", "Col D"}  # still "please check"


def test_without_the_ai_the_header_and_value_guesses_stand(session):
    client = add_client(session, "abc", "ABC Cleaning Supplies", setup_done=["details"])
    data = "Stock Code,Col B,Col C\n" + "".join(f"CLN-10{i},Thick bleach {i}L,CASE\n" for i in range(1, 6))
    assert service.receive_table_upload(client, "catalogue", "products.csv", data.encode(), session=session) is None
    assert pending.load("abc", "catalogue").mapping == {
        "Stock Code": "product_code", "Col B": "description", "Col C": "sell_unit"}


def test_clear_headers_never_reach_the_ai(session, fake_ai):
    client = add_client(session, "abc", "ABC Cleaning Supplies", setup_done=["details"])
    data = b"Stock Code,Description,Unit\nCLN-101,Thick bleach,CASE\n"
    service.receive_table_upload(client, "catalogue", "products.csv", data, session=session)
    assert fake_ai.calls == []
