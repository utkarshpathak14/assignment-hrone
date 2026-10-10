import pytest

from tests import fixtures


@pytest.fixture
def july(client, clean_db):
    fixtures.load(clean_db)
    return client


@pytest.mark.parametrize("emp_code", sorted(fixtures.EXPECTED_MONTHLY_JULY))
def test_monthly_summary_matches_hand_computed_values(july, emp_code):
    response = july.get(f"/analytics/employees/{emp_code}/monthly", params={"month": "2026-07"})

    assert response.status_code == 200
    assert response.json() == {"emp_code": emp_code, "month": "2026-07", **fixtures.EXPECTED_MONTHLY_JULY[emp_code]}


def test_month_boundaries_exclude_neighbouring_months(july):
    june = july.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-06"}).json()

    # Only 2026-06-30 (a Tuesday) is in June; June 2026 has 22 working days.
    assert june["working_days"] == 22
    assert june["present_days"] == 1
    assert june["total_late_minutes"] == 99
    assert june["attendance_pct"] == 4.55


def test_december_rolls_over_to_the_next_year(july):
    response = july.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-12"})

    assert response.status_code == 200
    assert response.json()["working_days"] == 23


def test_attendance_pct_rounds_half_up(client, clean_db):
    # 1 present day out of 8 working days = 12.5 % exactly; add a half day: 1.5 / 8 = 18.75 % exactly.
    # Use a joiner so the working-day count is small: joined Fri 2026-07-22 -> 22..31 July = 8 working days.
    clean_db.employees.insert_one({**fixtures.EMPLOYEES[0], "emp_code": "EMP0100", "joined_on": "2026-07-22"})
    clean_db.attendance_logs.insert_many([
        {"emp_code": "EMP0100", "date": "2026-07-22", "status": "PRESENT", "work_hours": 9.0, "half_day": False},
        {"emp_code": "EMP0100", "date": "2026-07-23", "status": "PRESENT", "work_hours": 4.0, "half_day": True},
    ])

    body = client.get("/analytics/employees/EMP0100/monthly", params={"month": "2026-07"}).json()

    assert body["working_days"] == 8
    assert body["present_days"] == 1.5
    assert body["attendance_pct"] == 18.75


def test_unknown_employee_returns_404(july):
    assert july.get("/analytics/employees/EMP9999/monthly", params={"month": "2026-07"}).status_code == 404


@pytest.mark.parametrize("params", [{}, {"month": "2026-13"}, {"month": "2026-7"}, {"month": "0000-01"},
                                    {"month": "2026-07-01"}])
def test_invalid_month_returns_422(july, params):
    assert july.get("/analytics/employees/EMP0001/monthly", params=params).status_code == 422
