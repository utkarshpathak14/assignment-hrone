import pytest

from tests import fixtures


@pytest.fixture
def july(client, clean_db):
    fixtures.load(clean_db)
    return client


def summary(client, **params):
    return client.get("/analytics/departments/summary", params={"month": "2026-07", **params})


def test_summary_matches_hand_computed_values(july):
    response = summary(july)

    assert response.status_code == 200
    assert response.json() == {"month": "2026-07", "items": fixtures.EXPECTED_SUMMARY_JULY}


def test_employee_without_logs_counts_in_headcount(july):
    engineering = summary(july, department="Engineering").json()["items"][0]

    assert engineering["headcount"] == 3  # EMP0003 has no logs at all


def test_average_is_over_records_and_rounds_half_up(july):
    engineering = summary(july, department="Engineering").json()["items"][0]

    # 56.36 / 8 = 7.045 -> 7.05. An average of per-employee averages would be 7.085 -> 7.09;
    # doing it in doubles would give 7.04.
    assert engineering["avg_work_hours"] == 7.05


def test_department_with_no_joined_employees_is_omitted(july):
    departments = [item["department"] for item in summary(july).json()["items"]]

    assert "Lab" not in departments


def test_department_appears_once_its_employees_have_joined(july):
    september = summary(july, month="2026-09").json()["items"]

    lab = next(item for item in september if item["department"] == "Lab")
    assert lab["headcount"] == 1
    assert lab["avg_work_hours"] is None  # no records: null, not 0


def test_filter_by_department(july):
    response = summary(july, department="Sales")

    assert response.json()["items"] == [fixtures.EXPECTED_SUMMARY_JULY[2]]


def test_unknown_department_returns_no_items(july):
    assert summary(july, department="Nope").json()["items"] == []


def test_department_filter_is_case_sensitive(july):
    assert summary(july, department="sales").json()["items"] == []


@pytest.mark.parametrize("params", [{}, {"month": "2026-00"}, {"month": "July"}])
def test_invalid_month_returns_422(july, params):
    assert july.get("/analytics/departments/summary", params=params).status_code == 422
