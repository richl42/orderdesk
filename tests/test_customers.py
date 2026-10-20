"""Pipeline step 3: customer matching, deterministic first, never confident from a name alone."""

from pipeline.customers import CustomerRecord, match_customer

CUSTOMERS = [
    CustomerRecord(1, "RIVERS01", "Riverside Hotel", ["purchasing@riverside-hotel.example"], ["riverside-hotel.example"]),
    CustomerRecord(2, "HARBVW01", "Harbour View Hotel", ["orders@harbourhotels.example"], ["harbourhotels.example"]),
    CustomerRecord(3, "HARBPT01", "Harbour Point Hotel", ["supplies@harbourhotels.example"], ["harbourhotels.example"]),
    CustomerRecord(4, "LARKSP01", "Larkspur Care Home", ["housekeeping@larkspurcare.example"], ["larkspurcare.example"]),
    CustomerRecord(5, "MILLLN01", "Mill Lane Café", ["hello@milllanecafe.example"], ["milllanecafe.example"]),
]


def test_exact_email_is_confident():
    result = match_customer("Purchasing@Riverside-Hotel.example", None, CUSTOMERS)
    assert (result.customer_id, result.method, result.status) == (1, "email", "ready")
    assert result.flags == []


def test_exact_email_wins_even_when_the_domain_is_shared():
    result = match_customer("supplies@harbourhotels.example", None, CUSTOMERS)
    assert (result.customer_id, result.method) == (3, "email")


def test_a_domain_only_one_customer_uses_is_confident():
    result = match_customer("chef@riverside-hotel.example", None, CUSTOMERS)
    assert (result.customer_id, result.method, result.status) == (1, "domain", "ready")


def test_a_shared_domain_alone_is_not_enough():
    result = match_customer("someone@harbourhotels.example", None, CUSTOMERS)
    assert result.customer_id is None and result.status == "problem"
    assert result.flags[0]["code"] == "unknown_customer"


def test_name_in_the_order_is_a_check_never_confident():
    result = match_customer("jo.patel@mailbox.example", "Larkspur Care Home", CUSTOMERS)
    assert (result.customer_id, result.method, result.status) == (4, "name", "check")
    assert result.flags[0]["code"] == "customer_by_name"


def test_a_name_that_could_be_two_customers_is_unknown():
    result = match_customer("jo@mailbox.example", "Harbour Hotel", CUSTOMERS)
    assert result.customer_id is None and result.status == "problem"
    assert {c["name"] for c in result.candidates[:2]} == {"Harbour View Hotel", "Harbour Point Hotel"}


def test_accents_and_case_do_not_matter_for_names():
    assert match_customer(None, "MILL LANE CAFE", CUSTOMERS).customer_id == 5


def test_completely_unknown_offers_the_three_closest_names():
    result = match_customer("office@brightsidelettings.example", "Brightside Lettings", CUSTOMERS)
    assert result.customer_id is None and result.method is None
    assert len(result.candidates) == 3
    assert "Brightside Lettings" in result.flags[0]["message"]


def test_email_of_one_customer_naming_another_is_a_check():
    result = match_customer("purchasing@riverside-hotel.example", "Larkspur Care Home", CUSTOMERS)
    assert (result.customer_id, result.status) == (1, "check")
    assert result.flags[0]["code"] == "customer_conflict"
