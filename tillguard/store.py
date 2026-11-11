"""In-memory audit log, approval queue, and shared demo ledger."""

from __future__ import annotations

import json
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from tillguard.amounts import money, money_str
from tillguard.integrity import fingerprint_key, seal, verify_chain
from tillguard.policy import Decision, DecisionKind, PolicyPack


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class AuditEntry:
    id: str
    ts: str
    tool: str
    arguments: dict[str, Any]
    decision: str
    reason: str
    amount: str | None
    policy_rule: str | None
    result: Any = None
    approval_id: str | None = None
    injection: dict[str, Any] | None = None
    idempotency_key: str | None = None
    prev_hash: str | None = None
    entry_hash: str | None = None
    signature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "tool": self.tool,
            "arguments": self.arguments,
            "decision": self.decision,
            "reason": self.reason,
            "amount": self.amount,
            "policy_rule": self.policy_rule,
            "result": self.result,
            "approval_id": self.approval_id,
            "injection": self.injection,
            "idempotency_key": self.idempotency_key,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "signature": self.signature,
        }


@dataclass
class ApprovalRequest:
    id: str
    ts: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    amount: str | None
    currency: str
    status: str = "pending"  # pending | approved | denied | executed | failed
    decided_at: str | None = None
    execute_result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "tool": self.tool,
            "arguments": self.arguments,
            "reason": self.reason,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "decided_at": self.decided_at,
            "execute_result": self.execute_result,
        }


@dataclass
class AppState:
    mode: str
    policy: PolicyPack
    audit: list[AuditEntry] = field(default_factory=list)
    approvals: list[ApprovalRequest] = field(default_factory=list)
    # The mock (and a cache of live) commerce objects live here too.
    invoices: dict[str, dict[str, Any]] = field(default_factory=dict)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    captures: dict[str, dict[str, Any]] = field(default_factory=dict)
    refunds: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    signing_key: str = field(default_factory=lambda: secrets.token_hex(32))
    pending_policy: dict[str, Any] | None = None
    last_redteam: dict[str, Any] | None = None
    idempotency: dict[str, dict[str, Any]] = field(default_factory=dict)

    def daily_refunds_executed(self) -> Decimal:
        today = _today()
        total = Decimal("0.00")
        for refund in self.refunds.values():
            if refund.get("status") in {"COMPLETED", "PENDING"} and str(refund.get("date", "")).startswith(today):
                total += money(refund.get("amount", "0"))
        return money(total)

    def log(
        self,
        tool: str,
        arguments: dict[str, Any],
        decision: Decision,
        result: Any = None,
        approval_id: str | None = None,
        injection: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> AuditEntry:
        prev = self.audit[0].entry_hash if self.audit and self.audit[0].entry_hash else None
        entry = AuditEntry(
            id=_new_id("aud"),
            ts=_now(),
            tool=tool,
            arguments=arguments,
            decision=decision.kind.value,
            reason=decision.reason,
            amount=money_str(decision.amount) if decision.amount is not None else None,
            policy_rule=decision.policy_rule,
            result=result,
            approval_id=approval_id,
            injection=injection,
            idempotency_key=idempotency_key,
        )
        sealed = seal(entry.to_dict(), prev, self.signing_key)
        entry.prev_hash = sealed["prev_hash"]
        entry.entry_hash = sealed["entry_hash"]
        entry.signature = sealed["signature"]
        self.audit.insert(0, entry)
        return entry

    def integrity(self) -> dict[str, Any]:
        chronological = list(reversed(self.audit))
        report = verify_chain([item.to_dict() for item in chronological], self.signing_key)
        report["key_fingerprint"] = fingerprint_key(self.signing_key)
        return report

    def enqueue_approval(self, tool: str, arguments: dict[str, Any], decision: Decision) -> ApprovalRequest:
        req = ApprovalRequest(
            id=_new_id("apr"),
            ts=_now(),
            tool=tool,
            arguments=arguments,
            reason=decision.reason,
            amount=money_str(decision.amount) if decision.amount is not None else None,
            currency=decision.currency,
        )
        self.approvals.insert(0, req)
        return req

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        for item in self.approvals:
            if item.id == approval_id:
                return item
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "policy": self.policy.to_dict(),
            "daily_refunds_executed": money_str(self.daily_refunds_executed()),
            "audit": [item.to_dict() for item in self.audit[:50]],
            "approvals": [item.to_dict() for item in self.approvals[:50]],
            "invoices": list(self.invoices.values()),
            "orders": list(self.orders.values()),
            "captures": list(self.captures.values()),
            "refunds": list(self.refunds.values()),
            "integrity": self.integrity(),
            "pending_policy": self.pending_policy,
            "redteam": self.last_redteam,
        }


def seed_demo_data(state: AppState) -> None:
    """Seed a paid capture and a sent invoice so refund demos have something to act on."""
    order_id = "TGORDER00000000001"
    capture_id = "CAPDEMO000001"
    invoice_id = "INV-DEMO-1001"
    state.orders[order_id] = {
        "id": order_id,
        "status": "COMPLETED",
        "amount": "40.00",
        "currency": "USD",
        "items": [{"name": "Workshop seat", "quantity": 1, "unit_price": "40.00"}],
        "capture_id": capture_id,
        "source": "seed",
    }
    state.captures[capture_id] = {
        "id": capture_id,
        "order_id": order_id,
        "status": "COMPLETED",
        "amount": "40.00",
        "currency": "USD",
        "refunded": "0.00",
        "source": "seed",
    }
    state.invoices[invoice_id] = {
        "id": invoice_id,
        "status": "SENT",
        "amount": "120.00",
        "currency": "USD",
        "recipient_email": "alex@example.com",
        "items": [{"name": "Consulting", "quantity": 2, "unit_price": "60.00"}],
        "note": "Seed invoice for the demo desk.",
        "source": "seed",
    }


def format_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)
