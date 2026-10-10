import pytest

from tests.helpers import employee_body, fire_in_parallel, ist, ist_ms

URL = "/attendance/EMP0001/2026-07-07"
WHO = {"reason": "biometric glitch", "regularized_by": "hr.admin"}


@pytest.fixture
def employees(client):
    for overrides in ({"emp_code": "EMP0001"},
                      {"emp_code": "EMP0005", "shift_start": "22:00", "shift_end": "06:00"}):
        assert client.post("/employees", json=employee_body(**overrides)).status_code == 201
    return client


@pytest.fixture
def late_day(employees):
    """EMP0001 on 2026-07-07: in 10:05 (35 late), out 18:30 -> 8.42 h. The 'before' state of sample document 8."""
    employees.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ist_ms(2026, 7, 7, 10, 5)})
    employees.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ist_ms(2026, 7, 7, 18, 30)})
    return employees


def patch(client, url=URL, **body):
    return client.patch(url, json={**WHO, **body})


# --------------------------------------------------------------------------- #
# Corrections and history
# --------------------------------------------------------------------------- #
def test_correcting_punch_in_matches_sample_document_8(late_day):
    response = patch(late_day, punch_in=ist_ms(2026, 7, 7, 9, 28))

    assert response.status_code == 200
    body = response.json()
    assert body["punch_in"] == ist_ms(2026, 7, 7, 9, 28)
    assert body["late_minutes"] == 0
    assert body["work_hours"] == 9.03
    assert len(body["history"]) == 1
    entry = body["history"][0]
    assert entry["by"] == "hr.admin"
    assert entry["reason"] == "biometric glitch"
    assert isinstance(entry["at"], int)
    # Only the fields that actually changed; punch times are epoch ms inside history too.
    assert entry["changes"] == {
        "punch_in": {"from": ist_ms(2026, 7, 7, 10, 5), "to": ist_ms(2026, 7, 7, 9, 28)},
        "late_minutes": {"from": 35, "to": 0},
        "work_hours": {"from": 8.42, "to": 9.03},
    }


def test_history_is_stored_with_bson_dates(late_day, clean_db):
    patch(late_day, punch_in=ist_ms(2026, 7, 7, 9, 28))

    stored = clean_db.attendance_logs.find_one({"emp_code": "EMP0001"})["history"][0]
    assert stored["at"].tzinfo is not None
    assert stored["changes"]["punch_in"]["to"] == ist(2026, 7, 7, 9, 28)


def test_second_correction_appends_and_keeps_the_first(late_day):
    patch(late_day, punch_in=ist_ms(2026, 7, 7, 9, 28))

    body = patch(late_day, punch_out=ist_ms(2026, 7, 7, 19, 30), reason="forgot to punch out").json()

    assert len(body["history"]) == 2
    assert body["history"][0]["changes"]["punch_in"]["to"] == ist_ms(2026, 7, 7, 9, 28)
    assert set(body["history"][1]["changes"]) == {"punch_out", "work_hours", "overtime_minutes"}
    assert body["overtime_minutes"] == 60


def test_present_to_absent_clears_punches_and_resets_derived_fields(late_day):
    body = patch(late_day, status="ABSENT").json()

    assert body["status"] == "ABSENT"
    assert body["punch_in"] is None and body["punch_out"] is None
    assert body["work_hours"] is None
    assert body["late_minutes"] == 0
    assert set(body["history"][0]["changes"]) == {"status", "punch_in", "punch_out", "work_hours", "late_minutes"}


def test_absent_to_present_with_times(employees, clean_db):
    clean_db.attendance_logs.insert_one({"emp_code": "EMP0001", "date": "2026-07-07", "status": "ABSENT",
                                         "punch_in": None, "punch_out": None, "work_hours": None})

    body = patch(employees, status="WFH", punch_in=ist_ms(2026, 7, 7, 9, 45),
                 punch_out=ist_ms(2026, 7, 7, 13, 0)).json()

    assert body["status"] == "WFH"
    assert body["late_minutes"] == 15
    assert body["work_hours"] == 3.25
    assert body["half_day"] is True
    assert set(body["history"][0]["changes"]) == {"status", "punch_in", "punch_out", "work_hours",
                                                  "late_minutes", "half_day"}


def test_status_only_change_between_presence_statuses(late_day):
    body = patch(late_day, status="ON_DUTY").json()

    assert body["history"][0]["changes"] == {"status": {"from": "PRESENT", "to": "ON_DUTY"}}


def test_overnight_punch_in_after_midnight_stays_on_the_shift_date(employees):
    employees.post("/attendance/punch-in", json={"emp_code": "EMP0005", "punched_at": ist_ms(2026, 7, 6, 21, 55)})

    response = patch(employees, "/attendance/EMP0005/2026-07-06", punch_in=ist_ms(2026, 7, 7, 0, 30))

    assert response.status_code == 200
    assert response.json()["late_minutes"] == 150


def test_derived_fields_in_the_body_are_ignored(late_day):
    body = patch(late_day, punch_in=ist_ms(2026, 7, 7, 9, 28), work_hours=99, late_minutes=500).json()

    assert body["work_hours"] == 9.03
    assert body["late_minutes"] == 0


