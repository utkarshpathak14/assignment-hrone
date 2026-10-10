"""Check every explain target for IXSCAN / COLLSCAN and time the real endpoints, against a running server.

Usage (with the 100k dataset from scripts/gen_data.py):
    MONGO_DB=attendance_perf uvicorn app.main:app --port 8002          # in one terminal
    python scripts/check_explain.py --url http://127.0.0.1:8002        # in another
"""
import argparse
import json
import sys
import time

import httpx

# (explain target, real endpoint path, query params of the real endpoint, extra params only explain needs)
CASES = [
    ("attendance_list", "/attendance", {}, {}),
    ("attendance_list", "/attendance", {"emp_code": "EMP0001"}, {}),
    ("attendance_list", "/attendance", {"status": "LEAVE"}, {}),
    ("attendance_list", "/attendance", {"date_from": "2026-03-01", "date_to": "2026-03-31", "page": 3}, {}),
    ("attendance_list", "/attendance", {"emp_code": "EMP0001", "status": "PRESENT", "date_from": "2026-01-01"}, {}),
    ("employee_monthly", "/analytics/employees/EMP0001/monthly", {"month": "2026-03"}, {"emp_code": "EMP0001"}),
    ("department_summary", "/analytics/departments/summary", {"month": "2026-03"}, {}),
    ("department_summary", "/analytics/departments/summary", {"month": "2026-03", "department": "Engineering"}, {}),
    ("late_leaderboard", "/analytics/leaderboard/late", {"month": "2026-03"}, {}),
    ("late_leaderboard", "/analytics/leaderboard/late", {"month": "2026-03", "limit": 5, "department": "Sales"}, {}),
    ("department_trend", "/analytics/departments/Engineering/trend", {"from": "2026-03-01", "to": "2026-05-31"},
     {"department": "Engineering"}),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    failures = 0
    with httpx.Client(base_url=args.url, timeout=60) as http:
        for target, path, params, explain_only in CASES:
            plan = json.dumps(http.get(f"/admin/explain/{target}", params={**params, **explain_only})
                              .raise_for_status().json())
            started = time.perf_counter()
            http.get(path, params=params).raise_for_status()
            elapsed_ms = (time.perf_counter() - started) * 1000
            ok = "IXSCAN" in plan and "COLLSCAN" not in plan
            failures += not ok
            print(f"{'OK  ' if ok else 'FAIL'} {target:<19} IXSCAN={'IXSCAN' in plan!s:<5} "
                  f"COLLSCAN={'COLLSCAN' in plan!s:<5} {elapsed_ms:7.1f} ms  {params}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
