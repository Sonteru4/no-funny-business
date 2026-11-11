from __future__ import annotations

from tillguard.config import Settings
from tillguard.paypal.live import LivePayPalBackend
from tillguard.paypal.mock import MockPayPalBackend
from tillguard.store import AppState


def build_backend(settings: Settings, state: AppState):
    if settings.uses_live_sandbox:
        return LivePayPalBackend(settings, state)
    return MockPayPalBackend(state)
