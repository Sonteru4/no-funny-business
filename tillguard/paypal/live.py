"""Live PayPal sandbox backend.

Prefers the official PayPal Agent Toolkit (paypal-agent-toolkit) PayPalAPI
for orders and invoices. create_refund is implemented on the Payments API
because toolkit 1.11.0 does not ship that tool, even though PayPal documents it.
"""

from __future__ import annotations

import json
from typing import Any

from tillguard.amounts import extract_amount, money, money_str
from tillguard.config import Settings
from tillguard.store import AppState


def _as_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
            return {"raw": parsed}
        except json.JSONDecodeError:
            return {"raw": result}
    return {"raw": result}


class LivePayPalBackend:
    def __init__(self, settings: Settings, state: AppState):
        if not settings.has_paypal_credentials:
            raise RuntimeError("PayPal sandbox credentials are required for live mode.")
        self.settings = settings
        self.state = state
        self._toolkit_api = None
        self._client = None
        self._init_clients()

    def _init_clients(self) -> None:
        try:
            from paypal_agent_toolkit.shared.api import PayPalAPI
            from paypal_agent_toolkit.shared.configuration import Context

            self._toolkit_api = PayPalAPI(
                client_id=self.settings.paypal_client_id,
                secret=self.settings.paypal_client_secret,
                context=Context(sandbox=True),
            )
            self._client = self._toolkit_api._paypal_client
            self.transport = "paypal-agent-toolkit"
            return
        except Exception:
            self.transport = "paypal-rest"
            self._client = RestPayPalClient(
                client_id=self.settings.paypal_client_id or "",
                secret=self.settings.paypal_client_secret or "",
                sandbox=True,
            )

    def lookup_capture(self, capture_id: str | None) -> dict[str, Any] | None:
        if not capture_id:
            return None
        cached = self.state.captures.get(capture_id)
        if cached:
            return cached
        if not hasattr(self._client, "get"):
            return None
        try:
            raw = self._client.get(f"/v2/payments/captures/{capture_id}")
            amount = (raw.get("amount") or {}).get("value")
            currency = (raw.get("amount") or {}).get("currency_code") or "USD"
            record = {
                "id": capture_id,
                "status": raw.get("status"),
                "amount": amount,
                "currency": currency,
                "refunded": ((raw.get("seller_receivable_breakdown") or {}).get("total_refunded_amount") or {}).get("value")
                or "0.00",
                "source": "sandbox",
                "raw": raw,
            }
            self.state.captures[capture_id] = record
            return record
        except Exception:
            return None

    def run(self, method: str, params: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        self._last_idempotency_key = idempotency_key
        if method == "create_refund":
            return self._create_refund(params)
        if method in {"list_policy", "list_pending_approvals"}:
            # These are local tools, not PayPal APIs.
            if method == "list_policy":
                return {"ok": True, "policy": self.state.policy.to_dict()}
            pending = [item.to_dict() for item in self.state.approvals if item.status == "pending"]
            return {"ok": True, "count": len(pending), "approvals": pending}

        if self._toolkit_api is not None and method in {
            "create_order",
            "get_order",
            "get_order_details",
            "pay_order",
            "create_invoice",
            "list_invoices",
            "get_invoice",
            "send_invoice",
            "record_refund_for_invoice",
        }:
            toolkit_method = "get_order_details" if method == "get_order" else method
            official = self._to_official_params(method, params)
            raw = self._toolkit_api.run(toolkit_method, official)
            parsed = _as_dict(raw)
            self._cache_from_result(method, params, parsed)
            return {"ok": True, "transport": "paypal-agent-toolkit", "result": parsed}

        # Fallback REST for get_order alias or when the toolkit is unavailable.
        return self._rest_fallback(method, params)

    def _to_official_params(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "create_order":
            items = []
            for item in params.get("items") or []:
                qty = int(item.get("quantity") or 1)
                cost = float(money(item.get("unit_price") or item.get("item_cost") or 0))
                items.append(
                    {
                        "name": item.get("name") or "Item",
                        "quantity": qty,
                        "item_cost": cost,
                        "item_total": cost * qty,
                        "tax_percent": 0,
                        "description": item.get("description"),
                    }
                )
            return {"currency_code": params.get("currency") or "USD", "items": items}
        if method == "create_invoice":
            official_items = []
            currency = params.get("currency") or params.get("currency_code") or "USD"
            for item in params.get("items") or []:
                official_items.append(
                    {
                        "name": item.get("name") or "Item",
                        "quantity": str(item.get("quantity") or 1),
                        "unit_amount": {
                            "currency_code": currency,
                            "value": money_str(item.get("unit_price") or 0),
                        },
                    }
                )
            return {
                "currency_code": currency,
                "note": params.get("note"),
                "primary_recipients": [
                    {"billing_info": {"email_address": params.get("recipient_email")}}
                ],
                "items": official_items,
            }
        if method == "record_refund_for_invoice":
            amount = params.get("amount")
            payload: dict[str, Any] = {
                "invoice_id": params.get("invoice_id"),
                "method": params.get("method") or "PAYPAL",
            }
            if amount not in (None, ""):
                if isinstance(amount, dict):
                    payload["amount"] = amount
                else:
                    payload["amount"] = {
                        "currency_code": params.get("currency") or "USD",
                        "value": money_str(amount),
                    }
            return payload
        if method in {"get_order", "pay_order"}:
            return {"order_id": params.get("order_id")}
        if method in {"get_invoice", "send_invoice"}:
            return {"invoice_id": params.get("invoice_id"), "send_to_recipient": True}
        if method == "list_invoices":
            return {
                "page": params.get("page") or 1,
                "page_size": params.get("page_size") or 20,
            }
        return params

    def _cache_from_result(self, method: str, params: dict[str, Any], parsed: dict[str, Any]) -> None:
        if method == "create_order":
            order_id = parsed.get("id")
            if order_id:
                amount = extract_amount(params)
                links = parsed.get("links") or []
                approve = next((link.get("href") for link in links if link.get("rel") == "approve"), None)
                self.state.orders[order_id] = {
                    "id": order_id,
                    "status": parsed.get("status", "CREATED"),
                    "amount": money_str(amount) if amount is not None else None,
                    "currency": params.get("currency") or "USD",
                    "items": params.get("items") or [],
                    "approve_url": approve,
                    "source": "sandbox",
                }
        if method == "pay_order":
            raw = parsed.get("raw") or parsed
            captures = (
                ((raw.get("purchase_units") or [{}])[0].get("payments") or {}).get("captures") or []
            )
            if captures:
                cap = captures[0]
                cap_id = cap.get("id")
                order_id = params.get("order_id")
                amount = (cap.get("amount") or {}).get("value")
                currency = (cap.get("amount") or {}).get("currency_code") or "USD"
                if cap_id:
                    self.state.captures[cap_id] = {
                        "id": cap_id,
                        "order_id": order_id,
                        "status": cap.get("status", "COMPLETED"),
                        "amount": amount,
                        "currency": currency,
                        "refunded": "0.00",
                        "source": "sandbox",
                    }
                    if order_id and order_id in self.state.orders:
                        self.state.orders[order_id]["capture_id"] = cap_id
                        self.state.orders[order_id]["status"] = "COMPLETED"
        if method == "create_invoice":
            href = (parsed.get("href") or "")
            invoice_id = parsed.get("id")
            if not invoice_id and "/invoices/" in href:
                invoice_id = href.rstrip("/").split("/")[-1]
            if invoice_id:
                self.state.invoices[invoice_id] = {
                    "id": invoice_id,
                    "status": "DRAFT",
                    "amount": money_str(extract_amount(params) or 0),
                    "currency": params.get("currency") or "USD",
                    "recipient_email": params.get("recipient_email"),
                    "items": params.get("items") or [],
                    "source": "sandbox",
                }


    def _post_with_idempotency(self, uri: str, payload: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        client = self._client
        if isinstance(client, RestPayPalClient):
            return client.post(uri, payload, request_id=idempotency_key)
        # Official toolkit client: still send the header when the method allows it.
        if idempotency_key and hasattr(client, "post"):
            try:
                return client.post(uri, payload, headers={"PayPal-Request-Id": idempotency_key})
            except TypeError:
                pass
        return client.post(uri, payload)

    def _create_refund(self, params: dict[str, Any]) -> dict[str, Any]:
        capture_id = params.get("capture_id")
        if not capture_id:
            return {"ok": False, "error": "capture_id is required."}
        payload: dict[str, Any] = {}
        if params.get("amount") not in (None, ""):
            payload["amount"] = {
                "value": money_str(params["amount"]),
                "currency_code": params.get("currency") or "USD",
            }
        try:
            raw = self._post_with_idempotency(
                f"/v2/payments/captures/{capture_id}/refund",
                payload or {},
                getattr(self, "_last_idempotency_key", None),
            )
        except Exception as exc:
            return {"ok": False, "error": "PayPal refund failed."}
        parsed = _as_dict(raw)
        refund_id = parsed.get("id")
        amount = (parsed.get("amount") or {}).get("value") or params.get("amount")
        record = {
            "id": refund_id,
            "capture_id": capture_id,
            "status": parsed.get("status", "COMPLETED"),
            "amount": money_str(amount) if amount not in (None, "") else None,
            "currency": (parsed.get("amount") or {}).get("currency_code") or params.get("currency") or "USD",
            "source": "sandbox",
        }
        if refund_id:
            self.state.refunds[refund_id] = record
        return {"ok": True, "transport": self.transport, "refund": record, "raw": parsed}

    def _rest_fallback(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": f"No live handler for {method}."}


class RestPayPalClient:
    """Minimal PayPal REST client used only if the official toolkit cannot be imported."""

    def __init__(self, client_id: str, secret: str, sandbox: bool = True):
        self.client_id = client_id
        self.secret = secret
        self.base_url = "https://api-m.sandbox.paypal.com" if sandbox else "https://api-m.paypal.com"

    def _token(self) -> str:
        import requests

        response = requests.post(
            f"{self.base_url}/v1/oauth2/token",
            headers={"Accept": "application/json"},
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.secret),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError("PayPal did not return an access token.")
        return token

    def _headers(self, request_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        return headers

    def post(self, uri: str, payload: dict[str, Any] | None, request_id: str | None = None) -> dict[str, Any]:
        import requests

        response = requests.post(
            f"{self.base_url}{uri}",
            headers=self._headers(request_id=request_id),
            json=payload or {},
            timeout=30,
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def get(self, uri: str) -> dict[str, Any]:
        import requests

        response = requests.get(f"{self.base_url}{uri}", headers=self._headers(), timeout=30)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()
