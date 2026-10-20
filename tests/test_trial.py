"""Screen 5: staff entries upload, the comparison on screen, notes and tags, and the client report.

The demo test is Milestone 7's "Done when": the 20 demo orders reviewed as in
Milestone 5, then staff_entries_demo.csv uploaded through the screen, must show
exactly the 3 planted differences and these tiles.
"""

import html
import json
from datetime import date, datetime

import pytest
from sqlmodel import select

from app import ai_client
from app.models import Client, Comparison, StaffEntryLine, StaffEntryUpload
from app.orders import service
from app.trial import service as trial
from eval.run import FIXTURES_DIR
from pipeline.ai import AnthropicAI
from tests.helpers import add_client
from tests.test_exports import approved_order, review_like_the_operator

HX = {"HX-Request": "true"}


@pytest.fixture
def reviewed_demo(session, demo_client, demo_files, monkeypatch):
    """The 20 demo orders read with the saved AI answers and reviewed like the operator did."""
    if not FIXTURES_DIR.is_dir():
        pytest.skip("No saved AI answers: run make eval ARGS=\"--save-test-answers\"")
    replay = AnthropicAI("", "claude-sonnet-5-5", cache_dir=FIXTURES_DIR)
    monkeypatch.setattr(ai_client, "factory", lambda: replay)
    monkeypatch.setattr(service, "run_later", lambda fn, *args: fn(*args))
    answers = sorted((json.loads(p.read_text()) for p in demo_files["answers"].glob("*.json")),
                     key=lambda a: a["received_at"])
    for answer in answers:
        path = demo_files["orders"] / answer["file"]
        received = datetime.fromisoformat(answer["received_at"].replace("Z", "+00:00"))
        service.receive_files(session, demo_client, [(path.name, path.read_bytes())],
                              sender_email=answer["sender_email"], received_at=received)
    session.expire_all()
    from app.models import Order

    for order in session.exec(select(Order).order_by(Order.received_at)).all():
        review_like_the_operator(session, demo_client, order)
    return demo_client


def upload_staff_file(browser, path):
    response = browser.post("/clients/demo/trial/staff/upload",
                            files={"file": (path.name, path.read_bytes(), "text/csv")}, headers=HX)
    assert response.status_code == 200 and "79 rows found" in response.text
    # Save with the guessed columns, as the operator would after checking them.
    import re

    form = {}
    for name, block in re.findall(r'<select name="(map_\d+)"(.*?)</select>', response.text, flags=re.S):
        chosen = re.search(r'<option value="([^"]+)" selected', block)
        form[name] = chosen.group(1) if chosen else "ignore"
    return browser.post("/clients/demo/trial/staff/save", data=form, headers=HX)


def test_demo_staff_entries_show_exactly_the_three_planted_differences(logged_in, session, reviewed_demo, demo_files):
    saved = upload_staff_file(logged_in, demo_files["staff_entries"])
    assert saved.status_code == 204 and saved.headers["HX-Redirect"] == "/clients/demo/trial"

    view = trial.trial_view(session, reviewed_demo, today=date(2026, 10, 23))
    found = [(d.order_label, d.field, d.ours, d.theirs) for d in view.differences]
    assert found == [
        ("PO HV-3390 · Harbour View Hotel", "Quantity (CLN-103)", "3 cases", "2 cases"),
        ("PO ML-0907 · Mill Lane Café", "Delivery date", "16/10/2026", "19/10/2026"),
        ("PO SAP-0412 · St Anne's Primary School", "Product", "BAG-404 × 2 boxes", "BAG-405 × 2 boxes"),
    ]
    m = view.measures
    assert (m.compared, m.exactly_right, m.confidently_wrong, m.flagged) == (17, 14, 0, 10)
    assert m.pct(m.exactly_right) == 82.4 and m.pct(m.flagged) == 58.8 and m.hours_saved == 1.4
    assert m.review_seconds == 30
    assert m.first_pass_known == 17
    assert (view.matched_by_po, view.staff_orders, view.only_ours, view.only_theirs) == (14, 17, 0, 0)
    assert [(d.label, d.right, d.compared) for d in view.days] == [
        ("Mon 12 Oct", 2, 2), ("Tue 13 Oct", 1, 2), ("Wed 14 Oct", 1, 1), ("Thu 15 Oct", 0, 1), ("Fri 16 Oct", 1, 1),
        ("Mon 19 Oct", 2, 2), ("Tue 20 Oct", 1, 2), ("Wed 21 Oct", 2, 2), ("Thu 22 Oct", 2, 2), ("Fri 23 Oct", 2, 2),
    ]

    page = html.unescape(logged_in.get("/clients/demo/trial").text)
    assert "Answer key loaded" in page and "staff_entries_demo.csv" in page
    assert "14 of 17" in page and "82.4%" in page and "1.4 h" in page
    assert "tile-good" in page  # confidently wrong 0 shows green
    assert "14 of 17 (82.4%) matched what your staff entered exactly" in page
    assert "None of the orders the system was sure about turned out to be wrong" in page


