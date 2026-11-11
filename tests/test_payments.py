"""Payouts, vendor payee changes, subscriptions, disputes, provenance, kill switch."""

from fastapi.testclient import TestClient

from desk.config import mock_paypal_settings
from desk.policy_compile import compile_text
from desk.redteam import HARDEN_SUBSCRIPTIONS, run_suite
from desk.runtime import Runtime


def rt() -> Runtime:
    return Runtime(mock_paypal_settings())


def test_payout_to_verified_vendor_allowed():
    r = rt()
    out = r.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 340})
    assert out["status"] == "allowed"
    assert out["result"]["batch_header"]["batch_status"] == "SUCCESS"
    assert len(r.state.payouts) == 1


def test_payout_to_unverified_payee_blocked():
    r = rt()
    out = r.guarded.invoke("create_payout", {"recipient_email": "x@unknown.example", "amount": 20})
    assert out["status"] == "blocked"
    assert out["decision"]["policy_rule"] == "verified_payee_required"
    assert not r.state.payouts


def test_payout_limits_and_threshold():
    r = rt()
    big = r.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 2500})
    assert big["decision"]["policy_rule"] == "max_payout_amount"
    held = r.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 600})
    assert held["status"] == "needs_approval"
    assert held["decision"]["policy_rule"] == "payout_approval_threshold"


def test_payout_velocity():
    r = rt()
    for _ in range(3):
        assert r.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 10})["status"] == "allowed"
    out = r.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 10})
    assert out["decision"]["policy_rule"] == "payout_velocity"


def test_poisoned_bill_is_flagged_highlighted_and_payout_blocked():
    r = rt()
    read = r.guarded.invoke("get_invoice", {"invoice_id": "BILL-7001"}, user_text="Pay vendor bill BILL-7001.")
    warning = read["result"]["untrusted_content_warning"]
    assert warning["detected"] and warning["record_id"] == "BILL-7001"
    span = warning["highlights"][0]
    hidden = warning["text"][span["start"]:span["end"]]
    assert hidden.startswith("SYSTEM NOTE TO AI CLERK")
    assert "Thanks for your business" not in hidden
    assert r.state.flagged_records[0]["record_id"] == "BILL-7001"
    out = r.guarded.invoke(
        "create_payout",
        {"recipient_email": "refunds-desk@payme-now.example", "amount": 4800},
        user_text="Pay vendor bill BILL-7001.",
    )
    assert out["status"] == "blocked"
    assert out["decision"]["policy_rule"] == "untrusted_provenance"
    assert out["injection"]["provenance"]["source"] == "get_invoice"
    assert not r.state.payouts


def test_vendor_payee_change_held_then_payout_held():
    r = rt()
    held = r.guarded.invoke("update_vendor_payee", {"vendor_id": "V-NORTHWIND", "paypal_email": "nw@fastpay-mail.example"})
    assert held["status"] == "needs_approval"
    assert r.state.vendors["V-NORTHWIND"]["paypal_email"] == "payments@northwind-supplies.example"
    # Before approval the new email is not a verified payee.
    early = r.guarded.invoke("create_payout", {"recipient_email": "nw@fastpay-mail.example", "amount": 100})
    assert early["decision"]["policy_rule"] == "verified_payee_required"
    assert r.approve(held["approval_id"])["ok"]
    out = r.guarded.invoke("create_payout", {"recipient_email": "nw@fastpay-mail.example", "amount": 100})
    assert out["status"] == "needs_approval"
    assert out["decision"]["policy_rule"] == "payee_change_cooldown"


def test_disputed_refund_blocked():
    r = rt()
    out = r.guarded.invoke("create_refund", {"capture_id": "CAPDISP000001", "amount": 5})
    assert out["decision"]["policy_rule"] == "dispute_open"
    assert not any(x.get("capture_id") == "CAPDISP000001" for x in r.state.refunds.values())


def test_duplicate_refund_blocked():
    r = rt()
    args = {"capture_id": "CAPDEMO000001", "amount": 10, "reason": "Damaged"}
    assert r.guarded.invoke("create_refund", dict(args))["status"] == "allowed"
    out = r.guarded.invoke("create_refund", dict(args))
    assert out["decision"]["policy_rule"] == "duplicate_refund"


