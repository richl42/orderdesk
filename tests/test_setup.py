"""Screen 2: creating a client through the setup screen alone."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import db
from app.models import CatalogueItem, Client, ColumnMapping, Customer, OutputTemplate, PriceListEntry
from tests.conftest import TEST_PASSWORD

HX = {"HX-Request": "true"}


def create(browser, name="ABC Cleaning Supplies", **extra):
    form = {"name": name, "short_name": "", "erp_system": "Sage 200", "mode": "trial",
            "contact_name": "Operations manager", "contact_email": "ops@abc.example",
            "trial_start_date": "2026-10-12", "trial_working_days": "10", **extra}
    return browser.post("/clients/new", data=form, follow_redirects=False)


def upload(browser, client_id, kind, path: Path, headers=HX):
    url = f"/clients/{client_id}/setup/output/upload" if kind == "output" else \
        f"/clients/{client_id}/setup/files/{kind}/upload"
    with path.open("rb") as f:
        return browser.post(url, files={"file": (path.name, f.read())}, headers=headers)


def mapping_form(section_html: str) -> dict:
    """The dropdown values the page pre-selected, as a browser would submit them."""
    import re
    form = {}
    for select in re.finditer(r'<select name="(map_\d+)".*?</select>', section_html, flags=re.S):
        chosen = re.search(r'<option value="([^"]+)" selected', select.group(0))
        form[select.group(1)] = chosen.group(1) if chosen else "ignore"
    return form


def output_form(section_html: str) -> dict:
    import re
    form = {}
    for select in re.finditer(r'<select name="([a-z_0-9]+)".*?</select>', section_html, flags=re.S):
        chosen = re.search(r'<option value="([^"]*)" selected', select.group(0))
        if chosen:
            form[select.group(1)] = chosen.group(1)
    if 'name="include_header" checked' in section_html:
        form["include_header"] = "on"
    return form


def test_new_client_page_shows_details_and_locked_sections(logged_in):
    page = logged_in.get("/clients/new").text
    assert "Add a client" in page
    assert "Save the client details first." in page
    assert "Not connected yet" in page


def test_details_are_checked_in_plain_words(logged_in):
    response = create(logged_in, name="", contact_email="not-an-email")
    assert response.status_code == 422
    assert "Enter the client&#39;s name." in response.text
    assert "doesn&#39;t look like an email address" in response.text


def test_full_setup_through_the_screens_survives_a_restart(logged_in, demo_files, session):
    # 1. Details
    response = create(logged_in)
    assert response.status_code == 303
    assert response.headers["location"] == "/clients/abccleaningsupplies/setup#catalogue"
    cid = "abccleaningsupplies"

    # 2. Catalogue: upload, check guesses, save
    section = upload(logged_in, cid, "catalogue", demo_files["products"]).text
    assert "81 rows found" in section
    assert "Check our column guesses" in section
    response = logged_in.post(f"/clients/{cid}/setup/files/catalogue/save", data=mapping_form(section), headers=HX)
    assert "Saved 81 products." in response.text
    assert 'hx-swap-oob="true"' in response.text  # step strip updates too

    # Optional band prices
    section = upload(logged_in, cid, "prices", demo_files["prices"]).text
    response = logged_in.post(f"/clients/{cid}/setup/files/prices/save", data=mapping_form(section), headers=HX)
    assert "Saved 20 prices." in response.text

    # 3. Customers
    section = upload(logged_in, cid, "customers", demo_files["customers"]).text
    response = logged_in.post(f"/clients/{cid}/setup/files/customers/save", data=mapping_form(section), headers=HX)
    assert "Saved 15 customers." in response.text
    assert "share the email domain harbourhotels.example" in response.text

    # 4. Output format, with the live preview
    section = upload(logged_in, cid, "output", demo_files["template"]).text
    assert "8 columns found" in section
    assert "AccountRef,CustOrderNo,OrderDate,StockCode,Qty,UnitPrice,DeliveryDate,Notes" in section
    response = logged_in.post(f"/clients/{cid}/setup/output/save", data=output_form(section), headers=HX)
    assert "Output format saved." in response.text

    # 5. Rules
    response = logged_in.post(f"/clients/{cid}/setup/rules", headers=HX, data={
        "cutoff_time": "15:00", "min_order_value": "75", "duplicate_window_days": "7", "minutes_per_order": "6",
        "flag_price_differences": "on", "duplicate_check": "on", "convert_units": "on",
        "unit_0": "CASE", "words_0": "case, cases, cs", "unit_1": "bottle", "words_1": "bottle, bottles, btl",
        "unit_2": "", "words_2": "",
    })
    assert "Rules saved." in response.text

    finish = logged_in.post(f"/clients/{cid}/setup/finish", follow_redirects=False)
    assert finish.status_code == 303 and finish.headers["location"] == "/"

    # Restart: a fresh app and database connection see everything saved.
    db.reset_engine()
    from app.main import create_app
    fresh = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    fresh.post("/login", data={"password": TEST_PASSWORD})
    page = fresh.get(f"/clients/{cid}/setup").text
    assert "81 products saved" in page
    assert "15 customers saved" in page
    assert "20 prices saved" in page
    assert "SalesOrderImport_example.csv" in page
    assert "ABC Cleaning Supplies: settings" in page

    with Session(db.get_engine()) as s:
        client = s.get(Client, cid)
        assert set(client.setup_done) == {"details", "catalogue", "customers", "output", "rules"}
        assert client.rules["min_order_value"] == "75.00"
        assert client.rules["cutoff_time"] == "15:00"
        assert client.minutes_per_order == 6
        assert client.unit_synonyms["bottle"] == ["bottle", "bottles", "btl"]
        mappings = {m.kind: m.mapping for m in s.exec(select(ColumnMapping).where(ColumnMapping.client_id == cid))}
        assert mappings["catalogue"]["Stock Code"] == "product_code"
        assert mappings["customers"]["Order Email"] == "emails"
        assert len(s.exec(select(CatalogueItem).where(CatalogueItem.client_id == cid)).all()) == 81
        assert len(s.exec(select(Customer).where(Customer.client_id == cid)).all()) == 15
        assert len(s.exec(select(PriceListEntry).where(PriceListEntry.client_id == cid)).all()) == 20
        template = s.exec(select(OutputTemplate).where(OutputTemplate.client_id == cid)).one()
        assert template.fill["Qty"] == {"field": "quantity"}
        assert template.date_format == "%d/%m/%Y"

    home = fresh.get("/").text
    assert "ABC Cleaning Supplies" in home
    assert "Shadow trial" in home


def test_save_is_blocked_until_code_and_description_are_chosen(logged_in, demo_files):
    create(logged_in)
    cid = "abccleaningsupplies"
    section = upload(logged_in, cid, "catalogue", demo_files["products"]).text
    form = mapping_form(section)
    form["map_1"] = "ignore"  # Description
    checked = logged_in.post(f"/clients/{cid}/setup/files/catalogue/check", data=form, headers=HX).text
    assert "Choose which column holds: Description" in checked
    assert "disabled" in checked
    saved = logged_in.post(f"/clients/{cid}/setup/files/catalogue/save", data=form, headers=HX).text
    assert "Choose which columns hold: Description." in saved


def test_duplicate_codes_are_shown_before_saving(logged_in, tmp_path):
    create(logged_in)
    cid = "abccleaningsupplies"
    path = tmp_path / "abc_products.csv"
    path.write_text("SKU,Product Name,Price\nA-1,Widget,1.00\nA-1,Widget again,2.00\nA-2,Gadget,3.00\n")
    section = upload(logged_in, cid, "catalogue", path).text
    assert "1 code appear" in section or "1 code appears" in section
    assert "A-1" in section
    saved = logged_in.post(f"/clients/{cid}/setup/files/catalogue/save", data=mapping_form(section), headers=HX).text
    assert "Saved 2 products." in saved
    assert "1 repeated code kept once" in saved


def test_old_excel_files_get_a_plain_message(logged_in, tmp_path):
    create(logged_in)
    path = tmp_path / "old.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0 not really excel")
    section = upload(logged_in, "abccleaningsupplies", "catalogue", path).text
    assert "old-style Excel file" in section


def test_output_preview_follows_the_dropdowns(logged_in, demo_files):
    create(logged_in)
    cid = "abccleaningsupplies"
    section = upload(logged_in, cid, "output", demo_files["template"]).text
    form = output_form(section)
    form.update({"delimiter": ";", "date_format": "%Y-%m-%d", "fill_7": "fixed", "fixed_7": "Web order"})
    preview = logged_in.post(f"/clients/{cid}/setup/output/preview", data=form, headers=HX).text
    assert "AccountRef;CustOrderNo;OrderDate" in preview
    assert ";Web order" in preview
    assert 'name="fixed_7"' in preview


def test_output_example_must_be_csv(logged_in, demo_files):
    create(logged_in)
    section = upload(logged_in, "abccleaningsupplies", "output", demo_files["products"]).text
    assert "needs to be a CSV" in section


def test_rules_errors_are_plain(logged_in):
    create(logged_in)
    response = logged_in.post("/clients/abccleaningsupplies/setup/rules", headers=HX, data={
        "cutoff_time": "3pm", "min_order_value": "lots", "duplicate_window_days": "7", "minutes_per_order": "5",
        "unit_0": "CASE", "words_0": "case, box", "unit_1": "BOX", "words_1": "box, boxes",
    })
    assert "Enter a time like 15:00." in response.text
    assert "Enter an amount in pounds" in response.text
    assert "The word &#39;box&#39; is listed for both" in response.text


def test_pending_upload_survives_a_restart(logged_in, demo_files):
    create(logged_in)
    cid = "abccleaningsupplies"
    upload(logged_in, cid, "catalogue", demo_files["products"])
    db.reset_engine()
    page = logged_in.get(f"/clients/{cid}/setup").text
    assert "81 rows found. Check the guesses below, then save." in page


def test_settings_tab_opens_setup_for_existing_client(logged_in, demo_client):
    page = logged_in.get("/clients/demo/setup").text
    assert "Demo Cleaning Supplies: settings" in page
    assert "81 products saved" in page
    assert logged_in.get("/clients/new?client=demo", follow_redirects=False).headers["location"] == "/clients/demo/setup"


def test_finish_lists_missing_steps(logged_in):
    create(logged_in)
    response = logged_in.post("/clients/abccleaningsupplies/setup/finish")
    assert response.status_code == 422
    assert "Still to do before this client can take orders: Catalogue, Customers, Output format." in response.text
