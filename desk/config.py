"""Environment and runtime settings. Never log secret values."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
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
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    llm_provider_override: str | None = None
    audit_signing_key: str | None = None

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

    @property
    def llm_provider(self) -> str | None:
        forced = (self.llm_provider_override or "").lower() or None
        if forced == "gemini" and self.gemini_api_key:
            return "gemini"
        if forced == "openai" and self.openai_api_key:
            return "openai"
        if forced in {"gemini", "openai"}:
            # Forced provider is missing a key; fall through to whatever is available.
            pass
        if self.gemini_api_key:
            return "gemini"
        if self.openai_api_key:
            return "openai"
        return None

    @property
    def llm_model(self) -> str:
        if self.llm_provider == "gemini":
            return self.gemini_model
        return self.openai_model

    @property
    def llm_ready(self) -> bool:
        return self.llm_provider is not None

    def secret_values(self) -> list[str]:
        """Return configured secrets so logs can redact them. Do not print this list."""
        return [
            value
            for value in (
                self.openai_api_key,
                self.gemini_api_key,
                self.paypal_client_id,
                self.paypal_client_secret,
                self.audit_signing_key,
            )
            if value
        ]


def load_settings() -> Settings:
    override = (_first_env("PAYPAL_MODE") or "").lower() or None
    if override not in {None, "mock", "sandbox"}:
        override = None
    llm_override = (_first_env("DESK_LLM", "TILLGUARD_LLM") or "").lower() or None
    if llm_override not in {None, "gemini", "openai"}:
        llm_override = None
    return Settings(
        openai_api_key=_first_env("OPENAI_API_KEY"),
        openai_model=_first_env("OPENAI_MODEL", default="gpt-4o-mini") or "gpt-4o-mini",
        paypal_client_id=_first_env("PAYPAL_CLIENT_ID"),
        paypal_client_secret=_first_env("PAYPAL_CLIENT_SECRET", "PAYPAL_SECRET"),
        paypal_mode_override=override,
        host=_first_env("DESK_HOST", "TILLGUARD_HOST", default="127.0.0.1") or "127.0.0.1",
        port=int(_first_env("DESK_PORT", "TILLGUARD_PORT", default="8000") or "8000"),
        gemini_api_key=_first_env("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        gemini_model=_first_env("GEMINI_MODEL", default="gemini-flash-latest") or "gemini-flash-latest",
        llm_provider_override=llm_override,
        audit_signing_key=_first_env("DESK_AUDIT_KEY", "TILLGUARD_AUDIT_KEY"),
    )


def mock_paypal_settings(settings: Settings | None = None) -> Settings:
    loaded = settings or load_settings()
    return replace(
        loaded,
        paypal_client_id=None,
        paypal_client_secret=None,
        paypal_mode_override="mock",
    )


def credentials_present() -> dict[str, bool]:
    """Report which secrets exist, without revealing values."""
    return {
        "GEMINI_API_KEY": _secret_is_set("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "OPENAI_API_KEY": _secret_is_set("OPENAI_API_KEY"),
        "PAYPAL_CLIENT_ID": _secret_is_set("PAYPAL_CLIENT_ID"),
        "PAYPAL_CLIENT_SECRET": _secret_is_set("PAYPAL_CLIENT_SECRET", "PAYPAL_SECRET"),
        "DESK_AUDIT_KEY": _secret_is_set("DESK_AUDIT_KEY", "TILLGUARD_AUDIT_KEY"),
    }
