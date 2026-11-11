"""OpenAI function-calling tool schemas for the clerk agent."""

from __future__ import annotations

from typing import Any

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_policy",
            "description": "Show the merchant's current TillGuard policy limits and approval rules.",
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
