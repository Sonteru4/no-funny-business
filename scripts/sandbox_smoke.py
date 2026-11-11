"""PayPal sandbox smoke test. Every call goes through the same policy interceptor the clerk uses.

Needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET for a sandbox REST app (developer.paypal.com).
Secrets are read from the environment and never printed.

Steps:
  1. Create and send a sandbox invoice.
  2. Create a sandbox order and print its buyer approve URL. With --wait, poll until a sandbox
     buyer approves it, then capture and refund $1.00 on the capture.
  3. Send one clean $1.00 payout to a sandbox personal account (--payout-to or SANDBOX_PAYOUT_RECEIVER),
     after registering that email as a verified vendor on the local desk.
  4. Show the poisoned-note payout being blocked before any PayPal call.

Usage:
  python scripts/sandbox_smoke.py --payout-to sb-buyer@personal.example.com [--wait]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk.config import load_settings  # noqa: E402
from desk.runtime import Runtime  # noqa: E402


def show(label: str, result: dict) -> dict:
    status = result.get("status")
    rule = (result.get("decision") or {}).get("policy_rule")
    body = result.get("result") or {}
    inner = body.get("result") if isinstance(body.get("result"), dict) else body
    ids = {
        k: v
        for k, v in {
            "id": inner.get("id"),
            "status": inner.get("status"),
            "payout_batch_id": (body.get("batch_header") or {}).get("payout_batch_id"),
            "batch_status": (body.get("batch_header") or {}).get("batch_status"),
            "refund_id": (body.get("refund") or {}).get("id"),
            "error": body.get("error"),
        }.items()
        if v
    }
    print(f"- {label}: {status} ({rule}) {ids}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payout-to", default=os.environ.get("SANDBOX_PAYOUT_RECEIVER"))
    parser.add_argument("--invoice-to", default=os.environ.get("SANDBOX_INVOICE_RECIPIENT"))
    parser.add_argument("--wait", action="store_true", help="Wait for buyer approval, then capture and refund.")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.has_paypal_credentials:
        print("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are not set. Nothing to do.")
        return 2
    rt = Runtime(settings)
    if rt.state.mode != "sandbox":
        print("Desk is not in sandbox mode. Unset DESK_PAYPAL_MODE=mock if it is set.")
        return 2
    g = rt.guarded
    print(f"Sandbox smoke via {getattr(rt.backend, 'transport', 'paypal')} (sandbox only, no production calls)")

    recipient = args.invoice_to or args.payout_to or "buyer@example.com"
    created = show("create_invoice", g.invoke("create_invoice", {
        "recipient_email": recipient,
        "items": [{"name": "Smoke test consulting", "quantity": 1, "unit_price": 5}],
        "note": "Sandbox smoke test.",
    }))
    invoice_ids = [i for i, inv in rt.state.invoices.items() if inv.get("source") == "sandbox"]
    if invoice_ids:
        show("send_invoice", g.invoke("send_invoice", {"invoice_id": invoice_ids[-1]}))
    _ = created

    show("create_order", g.invoke("create_order", {"items": [{"name": "Smoke test item", "quantity": 1, "unit_price": 5}]}))
    orders = [o for o in rt.state.orders.values() if o.get("source") == "sandbox"]
    if orders:
        order = orders[-1]
        print(f"  approve URL (log in as a sandbox buyer): {order.get('approve_url')}")
        if args.wait:
            deadline = time.time() + 300
            approved = False
            while time.time() < deadline:
                got = g.invoke("get_order", {"order_id": order["id"]})
                raw = ((got.get("result") or {}).get("result") or {})
                if str(raw.get("status", "")).upper() == "APPROVED":
                    approved = True
                    break
                time.sleep(5)
            if approved:
                show("pay_order (capture)", g.invoke("pay_order", {"order_id": order["id"]}))
                capture_id = rt.state.orders.get(order["id"], {}).get("capture_id")
                if capture_id:
                    show("create_refund $1.00", g.invoke("create_refund", {"capture_id": capture_id, "amount": 1, "reason": "Smoke test"}))
            else:
                print("  order was not approved within 5 minutes; skipped capture and refund")

    if args.payout_to:
        rt.state.vendors["V-SANDBOX"] = {
            "id": "V-SANDBOX",
            "name": "Sandbox test vendor",
            "paypal_email": args.payout_to,
            "verified": True,
            "payee_changed_at": None,
            "source": "smoke",
        }
        show("create_payout $1.00 (clean)", g.invoke("create_payout", {
            "recipient_email": args.payout_to, "amount": 1, "note": "Sandbox smoke payout",
        }))
    else:
        print("- create_payout skipped (pass --payout-to with a sandbox personal account email)")

    g.invoke("get_invoice", {"invoice_id": "BILL-7001"}, user_text="Pay vendor bill BILL-7001.")
    show("poisoned-note payout (should be blocked, no PayPal call)", g.invoke(
        "create_payout",
        {"recipient_email": "refunds-desk@payme-now.example", "amount": 4800},
        user_text="Pay vendor bill BILL-7001.",
    ))
    print(f"Audit chain verified: {rt.state.integrity().get('ok')} ({len(rt.state.audit)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