def test_notes_and_tags_change_the_tiles_and_survive_a_reupload(logged_in, session, reviewed_demo, demo_files):
    upload_staff_file(logged_in, demo_files["staff_entries"])
    comp = session.exec(select(Comparison).where(Comparison.result == "different")).first()
    response = logged_in.post(f"/clients/demo/trial/differences/{comp.id}/0",
                              data={"note": "Staff rounded down", "tag": "staff_error"}, headers=HX)
    assert "Saved" in response.text
    session.expire_all()  # the note was saved by the app's own session
    assert trial.trial_view(session, reviewed_demo).measures.exactly_right == 15

    upload_staff_file(logged_in, demo_files["staff_entries"])  # a newer file for the same days
    session.expire_all()
    assert len(session.exec(select(StaffEntryUpload)).all()) == 1
    view = trial.trial_view(session, reviewed_demo)
    kept = [d for d in view.differences if d.note == "Staff rounded down"]
    assert kept and kept[0].tag == "staff_error" and view.measures.exactly_right == 15
    report = html.unescape(logged_in.get("/clients/demo/trial/report").text)
    assert "Staff rounded down · Staff error" in report
    assert "do not count against exactly right" in report


def test_summary_can_be_edited_and_written_again(logged_in, session, reviewed_demo, demo_files):
    upload_staff_file(logged_in, demo_files["staff_entries"])
    logged_in.post("/clients/demo/trial/summary", data={"text": "Our own words."})
    assert "Our own words." in logged_in.get("/clients/demo/trial/report").text
    logged_in.post("/clients/demo/trial/summary", data={"reset": "1"})
    assert "matched what your staff entered exactly" in logged_in.get("/clients/demo/trial/report").text


def test_report_never_shows_internal_measures(logged_in, session, reviewed_demo, demo_files):
    upload_staff_file(logged_in, demo_files["staff_entries"])
    report = logged_in.get("/clients/demo/trial/report").text
    assert "AI cost" not in report and "First pass" not in report and "tokens" not in report
    assert "Print or save as PDF" in report
    assert "First pass exact" in logged_in.get("/clients/demo/trial").text  # only on the trial page


def test_compare_again_picks_up_newly_approved_orders(logged_in, session, demo_client, demo_files):
    approved_order(session, po="2291")
    upload_staff_file(logged_in, demo_files["staff_entries"])
    view = trial.trial_view(session, demo_client)
    before = view.measures.compared
    logged_in.post("/clients/demo/trial/compare")
    assert trial.trial_view(session, demo_client).measures.compared == before


def test_staff_entries_of_another_client_are_never_used(logged_in, session, demo_client, demo_files):
    add_client(session, "abc", "ABC Cleaning Supplies")
    upload_staff_file(logged_in, demo_files["staff_entries"])
    assert session.exec(select(StaffEntryLine).where(StaffEntryLine.client_id == "abc")).first() is None
    other = session.get(Client, "abc")
    assert trial.trial_view(session, other).upload is None
    comp = session.exec(select(Comparison)).first()
    response = logged_in.post(f"/clients/abc/trial/differences/{comp.id}/0", data={"note": "x"}, headers=HX)
    assert response.status_code == 404


def test_trial_page_before_any_staff_entries(logged_in, demo_client):
    page = logged_in.get("/clients/demo/trial").text
    assert "Shadow trial results" in page and "Started Mon 12 Oct 2026" in page
    assert "Upload their file" in page and "Export report for client" not in page
