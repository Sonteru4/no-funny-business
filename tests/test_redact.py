from desk.guard import GuardedPaypal
from desk.paypal.mock import MockPayPalBackend
from desk.policy import PolicyPack
from desk.redact import Redactor
from desk.store import AppState, seed_demo_data


def test_redactor_scrubs_known_secret_and_key_patterns():
    redactor = Redactor(["super-secret-value-123456"])
    text = redactor.redact_text("token=super-secret-value-123456 and sk-abcdefghijklmnopqrstuv")
    assert "super-secret-value-123456" not in text
    assert "sk-abcdefghijklmnopqrstuv" not in text
    assert "[REDACTED]" in text


def test_audit_log_does_not_keep_a_pasted_secret():
    state = AppState(mode="mock", policy=PolicyPack())
    seed_demo_data(state)
    secret = "-".join(["sk", "live", "this", "must", "never", "be", "logged", "999"])  # fake test value
    guard = GuardedPaypal(MockPayPalBackend(state), state, redactor=Redactor([secret]))
    guard.invoke(
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 10, "reason": f"key={secret}"},
    )
    blob = str(state.audit[0].to_dict())
    assert secret not in blob
    assert "sk-live-this-must-never-be-logged-999" not in blob
