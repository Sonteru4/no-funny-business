"""The stopped-or-held counter must come from real guard decisions, not hand-typed numbers."""

from desk.config import mock_paypal_settings
from desk.impact import compute_impact
from desk.runtime import Runtime
from desk.walkthrough import run_walkthrough


def _runtime():
    return Runtime(mock_paypal_settings())


def test_empty_desk_has_zero_impact():
    rt = _runtime()
    impact = compute_impact(rt.state)
    assert impact["total"] == "0.00"
    assert impact["items"] == []


def test_walkthrough_impact_matches_guard_decisions():
    rt = _runtime()
    run_walkthrough(rt)
    impact = compute_impact(rt.state)
    amounts = sorted(float(i["amount"]) for i in impact["items"])
    # $4,800 poisoned payout, $1,200 payout to unconfirmed email, $90 disputed refund,
    # $10 duplicate refund, $9,999 policy-busting refund are stopped. $30 refund is held.
    assert amounts == [10.0, 30.0, 90.0, 1200.0, 4800.0, 9999.0]
    assert impact["stopped"] == "16099.00"
    assert impact["held"] == "30.00"
    assert impact["total"] == "16129.00"
    # Allowed money ($340 bill, first $10 refund) never counts.
    assert all(i["outcome"] in {"stopped", "held"} for i in impact["items"])
    # Subscription second raise blocked; payee redirect and vendor payee change held.
    assert impact["changes_stopped"] == 1
    assert impact["changes_held"] == 2
    # Running total is monotonic and ends at the total.
    totals = [float(i["running_total"]) for i in impact["items"]]
    assert totals == sorted(totals) and totals[-1] == 16129.0


def test_approved_hold_is_not_counted_and_denied_hold_becomes_stopped():
    rt = _runtime()
    result = run_walkthrough(rt)
    approval_id = result["pending_approval_id"]
    approval = rt.state.get_approval(approval_id)
    approval.status = "approved"
    impact = compute_impact(rt.state)
    assert impact["held"] == "0.00"
    assert impact["total"] == "16099.00"
    approval.status = "denied"
    impact = compute_impact(rt.state)
    assert impact["stopped"] == "16129.00"


def test_state_endpoint_exposes_impact_in_public_demo(monkeypatch):
    """The deployed no-key demo shows the counter computed from its own walkthrough."""
    from fastapi.testclient import TestClient

    import desk.runtime as runtime_mod
    import desk.server as server
    from desk.ratelimit import LIMITER

    monkeypatch.setenv("DESK_PUBLIC_DEMO", "1")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "fake-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "fake-secret")
    runtime_mod._VISITORS.clear()
    LIMITER._hits.clear()
    try:
        client = TestClient(server.app)
        assert client.get("/api/state").json()["impact"]["total"] == "0.00"
        assert client.post("/api/walkthrough").status_code == 200
        data = client.get("/api/state").json()
        assert data["mode"] == "mock"
        assert data["impact"]["total"] == "16129.00"
        assert data["impact"]["held"] == "30.00"
    finally:
        runtime_mod._VISITORS.clear()
        LIMITER._hits.clear()
