"""ETA integration: the mock server controls the external route API's failures, latency and contract."""
import time

import pytest

OK = {"status": 200, "body": {"eta_sec": 420, "distance_m": 3100}}
PARAMS = {"lot_id": "LOT-A", "from_lat": 37.5665, "from_lng": 126.9780}


def stub(mock, *responses):
    mock.post("/__admin/stubs", json={"method": "GET", "path": "/route", "responses": list(responses)})


def calls(mock):
    return [r for r in mock.get("/__admin/requests").json() if r["path"] == "/route"]


def test_upstream_ok_returns_route_api_eta(api, mock):
    stub(mock, OK)
    body = api.get("/eta", params=PARAMS).json()
    assert body == {"lot_id": "LOT-A", "eta_seconds": 420, "source": "route_api", "reason": None}
    [req] = calls(mock)
    assert float(req["query"]["from_lat"]) == pytest.approx(37.5665)


def test_upstream_500_retries_once_then_falls_back(api, mock):
    """Planted defect D3 returns 500 here instead of falling back."""
    stub(mock, {"status": 500, "body": {"error": "boom"}})
    resp = api.get("/eta", params=PARAMS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "fallback"
    assert resp.json()["reason"] == "upstream_5xx"
    assert resp.json()["eta_seconds"] > 0
    assert len(calls(mock)) == 2


def test_upstream_503_then_200_recovers_on_retry(api, mock):
    stub(mock, {"status": 503}, OK)
    resp = api.get("/eta", params=PARAMS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "route_api"
    assert body["eta_seconds"] == 420
    assert len(calls(mock)) == 2


def test_upstream_slower_than_timeout_falls_back_within_budget(api, mock):
    stub(mock, {**OK, "delay_ms": 3000})
    t0 = time.monotonic()
    body = api.get("/eta", params=PARAMS).json()
    elapsed = time.monotonic() - t0
    assert body["source"] == "fallback"
    assert body["reason"] == "upstream_unavailable"
    assert elapsed < 4.5, f"2s timeout x 2 attempts should finish under 4.5s, took {elapsed:.2f}s"


def test_upstream_contract_change_is_detected(api, mock):
    stub(mock, {"status": 200, "body": {"eta_seconds": 420, "distance_m": 3100}})  # renamed field
    body = api.get("/eta", params=PARAMS).json()
    assert body["source"] == "fallback"
    assert body["reason"] == "contract_violation"
    assert len(calls(mock)) == 1  # contract errors are not retried
