"""Policy interceptor that sits in front of every PayPal tool call."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tillguard.amounts import money
from tillguard.policy import Decision, DecisionKind, PolicyContext, evaluate
from tillguard.store import AppState


class GuardedPaypal:
    def __init__(self, backend, state: AppState):
        self.backend = backend
        self.state = state

    def _context_for(self, tool: str, arguments: dict[str, Any]) -> PolicyContext:
        capture = None
        if tool == "create_refund":
            capture = self.backend.lookup_capture(arguments.get("capture_id"))
        known_amount = None
        refunded = Decimal("0.00")
        if capture:
            if capture.get("amount") not in (None, ""):
                known_amount = money(capture["amount"])
            if capture.get("refunded") not in (None, ""):
                refunded = money(capture["refunded"])
        return PolicyContext(
            environment=self.state.mode if self.state.mode in {"mock", "sandbox"} else "live",
            daily_refunds_executed=self.state.daily_refunds_executed(),
            known_capture_amount=known_amount,
            known_refunded_so_far=refunded,
        )

    def invoke(self, tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        arguments = arguments or {}
        decision = evaluate(tool, arguments, self.state.policy, self._context_for(tool, arguments))

        if decision.kind == DecisionKind.BLOCK:
            entry = self.state.log(tool, arguments, decision, result={"blocked": True})
            return {
                "status": "blocked",
                "decision": decision.to_dict(),
                "audit_id": entry.id,
                "message": (
                    "POLICY BLOCKED this tool call. It was not sent to PayPal. "
                    f"Reason: {decision.reason}"
                ),
            }

        if decision.kind == DecisionKind.NEED_APPROVAL:
            approval = self.state.enqueue_approval(tool, arguments, decision)
            entry = self.state.log(tool, arguments, decision, approval_id=approval.id)
            return {
                "status": "needs_approval",
                "decision": decision.to_dict(),
                "approval_id": approval.id,
                "audit_id": entry.id,
                "message": (
                    "POLICY held this tool call for human approval. It has NOT been sent to PayPal. "
                    f"Approval ID: {approval.id}. Reason: {decision.reason}"
                ),
            }

        result = self.backend.run(tool, arguments)
        entry = self.state.log(tool, arguments, decision, result=result)
        return {
            "status": "allowed",
            "decision": decision.to_dict(),
            "audit_id": entry.id,
            "result": result,
        }

    def execute_approved(self, approval_id: str) -> dict[str, Any]:
        approval = self.state.get_approval(approval_id)
        if approval is None:
            return {"ok": False, "error": f"Approval {approval_id} was not found."}
        if approval.status != "approved":
            return {"ok": False, "error": f"Approval {approval_id} is {approval.status}, not approved."}
        try:
            result = self.backend.run(approval.tool, approval.arguments)
        except Exception as exc:
            approval.status = "failed"
            approval.execute_result = {"error": str(exc)}
            return {"ok": False, "error": str(exc), "approval": approval.to_dict()}
        approval.status = "executed"
        approval.execute_result = result
        return {"ok": True, "approval": approval.to_dict(), "result": result}
