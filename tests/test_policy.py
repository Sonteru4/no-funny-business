from decimal import Decimal

from tillguard.policy import DecisionKind, PolicyContext, PolicyPack, evaluate


def pack() -> PolicyPack:
    return PolicyPack()


def ctx(**kwargs) -> PolicyContext:
    base = dict(environment="mock")
    base.update(kwargs)
    return PolicyContext(**base)


def test_order_under_limit_allowed():
    decision = evaluate(
        "create_order",
        {"items": [{"name": "Seat", "quantity": 1, "unit_price": 40}]},
        pack(),
        ctx(),
    )
    assert decision.kind == DecisionKind.ALLOW
    assert decision.amount == Decimal("40.00")


def test_order_over_limit_blocked():
    decision = evaluate(
        "create_order",
        {"items": [{"name": "Bulk", "quantity": 20, "unit_price": 40}]},
        pack(),
        ctx(),
    )
    assert decision.kind == DecisionKind.BLOCK
    assert decision.policy_rule == "max_order_amount"
    assert decision.amount == Decimal("800.00")


def test_invoice_over_limit_blocked():
    decision = evaluate(
        "create_invoice",
        {
            "recipient_email": "alex@example.com",
            "items": [{"name": "Retainer", "quantity": 1, "unit_price": 5000}],
        },
        pack(),
        ctx(),
    )
    assert decision.kind == DecisionKind.BLOCK
    assert decision.policy_rule == "max_invoice_amount"


def test_small_refund_auto_approved():
    decision = evaluate(
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 10},
        pack(),
        ctx(known_capture_amount=Decimal("40.00")),
    )
    assert decision.kind == DecisionKind.ALLOW
    assert decision.policy_rule == "refund_auto_approve_limit"


def test_large_refund_needs_approval():
    decision = evaluate(
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 80},
        pack(),
        ctx(known_capture_amount=Decimal("200.00")),
    )
    assert decision.kind == DecisionKind.NEED_APPROVAL
    assert decision.amount == Decimal("80.00")


def test_refund_over_capture_blocked():
    decision = evaluate(
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 50},
        pack(),
        ctx(known_capture_amount=Decimal("40.00"), known_refunded_so_far=Decimal("0.00")),
    )
    assert decision.kind == DecisionKind.BLOCK
    assert decision.policy_rule == "refund_cannot_exceed_capture"


def test_daily_cap_blocks_even_small_refund():
    decision = evaluate(
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 10},
        pack(),
        ctx(
            known_capture_amount=Decimal("40.00"),
            daily_refunds_executed=Decimal("195.00"),
        ),
    )
    assert decision.kind == DecisionKind.BLOCK
    assert decision.policy_rule == "daily_refund_cap"


def test_live_environment_blocked_when_sandbox_only():
    decision = evaluate("list_invoices", {}, pack(), PolicyContext(environment="live"))
    assert decision.kind == DecisionKind.BLOCK
    assert decision.policy_rule == "sandbox_only"


def test_read_tools_allowed():
    decision = evaluate("list_invoices", {}, pack(), ctx())
    assert decision.kind == DecisionKind.ALLOW
