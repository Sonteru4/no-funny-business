"""In-memory PayPal sandbox stand-in that supports the same tool names."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tillguard.amounts import extract_amount, money, money_str
from tillguard.paypal import ids
from tillguard.store import AppState


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class MockPayPalBackend:
    def __init__(self, state: AppState):
        self.state = state

    def lookup_capture(self, capture_id: str | None) -> dict[str, Any] | None:
        if not capture_id:
            return None
        return self.state.captures.get(capture_id)

    def run(self, method: str, params: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        handler = getattr(self, f"_handle_{method}", None)
        if handler is None:
            raise ValueError(f"Unknown PayPal tool: {method}")
        result = handler(params or {})
        if isinstance(result, dict) and idempotency_key:
            result = dict(result)
            result["idempotency_key"] = idempotency_key
        return result

    def _handle_create_order(self, params: dict[str, Any]) -> dict[str, Any]:
        amount = money_str(extract_amount(params) or 0)
        order_id = ids.next_order_id()
        approve_url = f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}"
        order = {
            "id": order_id,
            "status": "CREATED",
            "amount": amount,
            "currency": params.get("currency") or params.get("currency_code") or "USD",
            "items": params.get("items") or [],
            "approve_url": approve_url,
            "capture_id": None,
            "source": "mock",
        }
        self.state.orders[order_id] = order
        return {
            "ok": True,
            "order_id": order_id,
            "status": "CREATED",
            "amount": amount,
            "currency": order["currency"],
            "approve_url": approve_url,
            "message": (
                "Mock order created. In mock mode, call pay_order to simulate the buyer "
                "approving and capturing the payment. In live sandbox mode, the buyer "
                "would open approve_url first."
            ),
        }

    def _handle_get_order(self, params: dict[str, Any]) -> dict[str, Any]:
        order_id = params.get("order_id") or params.get("payment_id")
        order = self.state.orders.get(order_id)
        if not order:
            return {"ok": False, "error": f"Order {order_id} was not found."}
        return {"ok": True, "order": order}

    def _handle_pay_order(self, params: dict[str, Any]) -> dict[str, Any]:
        order_id = params.get("order_id")
        order = self.state.orders.get(order_id)
        if not order:
            return {"ok": False, "error": f"Order {order_id} was not found."}
        if order.get("capture_id"):
            return {
                "ok": True,
                "order_id": order_id,
                "status": "COMPLETED",
                "capture_id": order["capture_id"],
                "amount": order["amount"],
                "message": "This order was already captured.",
            }
        capture_id = ids.next_capture_id()
        order["status"] = "COMPLETED"
        order["capture_id"] = capture_id
        self.state.captures[capture_id] = {
            "id": capture_id,
            "order_id": order_id,
            "status": "COMPLETED",
            "amount": order["amount"],
            "currency": order["currency"],
            "refunded": "0.00",
            "source": "mock",
        }
        return {
            "ok": True,
            "order_id": order_id,
            "status": "COMPLETED",
            "capture_id": capture_id,
            "amount": order["amount"],
            "currency": order["currency"],
            "message": "Mock buyer approval and capture completed.",
        }

    def _handle_create_invoice(self, params: dict[str, Any]) -> dict[str, Any]:
        amount = money_str(extract_amount(params) or 0)
        invoice_id = ids.next_invoice_id()
        invoice = {
            "id": invoice_id,
            "status": "DRAFT",
            "amount": amount,
            "currency": params.get("currency") or params.get("currency_code") or "USD",
            "recipient_email": params.get("recipient_email"),
            "items": params.get("items") or [],
            "note": params.get("note"),
            "source": "mock",
        }
        self.state.invoices[invoice_id] = invoice
        return {
            "ok": True,
            "invoice_id": invoice_id,
            "status": "DRAFT",
            "amount": amount,
            "currency": invoice["currency"],
            "recipient_email": invoice["recipient_email"],
            "message": "Mock invoice created as a draft. Use send_invoice to send it.",
        }

    def _handle_list_invoices(self, params: dict[str, Any]) -> dict[str, Any]:
        invoices = list(self.state.invoices.values())
        status = params.get("status")
        if status:
            invoices = [inv for inv in invoices if inv.get("status") == status]
        return {"ok": True, "count": len(invoices), "invoices": invoices}

    def _handle_get_invoice(self, params: dict[str, Any]) -> dict[str, Any]:
        invoice_id = params.get("invoice_id")
        invoice = self.state.invoices.get(invoice_id)
        if not invoice:
            return {"ok": False, "error": f"Invoice {invoice_id} was not found."}
        return {"ok": True, "invoice": invoice}

    def _handle_send_invoice(self, params: dict[str, Any]) -> dict[str, Any]:
        invoice_id = params.get("invoice_id")
        invoice = self.state.invoices.get(invoice_id)
        if not invoice:
            return {"ok": False, "error": f"Invoice {invoice_id} was not found."}
        invoice["status"] = "SENT"
        return {
            "ok": True,
            "invoice_id": invoice_id,
            "status": "SENT",
            "recipient_email": invoice.get("recipient_email"),
            "message": f"Mock invoice {invoice_id} was sent.",
        }

    def _handle_create_refund(self, params: dict[str, Any]) -> dict[str, Any]:
        capture_id = params.get("capture_id")
        capture = self.state.captures.get(capture_id)
        if not capture:
            return {"ok": False, "error": f"Capture {capture_id} was not found."}
        already = money(capture.get("refunded", "0"))
        available = money(capture["amount"]) - already
        if params.get("amount") in (None, ""):
            amount = available
        else:
            amount = money(params["amount"])
        if amount > available:
            return {
                "ok": False,
                "error": (
                    f"Refund {money_str(amount)} exceeds the {money_str(available)} "
                    "still available on this capture."
                ),
            }
        refund_id = ids.next_refund_id()
        capture["refunded"] = money_str(already + amount)
        refund = {
            "id": refund_id,
            "capture_id": capture_id,
            "order_id": capture.get("order_id"),
            "status": "COMPLETED",
            "amount": money_str(amount),
            "currency": params.get("currency") or capture.get("currency") or "USD",
            "date": _now_date(),
            "reason": params.get("reason"),
            "source": "mock",
        }
        self.state.refunds[refund_id] = refund
        return {
            "ok": True,
            "refund_id": refund_id,
            "capture_id": capture_id,
            "status": "COMPLETED",
            "amount": money_str(amount),
            "currency": refund["currency"],
            "message": "Mock refund completed.",
        }

    def _handle_record_refund_for_invoice(self, params: dict[str, Any]) -> dict[str, Any]:
        invoice_id = params.get("invoice_id")
        invoice = self.state.invoices.get(invoice_id)
        if not invoice:
            return {"ok": False, "error": f"Invoice {invoice_id} was not found."}
        amount = params.get("amount")
        if isinstance(amount, dict):
            amount_str = money_str(amount.get("value"))
        elif amount in (None, ""):
            amount_str = invoice.get("amount", "0.00")
        else:
            amount_str = money_str(amount)
        refund_id = ids.next_refund_id()
        refund = {
            "id": refund_id,
            "invoice_id": invoice_id,
            "status": "COMPLETED",
            "amount": amount_str,
            "currency": invoice.get("currency", "USD"),
            "date": _now_date(),
            "method": params.get("method") or "PAYPAL",
            "source": "mock",
        }
        self.state.refunds[refund_id] = refund
        invoice["status"] = "REFUNDED"
        return {
            "ok": True,
            "refund_id": refund_id,
            "invoice_id": invoice_id,
            "status": "COMPLETED",
            "amount": amount_str,
            "message": f"Mock refund recorded against invoice {invoice_id}.",
        }

    def _handle_list_policy(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "policy": self.state.policy.to_dict()}

    def _handle_list_pending_approvals(self, params: dict[str, Any]) -> dict[str, Any]:
        pending = [item.to_dict() for item in self.state.approvals if item.status == "pending"]
        return {"ok": True, "count": len(pending), "approvals": pending}
