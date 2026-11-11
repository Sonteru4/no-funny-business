from desk.guard import GuardedPaypal
from desk.paypal.mock import MockPayPalBackend
from desk.policy import PolicyPack
from desk.store import AppState, seed_demo_data


def test_same_idempotency_key_does_not_refund_twice():
    state = AppState(mode="mock", policy=PolicyPack())
    seed_demo_data(state)
    guard = GuardedPaypal(MockPayPalBackend(state), state)
    first = guard.invoke(
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 10},
        idempotency_key="tg_refund_demo_10",
    )
    second = guard.invoke(
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 10},
        idempotency_key="tg_refund_demo_10",
    )
    assert first["status"] == "allowed"
    assert second["idempotent_replay"] is True
    assert state.captures["CAPDEMO000001"]["refunded"] == "10.00"
    assert len(state.refunds) == 1
