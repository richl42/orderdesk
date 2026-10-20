"""Pipeline step 1: every input type becomes plain text, and unreadable files say why."""

import io
from email.message import EmailMessage

import pytest
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pipeline import ingest
from pipeline.vocab import SourceType


def pdf_bytes(lines=(), image=False, table=None) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    y = 780
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 18
    if table:
        from reportlab.platypus import Table, TableStyle

        t = Table(table)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, (0, 0, 0))]))
        w, h = t.wrapOn(pdf, 400, 400)
        t.drawOn(pdf, 60, y - h)
    if image:
        from PIL import Image
        from reportlab.lib.utils import ImageReader

        img = Image.new("L", (200, 200), 200)
        raw = io.BytesIO()
        img.save(raw, format="PNG")
        raw.seek(0)
        pdf.drawImage(ImageReader(raw), 0, 0, width=595, height=842)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def xlsx_bytes(rows, sheets=None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Order"
    for row in rows:
        ws.append(row)
    for name, extra in (sheets or {}).items():
        other = wb.create_sheet(name)
        for row in extra:
            other.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_pasted_text_is_kept_as_written_but_tidied():
    result = ingest.ingest_paste("Hi\r\n\r\n\r\n\r\n2 cases bleach   \r\nThanks")
    assert result.source_type == SourceType.PASTE
    assert result.text == "Hi\n\n2 cases bleach\nThanks"


def test_text_file_in_windows_encoding():
    result = ingest.ingest_file("order.txt", "Café order: 2 cases".encode("cp1252"))
    assert result.text == "Café order: 2 cases"


def test_eml_plain_body_sender_subject_and_date():
    msg = EmailMessage()
    msg["From"] = "Maria Lopez <Purchasing@Riverside-Hotel.example>"
    msg["Subject"] = "Order for Thursday"
    msg["Date"] = "Mon, 12 Oct 2026 09:14:00 +0100"
    msg.set_content("120 bottles of the antibac spray\n\nThanks\nMaria")
    result = ingest.ingest_file("order.eml", bytes(msg))
    assert result.source_type == SourceType.EML
    assert result.sender_email == "purchasing@riverside-hotel.example"
    assert result.sender_name == "Maria Lopez"
    assert result.subject == "Order for Thursday"
    assert result.sent_at.isoformat() == "2026-10-12T08:14:00+00:00"
    assert "120 bottles of the antibac spray" in result.text


def test_eml_envelope_reads_headers_only():
    msg = EmailMessage()
    msg["From"] = "a@b.example"
    msg["Subject"] = "Hello"
    msg["Date"] = "Tue, 13 Oct 2026 08:00:00 +0100"
    msg.set_content("x")
    sender, subject, sent = ingest.email_envelope(bytes(msg))
    assert (sender, subject, sent.hour) == ("a@b.example", "Hello", 7)


def test_eml_html_only_body_becomes_text():
    msg = EmailMessage()
    msg["From"] = "a@b.example"
    msg.set_content("<html><head><style>p{color:red}</style></head><body><p>Please send:</p>"
                    "<table><tr><td>CLN-101</td><td>2 cases</td></tr></table><p>Thanks<br>Jo</p></body></html>",
                    subtype="html")
    result = ingest.ingest_file("order.eml", bytes(msg))
    assert "color" not in result.text
    assert "Please send:" in result.text
    assert "CLN-101 | 2 cases" in result.text
    assert "Thanks\nJo" in result.text


def test_eml_attachments_are_read_as_part_of_the_same_order():
    msg = EmailMessage()
    msg["From"] = "a@b.example"
    msg.set_content("Order attached, thanks.")
    msg.add_attachment(b"Code,Qty\nCLN-101,2\n", maintype="text", subtype="csv", filename="order.csv")
    msg.add_attachment(pdf_bytes(["PO 2291", "Toilet cleaner 750ml - 2 cases"]), maintype="application",
                       subtype="pdf", filename="po.pdf")
    msg.add_attachment(b"\x89PNG fake", maintype="image", subtype="png", filename="logo.png")
    msg.add_attachment(b"PK fake", maintype="application", subtype="msword", filename="terms.doc")
    result = ingest.ingest_file("order.eml", bytes(msg))
    assert result.attachments == ["order.csv", "po.pdf"]
    assert "--- Attached file: order.csv ---\nCode: CLN-101 | Qty: 2" in result.text
    assert "Toilet cleaner 750ml - 2 cases" in result.text
    assert any("terms.doc" in note for note in result.notes)
    assert not any("logo.png" in note for note in result.notes)


def test_eml_with_a_scanned_pdf_attachment_is_a_problem():
    msg = EmailMessage()
    msg["From"] = "a@b.example"
    msg.set_content("See attached")
    msg.add_attachment(pdf_bytes(image=True), maintype="application", subtype="pdf", filename="scan.pdf")
    assert ingest.ingest_file("order.eml", bytes(msg)).problem == ingest.SCANNED_PDF


def test_pdf_text():
    result = ingest.ingest_file("po.pdf", pdf_bytes(["Purchase order 2291", "30 bottles of glass cleaner"]))
    assert result.problem is None
    assert "Purchase order 2291\n30 bottles of glass cleaner" in result.text


def test_pdf_tables_become_column_value_rows_in_place():
    data = [["Code", "Description", "Qty"], ["TOW-301", "Toilet roll", "4"], ["CLN-104", "Cleaner", "2"]]
    result = ingest.ingest_file("po.pdf", pdf_bytes(["PO number: TH-4410"], table=data))
    lines = result.text.split("\n")
    assert lines[0] == "PO number: TH-4410"
    assert "Code: TOW-301 | Description: Toilet roll | Qty: 4" in lines
    assert "Code: CLN-104 | Description: Cleaner | Qty: 2" in lines
    assert result.text.count("TOW-301") == 1  # the table is not repeated as loose text


def test_pdf_with_only_an_image_is_scanned():
    assert ingest.ingest_file("scan.pdf", pdf_bytes(image=True)).problem == ingest.SCANNED_PDF


def test_empty_pdf_is_scanned():
    assert ingest.ingest_file("blank.pdf", pdf_bytes()).problem == ingest.SCANNED_PDF


def test_broken_pdf_is_refused_in_plain_words():
    with pytest.raises(ingest.NotSupported, match="could not be opened"):
        ingest.ingest_file("broken.pdf", b"not a pdf at all")


def test_xlsx_keeps_rows_above_the_table_and_writes_each_row_as_column_value():
    data = xlsx_bytes([
        ["Oakfield Academy order"], [], ["Order ref:", "OA-7781"], ["Delivery date:", "22/10/2026"], [],
        ["Item code", "Item", "Qty", "Unit"],
        ["CLN-101", "Antibacterial surface spray 750ml", 2, "CASE"],
        ["GLV-202", "Blue nitrile gloves M", 3.0, "BOX"],
        ["Notes: rear door"],
    ])
    text = ingest.ingest_file("order.xlsx", data).text
    assert text.split("\n")[:3] == ["Oakfield Academy order", "Order ref: OA-7781", "Delivery date: 22/10/2026"]
    assert "Item code: CLN-101 | Item: Antibacterial surface spray 750ml | Qty: 2 | Unit: CASE" in text
    assert "Item code: GLV-202 | Item: Blue nitrile gloves M | Qty: 3 | Unit: BOX" in text
    assert text.endswith("Notes: rear door")


def test_xlsx_reads_every_sheet():
    data = xlsx_bytes([["Code", "Qty", "Unit"], ["CLN-101", 1, "CASE"]],
                      sheets={"Second site": [["Code", "Qty", "Unit"], ["GLV-202", 2, "BOX"]]})
    text = ingest.ingest_file("order.xlsx", data).text
    assert "Sheet: Order" in text and "Sheet: Second site" in text
    assert "Code: GLV-202 | Qty: 2 | Unit: BOX" in text


def test_csv_with_semicolons_in_windows_encoding():
    data = "Customer;Mill Lane Café\r\nProduct;Qty;Unit\r\nKitchen roll;1;case\r\n".encode("cp1252")
    text = ingest.ingest_file("order.csv", data).text
    assert "Customer: Mill Lane Café" in text
    assert "Product: Kitchen roll | Qty: 1 | Unit: case" in text


@pytest.mark.parametrize("name, message", [
    ("photo.jpg", "Photos"),
    ("order.docx", "Word"),
    ("order.xls", ".xlsx"),
    ("order.zip", "not supported"),
])
def test_unsupported_files_get_plain_reasons(name, message):
    with pytest.raises(ingest.NotSupported, match=message):
        ingest.detect_type(name)


def test_content_hash_ignores_case_and_spacing():
    assert ingest.content_hash("2 Cases  of\nBleach") == ingest.content_hash("2 cases of bleach")
    assert ingest.content_hash("2 cases of bleach") != ingest.content_hash("3 cases of bleach")


def test_all_demo_orders_can_be_read(demo_files):
    for path in sorted(demo_files["orders"].iterdir()):
        result = ingest.ingest_file(path.name, path.read_bytes())
        if path.name.startswith("10_"):
            assert result.problem == ingest.SCANNED_PDF
        else:
            assert result.problem is None and len(result.text) > 40, path.name
