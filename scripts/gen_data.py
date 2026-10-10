"""Generate a large, realistic dataset for performance and explain() checks.

Usage:
    python scripts/gen_data.py                       # ~100k logs into the database "attendance_perf"
    python scripts/gen_data.py --db attendance_db --employees 50 --days 60

It REPLACES both collections in the target database. The default target is a separate database on purpose,
so your development data is never touched by accident. Derived fields are computed with the app's own rule
helpers, so the data looks like what the API itself would write.
"""
import argparse
import os
import pathlib
import random
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pymongo import MongoClient  # noqa: E402

from app.main import IST, attendance_date, derive  # noqa: E402

DEPARTMENTS = ["Engineering", "Sales", "Operations", "Finance", "Support", "HR"]
SHIFTS = [("09:30", "18:30")] * 6 + [("10:00", "19:00"), ("07:00", "16:00"), ("22:00", "06:00")]


def make_employees(count: int, start: date, days: int, rng: random.Random) -> list[dict]:
    employees = []
    for i in range(1, count + 1):
        # Most people joined before the range; some join in the middle of it; a few have not joined yet.
        roll = rng.random()
        if roll < 0.75:
            joined = start - timedelta(days=rng.randint(1, 900))
        elif roll < 0.95:
            joined = start + timedelta(days=rng.randint(0, days - 1))
        else:
            joined = start + timedelta(days=days + rng.randint(1, 60))
        shift_start, shift_end = rng.choice(SHIFTS)
        employees.append({
            "emp_code": f"EMP{i:04d}",
            "name": f"Employee {i}",
            "email": f"emp{i:04d}@example.com",
            "department": rng.choice(DEPARTMENTS),
            "shift_start": shift_start,
            "shift_end": shift_end,
            "joined_on": joined.isoformat(),
            "created_at": datetime.combine(joined, datetime.min.time(), tzinfo=timezone.utc),
        })
    return employees


def make_log(emp: dict, day: date, rng: random.Random) -> dict | None:
    weekend = day.isoweekday() >= 6
    if rng.random() > (0.06 if weekend else 0.93):
        return None  # no record at all for this day
    roll = rng.random()
    if not weekend and roll < 0.04:
        status = "LEAVE"
    elif not weekend and roll < 0.07:
        status = "ABSENT"
    else:
        status = rng.choices(["PRESENT", "WFH", "ON_DUTY"], weights=[80, 15, 5])[0]

    doc = {"emp_code": emp["emp_code"], "date": day.isoformat(), "status": status,
           "punch_in": None, "punch_out": None, "history": []}
    if status in ("LEAVE", "ABSENT"):
        doc.update(derive(status, None, None, day, emp))
        return doc

    start = datetime.combine(day, datetime.strptime(emp["shift_start"], "%H:%M").time(), tzinfo=IST)
    punch_in = start + timedelta(seconds=rng.randint(-20 * 60, 75 * 60))
    if attendance_date(punch_in, emp) != day:  # keep R1 consistent for overnight shifts
        punch_in = start
    hours = rng.choice([rng.uniform(3, 4.6), rng.uniform(7.5, 10.5), rng.uniform(8, 9.5)])
    punch_out = punch_in + timedelta(seconds=int(hours * 3600))
    if rng.random() < 0.02:
        punch_out = None  # forgot to punch out
    punch_in, punch_out = punch_in.astimezone(timezone.utc), punch_out and punch_out.astimezone(timezone.utc)
    doc.update(punch_in=punch_in, punch_out=punch_out, **derive(status, punch_in, punch_out, day, emp))
    if rng.random() < 0.1:  # some legacy documents without the newer fields
        doc.pop("history")
        doc.pop("half_day")
    return doc


def generate(db, employees: int = 400, days: int = 365, start: date = date(2025, 10, 1), seed: int = 42) -> dict:
    rng = random.Random(seed)  # fixed seed: the same dataset every run
    emps = make_employees(employees, start, days, rng)
    db.employees.drop()
    db.attendance_logs.drop()
    db.employees.insert_many(emps)

    batch, total = [], 0
    for emp in emps:
        for offset in range(days):
            doc = make_log(emp, start + timedelta(days=offset), rng)
            if doc:
                batch.append(doc)
            if len(batch) >= 10_000:
                db.attendance_logs.insert_many(batch)
                total += len(batch)
                batch = []
    if batch:
        db.attendance_logs.insert_many(batch)
        total += len(batch)
    return {"employees": len(emps), "logs": total}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db", default="attendance_perf")
    parser.add_argument("--employees", type=int, default=400)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 10, 1))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    db = MongoClient(args.uri, tz_aware=True)[args.db]
    counts = generate(db, args.employees, args.days, args.start, args.seed)
    print(f"{db.name}: {counts['employees']} employees, {counts['logs']} attendance logs")


if __name__ == "__main__":
    main()
