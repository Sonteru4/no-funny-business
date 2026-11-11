"""Prompt-injection and policy-override detection, enforced in code (not by the model)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Specific on purpose: "what is the refund policy?" must not match.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("policy_override", re.compile(r"\bignore\b.{0,40}\b(rules?|policy|policies|instructions?|safety|guardrails?|limits?)\b", re.I)),
    ("policy_override", re.compile(r"\bdisregard\b.{0,40}\b(rules?|policy|policies|instructions?)\b", re.I)),
    ("policy_override", re.compile(r"\b(bypass|override|disable|skip)\b.{0,40}\b(policy|guard(?:rail)?|interceptor|approval|limits?)\b", re.I)),
    ("policy_override", re.compile(r"\bauto-?approve\b.{0,20}\b(all|every|everything)\b", re.I)),
    ("policy_override", re.compile(r"\b(no|without|skip)\b.{0,20}\b(human )?approval\b", re.I)),
    ("jailbreak", re.compile(r"\b(jailbreak|do anything now|\bdan mode\b)\b", re.I)),
    ("jailbreak", re.compile(r"\byou are now\b.{0,40}\b(unrestricted|jailbroken|dan|without rules)\b", re.I)),
    ("jailbreak", re.compile(r"\b(new|updated)\s+system\s+(prompt|instructions?)\b", re.I)),
    ("prompt_exfil", re.compile(r"\b(reveal|show|print|dump)\b.{0,30}\b(system|hidden|secret)\s+prompt\b", re.I)),
    ("role_hijack", re.compile(r"(?:system\s*:|\[inst\]|<\|system\|>|instruction to (?:the )?agent|hidden instruction)", re.I)),
    ("role_hijack", re.compile(r"\bdeveloper message\b.{0,20}\b(override|ignore)\b", re.I)),
]


def normalize(text: str) -> str:
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    # Strip zero-width and direction-override characters used to hide instructions.
    folded = "".join(ch for ch in folded if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in "\n\t")
    # Common leetspeak used to sneak past naive filters.
    table = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
    folded = folded.translate(table)
    return re.sub(r"\s+", " ", folded).strip()


def _scan_text(text: str) -> list[dict[str, str]]:
    normalized = normalize(text)
    if not normalized:
        return []
    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in _PATTERNS:
        match = pattern.search(normalized)
        if match:
            key = (kind, match.group(0).lower())
            if key in seen:
                continue
            seen.add(key)
            findings.append({"kind": kind, "match": match.group(0)[:80], "source": "text"})
    return findings


def _walk_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((path or "value", value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_walk_strings(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_walk_strings(item, f"{path}[{index}]"))
    return found


def scan_text(text: str | None) -> dict[str, Any]:
    findings = _scan_text(text or "")
    return {
        "detected": bool(findings),
        "findings": findings,
        "reason": _reason(findings, "the message"),
    }


def scan_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for path, text in _walk_strings(arguments or {}):
        for item in _scan_text(text):
            item = dict(item)
            item["source"] = path
            findings.append(item)
    return {
        "detected": bool(findings),
        "findings": findings,
        "reason": _reason(findings, "a tool argument"),
    }


def _reason(findings: list[dict[str, str]], where: str) -> str | None:
    if not findings:
        return None
    kinds = sorted({item["kind"] for item in findings})
    sources = sorted({item.get("source", "text") for item in findings if item.get("source") and item.get("source") != "text"})
    kind_text = ", ".join(kinds)
    if sources:
        return (
            f"Prompt injection detected in {where} ({kind_text} in {', '.join(sources)}). "
            "The call was blocked in code and was not sent to PayPal."
        )
    return (
        f"Prompt injection detected in {where} ({kind_text}). "
        "The call was blocked in code and was not sent to PayPal."
    )


def is_policy_override(scan: dict[str, Any] | None) -> bool:
    if not scan or not scan.get("detected"):
        return False
    return any(item.get("kind") in {"policy_override", "jailbreak", "role_hijack"} for item in scan.get("findings") or [])
