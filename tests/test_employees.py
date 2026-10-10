import time
from datetime import datetime, timezone

import pytest

from tests.helpers import employee_body as employee, fire_in_parallel



# --------------------------------------------------------------------------- #
# POST /employees
# --------------------------------------------------------------------------- #
def test_create_returns_201_with_defaults_and_ms_created_at(client):
    before = int(time.time() * 1000)
    response = client.post("/employees", json=employee())
    after = int(time.time() * 1000)

    assert response.status_code == 201
    body = response.json()
    assert body["shift_start"] == "09:30"
    assert body["shift_end"] == "18:30"
    assert isinstance(body["created_at"], int)
    assert before - 1000 <= body["created_at"] <= after + 1000
    assert "_id" not in body and "id" not in body


def test_create_stores_created_at_as_a_utc_datetime(client, clean_db):
    client.post("/employees", json=employee())

    stored = clean_db.employees.find_one({"emp_code": "EMP0001"})
    assert isinstance(stored["created_at"], datetime)
    assert abs(stored["created_at"] - datetime.now(timezone.utc)).total_seconds() < 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"emp_code": "EMP12"},           # too few digits
        {"emp_code": "EMP1234567"},      # too many digits
        {"emp_code": "emp0001"},         # lowercase
        {"emp_code": 1},                 # not a string
        {"name": ""},
        {"name": "x" * 101},
        {"email": "not-an-email"},
        {"email": "a b@example.com"},
        {"department": ""},
        {"shift_start": "25:00"},
        {"shift_start": "9:30"},
        {"shift_end": "18:60"},
        {"shift_start": "10:00", "shift_end": "10:00"},  # zero-length shift
        {"joined_on": "2026-02-30"},     # impossible date
        {"joined_on": "2026-2-3"},       # wrong format
        {"joined_on": "05/01/2026"},
    ],
)
def test_create_rejects_invalid_input_with_422(client, overrides):
    response = client.post("/employees", json=employee(**overrides))

    assert response.status_code == 422


def test_create_requires_joined_on(client):
    body = employee()
    del body["joined_on"]

    assert client.post("/employees", json=body).status_code == 422


def test_overnight_shift_is_allowed(client):
    response = client.post("/employees", json=employee(shift_start="22:00", shift_end="06:00"))

    assert response.status_code == 201


def test_duplicate_emp_code_returns_409(client):
    assert client.post("/employees", json=employee()).status_code == 201

    response = client.post("/employees", json=employee(name="Someone Else"))

    assert response.status_code == 409


def test_parallel_duplicate_creates_yield_exactly_one_201(live_url, clean_db):
    responses = fire_in_parallel(20, lambda http: http.post(f"{live_url}/employees", json=employee()))

    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1
    assert codes.count(409) == 19
    assert clean_db.employees.count_documents({"emp_code": "EMP0001"}) == 1


# --------------------------------------------------------------------------- #
# GET /employees
# --------------------------------------------------------------------------- #
@pytest.fixture
def five_employees(client):
    # Inserted out of order on purpose, to prove the endpoint sorts.
    for code, dept in [("EMP0004", "Sales"), ("EMP0002", "Engineering"), ("EMP0005", "Engineering"),
                       ("EMP0001", "Engineering"), ("EMP0003", "Sales")]:
        assert client.post("/employees", json=employee(emp_code=code, department=dept)).status_code == 201
    return client


def codes(response):
    return [item["emp_code"] for item in response.json()["items"]]


def test_list_page_1_starts_at_the_first_employee(five_employees):
    response = five_employees.get("/employees")

    assert response.status_code == 200
    assert codes(response) == ["EMP0001", "EMP0002", "EMP0003", "EMP0004", "EMP0005"]
    assert response.json()["total"] == 5
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 20


def test_list_paginates(five_employees):
    page2 = five_employees.get("/employees", params={"page": 2, "page_size": 2})

    assert codes(page2) == ["EMP0003", "EMP0004"]
    assert page2.json()["total"] == 5


def test_list_page_past_the_end_is_empty_with_correct_total(five_employees):
    response = five_employees.get("/employees", params={"page": 9})

    assert response.json()["items"] == []
    assert response.json()["total"] == 5


def test_list_filters_by_department_and_total_reflects_filter(five_employees):
    response = five_employees.get("/employees", params={"department": "Sales"})

    assert codes(response) == ["EMP0003", "EMP0004"]
    assert response.json()["total"] == 2


def test_list_department_filter_is_case_sensitive(five_employees):
    response = five_employees.get("/employees", params={"department": "sales"})

    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_list_returns_created_at_as_ms_for_seeded_documents(client, clean_db):
    clean_db.employees.insert_one({
        "emp_code": "EMP0009", "name": "Seeded", "email": "s@example.com", "department": "Ops",
        "shift_start": "09:30", "shift_end": "18:30", "joined_on": "2026-01-05",
        "created_at": datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc),
    })

    item = client.get("/employees").json()["items"][0]

    assert item["created_at"] == 1767587400000
    assert "_id" not in item


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": -1}, {"page": "abc"}, {"page_size": 0}, {"page_size": 101}, {"page_size": 2.5}],
)
def test_list_rejects_bad_pagination_with_422(client, params):
    assert client.get("/employees", params=params).status_code == 422
