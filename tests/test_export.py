from pipeline import export

TEMPLATE = {
    "columns": ["AccountRef", "CustOrderNo", "OrderDate", "StockCode", "Qty", "UnitPrice", "DeliveryDate", "Notes"],
    "fill": {
        "AccountRef": {"field": "account_code"}, "CustOrderNo": {"field": "po_number"},
        "OrderDate": {"field": "date_received"}, "StockCode": {"field": "product_code"},
        "Qty": {"field": "quantity"}, "UnitPrice": {"field": "price"},
        "DeliveryDate": {"field": "delivery_date"}, "Notes": {"field": "delivery_notes"},
    },
    "date_format": "%d/%m/%Y", "decimal_places": 2, "delimiter": ",", "encoding": "utf-8", "include_header": True,
}

ORDER = {
    "customer": {"account_code": "CUST-018", "name": "Riverside Hotel"},
    "po_number": "2291",
    "received_at": "2026-10-19T14:42:00Z",
    "requested_delivery_date": "2026-10-22",
    "delivery_notes": "Kitchen entrance",
    "lines": [
        {"line_no": 1, "product_code": "SPR-12", "qty": 10, "unit": "CASE", "unit_price": "28.00"},
        {"line_no": 2, "product_code": "GLV-202", "qty": "4", "unit": "BOX", "unit_price": "6.2"},
    ],
}


def test_rows_match_the_wireframe_preview():
    text = export.render_orders_text([ORDER], TEMPLATE)
    assert text == (
        "AccountRef,CustOrderNo,OrderDate,StockCode,Qty,UnitPrice,DeliveryDate,Notes\r\n"
        "CUST-018,2291,19/10/2026,SPR-12,10,28.00,22/10/2026,Kitchen entrance\r\n"
        "CUST-018,2291,19/10/2026,GLV-202,4,6.20,22/10/2026,Kitchen entrance\r\n"
    )


def test_received_date_is_the_london_date():
    late = dict(ORDER, received_at="2026-07-01T23:30:00Z")  # 00:30 on 2 July in London
    assert export.render_rows(late, TEMPLATE)[0][2] == "02/07/2026"


def test_missing_values_are_left_blank_never_guessed():
    order = {"customer": None, "lines": [{"product_code": None, "qty": None, "unit_price": None}]}
    assert export.render_rows(order, TEMPLATE) == [["", "", "", "", "", "", "", ""]]


def test_fixed_and_blank_columns_and_format_options():
    template = dict(TEMPLATE, columns=["Type", "AccountRef", "Spare", "UnitPrice", "DeliveryDate"],
                    fill={"Type": {"fixed": "SO"}, "AccountRef": {"field": "account_code"}, "Spare": {"blank": True},
                          "UnitPrice": {"field": "price"}, "DeliveryDate": {"field": "delivery_date"}},
                    delimiter=";", decimal_places=3, date_format="%Y-%m-%d", include_header=False)
    assert export.render_orders_text([ORDER], template) == (
        "SO;CUST-018;;28.000;2026-10-22\r\nSO;CUST-018;;6.200;2026-10-22\r\n"
    )


def test_quantities_and_rounding():
    assert export.format_quantity("10.0") == "10"
    assert export.format_quantity("2.50") == "2.5"
    assert export.format_money("2.005", 2) == "2.01"
    assert export.format_money("7", 0) == "7"


def test_values_with_commas_are_quoted():
    order = dict(ORDER, delivery_notes="Rear door, ring bell")
    assert '"Rear door, ring bell"' in export.render_orders_text([order], TEMPLATE)


def test_encoding_with_bom():
    assert export.to_bytes("Café", {"encoding": "utf-8-sig"}).startswith(b"\xef\xbb\xbf")
    assert export.to_bytes("Café", {"encoding": "cp1252"}) == b"Caf\xe9"


def test_windows_encoding_keeps_accents_as_excel_on_a_uk_pc_expects():
    template = dict(TEMPLATE, encoding="cp1252")
    order = dict(ORDER, delivery_notes="Café entrance")
    data = export.to_bytes(export.render_orders_text([order], template), template)
    assert "Café entrance".encode("cp1252") in data
