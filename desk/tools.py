"""OpenAI function-calling tool schemas for the clerk agent."""

from __future__ import annotations

from typing import Any

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_policy",
            "description": "Show the merchant's current desk policy limits and approval rules.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pending_approvals",
            "description": "List refunds or other money actions waiting for a human to approve or deny.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_invoice",
            "description": (
                "Create a PayPal invoice draft for a recipient. "
                "The policy layer will block invoices above the invoice limit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_email": {
                        "type": "string",
                        "description": "Email address of the invoice recipient.",
                    },
                    "items": {
                        "type": "array",
                        "description": "Line items on the invoice.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit_price": {"type": "number"},
                            },
                            "required": ["name", "quantity", "unit_price"],
                            "additionalProperties": False,
                        },
                    },
                    "note": {"type": "string", "description": "Optional note on the invoice."},
                    "currency": {"type": "string", "description": "Currency code, default USD."},
                },
                "required": ["recipient_email", "items"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_invoices",
            "description": "List invoices on the merchant desk, with optional status filter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional status filter such as DRAFT, SENT, PAID, or REFUNDED.",
                    },
                    "page": {"type": "integer"},
                    "page_size": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_invoice",
            "description": "Get details for one invoice by ID.",
            "parameters": {
                "type": "object",
                "properties": {"invoice_id": {"type": "string"}},
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_invoice",
            "description": "Send an existing draft invoice to the recipient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": (
                "Create a PayPal checkout order. "
                "The policy layer will block orders above the order limit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit_price": {"type": "number"},
                            },
                            "required": ["name", "quantity", "unit_price"],
                            "additionalProperties": False,
                        },
                    },
                    "currency": {"type": "string"},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Get details for a PayPal order, including capture IDs after payment.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pay_order",
            "description": (
                "Capture payment for an existing order. In mock mode this also simulates "
                "buyer approval. In live sandbox mode, the buyer must approve the order first."
            ),
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_refund",
            "description": (
                "Refund a captured PayPal payment. Refunds at or above the auto-approve "
                "limit are not sent to PayPal until a human approves them. Refunds that "
                "break the daily cap or exceed the capture are blocked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "capture_id": {"type": "string", "description": "Captured payment ID to refund."},
                    "amount": {
                        "type": "number",
                        "description": "Refund amount. If omitted, the remaining capture balance is used.",
                    },
                    "currency": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["capture_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_refund_for_invoice",
            "description": (
                "Record a refund against a PayPal invoice. The same refund policy applies: "
                "low amounts auto-approve, higher amounts need a human, daily caps can block."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                    "method": {"type": "string", "description": "Payment method, default PAYPAL."},
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        },
    },
]


def _fn(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS += [
    _fn(
        "create_payout",
        "Send money to a vendor with the PayPal Payouts API. Only verified vendor payees are allowed, and large "
        "payouts or payouts right after a payee change wait for a human.",
        {
            "recipient_email": {"type": "string", "description": "PayPal email of the payee."},
            "amount": {"type": "number"},
            "currency": {"type": "string"},
            "note": {"type": "string", "description": "Short note for the payee."},
            "invoice_id": {"type": "string", "description": "Vendor bill this payout settles, if any."},
        },
        ["recipient_email", "amount"],
    ),
    _fn("list_vendors", "List vendors and their verified PayPal payout emails."),
    _fn("get_vendor", "Look up one vendor by id.", {"vendor_id": {"type": "string"}}, ["vendor_id"]),
    _fn(
        "update_vendor_payee",
        "Change the PayPal email a vendor is paid at. This always needs human approval.",
        {"vendor_id": {"type": "string"}, "paypal_email": {"type": "string"}},
        ["vendor_id", "paypal_email"],
    ),
    _fn("list_subscriptions", "List customer subscriptions (PayPal Subscriptions API)."),
    _fn("get_subscription", "Look up one subscription by id.", {"subscription_id": {"type": "string"}}, ["subscription_id"]),
    _fn(
        "update_subscription",
        "Revise a subscription: change its price, move it to another plan (P-BASIC, P-PRO, P-TEAM, P-ENTERPRISE), "
        "or change the payee that receives its payments.",
        {
            "subscription_id": {"type": "string"},
            "price": {"type": "number"},
            "plan_id": {"type": "string"},
            "payee_email": {"type": "string"},
        },
        ["subscription_id"],
    ),
    _fn("list_disputes", "List open and closed PayPal disputes."),
    _fn("get_dispute", "Look up one PayPal dispute by id.", {"dispute_id": {"type": "string"}}, ["dispute_id"]),
]
