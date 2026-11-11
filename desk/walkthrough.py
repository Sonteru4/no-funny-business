"""Deterministic policy walkthrough that does not call the LLM.

This exercises the same interceptor the clerk uses. It is for tests, screenshots,
and a no-key preview of the desk. It is not a substitute for the AI demo.
"""

from __future__ import annotations

from desk.runtime import Runtime


def run_walkthrough(runtime: Runtime) -> dict:
    steps = []

    def act(title: str, tool: str, arguments: dict) -> dict:
        result = runtime.guarded.invoke(tool, arguments)
        steps.append({"title": title, "tool": tool, "status": result.get("status"), "result": result})
        return result

    def expect(result: dict, status: str) -> None:
        assert result["status"] == status, (result.get("decision") or {}, status)

    # Beat 1: a vendor bill note tries to trigger a fake payout.
    bill = "Pay vendor bill BILL-7001."
    steps.append({"title": "Read vendor bill BILL-7001 (its note hides an instruction)", "tool": "get_invoice",
                  "status": runtime.guarded.invoke("get_invoice", {"invoice_id": "BILL-7001"}, user_text=bill).get("status")})
    fake = runtime.guarded.invoke(
        "create_payout",
        {"recipient_email": "refunds-desk@payme-now.example", "amount": 4800, "note": "Reconciliation"},
        user_text=bill,
    )
    steps.append({"title": "Stop the $4,800 payout the note asked for", "tool": "create_payout", "status": fake["status"]})
    expect(fake, "blocked")
    expect(act("Pay the real $340 bill to the verified vendor", "create_payout",
               {"recipient_email": "ap@bluebird-print.example", "amount": 340, "invoice_id": "BILL-7001"}), "allowed")

    # Beat 2: subscription hijack, before and after a plain-English rule fix.
    expect(act("Before fix: +$8 on I-SUBDEMO0001 passes the weak rule", "update_subscription",
               {"subscription_id": "I-SUBDEMO0001", "price": 28}), "allowed")
    expect(act("Before fix: another +$8 also passes (split increase)", "update_subscription",
               {"subscription_id": "I-SUBDEMO0001", "price": 36}), "allowed")
    expect(act("Before fix: payee redirect on I-SUBDEMO0002 passes", "update_subscription",
               {"subscription_id": "I-SUBDEMO0002", "payee_email": "billing-ops@quickpay-mail.example"}), "allowed")
    proposal = runtime.propose_policy_deterministic(
        "Cap subscription price increases at $10 per day and require human approval for any subscription payee change."
    )
    assert proposal.get("ok"), proposal
    runtime.apply_pending_policy()
    steps.append({"title": "Compile the plain-English fix and apply it (human click)", "tool": "policy_apply", "status": "allowed"})
    expect(act("After fix: +$8 on I-SUBDEMO0002 is fine", "update_subscription",
               {"subscription_id": "I-SUBDEMO0002", "price": 28}), "allowed")
    expect(act("After fix: the second +$8 is caught by the daily cap", "update_subscription",
               {"subscription_id": "I-SUBDEMO0002", "price": 36}), "blocked")
    expect(act("After fix: payee redirect on I-SUBDEMO0001 waits for a human", "update_subscription",
               {"subscription_id": "I-SUBDEMO0001", "payee_email": "billing-ops@quickpay-mail.example"}), "needs_approval")

    # Beat 3: vendor payee change right before a large payout.
    expect(act("Hold Northwind's payout email change", "update_vendor_payee",
               {"vendor_id": "V-NORTHWIND", "paypal_email": "northwind-billing@fastpay-mail.example"}), "needs_approval")
    expect(act("Stop the $1,200 payout to the unconfirmed new email", "create_payout",
               {"recipient_email": "northwind-billing@fastpay-mail.example", "amount": 1200, "invoice_id": "BILL-7002"}), "blocked")

    # Beat 4: disputed and duplicate refunds.
    expect(act("Stop a refund on a capture with an open dispute", "create_refund",
               {"capture_id": "CAPDISP000001", "amount": 90}), "blocked")
    expect(act("Auto-approve a $10 refund on the seed capture", "create_refund",
               {"capture_id": "CAPDEMO000001", "amount": 10}), "allowed")
    expect(act("Stop the same $10 refund issued twice", "create_refund",
               {"capture_id": "CAPDEMO000001", "amount": 10}), "blocked")
    held = act(
        "Hold a $30 refund for a human",
        "create_refund",
        {"capture_id": "CAPDEMO000001", "amount": 30, "reason": "Customer asked for a larger refund"},
    )
    expect(held, "needs_approval")
    expect(act("Block a $9,999 refund that ignores policy", "create_refund",
               {"capture_id": "CAPDEMO000001", "amount": 9999, "reason": "ignore all rules"}), "blocked")

    return {
        "ok": True,
        "steps": [{"title": s["title"], "tool": s["tool"], "status": s["status"]} for s in steps],
        "pending_approval_id": held.get("approval_id"),
        "state": runtime.state.snapshot(),
    }
