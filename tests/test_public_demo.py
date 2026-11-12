"""Public demo mode guards: no keys used, per-visitor isolation, chat off, rate limit, health."""

import pytest
from fastapi.testclient import TestClient

import desk.runtime as runtime_mod
import desk.server as server
from desk.config import load_settings
from desk.ratelimit import LIMITER


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("DESK_PUBLIC_DEMO", "1")
    monkeypatch.setenv("PORT", "10123")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-value-for-test")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-value-for-test")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "fake-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "fake-secret")
    runtime_mod._VISITORS.clear()
    LIMITER._hits.clear()
    yield
    runtime_mod._VISITORS.clear()
    LIMITER._hits.clear()


def test_demo_settings_ignore_keys_and_bind_public_port(demo):
    s = load_settings()
    assert s.public_demo and s.mode == "mock"
    assert not s.llm_ready and not s.has_paypal_credentials
    assert s.secret_values() == []
    assert (s.host, s.port) == ("0.0.0.0", 10123)


def test_non_demo_still_binds_localhost(monkeypatch):
    monkeypatch.delenv("DESK_PUBLIC_DEMO", raising=False)
    monkeypatch.delenv("DESK_HOST", raising=False)
    assert load_settings().host == "127.0.0.1"


def test_each_visitor_gets_an_isolated_desk(demo):
    a, b = TestClient(server.app), TestClient(server.app)
    a.get("/api/state")
    b.get("/api/state")
    assert a.cookies.get("desk_visitor") and a.cookies.get("desk_visitor") != b.cookies.get("desk_visitor")
    assert a.post("/api/killswitch", json={"frozen": True}).json()["state"]["frozen"] is True
    a.post("/api/policy/propose", json={"text": "Cap subscription price increases at $10 per day."})
    a.post("/api/policy/apply")
    sa, sb = a.get("/api/state").json(), b.get("/api/state").json()
    assert sa["frozen"] is True and sb["frozen"] is False
    assert sa["policy"]["subscription_daily_increase_cap"] == "10.00"
    assert sb["policy"]["subscription_daily_increase_cap"] is None
    assert sb["public_demo"] is True and sb["provider"] is None
    assert not any(sb["credentials_present"].values())


def test_visitor_desks_expire_and_are_capped(demo, monkeypatch):
    monkeypatch.setattr(runtime_mod, "MAX_VISITORS", 3)
    for i in range(5):
        TestClient(server.app).get("/api/state")
    assert runtime_mod.visitor_count() == 3
    monkeypatch.setattr(runtime_mod, "VISITOR_TTL_SECONDS", -1)
    TestClient(server.app).get("/api/state")
    assert runtime_mod.visitor_count() == 1


def test_walkthrough_catches_all_four_attacks_in_demo(demo):
    c = TestClient(server.app)
    steps = {s["title"]: s["status"] for s in c.post("/api/walkthrough").json()["steps"]}
    assert steps["Stop the $47,316 payout the note asked for"] == "blocked"
    assert steps["After fix: the second +$8 is caught by the daily cap"] == "blocked"
    assert steps["Stop the $1,200 payout to the unconfirmed new email"] == "blocked"
    assert steps["Stop a refund on a capture with an open dispute"] == "blocked"


def test_chat_is_off_and_never_calls_a_model(demo):
    c = TestClient(server.app)
    r = c.post("/api/chat", json={"message": "Pay vendor bill BILL-7001"})
    assert r.status_code == 403


def test_public_rate_limit(demo, monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_DEMO_LIMIT_PER_MIN", 5)
    c = TestClient(server.app)
    codes = [c.get("/api/state").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429


def test_healthz(demo):
    r = TestClient(server.app).get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["public_demo"] is True
