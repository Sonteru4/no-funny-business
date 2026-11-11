"""Process-wide desk runtime (state, backend, agent)."""

from __future__ import annotations

from datetime import datetime, timezone

from desk.agent import DeskAgent
from desk.config import Settings, load_settings
from desk.guard import GuardedPaypal
from desk.paypal.factory import build_backend
from desk.policy import PolicyPack
from desk.policy_compile import apply_candidate, compile_text, compile_with_llm
from desk.redact import Redactor
from desk.store import AppState, seed_demo_data, seed_extended_demo_data


class Runtime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.redactor = Redactor(self.settings.secret_values())
        self.policy = PolicyPack()
        self.state = AppState(mode=self.settings.mode, policy=self.policy)
        if self.settings.audit_signing_key:
            self.state.signing_key = self.settings.audit_signing_key
        if self.settings.mode == "mock":
            seed_demo_data(self.state)
        else:
            seed_extended_demo_data(self.state, local_records_only=True)
        self.backend = build_backend(self.settings, self.state)
        self.guarded = GuardedPaypal(self.backend, self.state, redactor=self.redactor)
        self.agent: DeskAgent | None = None
        if self.settings.llm_ready:
            self.agent = DeskAgent(self.settings, self.guarded)

    def require_agent(self) -> DeskAgent:
        if self.agent is None:
            raise RuntimeError(
                "No language model key is set. Add GEMINI_API_KEY (preferred) or "
                "OPENAI_API_KEY to your environment or .env file."
            )
        return self.agent

    def reset(self) -> None:
        session_ids = list(self.agent.histories.keys()) if self.agent else []
        self.__init__(self.settings)  # rebuild a clean runtime, keep settings
        _ = session_ids

    def approve(self, approval_id: str) -> dict:
        approval = self.state.get_approval(approval_id)
        if approval is None:
            return {"ok": False, "error": f"Approval {approval_id} was not found."}
        if approval.status != "pending":
            return {"ok": False, "error": f"Approval {approval_id} is {approval.status}."}
        approval.status = "approved"
        approval.decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.state.frozen:
            return {"ok": False, "error": "The kill switch is on. Turn it off before approving money moves."}
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

    def propose_policy_deterministic(self, text: str) -> dict:
        result = compile_text(text, self.state.policy)
        if result.get("ok"):
            self.state.pending_policy = result
        return result

    def propose_policy(self, text: str) -> dict:
        def llm_json(prompt: str) -> dict:
            return self.require_agent().compile_json(prompt)

        compiler = None
        if self.agent is not None:
            compiler = llm_json
        result = compile_with_llm(text, self.state.policy, compiler)
        if not result.get("ok"):
            # If Gemini was attempted and failed, compile_with_llm already fell back.
            # If both failed, try deterministic alone so the error is from the parser.
            if compiler is not None and "could not derive" in str(result.get("error", "")).lower():
                result = compile_text(text, self.state.policy)
        if result.get("ok"):
            self.state.pending_policy = result
        return result

    def apply_pending_policy(self) -> dict:
        pending = self.state.pending_policy
        if not pending or not pending.get("ok"):
            return {"ok": False, "error": "There is no pending policy diff to apply."}
        new_pack = apply_candidate(self.state.policy, pending["candidate"])
        self.policy = new_pack
        self.state.policy = new_pack
        applied = pending
        self.state.pending_policy = None
        return {"ok": True, "applied": applied, "policy": new_pack.to_dict()}

    def discard_pending_policy(self) -> dict:
        if not self.state.pending_policy:
            return {"ok": False, "error": "There is no pending policy diff to discard."}
        self.state.pending_policy = None
        return {"ok": True}


_RUNTIME: Runtime | None = None

# Public demo mode: one isolated desk per visitor, idle desks expire.
import contextvars  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import OrderedDict  # noqa: E402

VISITOR: contextvars.ContextVar[str | None] = contextvars.ContextVar("desk_visitor", default=None)
_VISITORS: "OrderedDict[str, tuple[float, Runtime]]" = OrderedDict()
_VISITORS_LOCK = threading.Lock()
MAX_VISITORS = 200
VISITOR_TTL_SECONDS = 30 * 60


def _visitor_runtime(visitor: str, fresh: bool = False) -> Runtime:
    now = time.monotonic()
    with _VISITORS_LOCK:
        for key in [k for k, (seen, _) in _VISITORS.items() if now - seen > VISITOR_TTL_SECONDS]:
            _VISITORS.pop(key, None)
        entry = _VISITORS.pop(visitor, None)
        runtime = None if (fresh or entry is None) else entry[1]
        if runtime is None:
            runtime = Runtime(load_settings())
        _VISITORS[visitor] = (now, runtime)
        while len(_VISITORS) > MAX_VISITORS:
            _VISITORS.popitem(last=False)
        return runtime


def visitor_count() -> int:
    return len(_VISITORS)


def get_runtime() -> Runtime:
    global _RUNTIME
    visitor = VISITOR.get()
    if visitor:
        return _visitor_runtime(visitor)
    if _RUNTIME is None:
        _RUNTIME = Runtime()
    return _RUNTIME


def reset_runtime() -> Runtime:
    global _RUNTIME
    visitor = VISITOR.get()
    if visitor:
        return _visitor_runtime(visitor, fresh=True)
    settings = _RUNTIME.settings if _RUNTIME else load_settings()
    _RUNTIME = Runtime(settings)
    return _RUNTIME
