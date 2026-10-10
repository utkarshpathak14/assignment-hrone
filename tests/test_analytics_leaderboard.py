import pytest

from tests import fixtures


@pytest.fixture
def july(client, clean_db):
    fixtures.load(clean_db)
    return client


def leaderboard(client, **params):
    return client.get("/analytics/leaderboard/late", params={"month": "2026-07", **params})


def test_leaderboard_matches_hand_computed_ranking(july):
    response = leaderboard(july)

    assert response.status_code == 200
    assert response.json() == {"month": "2026-07", "items": fixtures.LEADERBOARD_JULY}


def test_orphan_logs_are_ignored(july):
    codes = [item["emp_code"] for item in leaderboard(july).json()["items"]]

    assert "EMP9999" not in codes


@pytest.mark.parametrize(
    "limit, expected_codes",
    [
        (1, ["EMP0002", "EMP0006"]),             # tie at rank 1: both returned although limit is 1
        (2, ["EMP0002", "EMP0006"]),             # next rank is 3, which is > 2
        (3, ["EMP0002", "EMP0006", "EMP0004"]),
        (4, ["EMP0002", "EMP0006", "EMP0004", "EMP0001"]),
    ],
)
def test_limit_is_applied_after_ranking(july, limit, expected_codes):
    items = leaderboard(july, limit=limit).json()["items"]

    assert [item["emp_code"] for item in items] == expected_codes


def test_ranking_restarts_inside_a_department(july):
    items = leaderboard(july, department="Engineering").json()["items"]

    assert [(i["rank"], i["emp_code"]) for i in items] == [(1, "EMP0002"), (2, "EMP0001")]


def test_competition_ranking_skips_after_a_tie(client, clean_db):
    # Totals 90, 60, 60, 60, 30 -> ranks 1, 2, 2, 2, 5. With limit 3 the first four rows are returned.
    totals = {"EMP0011": 90, "EMP0012": 60, "EMP0013": 60, "EMP0014": 60, "EMP0015": 30}
    clean_db.employees.insert_many([{**fixtures.EMPLOYEES[0], "emp_code": code, "name": code} for code in totals])
    clean_db.attendance_logs.insert_many([
        {"emp_code": code, "date": "2026-07-06", "status": "PRESENT", "late_minutes": late}
        for code, late in totals.items()
    ])

    everyone = leaderboard(client).json()["items"]
    top3 = leaderboard(client, limit=3).json()["items"]

    assert [i["rank"] for i in everyone] == [1, 2, 2, 2, 5]
    assert [i["emp_code"] for i in top3] == ["EMP0011", "EMP0012", "EMP0013", "EMP0014"]


def test_month_without_late_records_is_empty(july):
    assert leaderboard(july, month="2026-03").json()["items"] == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"limit": "ten"}, {"month": "2026-7"}])
def test_invalid_parameters_return_422(july, params):
    assert leaderboard(july, **params).status_code == 422
