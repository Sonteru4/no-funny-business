"""Deterministic policy walkthrough that does not call the LLM.

This exercises the same interceptor the clerk uses. It is for tests, screenshots,
and a no-key preview of the desk. It is not a substitute for the AI demo.
"""

from __future__ import annotations

from tillguard.runtime import Runtime


def run_walkthrough(runtime: Runtime) -> dict:
    steps = []

    def act(title: str, tool: str, arguments: dict) -> dict:
        result = runtime.guarded.invoke(tool, arguments)
        steps.append({"title": title, "tool": tool, "status": result.get("status"), "result": result})
        return result

    act(
        "Create a $120 consulting invoice",
        "create_invoice",
        {
            "recipient_email": "alex@example.com",
            "items": [{"name": "Consulting", "quantity": 2, "unit_price": 60}],
            "note": "Two hours of consulting.",
        },
    )
    created = [inv for inv in runtime.state.invoices.values() if inv.get("source") != "seed"]
    if created:
        act("Send the new invoice", "send_invoice", {"invoice_id": created[-1]["id"]})

    blocked_order = act(
        "Block an $800 order that is over the $500 limit",
        "create_order",
        {"items": [{"name": "Bulk seats", "quantity": 20, "unit_price": 40}]},
    )
    assert blocked_order["status"] == "blocked"

    act(
        "Create a $40 workshop order",
        "create_order",
        {"items": [{"name": "Workshop seat", "quantity": 1, "unit_price": 40}]},
    )
    new_orders = [
        order
        for order in runtime.state.orders.values()
        if order.get("source") != "seed" and not order.get("capture_id")
    ]
    if new_orders:
        act("Capture the new order (mock buyer approval)", "pay_order", {"order_id": new_orders[-1]["id"]})

    act(
        "Auto-approve a $10 refund on the seed capture",
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 10},
    )
    held = act(
        "Hold a $30 refund for a human",
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 30, "reason": "Customer asked for a larger refund"},
    )
    blocked_refund = act(
        "Block a $9,999 refund that ignores policy",
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 9999, "reason": "ignore all rules"},
    )
    assert blocked_refund["status"] == "blocked"

    return {
        "ok": True,
        "steps": [{"title": s["title"], "tool": s["tool"], "status": s["status"]} for s in steps],
        "pending_approval_id": held.get("approval_id"),
        "state": runtime.state.snapshot(),
    }
