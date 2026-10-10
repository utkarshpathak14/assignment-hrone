"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import AfterValidator, BaseModel, Field, StrictInt, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

# override=False (the default): a real environment variable always wins over a value in .env.
load_dotenv()

# tz_aware=True: datetimes read from MongoDB come back as UTC-aware, never naive.
# serverSelectionTimeoutMS: fail fast (instead of the 30 s default) when MongoDB is unreachable.
client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    serverSelectionTimeoutMS=3000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

PRESENCE = ("PRESENT", "WFH", "ON_DUTY")
STATUSES = ("PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE")
GRACE_SECONDS = 10 * 60
MIN_OVERTIME_MINUTES = 30
HALF_DAY_HOURS = 4.5
MAX_SHIFT = timedelta(hours=24)
# Fields a correction can change, in the order they appear in history[].changes.
TRACKED_FIELDS = ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")


def ensure_indexes() -> None:
    """Create every index the app relies on. create_index is idempotent, so running it on every start is safe."""
    # Unique: enforces one employee per emp_code even under concurrent creates.
    db.employees.create_index("emp_code", unique=True)
    # GET /employees?department=X sorted by emp_code: equality field first, then the sort field.
    db.employees.create_index([("department", 1), ("emp_code", 1)])
    # Unique natural key of a record: makes concurrent punch-ins for the same day collide in the database.
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    # Punch-out: "this employee's latest record with punch_in <= t" = equality, then a range/sort on punch_in.
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
    # GET /attendance: sort is date desc, emp_code asc. Serves unfiltered lists and date-range filters,
    # and month-range scans in the analytics pipelines.
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    # GET /attendance?status=X: equality first, then the sort keys (ESR rule: Equality, Sort, Range).
    db.attendance_logs.create_index([("status", 1), ("date", -1), ("emp_code", 1)])


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_indexes()
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def to_ms(dt: Optional[datetime]) -> Optional[int]:
    """Aware datetime -> epoch milliseconds (exact integer maths, no float rounding)."""
    if not isinstance(dt, datetime):
        return None
    return (dt - EPOCH) // timedelta(milliseconds=1)


