import pytest

from tests.helpers import employee_body, fire_in_parallel, ist, ist_ms

HOUR = 3600 * 1000


@pytest.fixture
def employees(client):
    for overrides in ({"emp_code": "EMP0001"},
                      {"emp_code": "EMP0005", "shift_start": "22:00", "shift_end": "06:00"}):
        assert client.post("/employees", json=employee_body(**overrides)).status_code == 201
    return client


def punch_in(client, emp_code, at, status="PRESENT"):
    response = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": at, "status": status})
    assert response.status_code == 201
    return response.json()


def punch_out(client, emp_code, at=None):
    body = {"emp_code": emp_code}
    if at is not None:
        body["punched_at"] = at
    return client.post("/attendance/punch-out", json=body)


# --------------------------------------------------------------------------- #
# Happy path and shape
# --------------------------------------------------------------------------- #
def test_normal_day_matches_sample_document_1(employees):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))

    response = punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 18, 35))

    assert response.status_code == 200
    body = response.json()
    assert body["punch_out"] == ist_ms(2026, 7, 6, 18, 35)
    assert body["work_hours"] == 9.12
    assert body["overtime_minutes"] == 0       # 5 minutes is under the 30-minute minimum
    assert body["half_day"] is False
    assert body["late_minutes"] == 0
    assert body["history"] == []               # punch-out never writes history


def test_late_minutes_from_punch_in_are_kept(employees):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 10, 5))

    body = punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 19, 10)).json()

    assert body["late_minutes"] == 35
    assert body["overtime_minutes"] == 40
    assert body["work_hours"] == 9.08


def test_punch_out_is_truncated_to_whole_seconds(employees):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))

    body = punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 18, 35, 0, millis=999)).json()

    assert body["punch_out"] == ist_ms(2026, 7, 6, 18, 35, 0)


# --------------------------------------------------------------------------- #
# R3 - overtime (shift end 18:30)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "out, expected",
    [
        ((17, 0, 0), 0),     # left early
        ((18, 35, 0), 0),    # 5 minutes
        ((18, 59, 59), 0),   # 29 minutes 59 seconds -> 29 -> under the minimum
        ((19, 0, 0), 30),    # exactly the minimum
        ((19, 10, 30), 40),  # floor of 40.5
    ],
)
def test_overtime_follows_r3(employees, out, expected):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))

    body = punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, *out)).json()

    assert body["overtime_minutes"] == expected


# --------------------------------------------------------------------------- #
# R4 / R5 - work hours (half-up) and half day
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "seconds, hours, half_day",
    [
        (16182, 4.50, False),  # 4.495 h rounds up to 4.50, which is not < 4.50
        (14850, 4.13, True),   # 4.125 h: half-up gives 4.13; Python's round(4.125, 2) gives 4.12
        (16181, 4.49, True),   # 4.49472 h
        (16200, 4.50, False),  # exactly 4.5 h is not a half day
        (12600, 3.50, True),   # sample document 4
        (9 * 3600, 9.00, False),
    ],
)
def test_work_hours_round_half_up_and_half_day(employees, seconds, hours, half_day):
    start = ist_ms(2026, 7, 6, 9, 30)
    punch_in(employees, "EMP0001", start)

    body = punch_out(employees, "EMP0001", start + seconds * 1000).json()

    assert body["work_hours"] == hours
    assert body["half_day"] is half_day


# --------------------------------------------------------------------------- #
# Overnight shift
# --------------------------------------------------------------------------- #
def test_overnight_shift_closes_across_midnight_matching_sample_document_7(employees):
    punch_in(employees, "EMP0005", ist_ms(2026, 7, 6, 21, 55))

    body = punch_out(employees, "EMP0005", ist_ms(2026, 7, 7, 6, 40)).json()

    assert body["date"] == "2026-07-06"
    assert body["work_hours"] == 8.75
    assert body["overtime_minutes"] == 40   # shift end is 06:00 on the 7th, not the 6th


# --------------------------------------------------------------------------- #
# Which record is closed
# --------------------------------------------------------------------------- #
def test_closes_the_most_recent_record_started_before_the_punch_out(employees):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))
    punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 18, 30))
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 7, 9, 28))

    body = punch_out(employees, "EMP0001", ist_ms(2026, 7, 7, 18, 30)).json()

    assert body["date"] == "2026-07-07"


def test_ignores_records_that_start_after_the_punch_out(employees, clean_db):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 7, 9, 28))

    # A (back-dated) punch-out at 18:30 on the 6th closes the 6th, not the newer 7th.
    body = punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 18, 30)).json()

    assert body["date"] == "2026-07-06"
    assert clean_db.attendance_logs.find_one({"date": "2026-07-07"})["punch_out"] is None


def test_records_without_punch_in_are_never_matched(employees, clean_db):
    clean_db.attendance_logs.insert_one({"emp_code": "EMP0001", "date": "2026-07-06", "status": "ABSENT",
                                         "punch_in": None, "punch_out": None})

    assert punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 18, 30)).status_code == 404


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
def test_unknown_employee_returns_404(employees):
    assert punch_out(employees, "EMP9999", ist_ms(2026, 7, 6, 18, 30)).status_code == 404


def test_no_punch_in_returns_404(employees):
    assert punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 18, 30)).status_code == 404


def test_punch_out_before_any_punch_in_returns_404(employees):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))

    assert punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 0)).status_code == 404


def test_second_punch_out_returns_409(employees):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))
    assert punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 18, 30)).status_code == 200

    assert punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 19, 0)).status_code == 409


def test_punch_out_equal_to_punch_in_returns_422(employees):
    punch_in(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28))

    assert punch_out(employees, "EMP0001", ist_ms(2026, 7, 6, 9, 28)).status_code == 422


def test_punch_out_more_than_24_hours_later_returns_422(employees):
    start = ist_ms(2026, 7, 6, 9, 28)
    punch_in(employees, "EMP0001", start)

    assert punch_out(employees, "EMP0001", start + 24 * HOUR + 1000).status_code == 422


def test_punch_out_exactly_24_hours_later_is_allowed(employees):
    start = ist_ms(2026, 7, 6, 9, 28)
    punch_in(employees, "EMP0001", start)

    response = punch_out(employees, "EMP0001", start + 24 * HOUR)

    assert response.status_code == 200
    assert response.json()["work_hours"] == 24.0


@pytest.mark.parametrize("punched_at", ["1783312500000", 1783312500000.0, True, 1783312500])
def test_invalid_timestamps_return_422(employees, punched_at):
    response = employees.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": punched_at})

    assert response.status_code == 422


def test_parallel_punch_outs_yield_exactly_one_200(live_url, clean_db):
    clean_db.employees.insert_one({**employee_body(), "shift_start": "09:30", "shift_end": "18:30"})
    clean_db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-06", "status": "PRESENT",
        "punch_in": ist(2026, 7, 6, 9, 28), "punch_out": None, "work_hours": None,
        "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": [],
    })
    body = {"emp_code": "EMP0001", "punched_at": ist_ms(2026, 7, 6, 18, 35)}

    responses = fire_in_parallel(20, lambda http: http.post(f"{live_url}/attendance/punch-out", json=body))

    codes = [r.status_code for r in responses]
    assert codes.count(200) == 1
    assert codes.count(409) == 19
