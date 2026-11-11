"""Compile plain-English policy changes into a validated rule diff.

A human must approve the diff before it takes effect. The compiler cannot
turn off sandbox-only mode or raise limits above hard safety ceilings.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from desk.amounts import money, money_str
from desk.policy import PolicyPack

# Hard ceilings the compiler is not allowed to exceed. These are product
# safety rails, not the merchant's current policy pack.
SAFETY_CEILINGS = {
    "max_order_amount": Decimal("10000.00"),
    "max_invoice_amount": Decimal("20000.00"),
    "refund_auto_approve_limit": Decimal("500.00"),
    "daily_refund_cap": Decimal("5000.00"),
    "max_payout_amount": Decimal("10000.00"),
    "payout_approval_threshold": Decimal("2000.00"),
    "subscription_max_change_per_update": Decimal("100.00"),
    "subscription_daily_increase_cap": Decimal("100.00"),
}

# Fraud controls the compiler may switch on but never off.
ONE_WAY_FLAGS = ("subscription_payee_change_requires_approval",)

MONEY_FIELDS = tuple(SAFETY_CEILINGS)
_NUM = r"\$?\s*([0-9]+(?:\.[0-9]+)?)"

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
    ("subscription_daily_increase_cap", re.compile(r"subscription[^.]{0,60}?" + _NUM + r"\s*(?:per day|a day|each day|daily)", re.I)),
    ("subscription_daily_increase_cap", re.compile(r"daily (?:subscription )?(?:price )?increase cap(?:\s+(?:to|of|is|at))?\s*" + _NUM, re.I)),
    ("subscription_max_change_per_update", re.compile(r"subscription[^.]{0,40}?(?:per[- ]update|per change|each change|single change)[^0-9$.]{0,20}" + _NUM, re.I)),
    ("payout_approval_threshold", re.compile(r"payouts?\s+(?:over|above|of|at or above)\s*" + _NUM + r"[^.]{0,30}(?:need|require|wait)", re.I)),
    ("max_payout_amount", re.compile(r"payout (?:limit|cap)(?:\s+(?:to|of|is|at))?\s*" + _NUM, re.I)),
]

_FLAG_ON = {
    "subscription_payee_change_requires_approval": re.compile(
        r"(?:subscription|billing)[^.]{0,50}payee[^.]{0,50}(?:approv|human|review)"
        r"|payee changes?[^.]{0,30}subscriptions?[^.]{0,30}(?:approv|human|review)"
        r"|(?:approv|human|review)[^.]{0,50}(?:subscription|billing)[^.]{0,30}payee",
        re.I,
    ),
}
_FLAG_OFF = re.compile(r"\b(?:stop|don't|do not|no longer|never)\b[^.]{0,40}\b(?:requir|approv|review)", re.I)


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

    if _FLAG_OFF.search(source) and "payee" in source.lower():
        return {
            "ok": False,
            "error": "That request was refused. The compiler can turn fraud controls on, never off.",
        }

    proposed: dict[str, Any] = {}
    for field, pattern in _RULES:
        if field in proposed:
            continue
        match = pattern.search(source)
        if match:
            try:
                proposed[field] = money(match.group(1))
            except (InvalidOperation, ValueError):
                return {"ok": False, "error": f"Could not parse an amount for {field}."}
    for flag, pattern in _FLAG_ON.items():
        if pattern.search(source):
            proposed[flag] = True

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
    proposed: dict[str, Any],
    current: PolicyPack,
    source: str,
    compiler: str,
) -> dict[str, Any]:
    candidate: dict[str, Any] = {field: getattr(current, field) for field in MONEY_FIELDS}
    for flag in ONE_WAY_FLAGS:
        candidate[flag] = bool(getattr(current, flag))
    candidate["sandbox_only"] = True

    for field, value in proposed.items():
        if field in ONE_WAY_FLAGS:
            if value is not True:
                return {"ok": False, "error": f"{field} can only be switched on by the compiler."}
            candidate[field] = True
            continue
        if field not in SAFETY_CEILINGS:
            continue
        value = money(value)
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
        candidate[field] = value

    if candidate["refund_auto_approve_limit"] > candidate["daily_refund_cap"]:
        return {
            "ok": False,
            "error": "The auto-approve refund limit cannot be higher than the daily refund cap.",
        }
    if candidate["payout_approval_threshold"] > candidate["max_payout_amount"]:
        return {"ok": False, "error": "The payout approval threshold cannot be above the payout limit."}

    diff = []
    for field in MONEY_FIELDS:
        before_raw = getattr(current, field)
        after = candidate[field]
        if before_raw is None and after is None:
            continue
        if before_raw is None or after is None or money(before_raw) != money(after):
            diff.append({
                "field": field,
                "from": money_str(before_raw) if before_raw is not None else "none",
                "to": money_str(after) if after is not None else "none",
            })
    for flag in ONE_WAY_FLAGS:
        if bool(getattr(current, flag)) != candidate[flag]:
            diff.append({"field": flag, "from": "off", "to": "on"})
    if not diff:
        return {"ok": False, "error": "That text does not change any current limits."}

    def fmt(v: Any) -> Any:
        if isinstance(v, Decimal):
            return money_str(v)
        return v

    return {
        "ok": True,
        "compiler": compiler,
        "source": source,
        "diff": diff,
        "current": current.to_dict(),
        "candidate": {k: fmt(v) for k, v in candidate.items()},
        "safety_ceilings": {k: money_str(v) for k, v in SAFETY_CEILINGS.items()},
        "needs_human_approval": True,
        "notes": "Nothing has changed yet. A human must apply this diff before it takes effect.",
    }


def apply_candidate(current: PolicyPack, candidate: dict[str, Any]) -> PolicyPack:
    from dataclasses import replace

    updates: dict[str, Any] = {}
    for field in MONEY_FIELDS:
        value = candidate.get(field, getattr(current, field))
        updates[field] = money(value) if value not in (None, "", "none") else None
    for flag in ONE_WAY_FLAGS:
        updates[flag] = bool(getattr(current, flag)) or bool(candidate.get(flag))
    return replace(current, sandbox_only=True, **updates)


def _llm_prompt(text: str, current: PolicyPack) -> str:
    return (
        "You compile merchant payment policy changes into JSON. "
        "Return only JSON with any of these keys if they should change: "
        "max_order_amount, max_invoice_amount, refund_auto_approve_limit, daily_refund_cap, "
        "max_payout_amount, payout_approval_threshold, subscription_max_change_per_update, "
        "subscription_daily_increase_cap (USD numbers), and subscription_payee_change_requires_approval (true only). "
        "Amounts are USD numbers. Never disable sandbox_only. Never set unlimited values. "
        f"Current policy: {json.dumps(current.to_dict(), default=str)}. "
        f"Merchant request: {text}"
    )


def _amounts_from_llm(raw: dict[str, Any]) -> dict[str, Any]:
    proposed: dict[str, Any] = {}
    for field in SAFETY_CEILINGS:
        if field in raw and raw[field] not in (None, ""):
            proposed[field] = money(raw[field])
    for flag in ONE_WAY_FLAGS:
        if raw.get(flag) is True:
            proposed[flag] = True
    return proposed
