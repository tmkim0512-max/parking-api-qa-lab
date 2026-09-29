"""Parking reservation + ETA API (system under test).

SUT_DEFECTS=on enables three planted defects:
  D1 exactly-24h reservation is rejected (off-by-one)
  D2 capacity check and save are not atomic (race -> overbooking)
  D3 upstream 5xx on /eta is not retried/fallen back (SUT returns 500)
"""
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

from sut import route_client

DEFECTS = os.environ.get("SUT_DEFECTS", "off") == "on"
PLATE_RE = re.compile(r"^\d{2,3}[가-힣]\d{4}$")
MIN_DURATION, MAX_DURATION = timedelta(minutes=30), timedelta(hours=24)
LOTS = {
    "LOT-A": {"name": "Station North", "capacity": 10, "lat": 37.5547, "lng": 126.9706},
    "LOT-B": {"name": "Riverside", "capacity": 1000, "lat": 37.5283, "lng": 126.9326},
}

app = FastAPI(title="Parking API")
reservations: dict[str, dict] = {}
lock = threading.Lock()


class ReservationIn(BaseModel):
    lot_id: str
    plate: str
    start_at: datetime
    end_at: datetime


class Reservation(ReservationIn):
    id: str
    status: str


def error(status, code, message):
    raise HTTPException(status, detail={"code": code, "message": message})


def active(lot_id, start=None, end=None):
    """Confirmed reservations in a lot; if a window is given, only those overlapping it."""
    return [
        r for r in reservations.values()
        if r["lot_id"] == lot_id and r["status"] == "confirmed"
        and (start is None or (r["start_at"] < end and start < r["end_at"]))
    ]


@app.get("/health")
def health():
    return {"status": "ok", "defects": DEFECTS}


@app.get("/lots/{lot_id}")
def get_lot(lot_id: str):
    if lot_id not in LOTS:
        error(404, "LOT_NOT_FOUND", f"lot {lot_id} does not exist")
    return {"id": lot_id, **LOTS[lot_id], "active_reservations": len(active(lot_id))}


@app.post("/reservations", status_code=201, response_model=Reservation)
def create_reservation(body: ReservationIn):
    if body.lot_id not in LOTS:
        error(404, "LOT_NOT_FOUND", f"lot {body.lot_id} does not exist")
    if not PLATE_RE.match(body.plate):
        error(422, "INVALID_PLATE", "plate must look like 12가3456 or 123가4567")
    if body.start_at.tzinfo is None or body.end_at.tzinfo is None:
        error(422, "INVALID_TIME", "start_at/end_at must include a timezone offset")
    if body.start_at <= datetime.now(timezone.utc):
        error(422, "START_IN_PAST", "start_at must be in the future")
    duration = body.end_at - body.start_at
    too_long = duration >= MAX_DURATION if DEFECTS else duration > MAX_DURATION  # D1
    if duration < MIN_DURATION or too_long:
        error(422, "INVALID_DURATION", "duration must be between 30 minutes and 24 hours")

    def check_and_save():
        if len(active(body.lot_id, body.start_at, body.end_at)) >= LOTS[body.lot_id]["capacity"]:
            error(409, "LOT_FULL", "no space left for this time window")
        if DEFECTS:
            time.sleep(0.05)  # D2: widen the check-then-act window so the race is reproducible
        rid = uuid.uuid4().hex[:12]
        reservations[rid] = {"id": rid, "status": "confirmed", **body.model_dump()}
        return reservations[rid]

    if DEFECTS:
        return check_and_save()
    with lock:
        return check_and_save()


@app.get("/reservations/{rid}", response_model=Reservation)
def get_reservation(rid: str):
    if rid not in reservations:
        error(404, "RESERVATION_NOT_FOUND", f"reservation {rid} does not exist")
    return reservations[rid]


@app.delete("/reservations/{rid}", status_code=204)
def cancel_reservation(rid: str):
    r = reservations.get(rid) or error(404, "RESERVATION_NOT_FOUND", f"reservation {rid} does not exist")
    with lock:
        if r["status"] == "cancelled":
            error(409, "ALREADY_CANCELLED", "reservation is already cancelled")
        r["status"] = "cancelled"
    return Response(status_code=204)


@app.get("/eta")
def eta(
    lot_id: str,
    from_lat: float = Query(ge=-90, le=90),
    from_lng: float = Query(ge=-180, le=180),
):
    if lot_id not in LOTS:
        error(404, "LOT_NOT_FOUND", f"lot {lot_id} does not exist")
    lot = LOTS[lot_id]
    return {"lot_id": lot_id, **route_client.get_eta(from_lat, from_lng, lot["lat"], lot["lng"], DEFECTS)}
