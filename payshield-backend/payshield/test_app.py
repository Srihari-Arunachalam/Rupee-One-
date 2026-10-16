import itertools

import pytest
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)
_k = itertools.count()


@pytest.fixture(autouse=True)
def fresh():
    main.reset()


def pay(payee="Priya", amount=1000, payer="Ravi", **kw):
    return client.post("/payments", json={"payer": payer, "payee": payee, "amount": amount, **kw},
                       headers={"Idempotency-Key": f"a{next(_k)}"})


def test_quote_recommends_cheapest_rail_meeting_deadline():
    q = client.post("/quote", json={"amount": 500, "deadline_seconds": 60}).json()
    assert q["recommended"] == "UPI" and q["meets_deadline"]
    assert q["options"][0]["confidence"] > 0.9
    q2 = client.post("/quote", json={"amount": 150_000, "deadline_seconds": 60}).json()
    assert q2["recommended"] == "IMPS"  # UPI over its limit


def test_quote_reflects_rail_outage_and_unmeetable_deadline():
    client.post("/chaos/rails/UPI", params={"down": True})
    q = client.post("/quote", json={"amount": 500, "deadline_seconds": 60}).json()
    assert q["recommended"] == "IMPS"
    assert next(o for o in q["options"] if o["rail"] == "UPI")["confidence"] == 0
    tight = client.post("/quote", json={"amount": 500, "deadline_seconds": 1}).json()
    assert tight["meets_deadline"] is False and "No rail can meet" in tight["summary"]


def test_quote_503_when_every_rail_is_down():
    for r in main.RAILS:
        client.post(f"/chaos/rails/{r}", params={"down": True})
    assert client.post("/quote", json={"amount": 500}).status_code == 503


def test_rails_banner_explains_reroute():
    assert client.get("/rails").json()["banner"] is None
    client.post("/chaos/rails/UPI", params={"down": True})
    assert "rerouted to IMPS" in client.get("/rails").json()["banner"]


def test_wallet_moves_money_only_on_settlement():
    pay(amount=1000)
    assert client.get("/users/ravi").json()["balance"] == 999_000
    assert client.get("/users/priya").json()["balance"] == 101_000


def test_blocked_payment_takes_no_money():
    pay(payee="Viktor Petrov")
    assert client.get("/users/ravi").json()["balance"] == 1_000_000


def test_insufficient_funds_rejected_before_anything_happens():
    r = pay(payer="Priya", amount=500_000)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INSUFFICIENT_FUNDS"
    assert main.PAYMENTS == {}


@pytest.mark.parametrize("raw,typ", [
    ("Suresh Kumar", "NAME"), ("Suresh.K@okhdfc", "UPI"), ("98765 43210", "PHONE"),
    ("+91 98765-43210", "PHONE"), ("123456789012:HDFC0001234", "ACCOUNT")])
def test_payee_formats_normalise(raw, typ):
    assert main.normalize_payee(raw)["type"] == typ


@pytest.mark.parametrize("raw", ["12345", "abc@", "123456789012:BADIFSC", "1234567890"])
def test_invalid_payees_rejected(raw):
    r = pay(payee=raw)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_PAYEE"


def test_upi_handle_is_still_sanctions_screened():
    assert pay(payee="viktor.petrov@okbank").json()["status"] == "BLOCKED"


def test_passport_plain_language_and_steps():
    j = pay().json()
    v = client.get(f"/payments/{j['id']}/passport").json()
    assert v["headline"] == "Delivered via UPI"
    assert [s["done"] for s in v["steps"]] == [True, True, True, True]
    held = pay(amount=300_000, hour=2).json()
    hv = client.get(f"/payments/{held['id']}/passport").json()
    assert hv["headline"] == "Under review" and [s["done"] for s in hv["steps"]] == [True, True, False, False]
    assert "officer" in hv["next_step"]


def test_public_tracking_link_hides_payer_and_internal_scores():
    j = pay().json()
    t = client.get(f"/track/{j['share_token']}").json()
    assert t["status"] == "SETTLED" and "Ravi" not in str(t) and "compliance" not in t
    held = pay(amount=300_000, hour=2).json()
    assert client.get(f"/track/{held['share_token']}").json()["reason"] is None
    assert client.get("/track/nope").status_code == 404


def test_history_lists_users_payments_newest_first():
    a, b = pay(amount=100).json(), pay(payee="Meena", amount=200).json()
    h = client.get("/users/ravi/payments").json()
    assert [x["id"] for x in h] == [b["id"], a["id"]]


def _ids(n):
    return [pay(payee=f"P{i}", amount=1000).json()["id"] for i in range(n)]


def test_csv_statement_autodetected_and_reconciled():
    ids = _ids(4)
    csv = (f"date,reference,amount\n15-10-2026,{ids[0]},1000.00\n15-10-2026,{ids[1]},998.82\n"
           f"15-10-2026,{ids[1]},998.82\n15-10-2026,PAY-GHOST01,50.00\n")
    r = client.post("/statements/check", json={"text": csv}).json()
    assert r["detected"]["format"] == "csv" and r["detected"]["has_header"]
    assert r["detected"]["columns"]["reference"]["index"] == 1
    assert {i["type"] for i in r["issues"]} == {"AMOUNT_MISMATCH", "DUPLICATE", "UNKNOWN_AT_BANK", "MISSING_AT_BANK"}


def test_fixed_width_statement_autodetected_without_any_rule_sheet():
    ids = _ids(3)
    rows = [(ids[0], "1000.00"), (ids[1], "998.82")]
    text = "\n".join(f"{'15-10-2026':<12}{ref:<14}{amt:>10}" for ref, amt in rows)
    r = client.post("/statements/check", json={"text": text}).json()
    assert r["detected"]["format"] == "fixed-width"
    cols = r["detected"]["columns"]
    assert (cols["date"]["index"], cols["reference"]["index"], cols["amount"]["index"]) == (0, 1, 2)
    types = {i["type"] for i in r["issues"]}
    assert "AMOUNT_MISMATCH" in types and "MISSING_AT_BANK" in types


def test_unrecognised_statement_gives_clear_error():
    r = client.post("/statements/check", json={"text": "hello world\nfoo bar"})
    assert r.status_code == 422

