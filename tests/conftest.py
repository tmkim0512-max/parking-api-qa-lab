"""Tests run against a live stack: start it first with `scripts/stack.sh up`."""
import itertools
import os
import random
from datetime import datetime, timedelta, timezone

import httpx
import pytest

SUT_URL = os.environ.get("SUT_URL", "http://127.0.0.1:8080")
MOCK_URL = os.environ.get("MOCK_URL", "http://127.0.0.1:8081")
_slot = itertools.count()
# Random per-run anchor so re-running against a long-lived stack never reuses a filled time window.
# 39,000 buckets of 64 days (one run uses ~2 days per test) keeps dates below year 9999.
_ANCHOR = timedelta(days=64 * random.randrange(39_000))


@pytest.fixture(scope="session")
def api():
    with httpx.Client(base_url=SUT_URL, timeout=10) as c:
        try:
            c.get("/health").raise_for_status()
        except httpx.HTTPError as e:
            pytest.exit(f"ENVIRONMENT FAILURE: SUT not reachable at {SUT_URL} ({e}). Run scripts/stack.sh up")
        yield c


@pytest.fixture
def mock():
    with httpx.Client(base_url=MOCK_URL, timeout=10) as c:
        c.post("/__admin/reset").raise_for_status()
        yield c


@pytest.fixture
def window():
    """A start time no other test uses, so tests never compete for the same lot capacity."""
    base = datetime.now(timezone.utc).replace(microsecond=0) + _ANCHOR + timedelta(days=1 + 2 * next(_slot))
    return lambda **duration: (base, base + timedelta(**duration))


def reservation(start, end, lot_id="LOT-A", plate="12가3456"):
    return {"lot_id": lot_id, "plate": plate, "start_at": start.isoformat(), "end_at": end.isoformat()}
