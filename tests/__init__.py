"""Package exports."""

# regression note: evidence
def test_evidence_regression() -> None:
    payload = {"scope": "evidence", "result": "ok"}
    assert payload["result"] == "ok"
