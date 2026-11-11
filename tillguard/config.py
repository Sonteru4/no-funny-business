"""Environment and runtime settings. Never log secret values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _first_env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _secret_is_set(*names: str) -> bool:
    return _first_env(*names) is not None


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    paypal_client_id: str | None
    paypal_client_secret: str | None
    paypal_mode_override: str | None
    host: str
    port: int

    @property
    def has_paypal_credentials(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def mode(self) -> str:
        if self.paypal_mode_override in {"mock", "sandbox"}:
            if self.paypal_mode_override == "sandbox" and not self.has_paypal_credentials:
                return "mock"
            return self.paypal_mode_override
        return "sandbox" if self.has_paypal_credentials else "mock"

    @property
    def uses_live_sandbox(self) -> bool:
        return self.mode == "sandbox" and self.has_paypal_credentials


def load_settings() -> Settings:
    override = (_first_env("PAYPAL_MODE") or "").lower() or None
    if override not in {None, "mock", "sandbox"}:
        override = None
    return Settings(
        openai_api_key=_first_env("OPENAI_API_KEY"),
        openai_model=_first_env("OPENAI_MODEL", default="gpt-4o-mini") or "gpt-4o-mini",
        paypal_client_id=_first_env("PAYPAL_CLIENT_ID"),
        paypal_client_secret=_first_env("PAYPAL_CLIENT_SECRET", "PAYPAL_SECRET"),
        paypal_mode_override=override,
        host=_first_env("TILLGUARD_HOST", default="127.0.0.1") or "127.0.0.1",
        port=int(_first_env("TILLGUARD_PORT", default="8000") or "8000"),
    )


def credentials_present() -> dict[str, bool]:
    """Report which secrets exist, without revealing values."""
    return {
        "OPENAI_API_KEY": _secret_is_set("OPENAI_API_KEY"),
        "PAYPAL_CLIENT_ID": _secret_is_set("PAYPAL_CLIENT_ID"),
        "PAYPAL_CLIENT_SECRET": _secret_is_set("PAYPAL_CLIENT_SECRET", "PAYPAL_SECRET"),
    }
