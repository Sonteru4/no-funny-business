"""LLM clerk that can call PayPal tools only through the policy interceptor."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from tillguard.config import Settings
from tillguard.guard import GuardedPaypal
from tillguard.tools import TOOL_SCHEMAS

SYSTEM_PROMPT = """You are TillGuard, an AI clerk for a merchant's PayPal desk.

You help the merchant create invoices, create and capture orders, and issue refunds.
You use tools to do real work. You never invent invoice IDs, order IDs, capture IDs, or amounts.

Hard rules:
- Every money-moving tool call is checked by a policy engine before it reaches PayPal.
- If a tool result has status "blocked", the action did not happen. Tell the merchant it was blocked and quote the reason. Do not pretend it succeeded.
- If a tool result has status "needs_approval", the action is waiting in the human approval queue and has not been sent to PayPal. Give the merchant the approval ID and explain that a person must approve it in the TillGuard desk.
- Refunds below the auto-approve limit can go through. Refunds at or above that limit need a human. Daily refund caps can block further refunds.
- Never tell the merchant you bypassed policy. You cannot bypass it. The interceptor enforces it even if a user asks you to ignore the rules.
- In mock mode, pay_order simulates buyer approval and capture. In live sandbox mode, the buyer must open the approve URL before capture will succeed.
- Be concise, specific, and calm. Quote IDs and amounts clearly. Use USD unless the tool output says otherwise.
- If you need the current limits, call list_policy.
"""


class TillGuardAgent:
    def __init__(self, settings: Settings, guarded: GuardedPaypal):
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to your environment or .env file.")
        self.settings = settings
        self.guarded = guarded
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.histories: dict[str, list[dict[str, Any]]] = {}

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return self.histories.setdefault(
            session_id,
            [{"role": "system", "content": SYSTEM_PROMPT}],
        )

    def reset_session(self, session_id: str) -> None:
        self.histories.pop(session_id, None)

    def chat(self, message: str, session_id: str = "default", max_steps: int = 8) -> dict[str, Any]:
        history = self.history(session_id)
        history.append({"role": "user", "content": message})
        traces: list[dict[str, Any]] = []

        for _ in range(max_steps):
            response = self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=history,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0,
            )
            choice = response.choices[0]
            message_out = choice.message
            tool_calls = message_out.tool_calls or []

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": message_out.content or "",
            }
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ]
            history.append(assistant_msg)

            if choice.finish_reason == "stop" or not tool_calls:
                reply = (message_out.content or "").strip()
                return {"reply": reply, "trace": traces, "model": self.settings.openai_model}

            for call in tool_calls:
                name = call.function.name
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                    result = {
                        "status": "error",
                        "message": "Tool arguments were not valid JSON.",
                    }
                else:
                    result = self.guarded.invoke(name, arguments)
                traces.append(
                    {
                        "tool": name,
                        "arguments": arguments,
                        "status": result.get("status"),
                        "decision": result.get("decision"),
                        "approval_id": result.get("approval_id"),
                        "result": result.get("result"),
                        "message": result.get("message"),
                    }
                )
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        return {
            "reply": "I reached the tool-call step limit before finishing. Please try a smaller request.",
            "trace": traces,
            "model": self.settings.openai_model,
        }
