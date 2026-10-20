from datetime import datetime, timezone

from app.models import Client, Order


def add_client(session, client_id, name, mode="trial", setup_done=None, **extra):
    client = Client(id=client_id, name=name, short_name=name.replace(" ", ""), mode=mode,
                    setup_done=setup_done if setup_done is not None else ["details", "catalogue", "customers", "output"],
                    **extra)
    session.add(client)
    session.commit()
    return client


def add_order(session, client_id, status, received_at=None, flags=None, **extra):
    order = Order(client_id=client_id, source_type="paste", raw_text="test", status=status,
                  initial_status=status, processing_state="done",
                  received_at=received_at or datetime.now(timezone.utc), flags=flags or [], **extra)
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


class FakeAI:
    """Stands in for the AI. Each call returns the next answer: a dict (the tool's arguments),
    None (no tool call), or a function of the user message that returns one of those."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def call_tool(self, *, system, user, tool, attempt=1, max_tokens=16000):
        from pipeline.ai import ToolCall

        self.calls.append({"user": user, "attempt": attempt})
        answer = self.answers.pop(0) if len(self.answers) > 1 else (self.answers[0] if self.answers else None)
        if callable(answer):
            answer = answer(user)
        return ToolCall(input=answer, problem=None if answer is not None else "no tool call",
                        input_tokens=1000, output_tokens=200, model="fake")


def order_answer(lines=(), classification="new_order", **header):
    """A record_order tool answer. lines are (source_span, code, description, qty, unit, price) tuples."""
    answer = {
        "classification": classification,
        "classification_reason": "Test",
        "customer_name": None,
        "po_number": None,
        "delivery_date_written": None,
        "delivery_address": None,
        "delivery_notes": None,
        "several_orders": False,
        "several_orders_reason": None,
        "lines": [
            {"source_span": span, "product_code": code, "description": desc, "quantity": qty, "unit": unit,
             "price": price}
            for span, code, desc, qty, unit, price in lines
        ],
    }
    answer.update(header)
    return answer