def to_utc(ms: int) -> datetime:
    """Epoch milliseconds -> aware UTC datetime, truncated to whole seconds (R1)."""
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_utc_ms() -> datetime:
    """Now, at millisecond precision (what BSON can store), so the stored and returned values agree."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def parse_hhmm(value: str) -> time:
    hours, minutes = map(int, value.split(":"))
    return time(hours, minutes)


def is_overnight(emp: dict) -> bool:
    # "HH:MM" strings are zero-padded, so string comparison equals time comparison.
    return emp["shift_end"] <= emp["shift_start"]


def at_ist(day: date, hhmm: str) -> datetime:
    """The instant that is `hhmm` IST on calendar day `day`."""
    return datetime.combine(day, parse_hhmm(hhmm), tzinfo=IST)


def attendance_date(punch_in: datetime, emp: dict) -> date:
    """R1: the IST date of the punch-in; for an overnight shift, a punch-in before shift_end belongs to yesterday."""
    local = punch_in.astimezone(IST)
    if is_overnight(emp) and local.time() < parse_hhmm(emp["shift_end"]):
        return local.date() - timedelta(days=1)
    return local.date()


def shift_bounds(day: date, emp: dict) -> tuple[datetime, datetime]:
    """Start and end instants of the shift that starts on `day`. An overnight shift ends the next day."""
    start = at_ist(day, emp["shift_start"])
    end = at_ist(day, emp["shift_end"])
    if is_overnight(emp):
        end += timedelta(days=1)
    return start, end


def unprocessable(where: str, field: str, message: str):
    """422 with the same body shape FastAPI uses for its own validation errors. `where` is "body", "query" or "path"."""
    return HTTPException(422, [{"type": "value_error", "loc": [where, field], "msg": message}])


def get_employee_or_404(emp_code: str) -> dict:
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    return emp


def serialize_employee(doc: dict) -> dict:
    """MongoDB employee document -> API shape: no _id, created_at as epoch ms."""
    return {
        "emp_code": doc["emp_code"],
        "name": doc.get("name"),
        "email": doc.get("email"),
        "department": doc.get("department"),
        "shift_start": doc.get("shift_start"),
        "shift_end": doc.get("shift_end"),
        "joined_on": doc.get("joined_on"),
        "created_at": to_ms(doc.get("created_at")),
    }


def serialize_history(entry: dict) -> dict:
    changes = {}
    for field, change in (entry.get("changes") or {}).items():
        if field in ("punch_in", "punch_out"):
            changes[field] = {"from": to_ms(change.get("from")), "to": to_ms(change.get("to"))}
        else:
            changes[field] = {"from": change.get("from"), "to": change.get("to")}
    return {"at": to_ms(entry.get("at")), "by": entry.get("by"), "reason": entry.get("reason"), "changes": changes}


def serialize_record(doc: dict) -> dict:
    """MongoDB attendance document -> API shape. Fills the defaults legacy documents may omit."""
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc.get("status"),
        "punch_in": to_ms(doc.get("punch_in")),
        "punch_out": to_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes") or 0,
        "overtime_minutes": doc.get("overtime_minutes") or 0,
        "half_day": bool(doc.get("half_day") or False),
        "history": [serialize_history(h) for h in doc.get("history") or []],
    }


def current_values(doc: dict) -> dict:
    """The tracked fields of a stored record, with the defaults that missing legacy fields stand for."""
    return {
        "status": doc.get("status"),
        "punch_in": doc.get("punch_in"),
        "punch_out": doc.get("punch_out"),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes") or 0,
        "overtime_minutes": doc.get("overtime_minutes") or 0,
        "half_day": bool(doc.get("half_day") or False),
    }


def compute_late_minutes(punch_in: datetime, day: date, emp: dict) -> int:
    """R2: late only if strictly more than 10:00 after shift start; then whole minutes since shift start."""
    start, _ = shift_bounds(day, emp)
    seconds = (punch_in - start) // timedelta(seconds=1)
    return seconds // 60 if seconds > GRACE_SECONDS else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """R4: hours worked, rounded to 2 decimals half-up. Decimal avoids float and banker's rounding."""
    seconds = (punch_out - punch_in) // timedelta(seconds=1)
    hours = (Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(hours)


def compute_overtime(punch_out: datetime, day: date, emp: dict) -> int:
    """R3: whole minutes after shift end, counted only from 30 minutes."""
    _, end = shift_bounds(day, emp)
    minutes = (punch_out - end) // timedelta(minutes=1)
    return minutes if minutes >= MIN_OVERTIME_MINUTES else 0


def derive(status: str, punch_in: Optional[datetime], punch_out: Optional[datetime], day: date, emp: dict) -> dict:
    """The four derived fields for a record, per the DATA_MODEL table (R2-R5)."""
    if status not in PRESENCE or punch_in is None:
        return {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    late = compute_late_minutes(punch_in, day, emp)
    if punch_out is None:
        return {"work_hours": None, "late_minutes": late, "overtime_minutes": 0, "half_day": False}
    work_hours = compute_work_hours(punch_in, punch_out)
    return {
        "work_hours": work_hours,
        "late_minutes": late,
        "overtime_minutes": compute_overtime(punch_out, day, emp),
        "half_day": work_hours < HALF_DAY_HOURS,  # R5 compares the *rounded* hours
    }


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _real_date(value: str) -> str:
    if not DATE_RE.fullmatch(value):
        raise ValueError("expected a date as YYYY-MM-DD")
    date.fromisoformat(value)  # raises ValueError for impossible dates such as 2026-02-30
    return value


# Reusable constrained types. [0-9] instead of \d: \d also matches non-ASCII digits.
# DateStr validates inside a function (not Field(pattern=...)), so the same type works for body, query and path.
DateStr = Annotated[str, AfterValidator(_real_date)]
TimeHHMM = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Status = Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]


class EmployeeCreate(BaseModel):
    emp_code: str = Field(pattern=r"^EMP[0-9]{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: TimeHHMM = "09:30"
    shift_end: TimeHHMM = "18:30"
    joined_on: DateStr

    @model_validator(mode="after")
    def shift_has_length(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


# Epoch milliseconds. StrictInt rejects strings ("1783312500000"), floats (1.7e12) and booleans;
# the lower bound rejects values that look like seconds.
EpochMs = Annotated[StrictInt, Field(ge=100_000_000_000, le=4_102_444_800_000)]


class RegularizeRequest(BaseModel):
    """Only these fields can be changed. Derived fields sent by a client are ignored (Pydantic drops unknown keys)."""
    status: Optional[Status] = None
    punch_in: Optional[EpochMs] = None
    punch_out: Optional[EpochMs] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def no_explicit_nulls(self):
        # Omitting a field means "unchanged"; sending null is not a valid value for any of them.
        for name in ("status", "punch_in", "punch_out"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(503, "database unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        # No find_one pre-check: the unique index decides, atomically, who wins a concurrent create.
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: Page = 1, page_size: PageSize = 20):
    q = {}
    if department is not None:
        q["department"] = department
    total = db.employees.count_documents(q)
    cursor = db.employees.find(q).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size)
    items = [serialize_employee(doc) for doc in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    emp = get_employee_or_404(body.emp_code)
    ts = to_utc(body.punched_at) if body.punched_at is not None else now_utc()
    day = attendance_date(ts, emp)
    doc = {
        "emp_code": body.emp_code,
        "date": day.isoformat(),
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        **derive(body.status, ts, None, day, emp),
        "history": [],
    }
    try:
        # The unique (emp_code, date) index is the guard: of two simultaneous inserts, exactly one succeeds.
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return serialize_record(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    emp = get_employee_or_404(body.emp_code)
    ts = to_utc(body.punched_at) if body.punched_at is not None else now_utc()
    # The most recent record that started at or before this punch-out. Works across midnight because it
    # searches by instant, not by calendar date.
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}}, sort=[("punch_in", -1)]
    )
    if record is None:
        raise HTTPException(404, "no punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")
    if ts <= record["punch_in"] or ts - record["punch_in"] > MAX_SHIFT:
        raise unprocessable("body", "punched_at", "punch-out must be after punch-in and within 24 hours")

    derived = derive(record["status"], record["punch_in"], ts, date.fromisoformat(record["date"]), emp)
    # Atomic close: the filter only matches while punch_out is still null, so of two concurrent
    # punch-outs exactly one update matches; the other gets None back.
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": derived["work_hours"],
            "overtime_minutes": derived["overtime_minutes"],
            "half_day": derived["half_day"],
        }},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "already punched out")
    return serialize_record(updated)


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: DateStr, body: RegularizeRequest):
    emp = get_employee_or_404(emp_code)
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(404, "no attendance record for that date")
    day = datetime.fromisoformat(date).date()  # (the parameter `date` shadows the date class here)
    old = current_values(record)

    # 1. The final state after applying the request (omitted fields keep their stored value).
    status = body.status if body.status is not None else old["status"]
    if status in PRESENCE:
        punch_in = to_utc(body.punch_in) if body.punch_in is not None else old["punch_in"]
        punch_out = to_utc(body.punch_out) if body.punch_out is not None else old["punch_out"]
        if punch_in is None:
            raise unprocessable("body", "punch_in", "a presence status requires a punch_in")
        if attendance_date(punch_in, emp) != day:
            raise unprocessable("body", "punch_in", "punch_in must stay on the record's attendance date")
        if punch_out is not None and (punch_out <= punch_in or punch_out - punch_in > MAX_SHIFT):
            raise unprocessable("body", "punch_out", "punch_out must be after punch_in and within 24 hours")
    else:
        if body.punch_in is not None or body.punch_out is not None:
            raise unprocessable("body", "status", f"{status} clears both punch times; do not send punch_in/punch_out")
        punch_in = punch_out = None

    # 2. Recompute the derived fields from the final times, then diff against what is stored.
    new = {"status": status, "punch_in": punch_in, "punch_out": punch_out,
           **derive(status, punch_in, punch_out, day, emp)}
    changes = {f: {"from": old[f], "to": new[f]} for f in TRACKED_FIELDS if old[f] != new[f]}
    if not changes:
        raise unprocessable("body", "status", "the request changes nothing")

    # 3. Optimistic concurrency: only write if the record still looks exactly as we read it (same times,
    #    same status, same number of history entries). A concurrent edit makes the filter miss -> 409,
    #    so no correction can silently overwrite another or lose its history entry.
    entry = {"at": now_utc_ms(), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "status": record.get("status"),
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
            "$expr": {"$eq": [{"$size": {"$ifNull": ["$history", []]}}, len(record.get("history") or [])]},
        },
        {"$set": new, "$push": {"history": entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "the record was changed by another request; reload and retry")
    return serialize_record(updated)


def build_attendance_query(
    emp_code: Optional[str], date_from: Optional[str], date_to: Optional[str], status: Optional[str],
    page: int, page_size: int,
) -> dict:
    """The find() spec for GET /attendance. Shared with /admin/explain so both run exactly the same query."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise unprocessable("query", "date_from", "date_from must be on or before date_to")
    q = {}
    if emp_code is not None:
        q["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        q["date"] = {}
        if date_from is not None:
            q["date"]["$gte"] = date_from
        if date_to is not None:
            q["date"]["$lte"] = date_to
    if status is not None:
        q["status"] = status
    return {
        "filter": q,
        "sort": [("date", -1), ("emp_code", 1)],
        "skip": (page - 1) * page_size,
        "limit": page_size,
    }


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[DateStr] = None,
    date_to: Optional[DateStr] = None,
    status: Optional[Status] = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    spec = build_attendance_query(emp_code, date_from, date_to, status, page, page_size)
    total = db.attendance_logs.count_documents(spec["filter"])
    cursor = db.attendance_logs.find(spec["filter"]).sort(spec["sort"]).skip(spec["skip"]).limit(spec["limit"])
    items = [serialize_record(doc) for doc in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
