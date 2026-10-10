import json

import pytest

from tests import fixtures


@pytest.fixture
def july(client, clean_db):
    fixtures.load(clean_db)
    return client


@pytest.mark.parametrize(
    "endpoint, params, collection",
    [
        ("attendance_list", {}, "attendance_logs"),
        ("attendance_list", {"emp_code": "EMP0001", "date_from": "2026-07-01"}, "attendance_logs"),
        ("attendance_list", {"status": "LEAVE", "page": 2, "page_size": 5}, "attendance_logs"),
        ("employee_monthly", {"emp_code": "EMP0001", "month": "2026-07"}, "attendance_logs"),
        ("department_summary", {"month": "2026-07"}, "employees"),
        ("department_summary", {"month": "2026-07", "department": "Engineering"}, "employees"),
        ("late_leaderboard", {"month": "2026-07", "limit": 3}, "attendance_logs"),
        ("department_trend", {"department": "Engineering", "from": "2026-07-01", "to": "2026-07-31"}, "employees"),
    ],
)
def test_explain_uses_indexes_and_no_collection_scan(july, endpoint, params, collection):
    response = july.get(f"/admin/explain/{endpoint}", params=params)

    assert response.status_code == 200
    body = response.json()
    assert body["endpoint"] == endpoint
    assert body["collection"] == collection
    plan = json.dumps(body["explain"])
    assert "IXSCAN" in plan
    assert "COLLSCAN" not in plan
    assert "executionStats" in plan  # top level for find; inside stages[0].$cursor for aggregate


def test_explain_runs_the_same_query_as_the_endpoint(july):
    # Same filter, sort and pagination: the explained find returns exactly as many documents as the page has.
    params = {"emp_code": "EMP0001", "page": 2, "page_size": 3}
    page = july.get("/attendance", params=params).json()

    stats = july.get("/admin/explain/attendance_list", params=params).json()["explain"]["executionStats"]

    assert stats["nReturned"] == len(page["items"]) == 3


@pytest.mark.parametrize(
    "endpoint, params",
    [
        ("employee_monthly", {"month": "2026-07"}),            # emp_code missing
        ("employee_monthly", {"emp_code": "EMP0001"}),         # month missing
        ("department_summary", {}),
        ("late_leaderboard", {"limit": 3}),
        ("department_trend", {"from": "2026-07-01", "to": "2026-07-31"}),
        ("department_trend", {"department": "Engineering", "to": "2026-07-31"}),
        ("department_trend", {"department": "Engineering", "from": "2026-07-31", "to": "2026-07-01"}),
        ("attendance_list", {"date_from": "2026-07-31", "date_to": "2026-07-01"}),
        ("late_leaderboard", {"month": "2026-07", "limit": 99}),
        ("unknown_endpoint", {}),
    ],
)
def test_explain_validates_parameters(july, endpoint, params):
    assert july.get(f"/admin/explain/{endpoint}", params=params).status_code == 422
