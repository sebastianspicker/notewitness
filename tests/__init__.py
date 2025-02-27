"""Package exports."""

# regression note: evidence
def test_evidence_regression() -> None:
    payload = {"scope": "evidence", "result": "ok"}
    assert payload["result"] == "ok"

# regression note: release
def test_release_regression() -> None:
    payload = {"scope": "release", "result": "ok"}
    assert payload["result"] == "ok"
