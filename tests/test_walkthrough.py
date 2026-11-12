from desk.config import Settings
from desk.runtime import Runtime
from desk.walkthrough import run_walkthrough


def test_offline_walkthrough_covers_allow_hold_and_block():
    settings = Settings(
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        paypal_client_id=None,
        paypal_client_secret=None,
        paypal_mode_override="mock",
        host="127.0.0.1",
        port=8000,
    )
    runtime = Runtime(settings)
    result = run_walkthrough(runtime)
    statuses = {step["title"]: step["status"] for step in result["steps"]}
    assert statuses["Stop the $47,316 payout the note asked for"] == "blocked"
    assert statuses["Pay the real $340 bill to the verified vendor"] == "allowed"
    assert statuses["Before fix: another +$8 also passes (split increase)"] == "allowed"
    assert statuses["After fix: the second +$8 is caught by the daily cap"] == "blocked"
    assert statuses["After fix: payee redirect on I-SUBDEMO0001 waits for a human"] == "needs_approval"
    assert statuses["Hold Northwind's payout email change"] == "needs_approval"
    assert statuses["Stop the $1,200 payout to the unconfirmed new email"] == "blocked"
    assert statuses["Stop a refund on a capture with an open dispute"] == "blocked"
    assert statuses["Stop the same $10 refund issued twice"] == "blocked"
    assert runtime.state.flagged_records and runtime.state.flagged_records[0]["record_id"] == "BILL-7001"
    assert statuses["Stop a $480 refund promised in chat but outside the posted 30-day window"] == "blocked"
    assert runtime.state.captures["CAPLATE000001"]["refunded"] == "0.00"
    assert statuses["Auto-approve a $10 refund on the seed capture"] == "allowed"
    assert statuses["Hold a $30 refund for a human"] == "needs_approval"
    assert statuses["Block a $9,999 refund that ignores policy"] == "blocked"
    assert runtime.state.captures["CAPDEMO000001"]["refunded"] == "10.00"
    assert result["pending_approval_id"]
    assert not any(refund.get("amount") == "9999.00" for refund in runtime.state.refunds.values())
