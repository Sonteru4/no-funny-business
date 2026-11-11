"""Signed, tamper-evident hash chain for the audit log."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Iterable

GENESIS = hashlib.sha256(b"desk-audit-v1").hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint_key(key: bytes | str) -> str:
    raw = key.encode("utf-8") if isinstance(key, str) else key
    return hashlib.sha256(raw).hexdigest()[:16]


def entry_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """Fields that are covered by the hash. Hash and signature themselves are excluded."""
    return {
        "id": entry.get("id"),
        "ts": entry.get("ts"),
        "tool": entry.get("tool"),
        "arguments": entry.get("arguments"),
        "decision": entry.get("decision"),
        "reason": entry.get("reason"),
        "amount": entry.get("amount"),
        "policy_rule": entry.get("policy_rule"),
        "approval_id": entry.get("approval_id"),
        "injection": entry.get("injection"),
        "idempotency_key": entry.get("idempotency_key"),
        "prev_hash": entry.get("prev_hash") or GENESIS,
    }


def hash_entry(entry: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(entry_payload(entry)).encode("utf-8")).hexdigest()


def sign_hash(entry_hash: str, key: bytes | str) -> str:
    raw = key.encode("utf-8") if isinstance(key, str) else key
    return hmac.new(raw, entry_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def seal(entry: dict[str, Any], prev_hash: str | None, key: bytes | str) -> dict[str, Any]:
    sealed = dict(entry)
    sealed["prev_hash"] = prev_hash or GENESIS
    sealed["entry_hash"] = hash_entry(sealed)
    sealed["signature"] = sign_hash(sealed["entry_hash"], key)
    return sealed


def verify_entry(entry: dict[str, Any], key: bytes | str, expected_prev: str | None = None) -> dict[str, Any]:
    prev = entry.get("prev_hash") or GENESIS
    issues: list[str] = []
    if expected_prev is not None and prev != expected_prev:
        issues.append("prev_hash does not match the previous entry")
    expected_hash = hash_entry(entry)
    if entry.get("entry_hash") != expected_hash:
        issues.append("entry_hash does not match the canonical payload")
    expected_sig = sign_hash(expected_hash, key)
    if not hmac.compare_digest(str(entry.get("signature") or ""), expected_sig):
        issues.append("signature is invalid")
    return {"ok": not issues, "issues": issues, "expected_hash": expected_hash}


def verify_chain(entries: Iterable[dict[str, Any]], key: bytes | str) -> dict[str, Any]:
    """Entries should be in chronological order (oldest first)."""
    items = list(entries)
    expected_prev = GENESIS
    broken_at = None
    issues: list[dict[str, Any]] = []
    for item in items:
        result = verify_entry(item, key, expected_prev=expected_prev)
        if not result["ok"]:
            broken_at = item.get("id")
            issues.append({"id": item.get("id"), "issues": result["issues"]})
            break
        expected_prev = item.get("entry_hash") or expected_prev
    return {
        "ok": broken_at is None,
        "count": len(items),
        "broken_at": broken_at,
        "issues": issues,
        "head_hash": items[-1].get("entry_hash") if items else GENESIS,
        "genesis": GENESIS,
        "key_fingerprint": fingerprint_key(key),
    }


def explain(entry: dict[str, Any], chain: dict[str, Any] | None = None) -> dict[str, Any]:
    decision = entry.get("decision")
    if decision == "allowed":
        paypal = "The interceptor allowed this call, and it was forwarded to the PayPal backend."
    elif decision == "needs_approval":
        paypal = "The interceptor held this call. Nothing was sent to PayPal until a human approved it."
    elif decision == "blocked":
        paypal = "The interceptor blocked this call. Nothing was sent to PayPal."
    else:
        paypal = "The interceptor recorded this call."
    injection = entry.get("injection") or {}
    summary_parts = [f"{entry.get('tool')} was {decision}."]
    if entry.get("reason"):
        summary_parts.append(str(entry["reason"]))
    if injection.get("detected"):
        summary_parts.append("A prompt-injection finding was attached to this entry.")
    integrity = {
        "prev_hash": entry.get("prev_hash"),
        "entry_hash": entry.get("entry_hash"),
        "signature": entry.get("signature"),
        "chain_ok": None if chain is None else chain.get("ok"),
        "key_fingerprint": None if chain is None else chain.get("key_fingerprint"),
    }
    return {
        "id": entry.get("id"),
        "summary": " ".join(summary_parts),
        "decision": decision,
        "tool": entry.get("tool"),
        "amount": entry.get("amount"),
        "policy_rule": entry.get("policy_rule"),
        "reason": entry.get("reason"),
        "inputs": entry.get("arguments"),
        "injection": injection or None,
        "idempotency_key": entry.get("idempotency_key"),
        "approval_id": entry.get("approval_id"),
        "what_happened_to_paypal": paypal,
        "integrity": integrity,
        "generated_by": "deterministic explainer",
    }
