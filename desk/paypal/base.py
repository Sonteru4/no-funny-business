"""Backend interface shared by mock and live sandbox implementations."""

from __future__ import annotations

from typing import Any, Protocol


class PayPalBackend(Protocol):
    def run(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        ...

    def lookup_capture(self, capture_id: str | None) -> dict[str, Any] | None:
        ...
