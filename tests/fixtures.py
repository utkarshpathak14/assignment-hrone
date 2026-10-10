"""A small, hand-checkable dataset for July 2026 with every awkward case the business rules mention.

July 2026 starts on a Wednesday: 23 working days (Mon-Fri). Weekends: 4-5, 11-12, 18-19, 25-26 July.

The EXPECTED_* values below were worked out by hand from the rules in openapi.yaml, NOT by running the code.
The derived values stored on each log are what the analytics must read ("stored values are authoritative"),
so a few of them deliberately disagree with the punch times (see EMP0006 on 07-06).
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def _employee(code, name, department, joined_on, shift=("09:30", "18:30")):
    return {
        "emp_code": code, "name": name, "email": f"{code.lower()}@example.com", "department": department,
        "shift_start": shift[0], "shift_end": shift[1], "joined_on": joined_on,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


EMPLOYEES = [
    _employee("EMP0001", "Asha Rao", "Engineering", "2026-01-05"),
    _employee("EMP0002", "Vikram Shah", "Engineering", "2026-07-13"),        # joins mid-month (a Monday)
    _employee("EMP0003", "Meera Iyer", "Engineering", "2026-02-01"),         # no logs at all
    _employee("EMP0004", "Kabir Das", "Sales", "2026-01-01", ("22:00", "06:00")),  # overnight shift
    _employee("EMP0005", "Nisha Jain", "Sales", "2026-08-10"),               # joins after July
    _employee("EMP0006", "Ravi Kumar", "Operations", "2026-01-01"),
    _employee("EMP0007", "Zoya Khan", "Lab", "2026-09-01"),                  # whole department not joined yet
]


def _log(code, day, status, work_hours=None, late=0, overtime=0, half_day=False, legacy=False):
    y, m, d = map(int, day.split("-"))
    present = status in ("PRESENT", "WFH", "ON_DUTY")
    punch_in = datetime(y, m, d, 9, 30, tzinfo=IST) if present else None
    punch_out = punch_in + timedelta(hours=work_hours) if present and work_hours is not None else None
    doc = {
        "emp_code": code, "date": day, "status": status, "punch_in": punch_in, "punch_out": punch_out,
        "work_hours": work_hours, "late_minutes": late, "overtime_minutes": overtime,
    }
    if not legacy:  # legacy documents omit half_day and history entirely
        doc["half_day"] = half_day
        doc["history"] = []
    return doc


LOGS = [
    # EMP0001 - Engineering
    _log("EMP0001", "2026-06-30", "PRESENT", 8.00, late=99),                  # June: outside the month
    _log("EMP0001", "2026-07-06", "PRESENT", 9.12),
    _log("EMP0001", "2026-07-07", "PRESENT", 9.00, late=35, overtime=40),
    _log("EMP0001", "2026-07-08", "WFH", 3.50, half_day=True),               # half day = 0.5
    _log("EMP0001", "2026-07-09", "LEAVE"),
    _log("EMP0001", "2026-07-10", "ABSENT"),
    _log("EMP0001", "2026-07-11", "PRESENT", 5.00, late=20, overtime=30),     # Saturday
    _log("EMP0001", "2026-07-13", "ON_DUTY", 8.00, legacy=True),              # no half_day / history fields
    _log("EMP0001", "2026-07-14", "PRESENT", None, late=15),                  # still open (no punch-out)
    _log("EMP0001", "2026-08-01", "PRESENT", 8.00, late=99),                  # August: outside the month
    # EMP0002 - Engineering, joined 2026-07-13
    _log("EMP0002", "2026-07-13", "PRESENT", 8.50),
    _log("EMP0002", "2026-07-14", "PRESENT", 4.25, late=70, half_day=True),
    _log("EMP0002", "2026-07-15", "PRESENT", 8.99, late=70),
    # EMP0004 - Sales, overnight
    _log("EMP0004", "2026-07-06", "PRESENT", 8.75, overtime=40),
    _log("EMP0004", "2026-07-07", "PRESENT", 8.00, late=70),
    _log("EMP0004", "2026-07-12", "PRESENT", 8.00, late=10),                  # Sunday
    # EMP0006 - Operations. Stored late=140 although punch_in is 09:30: analytics must use the stored value.
    _log("EMP0006", "2026-07-06", "PRESENT", 8.00, late=140),
    _log("EMP0006", "2026-07-07", "ON_DUTY", 7.00),
    _log("EMP0006", "2026-07-08", "LEAVE"),
    # Orphan log: no such employee. Must be ignored by everything that joins on employees.
    _log("EMP9999", "2026-07-06", "PRESENT", 8.00, late=500),
]


def load(db):
    db.employees.delete_many({})
    db.attendance_logs.delete_many({})
    db.employees.insert_many([dict(e) for e in EMPLOYEES])
    db.attendance_logs.insert_many([dict(l) for l in LOGS])


# --------------------------------------------------------------------------- #
# Expected results, worked out by hand
# --------------------------------------------------------------------------- #
EXPECTED_MONTHLY_JULY = {
    # present: 07-06 1 + 07-07 1 + 07-08 0.5 + 07-13 1 + 07-14 1 = 4.5 (Saturday 07-11 excluded)
    # late: 07-07 35 + 07-11 20 + 07-14 15 (weekend counts for late/overtime); overtime 40 + 30
    # pct = 4.5 / 23 * 100 = 19.5652... -> 19.57
    "EMP0001": {"working_days": 23, "present_days": 4.5, "leave_days": 1, "late_count": 3,
                "total_late_minutes": 70, "total_overtime_minutes": 70, "attendance_pct": 19.57},
    # working days from 07-13 to 07-31 = 15; present 1 + 0.5 + 1 = 2.5; pct = 16.666... -> 16.67
    "EMP0002": {"working_days": 15, "present_days": 2.5, "leave_days": 0, "late_count": 2,
                "total_late_minutes": 140, "total_overtime_minutes": 0, "attendance_pct": 16.67},
    "EMP0003": {"working_days": 23, "present_days": 0, "leave_days": 0, "late_count": 0,
                "total_late_minutes": 0, "total_overtime_minutes": 0, "attendance_pct": 0.0},
    # Sunday 07-12 adds to late (10) but not to present days; pct = 2 / 23 * 100 = 8.6956... -> 8.70
    "EMP0004": {"working_days": 23, "present_days": 2, "leave_days": 0, "late_count": 2,
                "total_late_minutes": 80, "total_overtime_minutes": 40, "attendance_pct": 8.7},
    # joins in August: no working days in July -> pct is null
    "EMP0005": {"working_days": 0, "present_days": 0, "leave_days": 0, "late_count": 0,
                "total_late_minutes": 0, "total_overtime_minutes": 0, "attendance_pct": None},
    "EMP0006": {"working_days": 23, "present_days": 2, "leave_days": 1, "late_count": 1,
                "total_late_minutes": 140, "total_overtime_minutes": 0, "attendance_pct": 8.7},
}

EXPECTED_SUMMARY_JULY = [
    # Engineering: headcount 3 (EMP0003 has no logs but counts). Work hours over the 8 presence records with a value:
    # 9.12 + 9.00 + 3.50 + 5.00 + 8.00 + 8.50 + 4.25 + 8.99 = 56.36 / 8 = 7.045 -> 7.05 (half-up; float gives 7.04)
    # (The average of per-employee averages would be (34.62/5 + 21.74/3) / 2 = 7.085, which is wrong.)
    {"department": "Engineering", "headcount": 3, "present_days": 7.0, "avg_work_hours": 7.05,
     "late_count": 5, "total_late_minutes": 210, "leave_count": 1, "on_duty_count": 1},
    {"department": "Operations", "headcount": 1, "present_days": 2, "avg_work_hours": 7.5,
     "late_count": 1, "total_late_minutes": 140, "leave_count": 1, "on_duty_count": 1},
    # Sales: EMP0005 joins in August, so headcount 1. (8.75 + 8.00 + 8.00) / 3 = 8.25
    {"department": "Sales", "headcount": 1, "present_days": 2, "avg_work_hours": 8.25,
     "late_count": 2, "total_late_minutes": 80, "leave_count": 0, "on_duty_count": 0},
    # Lab is omitted: its only employee joins in September (headcount 0).
]

# Totals: EMP0002 140, EMP0006 140 (tie), EMP0004 80, EMP0001 70. EMP9999 (500) has no employee.
# Competition ranking: 1, 1, 3, 4.
LEADERBOARD_JULY = [
    {"rank": 1, "emp_code": "EMP0002", "name": "Vikram Shah", "department": "Engineering",
     "total_late_minutes": 140, "late_count": 2},
    {"rank": 1, "emp_code": "EMP0006", "name": "Ravi Kumar", "department": "Operations",
     "total_late_minutes": 140, "late_count": 1},
    {"rank": 3, "emp_code": "EMP0004", "name": "Kabir Das", "department": "Sales",
     "total_late_minutes": 80, "late_count": 2},
    {"rank": 4, "emp_code": "EMP0001", "name": "Asha Rao", "department": "Engineering",
     "total_late_minutes": 70, "late_count": 3},
]


def _row(day, working, headcount, present, late, rate, moving):
    return {"date": day, "is_working_day": working, "headcount": headcount, "present_count": present,
            "late_count": late, "attendance_rate": rate, "moving_avg_7d": moving}


# Engineering, 2026-07-04 (Sat) to 2026-07-15 (Wed). Headcount 2 (EMP0001, EMP0003) until EMP0002 joins on 07-13.
EXPECTED_TREND_ENGINEERING = [
    _row("2026-07-04", False, 2, 0, 0, None, None),
    _row("2026-07-05", False, 2, 0, 0, None, None),
    _row("2026-07-06", True, 2, 1, 0, 0.5, 0.5),
    _row("2026-07-07", True, 2, 1, 1, 0.5, 0.5),
    _row("2026-07-08", True, 2, 0.5, 0, 0.25, 0.4167),     # (0.5 + 0.5 + 0.25) / 3
    _row("2026-07-09", True, 2, 0, 0, 0.0, 0.3125),        # LEAVE: a working day with no presence -> 0.0
    _row("2026-07-10", True, 2, 0, 0, 0.0, 0.25),
    _row("2026-07-11", False, 2, 1, 1, None, 0.25),        # Saturday work: counted, but no rate
    _row("2026-07-12", False, 2, 0, 0, None, 0.25),
    _row("2026-07-13", True, 3, 2, 0, 0.6667, 0.2833),     # window 07-07..07-13: 1.4167 / 5
    _row("2026-07-14", True, 3, 1.5, 2, 0.5, 0.2833),      # window 07-08..07-14
    _row("2026-07-15", True, 3, 1, 1, 0.3333, 0.3),        # window 07-09..07-15: 1.5 / 5
]
