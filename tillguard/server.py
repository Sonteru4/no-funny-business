"""FastAPI app for the TillGuard merchant desk."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tillguard.config import credentials_present
from tillguard.integrity import explain
from tillguard.ratelimit import LIMITER
from tillguard.redact import DEFAULT_REDACTOR
from tillguard.redteam import run_suite
from tillguard.runtime import get_runtime, reset_runtime

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="TillGuard", version="1.1.0")
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(default="default", max_length=80)


class PolicyProposal(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


def _public_error(exc: Exception) -> str:
    runtime = get_runtime()
    text = runtime.redactor.redact_text(f"{type(exc).__name__}: {exc}")
    lowered = text.lower()
    if "429" in text or "no credits" in lowered or "resource_exhausted" in lowered or type(exc).__name__ == "RateLimitError":
        return (
            "The language model account has no credits remaining or is rate limited. "
            "Add billing credit or use a working GEMINI_API_KEY or OPENAI_API_KEY, then retry. "
            "Policy unit tests, the offline walkthrough, and the red-team suite still run without the LLM."
        )
    return f"The clerk could not reach the language model ({text})."


def _check_limit(request: Request, name: str, limit: int) -> None:
    key = f"{name}:{(request.client.host if request.client else 'local')}"
    result = LIMITER.check(key, limit=limit)
    if not result["ok"]:
        raise HTTPException(
            status_code=429,
            detail=f"Too many {name} requests. Try again in {result['retry_after']} seconds.",
            headers={"Retry-After": str(result["retry_after"])},
        )


def _state_payload():
    runtime = get_runtime()
    payload = runtime.state.snapshot()
    payload["llm_ready"] = runtime.settings.llm_ready
    payload["openai_ready"] = runtime.settings.llm_ready  # backward compatible alias
    payload["provider"] = runtime.settings.llm_provider
    payload["model"] = runtime.settings.llm_model
    payload["credentials_present"] = credentials_present()
    payload["policy"] = runtime.state.policy.to_dict()
    return runtime.redactor.redact(payload)


@app.get("/")
def index():
    index_path = STATIC / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI files are missing.")
    return FileResponse(index_path)


@app.get("/api/health")
def health():
    runtime = get_runtime()
    return {
        "ok": True,
        "mode": runtime.state.mode,
        "model": runtime.settings.llm_model,
        "provider": runtime.settings.llm_provider,
        "llm_ready": runtime.settings.llm_ready,
        "openai_ready": runtime.settings.llm_ready,
        "credentials_present": credentials_present(),
        "transport": getattr(runtime.backend, "transport", runtime.state.mode),
        "integrity": runtime.state.integrity(),
    }


@app.get("/api/state")
def state():
    return _state_payload()


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request):
    _check_limit(request, "chat", limit=20)
    runtime = get_runtime()
    try:
        agent = runtime.require_agent()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = agent.chat(body.message, session_id=body.session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_public_error(exc)) from exc
    return runtime.redactor.redact({
        "reply": result["reply"],
        "trace": result["trace"],
        "model": result.get("model"),
        "provider": result.get("provider"),
        "state": _state_payload(),
    })


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str, request: Request):
    _check_limit(request, "approvals", limit=30)
    if len(approval_id) > 80:
        raise HTTPException(status_code=400, detail="Invalid approval id.")
    runtime = get_runtime()
    result = runtime.approve(approval_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Approval failed."))
    return {"result": runtime.redactor.redact(result), "state": _state_payload()}


@app.post("/api/approvals/{approval_id}/deny")
def deny(approval_id: str, request: Request):
    _check_limit(request, "approvals", limit=30)
    if len(approval_id) > 80:
        raise HTTPException(status_code=400, detail="Invalid approval id.")
    runtime = get_runtime()
    result = runtime.deny(approval_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Deny failed."))
    return {"result": result, "state": _state_payload()}


@app.post("/api/walkthrough")
def walkthrough():
    """Populate the desk with a policy walkthrough that does not call the LLM."""
    from tillguard.walkthrough import run_walkthrough

    runtime = reset_runtime()
    return runtime.redactor.redact(run_walkthrough(runtime))


@app.post("/api/reset")
def reset():
    runtime = reset_runtime()
    return {"ok": True, "state": _state_payload() if runtime else None}


@app.get("/api/integrity")
def integrity():
    runtime = get_runtime()
    return runtime.state.integrity()


@app.get("/api/audit/{audit_id}/explain")
def explain_audit(audit_id: str):
    runtime = get_runtime()
    entry = next((item for item in runtime.state.audit if item.id == audit_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="That audit entry was not found.")
    return runtime.redactor.redact(explain(entry.to_dict(), runtime.state.integrity()))


@app.post("/api/policy/propose")
def propose_policy(body: PolicyProposal, request: Request):
    _check_limit(request, "policy", limit=10)
    runtime = get_runtime()
    result = runtime.propose_policy(body.text)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not compile that policy."))
    return {"proposal": result, "state": _state_payload()}


@app.post("/api/policy/apply")
def apply_policy(request: Request):
    _check_limit(request, "policy", limit=10)
    runtime = get_runtime()
    result = runtime.apply_pending_policy()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not apply that policy."))
    return {"result": result, "state": _state_payload()}


@app.post("/api/policy/discard")
def discard_policy():
    runtime = get_runtime()
    result = runtime.discard_pending_policy()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not discard that policy."))
    return {"result": result, "state": _state_payload()}


@app.post("/api/redteam")
def redteam(request: Request):
    _check_limit(request, "redteam", limit=6)
    runtime = get_runtime()
    report = run_suite()
    runtime.state.last_redteam = report
    return {"report": report, "state": _state_payload()}


def main() -> None:
    import uvicorn

    runtime = get_runtime()
    uvicorn.run(
        "tillguard.server:app",
        host=runtime.settings.host,
        port=runtime.settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
