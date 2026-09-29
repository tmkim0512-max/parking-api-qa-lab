"""External route API client: 2s timeout, one retry on 5xx/transport error, contract check, fallback."""
import json
import logging
import math
import os
from pathlib import Path

import httpx
import jsonschema

log = logging.getLogger("route_client")
SCHEMA = json.loads((Path(__file__).parent.parent / "contracts" / "route_api.schema.json").read_text())
FALLBACK_SPEED_MPS = 30_000 / 3600  # 30 km/h straight-line estimate


def _fallback(from_lat, from_lng, to_lat, to_lng, reason):
    # haversine distance
    r = 6_371_000
    p1, p2 = math.radians(from_lat), math.radians(to_lat)
    dp, dl = p2 - p1, math.radians(to_lng - from_lng)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    distance = 2 * r * math.asin(math.sqrt(a))
    return {"eta_seconds": round(distance / FALLBACK_SPEED_MPS), "source": "fallback", "reason": reason}


def get_eta(from_lat, from_lng, to_lat, to_lng, defects=False):
    url = os.environ.get("ROUTE_API_URL", "http://127.0.0.1:8081") + "/route"
    params = {"from_lat": from_lat, "from_lng": from_lng, "to_lat": to_lat, "to_lng": to_lng}
    reason = None
    for _ in range(2):  # first try + 1 retry
        try:
            resp = httpx.get(url, params=params, timeout=2.0)
        except httpx.TransportError:  # timeout, connection refused/reset
            reason = "upstream_unavailable"
            continue
        if resp.status_code >= 500:
            if defects:  # D3: no retry/fallback, error propagates as SUT 500
                resp.raise_for_status()
            reason = "upstream_5xx"
            continue
        try:
            body = resp.json()
            jsonschema.validate(body, SCHEMA)
        except (ValueError, jsonschema.ValidationError):
            log.warning("contract_violation: %s", resp.text[:200])
            return _fallback(from_lat, from_lng, to_lat, to_lng, "contract_violation")
        return {"eta_seconds": body["eta_sec"], "source": "route_api", "reason": None}
    return _fallback(from_lat, from_lng, to_lat, to_lng, reason)
