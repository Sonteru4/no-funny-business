from tillguard.redteam import run_suite


def test_red_team_suite_blocks_every_attack_and_allows_benign_cases():
    report = run_suite()
    assert report["isolated"] is True
    assert report["attacks_blocked"] == report["attacks_total"]
    assert report["benign_passed"] == report["benign_total"]
    assert report["ok"] is True
    failed = [item for item in report["cases"] if not item["pass"]]
    assert failed == []
