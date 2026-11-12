"""Deterministic checks for payouts, subscription changes, vendor payee changes,
and dispute-aware refunds. These run in code, before any PayPal call."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from desk.amounts import money, money_str
from desk.policy import Decision, DecisionKind
from desk.store import PLAN_CATALOG, AppState

EXTENDED_TOOLS = {"create_payout", "update_subscription", "update_vendor_payee"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _decision(kind: DecisionKind, tool: str, reason: str, rule: str, amount: Decimal | None = None, **details) -> Decision:
    return Decision(kind=kind, tool=tool, reason=reason, amount=amount, policy_rule=rule, details=details)


def _vendor_for_email(state: AppState, email: str | None) -> dict[str, Any] | None:
    if not email:
        return None
    lowered = email.strip().lower()
    for vendor in state.vendors.values():
        if str(vendor.get("paypal_email", "")).lower() == lowered:
            return vendor
    return None


def evaluate_payout(arguments: dict[str, Any], state: AppState) -> Decision:
    tool = "create_payout"
    pack = state.policy
    email = arguments.get("recipient_email")
    try:
        amount = money(arguments.get("amount"))
    except Exception:
        return _decision(DecisionKind.BLOCK, tool, "A payout needs a valid amount.", "input_validation")
    if amount <= 0:
        return _decision(DecisionKind.BLOCK, tool, "A payout amount must be positive.", "input_validation")
    if amount > pack.max_payout_amount:
        return _decision(
            DecisionKind.BLOCK, tool,
            f"Payout {money_str(amount)} USD is above the {money_str(pack.max_payout_amount)} USD payout limit.",
            "max_payout_amount", amount,
        )
    vendor = _vendor_for_email(state, email)
    if vendor is None:
        return _decision(
            DecisionKind.BLOCK, tool,
            f"Payee {email} is not a verified vendor on this desk. Payouts only go to verified payees.",
            "verified_payee_required", amount,
        )
    window = timedelta(hours=1)
    recent = [
        p for p in state.payouts.values()
        if (_parse(p.get("created_at")) or _now() - timedelta(days=1)) > _now() - window
    ]
    if len(recent) >= pack.payout_velocity_per_hour:
        return _decision(
            DecisionKind.BLOCK, tool,
            f"Payout velocity check: {len(recent)} payouts in the last hour already meets the limit of {pack.payout_velocity_per_hour}.",
            "payout_velocity", amount,
        )
    changed = _parse(vendor.get("payee_changed_at"))
    if changed and changed > _now() - timedelta(hours=pack.payee_change_cooldown_hours):
        minutes = int((_now() - changed).total_seconds() // 60)
        return _decision(
            DecisionKind.NEED_APPROVAL, tool,
            f"Payee details for {vendor['name']} changed {minutes} minutes ago. Payouts within "
            f"{pack.payee_change_cooldown_hours} hours of a payee change wait for a human. Nothing was sent to PayPal.",
            "payee_change_cooldown", amount, vendor_id=vendor["id"],
        )
    if amount >= pack.payout_approval_threshold:
        return _decision(
            DecisionKind.NEED_APPROVAL, tool,
            f"Payout {money_str(amount)} USD to {vendor['name']} is at or above the "
            f"{money_str(pack.payout_approval_threshold)} USD approval threshold. It has not been sent to PayPal.",
            "payout_approval_threshold", amount, vendor_id=vendor["id"],
        )
    return _decision(
        DecisionKind.ALLOW, tool,
        f"Payout {money_str(amount)} USD to verified vendor {vendor['name']} is within policy.",
        "payout_within_policy", amount, vendor_id=vendor["id"],
    )


def evaluate_vendor_payee(arguments: dict[str, Any], state: AppState) -> Decision:
    tool = "update_vendor_payee"
    vendor = state.vendors.get(arguments.get("vendor_id") or "")
    if vendor is None:
        return _decision(DecisionKind.BLOCK, tool, f"Vendor {arguments.get('vendor_id')} was not found.", "unknown_vendor")
    new_email = arguments.get("paypal_email")
    if str(new_email or "").lower() == str(vendor.get("paypal_email", "")).lower():
        return _decision(DecisionKind.ALLOW, tool, "Payee details are unchanged.", "payee_unchanged")
    return _decision(
        DecisionKind.NEED_APPROVAL, tool,
        f"Changing the payout email for {vendor['name']} is a common fraud pattern. A human must confirm it "
        f"out of band. After approval, payouts to this vendor wait for a human for {state.policy.payee_change_cooldown_hours} hours.",
        "payee_change_requires_approval", vendor_id=vendor["id"],
    )


def evaluate_subscription(arguments: dict[str, Any], state: AppState) -> Decision:
    tool = "update_subscription"
    pack = state.policy
    sub = state.subscriptions.get(arguments.get("subscription_id") or "")
    if sub is None:
        return _decision(DecisionKind.BLOCK, tool, f"Subscription {arguments.get('subscription_id')} was not found.", "unknown_subscription")
    current = money(sub["price"])
    new_price = current
    if arguments.get("plan_id"):
        plan = PLAN_CATALOG.get(arguments["plan_id"])
        if plan is None:
            return _decision(DecisionKind.BLOCK, tool, f"Plan {arguments['plan_id']} is not in the catalog.", "unknown_plan")
        new_price = money(plan["price"])
    if arguments.get("price") not in (None, ""):
        new_price = money(arguments["price"])
    if new_price < 0:
        return _decision(DecisionKind.BLOCK, tool, "A subscription price cannot be negative.", "input_validation")
    delta = money(new_price - current)
    if delta > pack.subscription_max_change_per_update:
        return _decision(
            DecisionKind.BLOCK, tool,
            f"Price change of {money_str(delta)} USD is above the {money_str(pack.subscription_max_change_per_update)} USD per-update limit.",
            "subscription_max_change_per_update", delta,
        )
    if pack.subscription_daily_increase_cap is not None and delta > 0:
        today = _now().strftime("%Y-%m-%d")
        so_far = sum(
            (money(c["delta"]) for c in state.subscription_changes
             if c["subscription_id"] == sub["id"] and str(c.get("ts", "")).startswith(today) and money(c["delta"]) > 0),
            Decimal("0.00"),
        )
        if so_far + delta > pack.subscription_daily_increase_cap:
            return _decision(
                DecisionKind.BLOCK, tool,
                f"Split-increase check: this change would bring today's increases on {sub['id']} to "
                f"{money_str(so_far + delta)} USD, above the {money_str(pack.subscription_daily_increase_cap)} USD daily cap.",
                "subscription_daily_increase_cap", delta,
            )
    payee = arguments.get("payee_email")
    if payee and str(payee).lower() != str(sub.get("payee_email", "")).lower():
        if pack.subscription_payee_change_requires_approval:
            return _decision(
                DecisionKind.NEED_APPROVAL, tool,
                f"Changing the billing payee on {sub['id']} to {payee} redirects future payments. A human must approve it.",
                "subscription_payee_change_requires_approval", delta,
            )
    return _decision(
        DecisionKind.ALLOW, tool,
        f"Subscription change on {sub['id']} passed the current rules (price change {money_str(delta)} USD).",
        "subscription_rules", delta,
    )


def evaluate_extended(tool: str, arguments: dict[str, Any], state: AppState) -> Decision:
    if tool == "create_payout":
        return evaluate_payout(arguments, state)
    if tool == "update_vendor_payee":
        return evaluate_vendor_payee(arguments, state)
    return evaluate_subscription(arguments, state)


def refund_anomaly(arguments: dict[str, Any], state: AppState) -> Decision | None:
    """Block refunds on captures with an open dispute, and duplicate refunds."""
    capture_id = arguments.get("capture_id")
    if not capture_id:
        return None
    for dispute in state.disputes.values():
        if dispute.get("capture_id") == capture_id and dispute.get("status") in {"OPEN", "WAITING_FOR_SELLER_RESPONSE", "UNDER_REVIEW"}:
            return _decision(
                DecisionKind.BLOCK, "create_refund",
                f"Capture {capture_id} has an open PayPal dispute ({dispute['id']}). Refunding now could pay the buyer twice "
                "if the dispute also resolves in their favor. Respond to the dispute instead.",
                "dispute_open", dispute_id=dispute["id"],
            )
    capture = state.captures.get(capture_id) or {}
    paid_at = _parse(capture.get("captured_at"))
    window_days = state.policy.refund_window_days
    if paid_at is not None and paid_at < _now() - timedelta(days=window_days):
        age = (_now() - paid_at).days
        requested = arguments.get("amount")
        amount = money(requested) if requested not in (None, "") else (money(capture["amount"]) if capture.get("amount") else None)
        return _decision(
            DecisionKind.BLOCK, "create_refund",
            f"Posted refund policy: refunds within {window_days} days of payment. Capture {capture_id} was paid "
            f"{age} days ago. A promise quoted from a chat does not change the posted policy, and the clerk cannot "
            "create a new one. Any exception is the merchant's decision, outside the clerk.",
            "refund_window", amount, paid_days_ago=age, refund_window_days=window_days,
        )
    if arguments.get("amount") in (None, ""):
        return None
    amount = money(arguments["amount"])
    window = timedelta(hours=state.policy.duplicate_refund_window_hours)
    for refund in state.refunds.values():
        if refund.get("capture_id") != capture_id or money(refund.get("amount") or 0) != amount:
            continue
        created = _parse(refund.get("created_at")) or _now()
        if created > _now() - window:
            return _decision(
                DecisionKind.BLOCK, "create_refund",
                f"Duplicate check: a {money_str(amount)} USD refund on {capture_id} already ran ({refund['id']}) "
                f"within {state.policy.duplicate_refund_window_hours} hours.",
                "duplicate_refund", amount,
            )
    return None