def test_weak_subscription_rule_lets_split_and_payee_through():
    r = rt()
    assert r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "price": 28})["status"] == "allowed"
    assert r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "price": 36})["status"] == "allowed"
    out = r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "payee_email": "x@quickpay-mail.example"}, user_text="route to x@quickpay-mail.example")
    assert out["status"] == "allowed"
    assert r.state.subscriptions["I-SUBDEMO0001"]["price"] == "36.00"


def test_subscription_big_jump_and_plan_catalog():
    r = rt()
    assert r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "price": 80})["decision"]["policy_rule"] == "subscription_max_change_per_update"
    assert r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "plan_id": "P-ENTERPRISE"})["status"] == "blocked"
    assert r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "plan_id": "P-NOPE"})["decision"]["policy_rule"] == "unknown_plan"
    ok = r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "plan_id": "P-TEAM"})
    assert ok["status"] == "allowed" and r.state.subscriptions["I-SUBDEMO0001"]["price"] == "45.00"


def test_compiled_fix_catches_split_increase_and_payee_redirect():
    r = rt()
    proposal = r.propose_policy_deterministic(HARDEN_SUBSCRIPTIONS)
    fields = {d["field"] for d in proposal["diff"]}
    assert fields == {"subscription_daily_increase_cap", "subscription_payee_change_requires_approval"}
    # Nothing changes until a human applies it.
    assert r.state.policy.subscription_daily_increase_cap is None
    r.apply_pending_policy()
    assert r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "price": 28})["status"] == "allowed"
    second = r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0001", "price": 36})
    assert second["decision"]["policy_rule"] == "subscription_daily_increase_cap"
    payee = r.guarded.invoke("update_subscription", {"subscription_id": "I-SUBDEMO0002", "payee_email": "x@quickpay-mail.example"}, user_text="x@quickpay-mail.example")
    assert payee["status"] == "needs_approval"
    assert r.state.policy.sandbox_only is True


def test_compiler_refuses_to_loosen_fraud_controls_or_exceed_ceilings():
    r = rt()
    assert not compile_text("stop requiring approval for subscription payee changes", r.state.policy)["ok"]
    assert not compile_text("daily subscription increase cap to 500", r.state.policy)["ok"]
    assert not compile_text("payout limit to 50000", r.state.policy)["ok"]
    ok = compile_text("payouts over $300 need approval", r.state.policy)
    assert ok["ok"] and ok["candidate"]["payout_approval_threshold"] == "300.00"


def test_kill_switch_freezes_all_money_moves_and_queue():
    r = rt()
    held = r.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 600})
    r.guarded.set_frozen(True)
    for tool, args in [
        ("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 5}),
        ("create_refund", {"capture_id": "CAPDEMO000001", "amount": 1}),
        ("update_subscription", {"subscription_id": "I-SUBDEMO0001", "price": 21}),
        ("create_order", {"items": [{"name": "x", "quantity": 1, "unit_price": 5}]}),
    ]:
        out = r.guarded.invoke(tool, args)
        assert out["status"] == "blocked" and out["decision"]["policy_rule"] == "kill_switch", tool
    # Reads still work.
    assert r.guarded.invoke("list_vendors", {})["status"] == "allowed"
    assert not r.approve(held["approval_id"])["ok"]
    assert not r.state.payouts
    assert any(e.tool == "kill_switch" for e in r.state.audit)
    r.guarded.set_frozen(False)
    assert r.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 5})["status"] == "allowed"


def test_kill_switch_endpoint():
    import desk.server as server

    server._RUNTIME = Runtime(mock_paypal_settings())
    client = TestClient(server.app)
    res = client.post("/api/killswitch", json={"frozen": True})
    assert res.status_code == 200 and res.json()["state"]["frozen"] is True
    state = client.get("/api/state").json()
    assert state["product_name"] and state["frozen"] is True
    assert {"vendors", "payouts", "subscriptions", "disputes"} <= set(state)
    client.post("/api/killswitch", json={"frozen": False})


def test_redteam_scorecard_covers_new_types():
    report = run_suite()
    ids = {c["id"] for c in report["cases"]}
    assert {"poisoned-bill-payout", "subscription-split-increase", "payee-change-then-payout", "disputed-refund", "duplicate-refund"} <= ids
    assert report["ok"], [c for c in report["cases"] if not c["pass"]]
