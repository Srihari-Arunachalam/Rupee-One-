import itertools

import pytest
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)
_k = itertools.count()


@pytest.fixture(autouse=True)
def fresh():
    main.reset()


def pay(payee="Suresh", amount=500, key=None, **kw):
    body = {"payer": "Ravi", "payee": payee, "amount": amount, **kw}
    return client.post("/payments", json=body, headers={"Idempotency-Key": key or f"k{next(_k)}"})


def test_happy_path_settles_on_cheapest_fast_rail():
    r = pay()
    assert r.status_code == 201
    j = r.json()
    assert j["status"] == "SETTLED" and j["rail"] == "UPI"
    assert [t["to"] for t in j["timeline"]] == ["VALIDATED", "SCREENED", "ROUTED", "SETTLED"]


def test_idempotent_replay_returns_same_payment_no_double_debit():
    a = pay(key="same")
    b = pay(key="same")
    assert a.json()["id"] == b.json()["id"]
    assert b.headers["Idempotent-Replay"] == "true"
    assert len(main.LEDGER) == 1


def test_same_key_different_body_is_rejected():
    pay(key="x", amount=500)
    r = pay(key="x", amount=900)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_intent_fingerprint_catches_duplicate_with_new_key():
    pay(key="a")
    r = pay(key="b")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "POSSIBLE_DUPLICATE"
    assert pay(key="c", confirm_duplicate=True).status_code == 201


def test_sanctions_fuzzy_match_blocks_misspelt_name():
    j = pay(payee="Ahmad Khan Aly").json()
    assert j["status"] in ("BLOCKED", "HELD")
    assert "Sanctions" in j["compliance"]["reason"] or "sanctions" in j["compliance"]["reason"]


def test_exact_sanctions_name_is_blocked():
    assert pay(payee="Viktor Petrov").json()["status"] == "BLOCKED"


def test_fraud_review_then_officer_approves():
    j = pay(amount=300_000, hour=2).json()
    assert j["status"] == "HELD"
    assert any(e["payment"] == j["id"] for e in client.get("/exceptions").json())
    done = client.post(f"/exceptions/{j['id']}/resolve", params={"approve": True}).json()
    assert done["status"] == "SETTLED"


def test_rail_failure_falls_back_to_next_rail():
    client.post("/chaos/rails/UPI", params={"down": True})
    j = pay().json()
    assert j["status"] == "SETTLED" and j["rail"] == "IMPS"
    assert j["attempts"][0] == {"rail": "UPI", "result": "rail down"}


def test_all_rails_down_fails_and_queues_exception():
    for r in main.RAILS:
        client.post(f"/chaos/rails/{r}", params={"down": True})
    j = pay().json()
    assert j["status"] == "FAILED"
    assert main.EXCEPTIONS[0]["type"] == "ROUTING_FAILED"


def test_circuit_breaker_opens_after_three_failures():
    client.post("/chaos/rails/UPI", params={"down": True})
    for i in range(3):
        pay(payee=f"P{i}")
    assert main.RAIL_STATE["UPI"]["open_until"] > 0
    j = pay(payee="P9").json()
    assert j["attempts"][0]["result"] == "circuit open"


def test_deadline_aware_router_skips_slow_neft():
    j = pay(amount=150_000, deadline_seconds=60, hour=12).json()
    assert j["rail"] == "IMPS"  # UPI over limit, NEFT too slow for 60s deadline
    slow = pay(payee="Other", amount=150_000, deadline_seconds=7200, hour=12).json()
    assert slow["rail"] == "NEFT"  # relaxed deadline -> cheapest


def test_illegal_transition_blocked():
    j = pay().json()
    with pytest.raises(ValueError):
        main.move(main.PAYMENTS[j["id"]], "VALIDATED", "nope")


def test_audit_chain_valid_then_detects_tampering():
    pay()
    assert client.get("/audit/verify").json()["valid"] is True
    main.AUDIT[1]["data"]["to"] = "TAMPERED"
    assert client.get("/audit/verify").json()["valid"] is False


def test_reconciliation_finds_missing_mismatch_duplicate_unknown():
    ids = [pay(payee=f"P{i}", amount=1000).json()["id"] for i in range(4)]
    rows = [
        {"reference": ids[0], "amount": 1000},
        {"reference": ids[1], "amount": 998.82},
        {"reference": ids[1], "amount": 998.82},
        {"reference": "PAY-GHOST", "amount": 50},
    ]
    issues = {i["type"] for i in client.post("/reconcile", json=rows).json()["issues"]}
    assert issues == {"AMOUNT_MISMATCH", "DUPLICATE", "UNKNOWN_AT_BANK", "MISSING_AT_BANK"}
