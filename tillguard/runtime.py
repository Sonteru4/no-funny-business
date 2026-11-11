"""Process-wide TillGuard runtime (state, backend, agent)."""

from __future__ import annotations

from datetime import datetime, timezone

from tillguard.agent import TillGuardAgent
from tillguard.config import Settings, load_settings
from tillguard.guard import GuardedPaypal
from tillguard.paypal.factory import build_backend
from tillguard.policy import PolicyPack
from tillguard.store import AppState, seed_demo_data


class Runtime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.policy = PolicyPack()
        self.state = AppState(mode=self.settings.mode, policy=self.policy)
        if self.settings.mode == "mock":
            seed_demo_data(self.state)
        self.backend = build_backend(self.settings, self.state)
        self.guarded = GuardedPaypal(self.backend, self.state)
        self.agent: TillGuardAgent | None = None
        if self.settings.openai_api_key:
            self.agent = TillGuardAgent(self.settings, self.guarded)

    def require_agent(self) -> TillGuardAgent:
        if self.agent is None:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to your environment or .env file.")
        return self.agent

    def reset(self) -> None:
        session_ids = list(self.agent.histories.keys()) if self.agent else []
        self.__init__(self.settings)  # rebuild a clean runtime, keep settings
        # We just wiped the agent; that is intentional on reset.
        _ = session_ids

    def approve(self, approval_id: str) -> dict:
        approval = self.state.get_approval(approval_id)
        if approval is None:
            return {"ok": False, "error": f"Approval {approval_id} was not found."}
        if approval.status != "pending":
            return {"ok": False, "error": f"Approval {approval_id} is {approval.status}."}
        approval.status = "approved"
        approval.decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self.guarded.execute_approved(approval_id)

    def deny(self, approval_id: str) -> dict:
        approval = self.state.get_approval(approval_id)
        if approval is None:
            return {"ok": False, "error": f"Approval {approval_id} was not found."}
        if approval.status != "pending":
            return {"ok": False, "error": f"Approval {approval_id} is {approval.status}."}
        approval.status = "denied"
        approval.decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"ok": True, "approval": approval.to_dict()}


_RUNTIME: Runtime | None = None


def get_runtime() -> Runtime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = Runtime()
    return _RUNTIME


def reset_runtime() -> Runtime:
    global _RUNTIME
    settings = _RUNTIME.settings if _RUNTIME else load_settings()
    _RUNTIME = Runtime(settings)
    return _RUNTIME
