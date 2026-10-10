"""Small helpers shared by test modules (fixtures live in conftest.py)."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

IST = timezone(timedelta(hours=5, minutes=30))


def ist_ms(year, month, day, hour=0, minute=0, second=0, millis=0) -> int:
    """Epoch milliseconds for a wall-clock time in IST. ist_ms(2026, 7, 6, 9, 40) = 09:40 IST on 6 July."""
    dt = datetime(year, month, day, hour, minute, second, millis * 1000, tzinfo=IST)
    return int(dt.timestamp()) * 1000 + millis


def ist(year, month, day, hour=0, minute=0, second=0) -> datetime:
    """An aware datetime for a wall-clock time in IST (for inserting fixture documents directly)."""
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


def employee_body(**overrides) -> dict:
    body = {
        "emp_code": "EMP0001",
        "name": "Asha Rao",
        "email": "asha@example.com",
        "department": "Engineering",
        "joined_on": "2026-01-05",
    }
    body.update(overrides)
    return body


def fire_in_parallel(n: int, send):
    """Call send(http_client) from n threads that all start at the same moment; return the responses."""
    barrier = threading.Barrier(n)

    def worker(_):
        with httpx.Client(timeout=10) as http:
            barrier.wait()  # every thread blocks here until all n have arrived, then they all go at once
            return send(http)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(worker, range(n)))
