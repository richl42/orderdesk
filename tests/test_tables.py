import csv
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlmodel import select

from app.clientsetup import importers
from app.models import CatalogueItem, Client, Customer
from pipeline.tables import (
    guess_mapping,
    guess_output_template,
    missing_required,
    parse_decimal,
    read_table,
)


def write_csv(path: Path, rows, encoding="utf-8", delimiter=","):
    with path.open("w", newline="", encoding=encoding) as f:
        csv.writer(f, delimiter=delimiter).writerows(rows)
    return path


@pytest.mark.parametrize("header", ["Stock Code", "SKU", "Item", "Product code", "STOCK_CODE"])
def test_product_code_headers_are_recognised(header):
    guess = guess_mapping([header, "Description"], "catalogue")
    assert guess.mapping[header] == "product_code"
    assert guess.mapping["Description"] == "description"


def test_unclear_headers_are_left_for_the_operator():
    guess = guess_mapping(["Ref", "Col B", "Thing"], "catalogue")
    assert guess.mapping["Col B"] == "ignore"
    assert "Col B" in guess.unsure and "Thing" in guess.unsure
    assert missing_required(guess.mapping, "catalogue") == ["description"]


def test_header_row_found_below_title_rows(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["ACME price list October"])
    ws.append([])
    ws.append(["Code", "Description", "Price"])
    ws.append(["A-1", "Widget", 1.5])
    ws.append(["A-2", "Gadget", 12])
    path = tmp_path / "list.xlsx"
    wb.save(path)
    table = read_table(path)
    assert table.columns == ["Code", "Description", "Price"]
    assert table.rows == [
        {"Code": "A-1", "Description": "Widget", "Price": "1.5"},
        {"Code": "A-2", "Description": "Gadget", "Price": "12"},
    ]


def test_windows_encoded_semicolon_csv(tmp_path):
    path = write_csv(tmp_path / "c.csv", [["Account", "Name", "Band"], ["CAFE01", "Mill Lane Café", "£A"]],
                     encoding="cp1252", delimiter=";")
    table = read_table(path)
    assert table.delimiter == ";"
    assert table.rows[0]["Name"] == "Mill Lane Café"
    assert table.rows[0]["Band"] == "£A"


def test_parse_decimal():
    assert str(parse_decimal("£1,234.50")) == "1234.50"
    assert parse_decimal("12 boxes") is None
    assert parse_decimal("") is None


def make_client(session, client_id="acme"):
    client = Client(id=client_id, name="Acme", short_name="Acme", setup_done=["details"])
    session.add(client)
    session.commit()
    return client


def test_duplicate_product_codes_are_reported_and_first_kept(session, tmp_path):
    client = make_client(session)
    path = write_csv(tmp_path / "cat.csv", [
        ["Stock Code", "Description", "Pack Qty", "Price"],
        ["A-1", "Widget, box of 10", "10", "4.00"],
        ["A-1", "Widget again", "10", "5.00"],
        ["A-2", "Gadget", "ten", "x"],
        ["", "No code here", "", ""],
    ])
    table = read_table(path)
    result = importers.import_catalogue(session, client, path, table, guess_mapping(table.columns, "catalogue").mapping)
    assert result.count == 2
    assert result.duplicates == ["A-1"]
    assert result.skipped_rows == [4]
    assert any("pack size 'ten'" in w for w in result.warnings)
    assert any("price 'x'" in w for w in result.warnings)
    widget = session.get(CatalogueItem, 1)
    assert widget.description == "Widget, box of 10"
    assert str(widget.price) == "4.00"


def test_import_refuses_until_code_and_description_are_mapped(session, tmp_path):
    client = make_client(session)
    path = write_csv(tmp_path / "cat.csv", [["Code", "Thing"], ["A-1", "Widget"]])
    table = read_table(path)
    with pytest.raises(importers.MappingIncomplete) as err:
        importers.import_catalogue(session, client, path, table, {"Code": "product_code", "Thing": "ignore"})
    assert err.value.missing == ["description"]


def test_customer_emails_split_and_public_domains_ignored(session, tmp_path):
    client = make_client(session)
    path = write_csv(tmp_path / "cust.csv", [
        ["Account Ref", "Account Name", "Email"],
        ["CAFE01", "Mill Lane Café", "owner.cafe@gmail.com; orders@milllane.example"],
    ])
    table = read_table(path)
    importers.import_customers(session, client, path, table, guess_mapping(table.columns, "customers").mapping)
    customer = session.exec(select(Customer)).one()
    assert customer.emails == ["owner.cafe@gmail.com", "orders@milllane.example"]
    assert customer.email_domains == ["milllane.example"]


def test_output_template_detection(demo_files):
    guess = guess_output_template(demo_files["template"])
    assert guess.columns == ["AccountRef", "CustOrderNo", "OrderDate", "StockCode", "Qty", "UnitPrice",
                             "DeliveryDate", "Notes"]
    assert guess.include_header is True
    assert guess.date_format == "%d/%m/%Y"
    assert guess.decimal_places == 2
    assert guess.delimiter == ","
    assert guess.fill["StockCode"] == {"field": "product_code"}
    assert guess.fill["Notes"] == {"field": "delivery_notes"}


def test_output_template_without_header_and_iso_dates(tmp_path):
    path = write_csv(tmp_path / "imp.csv", [["ACC1", "2026-10-05", "X-1", "3", "1.250"]], delimiter="\t")
    guess = guess_output_template(path)
    assert guess.include_header is False
    assert guess.columns == ["Column 1", "Column 2", "Column 3", "Column 4", "Column 5"]
    assert guess.date_format == "%Y-%m-%d"
    assert guess.decimal_places == 3
    assert guess.delimiter == "\t"
    assert all(v == {"blank": True} for v in guess.fill.values())


def test_unclear_headers_are_guessed_from_values():
    rows = [
        {"A": "TOW-310", "B": "Blue roll 2ply, pack of 6", "C": "PACK", "D": "6", "E": "14.40", "F": "5069990000440"},
        {"A": "TOW-311", "B": "Blue roll 2ply, single roll", "C": "EACH", "D": "1", "E": "2.60", "F": "5069990000457"},
    ]
    guess = guess_mapping(list("ABCDEF"), "catalogue", rows)
    assert guess.mapping == {"A": "product_code", "B": "description", "C": "sell_unit", "D": "pack_size",
                             "E": "price", "F": "barcode"}
    assert set(guess.unsure) == set("ABCDEF")  # still shown to the operator as guesses to check


def test_customer_values_guessing():
    rows = [{"Ref": "RIVERS01", "Who": "Riverside Hotel", "Contact": "a@b.example", "Lvl": "A"},
            {"Ref": "MILLLN01", "Who": "Mill Lane Café", "Contact": "c@d.example", "Lvl": "B"}]
    guess = guess_mapping(["Ref", "Who", "Contact", "Lvl"], "customers", rows)
    assert guess.mapping == {"Ref": "account_code", "Who": "name", "Contact": "emails", "Lvl": "price_band"}
