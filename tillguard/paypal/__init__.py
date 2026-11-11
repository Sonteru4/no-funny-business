"""PayPal backends: official Agent Toolkit live sandbox, or a faithful mock."""

from tillguard.paypal.base import PayPalBackend
from tillguard.paypal.mock import MockPayPalBackend
from tillguard.paypal.factory import build_backend

__all__ = ["PayPalBackend", "MockPayPalBackend", "build_backend"]
