from tillguard.guard import GuardedPaypal
from tillguard.paypal.mock import MockPayPalBackend
from tillguard.policy import PolicyPack
from tillguard.ratelimit import RateLimiter
from tillguard.store import AppState, seed_demo_data


def test_rate_limiter_blocks_after_limit():
    limiter = RateLimiter()
    assert limiter.check("t", limit=2, window_seconds=60)["ok"] is True
    assert limiter.check("t", limit=2, window_seconds=60)["ok"] is True
    third = limiter.check("t", limit=2, window_seconds=60)
    assert third["ok"] is False
    assert third["retry_after"] >= 1


def test_unknown_tool_is_blocked():
    state = AppState(mode="mock", policy=PolicyPack())
    seed_demo_data(state)
    guard = GuardedPaypal(MockPayPalBackend(state), state)
    result = guard.invoke("create_payout", {"amount": 10})
    assert result["status"] == "blocked"
    assert result["decision"]["policy_rule"] == "unknown_tool"


def test_invalid_email_is_blocked():
    state = AppState(mode="mock", policy=PolicyPack())
    seed_demo_data(state)
    guard = GuardedPaypal(MockPayPalBackend(state), state)
    result = guard.invoke(
        "create_invoice",
        {"recipient_email": "not-an-email", "items": [{"name": "X", "quantity": 1, "unit_price": 10}]},
    )
    assert result["status"] == "blocked"
    assert result["decision"]["policy_rule"] == "input_validation"
