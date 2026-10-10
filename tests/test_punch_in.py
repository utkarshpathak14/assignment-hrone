import time

import pytest

from tests.helpers import employee_body, fire_in_parallel, ist_ms

DAY_SHIFT = {"emp_code": "EMP0001", "shift_start": "09:30", "shift_end": "18:30"}
NIGHT_SHIFT = {"emp_code": "EMP0005", "shift_start": "22:00", "shift_end": "06:00"}


@pytest.fixture
def employees(client):
    for overrides in (DAY_SHIFT, NIGHT_SHIFT):
        assert client.post("/employees", json=employee_body(**overrides)).status_code == 201
    return client


def punch_in(client, emp_code, punched_at=None, **extra):
    body = {"emp_code": emp_code, **extra}
    if punched_at is not None:
        body["punched_at"] = punched_at
    return client.post("/attendance/punch-in", json=body)


# --------------------------------------------------------------------------- #
# Response shape
# --------------------------------------------------------------------------- #
def test_punch_in_returns_full_record_without_ids(employees):
    response = punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))

    assert response.status_code == 201
    assert response.json() == {
        "emp_code": "EMP0001",
        "date": "2026-07-06",
        "status": "PRESENT",
        "punch_in": ist_ms(2026, 7, 6, 9, 28),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }


def test_punch_in_defaults_to_now(employees):
    before = int(time.time()) * 1000
    response = punch_in(employees, "EMP0001")
    after = int(time.time()) * 1000 + 1000

    assert response.status_code == 201
    assert before <= response.json()["punch_in"] <= after
    assert response.json()["punch_in"] % 1000 == 0  # truncated to whole seconds


@pytest.mark.parametrize("status", ["PRESENT", "WFH", "ON_DUTY"])
def test_punch_in_accepts_presence_statuses(employees, status):
    response = punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28), status=status)

    assert response.status_code == 201
    assert response.json()["status"] == status


# --------------------------------------------------------------------------- #
# R2 - late minutes (shift 09:30, grace 10:00)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "hms, millis, expected",
    [
        ((9, 0, 0), 0, 0),      # early
        ((9, 30, 0), 0, 0),     # exactly on time
        ((9, 40, 0), 0, 0),     # exactly at the end of grace: not late
        ((9, 40, 0), 900, 0),   # 09:40:00.900 truncates to 09:40:00
        ((9, 40, 1), 0, 10),    # one second past grace: late, counted from shift start
        ((10, 15, 59), 0, 45),  # floor of 45.98 minutes
    ],
)
def test_late_minutes_follow_r2(employees, hms, millis, expected):
    response = punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, *hms, millis=millis))

    assert response.json()["late_minutes"] == expected


def test_punch_in_is_truncated_to_whole_seconds_and_stored_that_way(employees, clean_db):
    response = punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 40, 0, millis=900))

    assert response.json()["punch_in"] == ist_ms(2026, 7, 6, 9, 40, 0)
    stored = clean_db.attendance_logs.find_one({"emp_code": "EMP0001"})
    assert stored["punch_in"].microsecond == 0


# --------------------------------------------------------------------------- #
# R1 - attendance date
# --------------------------------------------------------------------------- #
def test_date_is_the_ist_date_not_the_utc_date(employees):
    # 01:00 IST on 7 July is 19:30 UTC on 6 July.
    response = punch_in(employees, "EMP0001", ist_ms(2026, 7, 7, 1, 0))

    assert response.json()["date"] == "2026-07-07"


@pytest.mark.parametrize(
    "punch, expected_date, expected_late",
    [
        ((2026, 7, 6, 21, 55), "2026-07-06", 0),    # before shift start, same evening
        ((2026, 7, 6, 22, 10), "2026-07-06", 0),    # inside grace
        ((2026, 7, 7, 0, 30), "2026-07-06", 150),   # after midnight, still yesterday's shift
        ((2026, 7, 7, 5, 59), "2026-07-06", 479),   # just before shift_end
        ((2026, 7, 7, 6, 0), "2026-07-07", 0),      # not earlier than shift_end: today's shift (early)
    ],
)
def test_overnight_shift_dating_and_lateness(employees, punch, expected_date, expected_late):
    response = punch_in(employees, "EMP0005", ist_ms(*punch))

    assert response.status_code == 201
    assert response.json()["date"] == expected_date
    assert response.json()["late_minutes"] == expected_late


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
def test_unknown_employee_returns_404(employees):
    assert punch_in(employees, "EMP9999", ist_ms(2026, 7, 6, 9, 28)).status_code == 404


def test_second_punch_in_same_day_returns_409(employees):
    assert punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28)).status_code == 201

    assert punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 14, 0)).status_code == 409


def test_overnight_punch_after_midnight_conflicts_with_previous_evening(employees):
    assert punch_in(employees, "EMP0005", ist_ms(2026, 7, 6, 21, 55)).status_code == 201

    # 02:00 on the 7th belongs to the shift of the 6th, which already has a record.
    assert punch_in(employees, "EMP0005", ist_ms(2026, 7, 7, 2, 0)).status_code == 409


@pytest.mark.parametrize(
    "punched_at",
    [
        "1783312500000",       # string
        1783312500000.0,       # float, even with no fraction
        1.7833125e12,          # float in exponent form
        True,                  # bool
        1783312500,            # seconds, not milliseconds
        99_999_999_999,        # just below the minimum
        4_102_444_800_001,     # just above the maximum
        -1,
    ],
)
def test_invalid_timestamps_return_422(employees, punched_at):
    response = employees.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": punched_at})

    assert response.status_code == 422


@pytest.mark.parametrize("status", ["ABSENT", "LEAVE", "present", "foo"])
def test_non_presence_status_returns_422(employees, status):
    assert punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28), status=status).status_code == 422


def test_missing_emp_code_returns_422(employees):
    assert employees.post("/attendance/punch-in", json={}).status_code == 422


def test_parallel_identical_punch_ins_yield_exactly_one_201(live_url, clean_db):
    clean_db.employees.insert_one({**employee_body(), "shift_start": "09:30", "shift_end": "18:30"})
    body = {"emp_code": "EMP0001", "punched_at": ist_ms(2026, 7, 6, 9, 28)}

    responses = fire_in_parallel(20, lambda http: http.post(f"{live_url}/attendance/punch-in", json=body))

    codes = [r.status_code for r in responses]
    assert codes.count(201) == 1
    assert codes.count(409) == 19
    assert clean_db.attendance_logs.count_documents({"emp_code": "EMP0001"}) == 1
