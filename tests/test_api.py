"""SUT API tests: happy path, errors, boundaries, contract, concurrency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import jsonschema
import pytest

from tests.conftest import SUT_URL, reservation

# --- happy path ---------------------------------------------------------------


def test_reservation_lifecycle_updates_lot_occupancy(api, window):
    before = api.get("/lots/LOT-A").json()["active_reservations"]
    start, end = window(hours=2)

    created = api.post("/reservations", json=reservation(start, end))
    assert created.status_code == 201
    rid = created.json()["id"]
    assert created.json()["status"] == "confirmed"
    assert api.get("/lots/LOT-A").json()["active_reservations"] == before + 1

    fetched = api.get(f"/reservations/{rid}").json()
    assert fetched["plate"] == "12가3456"
    assert datetime.fromisoformat(fetched["start_at"]) == start

    assert api.delete(f"/reservations/{rid}").status_code == 204
    assert api.get(f"/reservations/{rid}").json()["status"] == "cancelled"
    assert api.get("/lots/LOT-A").json()["active_reservations"] == before


def test_get_lot_returns_capacity_and_location(api):
    body = api.get("/lots/LOT-A").json()
    assert body["id"] == "LOT-A"
    assert body["capacity"] == 10
    assert {"lat", "lng", "name", "active_reservations"} <= body.keys()


# --- errors -------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, code",
    [("/lots/NOPE", "LOT_NOT_FOUND"), ("/reservations/doesnotexist", "RESERVATION_NOT_FOUND")],
)
def test_unknown_resource_is_404(api, path, code):
    resp = api.get(path)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == code


@pytest.mark.parametrize("plate", ["12가 3456", "12A3456", "1가3456", "1234가5678"])
def test_invalid_plate_is_422(api, window, plate):
    resp = api.post("/reservations", json=reservation(*window(hours=1), plate=plate))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_PLATE"


def test_end_before_start_is_422(api, window):
    start, _ = window(hours=1)
    resp = api.post("/reservations", json=reservation(start, start - timedelta(hours=1)))
    assert resp.json()["detail"]["code"] == "INVALID_DURATION"


def test_start_in_past_is_422(api):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    resp = api.post("/reservations", json=reservation(start, start + timedelta(hours=1)))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "START_IN_PAST"


def test_double_cancel_is_409(api, window):
    rid = api.post("/reservations", json=reservation(*window(hours=1))).json()["id"]
    assert api.delete(f"/reservations/{rid}").status_code == 204
    resp = api.delete(f"/reservations/{rid}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ALREADY_CANCELLED"


# --- boundaries ---------------------------------------------------------------


@pytest.mark.parametrize(
    "duration, expected",
    [
        (timedelta(minutes=29), 422),
        (timedelta(minutes=30), 201),
        (timedelta(hours=24), 201),  # planted defect D1 breaks this case
        (timedelta(hours=24, minutes=1), 422),
    ],
    ids=["29m", "30m", "24h", "24h+1m"],
)
def test_duration_boundaries(api, window, duration, expected):
    start, _ = window(minutes=1)
    resp = api.post("/reservations", json=reservation(start, start + duration))
    assert resp.status_code == expected, resp.text


def test_capacity_boundary_10th_ok_11th_full(api, window):
    start, end = window(hours=1)
    statuses = [api.post("/reservations", json=reservation(start, end)).status_code for _ in range(11)]
    assert statuses == [201] * 10 + [409]


@pytest.mark.parametrize("lat, expected", [(90, 200), (-90, 200), (90.0001, 422)])
def test_eta_latitude_boundaries(api, mock, lat, expected):
    mock.post("/__admin/stubs", json={"method": "GET", "path": "/route",
                                      "responses": [{"body": {"eta_sec": 60, "distance_m": 500}}]})
    assert api.get("/eta", params={"lot_id": "LOT-A", "from_lat": lat, "from_lng": 127}).status_code == expected


# --- contract -----------------------------------------------------------------


def test_reservation_response_matches_openapi_schema(api, window):
    spec = api.get("/openapi.json").json()
    schema = {**spec["components"]["schemas"]["Reservation"], "components": spec["components"]}
    body = api.post("/reservations", json=reservation(*window(hours=1))).json()
    jsonschema.validate(body, schema)


def test_domain_errors_share_code_message_format(api):
    for resp in (api.get("/lots/NOPE"), api.delete("/reservations/nope")):
        assert set(resp.json()["detail"]) == {"code", "message"}


# --- concurrency --------------------------------------------------------------


def test_concurrent_reservations_never_exceed_capacity(window):
    """30 parallel requests for a 10-space lot: exactly 10 may succeed (planted defect D2 overbooks)."""
    import httpx

    start, end = window(hours=1)

    def book(_):
        with httpx.Client(base_url=SUT_URL, timeout=10) as c:
            return c.post("/reservations", json=reservation(start, end)).status_code

    with ThreadPoolExecutor(max_workers=30) as pool:
        statuses = list(pool.map(book, range(30)))
    assert statuses.count(201) == 10, statuses
    assert statuses.count(409) == 20
