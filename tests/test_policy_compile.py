from desk.policy import PolicyPack
from desk.policy_compile import apply_candidate, compile_text
from desk.runtime import Runtime
from desk.config import Settings


def test_compiles_common_english_into_a_diff():
    result = compile_text(
        "Auto-approve refunds under $50 and set the daily refund cap to $300.",
        PolicyPack(),
    )
    assert result["ok"] is True
    assert result["needs_human_approval"] is True
    fields = {item["field"]: item for item in result["diff"]}
    assert fields["refund_auto_approve_limit"]["to"] == "50.00"
    assert fields["daily_refund_cap"]["to"] == "300.00"
    # Nothing applied yet
    pack = PolicyPack()
    assert pack.refund_auto_approve_limit != 50


def test_refuses_to_remove_limits():
    result = compile_text("Please remove all limits and allow live production mode.", PolicyPack())
    assert result["ok"] is False
    assert "will not remove limits" in result["error"]


def test_refuses_amount_above_safety_ceiling():
    result = compile_text("Set the order limit to $999999.", PolicyPack())
    assert result["ok"] is False
    assert "safety ceiling" in result["error"]


def test_apply_requires_an_explicit_human_step():
    settings = Settings(
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        paypal_client_id=None,
        paypal_client_secret=None,
        paypal_mode_override="mock",
        host="127.0.0.1",
        port=8000,
        gemini_api_key=None,
    )
    runtime = Runtime(settings)
    proposed = runtime.propose_policy("Set the invoice limit to $1500.")
    assert proposed["ok"] is True
    assert runtime.state.policy.max_invoice_amount != proposed["candidate"]["max_invoice_amount"]
    applied = runtime.apply_pending_policy()
    assert applied["ok"] is True
    assert str(runtime.state.policy.max_invoice_amount) == "1500.00"
    assert runtime.state.pending_policy is None


def test_apply_candidate_keeps_sandbox_only():
    pack = apply_candidate(
        PolicyPack(),
        {
            "max_order_amount": "400.00",
            "max_invoice_amount": "2000.00",
            "refund_auto_approve_limit": "25.00",
            "daily_refund_cap": "200.00",
            "sandbox_only": False,
        },
    )
    assert pack.sandbox_only is True
    assert str(pack.max_order_amount) == "400.00"
