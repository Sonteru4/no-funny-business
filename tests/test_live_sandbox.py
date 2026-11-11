"""Live sandbox handlers with mocked HTTP. No credentials or network needed."""

import json

import pytest

from desk.config import Settings
from desk.paypal import live as live_mod
from desk.runtime import Runtime


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


@pytest.fixture
def http(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, data=None, auth=None, timeout=None):
        calls.append({"method": "POST", "url": url, "headers": headers or {}, "json": json})
        if url.endswith("/v1/oauth2/token"):
            return FakeResponse(200, {"access_token": "test-token"})
        if url.endswith("/v1/payments/payouts"):
            return FakeResponse(201, {"batch_header": {"payout_batch_id": "SANDBOXBATCH1", "batch_status": "PENDING"}})
        return FakeResponse(404, {"name": "NOT_FOUND"})

    def fake_get(url, headers=None, timeout=None):
        calls.append({"method": "GET", "url": url, "headers": headers or {}})
        if "/v1/customer/disputes" in url and url.rstrip("/").endswith("PP-D-SBX1"):
            return FakeResponse(200, {
                "dispute_id": "PP-D-SBX1", "status": "OPEN", "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                "disputed_transactions": [{"seller_transaction_id": "SBXCAP1"}],
                "dispute_amount": {"value": "25.00", "currency_code": "USD"},
            })
        if "/v1/customer/disputes" in url:
            return FakeResponse(200, {"items": [{
                "dispute_id": "PP-D-SBX1", "status": "OPEN", "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                "disputed_transactions": [{"seller_transaction_id": "SBXCAP1"}],
                "dispute_amount": {"value": "25.00", "currency_code": "USD"},
            }]})
        return FakeResponse(404, {"name": "NOT_FOUND"})

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    # Skip the toolkit client so no other transport is touched.
    monkeypatch.setattr(live_mod.LivePayPalBackend, "_init_clients", lambda self: setattr(self, "transport", "paypal-rest") or setattr(self, "_client", self._rest))
    return calls


def sandbox_runtime() -> Runtime:
    settings = Settings(
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        paypal_client_id="test-client-id",
        paypal_client_secret="test-client-secret",
        paypal_mode_override="sandbox",
        host="127.0.0.1",
        port=8000,
    )
    rt = Runtime(settings)
    assert rt.state.mode == "sandbox"
    return rt


def payout_calls(calls):
    return [c for c in calls if c["url"].endswith("/v1/payments/payouts")]


def test_poisoned_note_payout_blocked_before_any_http(http):
    rt = sandbox_runtime()
    read = rt.guarded.invoke("get_invoice", {"invoice_id": "BILL-7001"}, user_text="Pay vendor bill BILL-7001.")
    assert read["result"]["transport"] == "local-merchant-records"
    assert read["result"]["untrusted_content_warning"]["detected"]
    out = rt.guarded.invoke(
        "create_payout",
        {"recipient_email": "refunds-desk@payme-now.example", "amount": 4800},
        user_text="Pay vendor bill BILL-7001.",
    )
    assert out["status"] == "blocked"
    assert out["decision"]["policy_rule"] == "untrusted_provenance"
    assert http == []  # nothing, not even an OAuth call, reached PayPal


def test_unverified_and_oversized_payouts_never_hit_http(http):
    rt = sandbox_runtime()
    assert rt.guarded.invoke("create_payout", {"recipient_email": "x@unknown.example", "amount": 5})["status"] == "blocked"
    assert rt.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 9000})["status"] == "blocked"
    held = rt.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 900})
    assert held["status"] == "needs_approval"
    assert payout_calls(http) == []


def test_clean_payout_posts_payouts_api_with_idempotency(http):
    rt = sandbox_runtime()
    out = rt.guarded.invoke(
        "create_payout",
        {"recipient_email": "ap@bluebird-print.example", "amount": 340, "invoice_id": "BILL-7001"},
        idempotency_key="tg_test_payout_0001",
    )
    assert out["status"] == "allowed", out
    calls = payout_calls(http)
    assert len(calls) == 1
    body = calls[0]["json"]
    assert calls[0]["url"] == "https://api-m.sandbox.paypal.com/v1/payments/payouts"
    assert calls[0]["headers"]["PayPal-Request-Id"] == "tg_test_payout_0001"
    assert body["sender_batch_header"]["sender_batch_id"] == "tg_test_payout_0001"
    assert body["items"][0] == {
        "recipient_type": "EMAIL",
        "amount": {"value": "340.00", "currency": "USD"},
        "receiver": "ap@bluebird-print.example",
        "note": "Vendor payment",
        "sender_item_id": "BILL-7001",
    }
    assert rt.state.payouts["SANDBOXBATCH1"]["source"] == "sandbox"
    # Replaying the same key does not post again.
    rt.guarded.invoke("create_payout", {"recipient_email": "ap@bluebird-print.example", "amount": 340}, idempotency_key="tg_test_payout_0001")
    assert len(payout_calls(http)) == 1


def test_live_disputes_feed_the_refund_check(http):
    rt = sandbox_runtime()
    listed = rt.guarded.invoke("list_disputes", {})
    assert listed["result"]["disputes"][0]["capture_id"] == "SBXCAP1"
    got = rt.guarded.invoke("get_dispute", {"dispute_id": "PP-D-SBX1"})
    assert got["result"]["dispute"]["status"] == "OPEN"
    refund = rt.guarded.invoke("create_refund", {"capture_id": "SBXCAP1", "amount": 25})
    assert refund["status"] == "blocked" and refund["decision"]["policy_rule"] == "dispute_open"
    assert not [c for c in http if "/refund" in c["url"]]


def test_sandbox_client_never_targets_production():
    client = live_mod.RestPayPalClient("a", "b", sandbox=False)
    assert client.base_url == "https://api-m.sandbox.paypal.com"
