"""Money helpers that stay in Decimal and never invent precision."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any

TWOPLACES = Decimal("0.01")


def money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    elif value is None or value == "":
        raise ValueError("amount is required")
    else:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid amount: {value!r}") from exc
    return amount.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def money_str(value: Any) -> str:
    return f"{money(value):.2f}"


def line_total(quantity: Any, unit_price: Any) -> Decimal:
    qty = Decimal(str(quantity))
    return money(qty * money(unit_price))


def items_total(items: list[dict[str, Any]] | None) -> Decimal:
    total = Decimal("0.00")
    for item in items or []:
        quantity = item.get("quantity", 1)
        unit_price = item.get("unit_price", item.get("item_cost", item.get("unit_amount")))
        if isinstance(unit_price, dict):
            unit_price = unit_price.get("value")
        total += line_total(quantity, unit_price)
    return money(total)


def extract_amount(payload: dict[str, Any] | None) -> Decimal | None:
    """Best-effort amount from a tool payload. Returns None if not present."""
    if not payload:
        return None
    if payload.get("items"):
        return items_total(payload["items"])
    raw = payload.get("amount")
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw in (None, ""):
        return None
    return money(raw)
