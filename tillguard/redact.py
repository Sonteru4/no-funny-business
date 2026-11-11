"""Redact secrets and credential-like strings from logs, traces, and API payloads."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|client_secret|paypal_secret|access_token|refresh_token|password|secret|authorization|bearer)([\"']?\s*[:=]\s*[\"']?)([^\s\"']+)"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AQ\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
]

REDACTED = "[REDACTED]"


class Redactor:
    def __init__(self, secrets: list[str] | tuple[str, ...] | None = None):
        self._secrets = [value for value in (secrets or []) if value and len(value) >= 8]

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        for secret in self._secrets:
            if secret in out:
                out = out.replace(secret, REDACTED)
        for pattern in _PATTERNS:
            out = pattern.sub(_replace, out)
        return out

    def redact(self, value: Any) -> Any:
        if value is None or isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, dict):
            return {self.redact_text(str(key)): self.redact(item) for key, item in value.items()}
        return self.redact_text(str(value))


def _replace(match: re.Match) -> str:
    if match.lastindex and match.lastindex >= 3:
        return f"{match.group(1)}{match.group(2)}{REDACTED}"
    return REDACTED


DEFAULT_REDACTOR = Redactor()
