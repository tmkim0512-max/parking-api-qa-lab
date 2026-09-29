"""Tests for the mock server itself: the tool is tested like any other product."""
import time


def test_stub_matches_method_and_path_unmatched_is_404(mock):
    mock.post("/__admin/stubs", json={"method": "GET", "path": "/x", "responses": [{"status": 202,
                                                                                    "body": {"a": 1}}]})
    assert mock.get("/x").status_code == 202
    assert mock.get("/x").json() == {"a": 1}
    assert mock.post("/x").status_code == 404
    assert mock.get("/y").json() == {"error": "no stub"}


def test_sequence_is_served_in_order_last_repeats_and_journaled(mock):
    mock.post("/__admin/stubs", json={"method": "GET", "path": "/s",
                                      "responses": [{"status": 503}, {"status": 200, "body": "raw"}]})
    assert [mock.get("/s", params={"n": i}).status_code for i in range(3)] == [503, 200, 200]
    journal = mock.get("/__admin/requests").json()
    assert [r["query"]["n"] for r in journal] == ["0", "1", "2"]


def test_reset_clears_stubs_and_journal(mock):
    mock.post("/__admin/stubs", json={"method": "GET", "path": "/r", "responses": [{"status": 200}]})
    mock.get("/r")
    mock.post("/__admin/reset")
    assert mock.get("/__admin/requests").json() == []
    assert mock.get("/r").status_code == 404


def test_delay_is_applied(mock):
    mock.post("/__admin/stubs", json={"method": "GET", "path": "/d", "responses": [{"delay_ms": 300}]})
    t0 = time.monotonic()
    mock.get("/d")
    assert time.monotonic() - t0 >= 0.3
