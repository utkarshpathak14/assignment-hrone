"""Sanity checks for the test data itself: the fixture loads, and the generator produces valid documents."""
from datetime import date

from scripts.gen_data import generate
from tests import fixtures


def test_fixture_loads(client, clean_db):
    fixtures.load(clean_db)

    assert clean_db.employees.count_documents({}) == len(fixtures.EMPLOYEES)
    assert clean_db.attendance_logs.count_documents({}) == len(fixtures.LOGS)


def test_generator_produces_unique_valid_records(client, clean_db):
    counts = generate(clean_db, employees=20, days=40, start=date(2026, 7, 1), seed=7)

    assert counts["employees"] == 20
    assert counts["logs"] > 300
    # One record per (emp_code, date): the unique index must be buildable on generated data.
    clean_db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    statuses = set(clean_db.attendance_logs.distinct("status"))
    assert statuses <= {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}
    # Every presence record with a punch-out has the derived values the API would compute.
    assert clean_db.attendance_logs.count_documents(
        {"status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]}, "punch_out": {"$ne": None}, "work_hours": None}
    ) == 0


def test_generator_is_deterministic(client, clean_db):
    first = generate(clean_db, employees=5, days=10, seed=3)
    second = generate(clean_db, employees=5, days=10, seed=3)

    assert first == second
