"""FastAPI app for the TillGuard merchant desk."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tillguard.config import credentials_present
from tillguard.runtime import get_runtime, reset_runtime

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="TillGuard", version="1.0.0")
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = "default"


def _public_llm_error(exc: Exception) -> str:
    name = type(exc).__name__
    message = str(exc)
    lowered = message.lower()
    if "429" in message or "no credits" in lowered or name == "RateLimitError":
        return (
            "The language model account has no credits remaining. "
            "Add billing credit or use a working key, then retry. "
            "Policy unit tests and the offline walkthrough still run without the LLM."
        )
    return f"The clerk could not reach the language model ({name})."


@app.get("/")
def index():
    index_path = STATIC / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI files are missing.")
    return FileResponse(index_path)


@app.get("/api/health")
def health():
    runtime = get_runtime()
    creds = credentials_present()
    return {
        "ok": True,
        "mode": runtime.state.mode,
        "model": runtime.settings.openai_model,
        "openai_ready": runtime.agent is not None,
        "credentials_present": creds,
        "transport": getattr(runtime.backend, "transport", runtime.state.mode),
    }


@app.get("/api/state")
def state():
    runtime = get_runtime()
    payload = runtime.state.snapshot()
    payload["openai_ready"] = runtime.agent is not None
    payload["model"] = runtime.settings.openai_model
    payload["credentials_present"] = credentials_present()
    return payload


@app.post("/api/chat")
def chat(body: ChatRequest):
    runtime = get_runtime()
    try:
        agent = runtime.require_agent()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = agent.chat(body.message, session_id=body.session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_public_llm_error(exc)) from exc
    return {
        "reply": result["reply"],
        "trace": result["trace"],
        "model": result["model"],
        "state": runtime.state.snapshot(),
    }


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str):
    runtime = get_runtime()
    result = runtime.approve(approval_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Approval failed."))
    return {"result": result, "state": runtime.state.snapshot()}


@app.post("/api/approvals/{approval_id}/deny")
def deny(approval_id: str):
    runtime = get_runtime()
    result = runtime.deny(approval_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Deny failed."))
    return {"result": result, "state": runtime.state.snapshot()}


@app.post("/api/walkthrough")
def walkthrough():
    """Populate the desk with a policy walkthrough that does not call the LLM."""
    from tillguard.walkthrough import run_walkthrough

    runtime = reset_runtime()
    return run_walkthrough(runtime)


@app.post("/api/reset")
def reset():
    runtime = reset_runtime()
    return {"ok": True, "state": runtime.state.snapshot()}


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
