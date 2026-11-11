from tillguard.config import Settings
from tillguard.runtime import Runtime
from tillguard.walkthrough import run_walkthrough


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
    assert statuses["Create a $120 consulting invoice"] == "allowed"
    assert statuses["Block an $800 order that is over the $500 limit"] == "blocked"
    assert statuses["Auto-approve a $10 refund on the seed capture"] == "allowed"
    assert statuses["Hold a $30 refund for a human"] == "needs_approval"
    assert statuses["Block a $9,999 refund that ignores policy"] == "blocked"
    assert runtime.state.captures["CAPDEMO000001"]["refunded"] == "10.00"
    assert result["pending_approval_id"]
    assert not any(refund.get("amount") == "9999.00" for refund in runtime.state.refunds.values())
