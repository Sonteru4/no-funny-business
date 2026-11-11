"""Identifiers that satisfy PayPal Agent Toolkit regexes, used by the mock."""

from __future__ import annotations

import itertools
import string

# Order IDs must match ^[A-Z0-9]{17,32}$
_ORDER_COUNTER = itertools.count(2)
_CAPTURE_COUNTER = itertools.count(2)
_INVOICE_COUNTER = itertools.count(1002)
_REFUND_COUNTER = itertools.count(1)

_ALPHANUM = string.ascii_uppercase + string.digits


def next_order_id() -> str:
    n = next(_ORDER_COUNTER)
    return f"TGORDER{n:011d}"  # 7 + 11 = 18 chars


def next_capture_id() -> str:
    n = next(_CAPTURE_COUNTER)
    return f"CAPDEMO{n:06d}"  # 7 + 6 = 13 chars


def next_invoice_id() -> str:
    n = next(_INVOICE_COUNTER)
    return f"INV-DEMO-{n:04d}"


def next_refund_id() -> str:
    n = next(_REFUND_COUNTER)
    return f"REFDEMO{n:06d}"
