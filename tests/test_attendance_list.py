import pytest

from tests.helpers import ist


def log(emp_code, day, status="PRESENT", **fields):
    doc = {
        "emp_code": emp_code,
        "date": day,
        "status": status,
        "punch_in": None,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    doc.update(fields)
    return doc


@pytest.fixture
def logs(client, clean_db):
    clean_db.attendance_logs.insert_many([
        log("EMP0002", "2026-07-06", punch_in=ist(2026, 7, 6, 9, 28)),
        log("EMP0001", "2026-07-06", "WFH", punch_in=ist(2026, 7, 6, 9, 30)),
        log("EMP0001", "2026-07-07"),
        log("EMP0003", "2026-07-07", "LEAVE"),
        log("EMP0001", "2026-07-08", "ABSENT"),
        log("EMP0002", "2026-07-09"),
    ])
    return client


def keys(response):
    return [(item["date"], item["emp_code"]) for item in response.json()["items"]]


def test_sorted_by_date_desc_then_emp_code_asc(logs):
    response = logs.get("/attendance")

    assert response.status_code == 200
    assert keys(response) == [
        ("2026-07-09", "EMP0002"),
        ("2026-07-08", "EMP0001"),
        ("2026-07-07", "EMP0001"),
        ("2026-07-07", "EMP0003"),
        ("2026-07-06", "EMP0001"),
        ("2026-07-06", "EMP0002"),
    ]
    assert response.json()["total"] == 6


def test_date_bounds_are_inclusive(logs):
    response = logs.get("/attendance", params={"date_from": "2026-07-07", "date_to": "2026-07-08"})

    assert keys(response) == [("2026-07-08", "EMP0001"), ("2026-07-07", "EMP0001"), ("2026-07-07", "EMP0003")]
    assert response.json()["total"] == 3


def test_only_date_from(logs):
    assert logs.get("/attendance", params={"date_from": "2026-07-08"}).json()["total"] == 2


def test_only_date_to(logs):
    assert logs.get("/attendance", params={"date_to": "2026-07-06"}).json()["total"] == 2


def test_filter_by_emp_code(logs):
    response = logs.get("/attendance", params={"emp_code": "EMP0001"})

    assert keys(response) == [("2026-07-08", "EMP0001"), ("2026-07-07", "EMP0001"), ("2026-07-06", "EMP0001")]


def test_filter_by_status(logs):
    response = logs.get("/attendance", params={"status": "WFH"})

    assert keys(response) == [("2026-07-06", "EMP0001")]
    assert response.json()["total"] == 1


def test_combined_filters_and_total_reflects_them(logs):
    response = logs.get(
        "/attendance",
        params={"emp_code": "EMP0001", "date_from": "2026-07-07", "status": "PRESENT", "page_size": 1},
    )

    assert keys(response) == [("2026-07-07", "EMP0001")]
    assert response.json()["total"] == 1


def test_pagination(logs):
    page2 = logs.get("/attendance", params={"page": 2, "page_size": 4})

    assert keys(page2) == [("2026-07-06", "EMP0001"), ("2026-07-06", "EMP0002")]
    assert page2.json()["total"] == 6
    assert page2.json()["page"] == 2


def test_items_use_the_contract_shape(logs):
    item = logs.get("/attendance", params={"status": "WFH"}).json()["items"][0]

    assert "_id" not in item and "id" not in item
    assert item["punch_in"] == 1783310400000  # 2026-07-06 09:30 IST = 04:00 UTC
    assert set(item) == {"emp_code", "date", "status", "punch_in", "punch_out", "work_hours",
                         "late_minutes", "overtime_minutes", "half_day", "history"}


def test_legacy_document_gets_defaults(client, clean_db):
    clean_db.attendance_logs.insert_one({
        "emp_code": "EMP0002", "date": "2026-07-14", "status": "PRESENT",
        "punch_in": ist(2026, 7, 14, 9, 32), "punch_out": ist(2026, 7, 14, 18, 31),
        "work_hours": 8.98, "late_minutes": 0, "overtime_minutes": 0,
    })

    item = client.get("/attendance").json()["items"][0]

    assert item["half_day"] is False
    assert item["history"] == []


def test_history_instants_are_returned_as_ms(client, clean_db):
    clean_db.attendance_logs.insert_one(log(
        "EMP0001", "2026-07-07",
        history=[{
            "at": ist(2026, 7, 8, 10, 30), "by": "hr.admin", "reason": "biometric glitch",
            "changes": {
                "punch_in": {"from": ist(2026, 7, 7, 10, 5), "to": ist(2026, 7, 7, 9, 28)},
                "late_minutes": {"from": 35, "to": 0},
            },
        }],
    ))

    entry = client.get("/attendance").json()["items"][0]["history"][0]

    assert entry["at"] == 1783486800000
    assert entry["changes"]["punch_in"] == {"from": 1783398900000, "to": 1783396680000}
    assert entry["changes"]["late_minutes"] == {"from": 35, "to": 0}


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-07-08", "date_to": "2026-07-01"},  # reversed range
        {"date_from": "2026-13-01"},
        {"date_to": "2026-02-30"},
        {"date_from": "07/06/2026"},
        {"status": "foo"},
        {"status": "present"},
        {"page": 0},
        {"page_size": 101},
    ],
)
def test_invalid_query_returns_422(client, params):
    assert client.get("/attendance", params=params).status_code == 422