def test_legacy_record_without_history_field(employees, clean_db):
    clean_db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-07", "status": "PRESENT",
        "punch_in": ist(2026, 7, 7, 9, 32), "punch_out": ist(2026, 7, 7, 18, 31),
        "work_hours": 8.98, "late_minutes": 0, "overtime_minutes": 0,
    })

    body = patch(employees, status="WFH").json()

    assert body["half_day"] is False
    assert len(body["history"]) == 1


# --------------------------------------------------------------------------- #
# Rule violations -> 422
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "ABSENT", "punch_in": ist_ms(2026, 7, 7, 9, 28)},
        {"status": "LEAVE", "punch_out": ist_ms(2026, 7, 7, 18, 0)},
        {"punch_in": ist_ms(2026, 7, 8, 9, 28)},                    # another attendance date
        {"punch_in": ist_ms(2026, 7, 7, 19, 0)},                    # after the stored punch_out
        {"punch_out": ist_ms(2026, 7, 7, 10, 5)},                   # equal to punch_in
        {"punch_out": ist_ms(2026, 7, 7, 9, 0)},                    # before punch_in
        {"punch_out": ist_ms(2026, 7, 8, 10, 6)},                   # more than 24 h after punch_in
        {"punch_in": ist_ms(2026, 7, 7, 10, 5)},                    # same as stored: changes nothing
        {"status": "PRESENT"},                                       # same as stored: changes nothing
        {},                                                          # nothing to change at all
    ],
)
def test_rule_violations_return_422(late_day, changes):
    assert patch(late_day, **changes).status_code == 422


def test_presence_status_without_any_punch_in_returns_422(employees, clean_db):
    clean_db.attendance_logs.insert_one({"emp_code": "EMP0001", "date": "2026-07-07", "status": "ABSENT",
                                         "punch_in": None, "punch_out": None})

    assert patch(employees, status="PRESENT").status_code == 422
    assert patch(employees, status="PRESENT", punch_out=ist_ms(2026, 7, 7, 18, 0)).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"punch_in": ist_ms(2026, 7, 7, 9, 28), "reason": "abc", "regularized_by": "hr"},   # reason too short
        {"punch_in": ist_ms(2026, 7, 7, 9, 28), "reason": "x" * 201, "regularized_by": "hr"},
        {"punch_in": ist_ms(2026, 7, 7, 9, 28), "reason": "biometric glitch"},               # no regularized_by
        {"punch_in": ist_ms(2026, 7, 7, 9, 28), "regularized_by": "hr"},                     # no reason
        {"punch_in": "1783396680000", **WHO},                                                 # string timestamp
        {"punch_in": None, **WHO},                                                            # explicit null
        {"status": "foo", **WHO},
    ],
)
def test_invalid_body_returns_422(late_day, body):
    assert late_day.patch(URL, json=body).status_code == 422


def test_invalid_path_date_returns_422(late_day):
    assert patch(late_day, "/attendance/EMP0001/2026-02-30", status="WFH").status_code == 422


def test_unknown_employee_returns_404(late_day):
    assert patch(late_day, "/attendance/EMP9999/2026-07-07", status="WFH").status_code == 404


def test_missing_record_returns_404(late_day):
    assert patch(late_day, "/attendance/EMP0001/2026-07-09", status="WFH").status_code == 404


def test_rejected_request_does_not_write_history(late_day, clean_db):
    patch(late_day, status="ABSENT", punch_in=ist_ms(2026, 7, 7, 9, 28))

    assert clean_db.attendance_logs.find_one({"emp_code": "EMP0001"})["history"] == []


# --------------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------------- #
def test_parallel_corrections_never_lose_history(live_url, clean_db):
    clean_db.employees.insert_one({**employee_body(), "shift_start": "09:30", "shift_end": "18:30"})
    clean_db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-07", "status": "PRESENT",
        "punch_in": ist(2026, 7, 7, 10, 5), "punch_out": ist(2026, 7, 7, 18, 30), "work_hours": 8.42,
        "late_minutes": 35, "overtime_minutes": 0, "half_day": False, "history": [],
    })
    counter = iter(range(10))

    def send(http):
        minute = next(counter)  # every request moves punch_in to a different minute
        body = {**WHO, "reason": f"correction {minute}", "punch_in": ist_ms(2026, 7, 7, 9, minute)}
        return http.patch(f"{live_url}{URL}", json=body)

    responses = fire_in_parallel(10, send)

    succeeded = [r.json() for r in responses if r.status_code == 200]
    assert {r.status_code for r in responses} <= {200, 409}
    assert len(succeeded) >= 1
    history = clean_db.attendance_logs.find_one({"emp_code": "EMP0001"})["history"]
    # Every successful correction left exactly one entry; nothing was overwritten.
    assert len(history) == len(succeeded)
    assert sorted(h["reason"] for h in history) == sorted(r["history"][-1]["reason"] for r in succeeded)
    # The audit trail is a consistent chain: each entry starts where the previous one ended.
    previous = ist(2026, 7, 7, 10, 5)
    for entry in history:
        assert entry["changes"]["punch_in"]["from"] == previous
        previous = entry["changes"]["punch_in"]["to"]
