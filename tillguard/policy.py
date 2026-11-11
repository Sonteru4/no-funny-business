"""Hard policy checks that run before any PayPal tool call is executed."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from tillguard.amounts import extract_amount, money, money_str


class DecisionKind(str, Enum):
    ALLOW = "allowed"
    BLOCK = "blocked"
    NEED_APPROVAL = "needs_approval"


# Tools that move or commit money, and how the policy treats them.
MONEY_TOOLS = {
    "create_order": "order",
    "create_invoice": "invoice",
    "pay_order": "capture",
    "create_refund": "refund",
    "record_refund_for_invoice": "refund",
}

READ_ONLY_TOOLS = {
    "get_order",
    "list_invoices",
    "get_invoice",
    "send_invoice",
    "list_policy",
    "list_pending_approvals",
}

# Least-privilege scopes. Unknown tools are denied in the interceptor.
DEFAULT_SCOPES = ("invoices", "orders", "refunds", "policy")
TOOL_SCOPES = {
    "create_order": "orders",
    "get_order": "orders",
    "pay_order": "orders",
    "create_invoice": "invoices",
    "list_invoices": "invoices",
    "get_invoice": "invoices",
    "send_invoice": "invoices",
    "create_refund": "refunds",
    "record_refund_for_invoice": "refunds",
    "list_policy": "policy",
    "list_pending_approvals": "policy",
}
KNOWN_TOOLS = set(TOOL_SCOPES)


@dataclass
class PolicyPack:
    name: str = "default-merchant-desk"
    currency: str = "USD"
    max_order_amount: Decimal = Decimal("500.00")
    max_invoice_amount: Decimal = Decimal("2000.00")
    refund_auto_approve_limit: Decimal = Decimal("25.00")
    daily_refund_cap: Decimal = Decimal("200.00")
    sandbox_only: bool = True
    enabled_scopes: tuple[str, ...] = DEFAULT_SCOPES

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "currency": self.currency,
            "max_order_amount": money_str(self.max_order_amount),
            "max_invoice_amount": money_str(self.max_invoice_amount),
            "refund_auto_approve_limit": money_str(self.refund_auto_approve_limit),
            "daily_refund_cap": money_str(self.daily_refund_cap),
            "sandbox_only": self.sandbox_only,
            "enabled_scopes": list(self.enabled_scopes),
            "notes": {
                "orders": f"Orders at or below {money_str(self.max_order_amount)} {self.currency} may be created automatically.",
                "invoices": f"Invoices at or below {money_str(self.max_invoice_amount)} {self.currency} may be created automatically.",
                "refunds": (
                    f"Refunds below {money_str(self.refund_auto_approve_limit)} {self.currency} "
                    f"are auto-approved. Refunds at or above that amount wait for a human. "
                    f"Total refunds in a day cannot exceed {money_str(self.daily_refund_cap)} {self.currency}."
                ),
            },
        }


@dataclass
class PolicyContext:
    environment: str  # "mock" or "sandbox" or "live"
    daily_refunds_executed: Decimal = Decimal("0.00")
    known_capture_amount: Decimal | None = None
    known_refunded_so_far: Decimal = Decimal("0.00")


@dataclass
class Decision:
    kind: DecisionKind
    tool: str
    reason: str
    amount: Decimal | None = None
    currency: str = "USD"
    policy_rule: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.kind.value,
            "tool": self.tool,
            "reason": self.reason,
            "amount": money_str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "policy_rule": self.policy_rule,
            "details": self.details,
        }


def evaluate(
    tool: str,
    arguments: dict[str, Any] | None,
    pack: PolicyPack,
    context: PolicyContext,
) -> Decision:
    arguments = arguments or {}

    if pack.sandbox_only and context.environment not in {"mock", "sandbox"}:
        return Decision(
            kind=DecisionKind.BLOCK,
            tool=tool,
            reason="Policy requires sandbox or mock mode. Live/production PayPal calls are blocked.",
            policy_rule="sandbox_only",
        )

    if tool not in MONEY_TOOLS:
        return Decision(
            kind=DecisionKind.ALLOW,
            tool=tool,
            reason="This tool is not a money-movement action, so it is allowed.",
            policy_rule="read_or_non_money",
        )

    kind = MONEY_TOOLS[tool]
    amount = extract_amount(arguments)

    if kind == "order":
        if amount is None:
            return Decision(
                kind=DecisionKind.BLOCK,
                tool=tool,
                reason="An order must include line items with a quantity and unit price.",
                policy_rule="max_order_amount",
            )
        if amount > pack.max_order_amount:
            return Decision(
                kind=DecisionKind.BLOCK,
                tool=tool,
                reason=(
                    f"Order total {money_str(amount)} {pack.currency} is above the "
                    f"{money_str(pack.max_order_amount)} {pack.currency} order limit."
                ),
                amount=amount,
                currency=pack.currency,
                policy_rule="max_order_amount",
            )
        return Decision(
            kind=DecisionKind.ALLOW,
            tool=tool,
            reason=f"Order total {money_str(amount)} {pack.currency} is within the order limit.",
            amount=amount,
            currency=pack.currency,
            policy_rule="max_order_amount",
        )

    if kind == "invoice":
        if amount is None:
            return Decision(
                kind=DecisionKind.BLOCK,
                tool=tool,
                reason="An invoice must include line items with a quantity and unit price.",
                policy_rule="max_invoice_amount",
            )
        if amount > pack.max_invoice_amount:
            return Decision(
                kind=DecisionKind.BLOCK,
                tool=tool,
                reason=(
                    f"Invoice total {money_str(amount)} {pack.currency} is above the "
                    f"{money_str(pack.max_invoice_amount)} {pack.currency} invoice limit."
                ),
                amount=amount,
                currency=pack.currency,
                policy_rule="max_invoice_amount",
            )
        return Decision(
            kind=DecisionKind.ALLOW,
            tool=tool,
            reason=f"Invoice total {money_str(amount)} {pack.currency} is within the invoice limit.",
            amount=amount,
            currency=pack.currency,
            policy_rule="max_invoice_amount",
        )

    if kind == "capture":
        # Capturing an already created (and policy-checked) order is allowed.
        return Decision(
            kind=DecisionKind.ALLOW,
            tool=tool,
            reason="Capturing an existing order is allowed. The original order already passed the spend limit.",
            policy_rule="pay_order",
        )

    # Refunds: create_refund and record_refund_for_invoice.
    if amount is None:
        if context.known_capture_amount is not None:
            remaining = money(context.known_capture_amount - context.known_refunded_so_far)
            amount = remaining if remaining > 0 else context.known_capture_amount
        else:
            return Decision(
                kind=DecisionKind.NEED_APPROVAL,
                tool=tool,
                reason=(
                    "This refund has no explicit amount, so it cannot be auto-approved. "
                    "A human must confirm it."
                ),
                policy_rule="refund_requires_amount_or_approval",
            )

    if context.known_capture_amount is not None:
        remaining = money(context.known_capture_amount - context.known_refunded_so_far)
        if amount > remaining:
            return Decision(
                kind=DecisionKind.BLOCK,
                tool=tool,
                reason=(
                    f"Refund {money_str(amount)} {pack.currency} is more than the "
                    f"{money_str(remaining)} {pack.currency} still available on this payment."
                ),
                amount=amount,
                currency=pack.currency,
                policy_rule="refund_cannot_exceed_capture",
            )

    projected_daily = money(context.daily_refunds_executed + amount)
    if projected_daily > pack.daily_refund_cap:
        remaining_cap = money(pack.daily_refund_cap - context.daily_refunds_executed)
        if remaining_cap < 0:
            remaining_cap = money(0)
        return Decision(
            kind=DecisionKind.BLOCK,
            tool=tool,
            reason=(
                f"Refund {money_str(amount)} {pack.currency} would take today's refunds to "
                f"{money_str(projected_daily)} {pack.currency}, above the daily cap of "
                f"{money_str(pack.daily_refund_cap)} {pack.currency}. "
                f"Remaining room today: {money_str(remaining_cap)} {pack.currency}."
            ),
            amount=amount,
            currency=pack.currency,
            policy_rule="daily_refund_cap",
            details={"daily_refunds_executed": money_str(context.daily_refunds_executed)},
        )

    if amount >= pack.refund_auto_approve_limit:
        return Decision(
            kind=DecisionKind.NEED_APPROVAL,
            tool=tool,
            reason=(
                f"Refund {money_str(amount)} {pack.currency} is at or above the "
                f"{money_str(pack.refund_auto_approve_limit)} {pack.currency} auto-approve limit. "
                "It is waiting in the approval queue and has not been sent to PayPal."
            ),
            amount=amount,
            currency=pack.currency,
            policy_rule="refund_auto_approve_limit",
        )

    return Decision(
        kind=DecisionKind.ALLOW,
        tool=tool,
        reason=(
            f"Refund {money_str(amount)} {pack.currency} is below the "
            f"{money_str(pack.refund_auto_approve_limit)} {pack.currency} auto-approve limit "
            "and within the daily cap."
        ),
        amount=amount,
        currency=pack.currency,
        policy_rule="refund_auto_approve_limit",
    )
