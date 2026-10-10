import pytest

from tests import fixtures


@pytest.fixture
def july(client, clean_db):
    fixtures.load(clean_db)
    return client


def trend(client, department, start, end):
    return client.get(f"/analytics/departments/{department}/trend", params={"from": start, "to": end})


def test_trend_matches_hand_computed_rows(july):
    response = trend(july, "Engineering", "2026-07-04", "2026-07-15")

    assert response.status_code == 200
    assert response.json() == {"department": "Engineering", "items": fixtures.EXPECTED_TREND_ENGINEERING}


def test_range_without_any_records_still_has_one_row_per_day(july):
    items = trend(july, "Operations", "2026-08-01", "2026-08-03").json()["items"]

    assert [i["date"] for i in items] == ["2026-08-01", "2026-08-02", "2026-08-03"]
    assert [i["attendance_rate"] for i in items] == [None, None, 0.0]   # Sat, Sun, Mon with no records
    assert [i["moving_avg_7d"] for i in items] == [None, None, 0.0]


def test_single_day_range(july):
    items = trend(july, "Engineering", "2026-07-06", "2026-07-06").json()["items"]

    assert len(items) == 1
    assert items[0]["attendance_rate"] == 0.5
    assert items[0]["moving_avg_7d"] == 0.5


def test_window_never_reaches_before_from(july):
    # Starting on 07-09 (rate 0.0) the 0.5 rates of 07-06/07 are outside the range and must not be used.
    items = trend(july, "Engineering", "2026-07-09", "2026-07-10").json()["items"]

    assert [i["moving_avg_7d"] for i in items] == [0.0, 0.0]


def test_department_whose_employees_have_not_joined_has_null_rates(july):
    items = trend(july, "Lab", "2026-07-01", "2026-07-02").json()["items"]

    assert [i["headcount"] for i in items] == [0, 0]
    assert [i["attendance_rate"] for i in items] == [None, None]
    assert [i["moving_avg_7d"] for i in items] == [None, None]


def test_92_days_is_the_maximum(july):
    assert trend(july, "Engineering", "2026-07-01", "2026-09-30").status_code == 200   # 92 days
    assert len(trend(july, "Engineering", "2026-07-01", "2026-09-30").json()["items"]) == 92
    assert trend(july, "Engineering", "2026-07-01", "2026-10-01").status_code == 422   # 93 days


def test_unknown_department_returns_404(july):
    assert trend(july, "Nope", "2026-07-01", "2026-07-02").status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-07-10", "to": "2026-07-01"},  # to < from
        {"from": "2026-07-01"},                       # to missing
        {"to": "2026-07-01"},                         # from missing
        {"from": "2026-02-30", "to": "2026-03-01"},
    ],
)
def test_invalid_range_returns_422(july, params):
    assert july.get("/analytics/departments/Engineering/trend", params=params).status_code == 422
