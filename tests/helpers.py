"""Small helpers shared by test modules (fixtures live in conftest.py)."""
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx


def fire_in_parallel(n: int, send):
    """Call send(http_client) from n threads that all start at the same moment; return the responses."""
    barrier = threading.Barrier(n)

    def worker(_):
        with httpx.Client(timeout=10) as http:
            barrier.wait()  # every thread blocks here until all n have arrived, then they all go at once
            return send(http)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(worker, range(n)))
