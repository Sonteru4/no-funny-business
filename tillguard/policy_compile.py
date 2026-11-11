"""Compile plain-English policy changes into a validated rule diff.

A human must approve the diff before it takes effect. The compiler cannot
turn off sandbox-only mode or raise limits above hard safety ceilings.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from tillguard.amounts import money, money_str
from tillguard.policy import PolicyPack

# Hard ceilings the compiler is not allowed to exceed. These are product
# safety rails, not the merchant's current policy pack.
SAFETY_CEILINGS = {
    "max_order_amount": Decimal("10000.00"),
    "max_invoice_amount": Decimal("20000.00"),
    "refund_auto_approve_limit": Decimal("500.00"),
    "daily_refund_cap": Decimal("5000.00"),
}

_UNLIMITED = re.compile(
    r"\b(no limits?|unlimited|remove (all )?limits?|disable (all )?(the )?policy|allow live|production mode)\b",
    re.I,
)

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("refund_auto_approve_limit", re.compile(r"(?:auto-?approve(?: refunds?)?|refunds? (?:are )?auto-?approved)\s+(?:refunds?\s+)?(?:under|below|less than|up to)\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)),
    ("refund_auto_approve_limit", re.compile(r"refund auto-?approve(?: limit)?(?:\s+(?:to|of|is))?\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)),
    ("daily_refund_cap", re.compile(r"daily (?:refund )?cap(?:\s+(?:to|of|is))?\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)),
    ("max_order_amount", re.compile(r"order(?:s)? (?:limit|cap)(?:\s+(?:to|of|is))?\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)),
    ("max_invoice_amount", re.compile(r"invoice(?:s)? (?:limit|cap)(?:\s+(?:to|of|is))?\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)),
]


def compile_text(text: str, current: PolicyPack) -> dict[str, Any]:
    """Deterministic compiler for common limit phrases. Fails closed."""
    source = (text or "").strip()
    if not source:
        return {"ok": False, "error": "Write a policy change in plain English first."}
    if len(source) > 2000:
        return {"ok": False, "error": "Policy text is too long. Keep it under 2,000 characters."}
    if _UNLIMITED.search(source):
        return {
            "ok": False,
            "error": (
                "That request was refused. The compiler will not remove limits, "
                "disable policy, or switch the desk to live/production PayPal."
            ),
        }

    proposed: dict[str, Decimal] = {}
    for field, pattern in _RULES:
        match = pattern.search(source)
        if match:
            try:
                proposed[field] = money(match.group(1))
            except (InvalidOperation, ValueError):
                return {"ok": False, "error": f"Could not parse an amount for {field}."}

    if not proposed:
        return {
            "ok": False,
            "error": (
                "I could not derive a rule change from that text. Try a phrase like "
                "'auto-approve refunds under $50 and set the daily refund cap to $300'."
            ),
        }
    return validate_proposal(proposed, current, source, compiler="deterministic")


def compile_with_llm(
    text: str,
    current: PolicyPack,
    llm_json: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Try the LLM compiler, then fall back to the deterministic parser."""
    if llm_json is not None:
        try:
            prompt = _llm_prompt(text, current)
            raw = llm_json(prompt)
            proposed = _amounts_from_llm(raw)
            if proposed:
                result = validate_proposal(proposed, current, text, compiler="gemini")
                if result.get("ok"):
                    return result
        except Exception:
            pass
    return compile_text(text, current)


def validate_proposal(
    proposed: dict[str, Decimal],
    current: PolicyPack,
    source: str,
    compiler: str,
) -> dict[str, Any]:
    candidate = {
        "max_order_amount": current.max_order_amount,
        "max_invoice_amount": current.max_invoice_amount,
        "refund_auto_approve_limit": current.refund_auto_approve_limit,
        "daily_refund_cap": current.daily_refund_cap,
        "sandbox_only": True,
    }
    for field, value in proposed.items():
        if field not in SAFETY_CEILINGS:
            continue
        if value < 0:
            return {"ok": False, "error": f"{field} cannot be negative."}
        if value > SAFETY_CEILINGS[field]:
            return {
                "ok": False,
                "error": (
                    f"{field} of {money_str(value)} is above the safety ceiling of "
                    f"{money_str(SAFETY_CEILINGS[field])}. The compiler will not apply it."
                ),
            }
        candidate[field] = money(value)

    if candidate["refund_auto_approve_limit"] > candidate["daily_refund_cap"]:
        return {
            "ok": False,
            "error": "The auto-approve refund limit cannot be higher than the daily refund cap.",
        }

    diff = []
    for field in ("max_order_amount", "max_invoice_amount", "refund_auto_approve_limit", "daily_refund_cap"):
        before = money(getattr(current, field))
        after = candidate[field]
        if before != after:
            diff.append({
                "field": field,
                "from": money_str(before),
                "to": money_str(after),
            })
    if not diff:
        return {"ok": False, "error": "That text does not change any current limits."}

    return {
        "ok": True,
        "compiler": compiler,
        "source": source,
        "diff": diff,
        "current": current.to_dict(),
        "candidate": {k: (money_str(v) if isinstance(v, Decimal) else v) for k, v in candidate.items()},
        "safety_ceilings": {k: money_str(v) for k, v in SAFETY_CEILINGS.items()},
        "needs_human_approval": True,
        "notes": "Nothing has changed yet. A human must apply this diff before it takes effect.",
    }


def apply_candidate(current: PolicyPack, candidate: dict[str, Any]) -> PolicyPack:
    return PolicyPack(
        name=current.name,
        currency=current.currency,
        max_order_amount=money(candidate["max_order_amount"]),
        max_invoice_amount=money(candidate["max_invoice_amount"]),
        refund_auto_approve_limit=money(candidate["refund_auto_approve_limit"]),
        daily_refund_cap=money(candidate["daily_refund_cap"]),
        sandbox_only=True,
        enabled_scopes=current.enabled_scopes,
    )


def _llm_prompt(text: str, current: PolicyPack) -> str:
    return (
        "You compile merchant payment policy changes into JSON. "
        "Return only JSON with any of these keys if they should change: "
        "max_order_amount, max_invoice_amount, refund_auto_approve_limit, daily_refund_cap. "
        "Amounts are USD numbers. Never disable sandbox_only. Never set unlimited values. "
        f"Current policy: {json.dumps(current.to_dict(), default=str)}. "
        f"Merchant request: {text}"
    )


def _amounts_from_llm(raw: dict[str, Any]) -> dict[str, Decimal]:
    proposed: dict[str, Decimal] = {}
    for field in SAFETY_CEILINGS:
        if field in raw and raw[field] not in (None, ""):
            proposed[field] = money(raw[field])
    return proposed
