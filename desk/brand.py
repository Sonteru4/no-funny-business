"""Single source of truth for the product name.

Final rename: change PRODUCT_NAME here (or set DESK_PRODUCT_NAME), then run
scripts/set-name.sh "New Name" to replace the {{PRODUCT_NAME}} token in docs.
"""

from __future__ import annotations

import os

PRODUCT_NAME = os.environ.get("DESK_PRODUCT_NAME", "").strip() or "Merchant Desk"
TAGLINE = "AI clerk for PayPal payouts, subscriptions, vendor payments, and refunds, with policy enforced in code"
