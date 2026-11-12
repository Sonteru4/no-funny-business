"""Money the guard kept from leaving the desk, computed from the signed audit log.

This is the number behind the on-screen "stopped or held" counter. It is derived
only from guard decisions that were actually recorded, never typed in by hand.

Rules:
- Only outgoing money counts: payouts and refunds.
- "stopped" = a blocked decision, or a held request a human later denied.
- "held" = a request waiting on a human (approval still pending).
- A held request that a human approved is not counted: that money went out by choice.
- Each distinct attempt counts once. A retry with the same idempotency key is not double counted.
- Subscription and payee changes carry no dollar amount, so they are counted as events, not dollars.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

OUTFLOW_TOOLS = {"create_payout", "create_refund", "record_refund_for_invoice"}
CHANGE_TOOLS = {"update_subscription", "update_vendor_payee"}


def _amount(entry: Any) -> Decimal | None:
    raw = entry.amount
    if raw is None and isinstance(entry.arguments, dict):
        raw = entry.arguments.get("amount")
    if raw is None:
        return None
    try:
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def compute_impact(state: Any) -> dict[str, Any]:
    approvals = {a.id: a.status for a in getattr(state, "approvals", [])}
    stopped = Decimal("0.00")
    held = Decimal("0.00")
    items: list[dict[str, Any]] = []
    changes_stopped = 0
    changes_held = 0
    seen: set[str] = set()

    # Oldest first, so the running total grows in the order things happened.
    for entry in reversed(list(getattr(state, "audit", []))):
        if entry.decision not in {"blocked", "needs_approval"}:
            continue
        key = entry.idempotency_key or entry.id
        if key in seen:
            continue
        seen.add(key)

        outcome = "stopped" if entry.decision == "blocked" else "held"
        if outcome == "held" and entry.approval_id:
            status = approvals.get(entry.approval_id, "pending")
            if status in {"approved", "executed"}:
                continue
            if status in {"denied", "failed"}:
                outcome = "stopped"

        if entry.tool in CHANGE_TOOLS:
            if outcome == "stopped":
                changes_stopped += 1
            else:
                changes_held += 1
            continue
        if entry.tool not in OUTFLOW_TOOLS:
            continue
        amount = _amount(entry)
        if amount is None or amount <= 0:
            continue
        if outcome == "stopped":
            stopped += amount
        else:
            held += amount
        items.append({
            "audit_id": entry.id,
            "tool": entry.tool,
            "outcome": outcome,
            "amount": f"{amount:.2f}",
            "rule": entry.policy_rule,
            "running_total": f"{(stopped + held):.2f}",
        })

    return {
        "currency": "USD",
        "stopped": f"{stopped:.2f}",
        "held": f"{held:.2f}",
        "total": f"{(stopped + held):.2f}",
        "items": items,
        "changes_stopped": changes_stopped,
        "changes_held": changes_held,
        "basis": "Computed from signed audit entries for payouts and refunds. Mock PayPal unless sandbox keys are set.",
    }
