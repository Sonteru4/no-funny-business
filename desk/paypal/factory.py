from __future__ import annotations

from desk.config import Settings
from desk.paypal.live import LivePayPalBackend
from desk.paypal.mock import MockPayPalBackend
from desk.store import AppState


def build_backend(settings: Settings, state: AppState):
    if settings.uses_live_sandbox:
        return LivePayPalBackend(settings, state)
    return MockPayPalBackend(state)
