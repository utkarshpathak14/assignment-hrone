# Employee Attendance & Analytics API

FastAPI + MongoDB service for punch-in/punch-out, manual corrections with an audit trail, and attendance analytics
built on MongoDB aggregation pipelines. All application code is in `app/main.py`.

## Requirements
- Python 3.11+
- MongoDB 6.0+ (developed and tested against 7.0), e.g. `docker run -d -p 27017:27017 --name mongo mongo:7`

## Run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # or export MONGO_URI / MONGO_DB (real environment variables take priority)
uvicorn app.main:app --port 8000
```

All indexes are created at startup (idempotently), so the app can start against an already-seeded database.
Interactive docs: http://localhost:8000/docs. `python sample_seed.py` loads the nine sample documents.

| Variable | Default | Meaning |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `attendance_db` | Database name |

## Tests
```bash
pytest -q
```
225 tests. They use a separate database (`attendance_test`) on the same `MONGO_URI`, and start a second server on
port 8001 for the concurrency tests (parallel punch-ins, punch-outs, corrections and duplicate creates).
`tests/fixtures.py` is a small July 2026 dataset whose expected analytics were worked out by hand.

## Performance check (about 100k records)
```bash
python scripts/gen_data.py                                   # 400 employees, ~100k logs into "attendance_perf"
MONGO_DB=attendance_perf uvicorn app.main:app --port 8002
python scripts/check_explain.py --url http://127.0.0.1:8002  # IXSCAN and no COLLSCAN for every explain target
```

## Assumptions
Points the contract leaves open, and how they are handled:

- **Trend range limit:** the range is inclusive, so `from`..`to` may cover at most 92 calendar days (07-01..09-30 is
  allowed; 07-01..10-01 is 422).
- **Moving average:** averages the reported (4-decimal, half-up) `attendance_rate` values, then rounds half-up to 4 decimals.
- **Punch-out at exactly 24 h** after punch-in is accepted ("more than 24 hours" is 422). If the latest record is
  already closed, 409 takes precedence over a 422 on the time.
- **Records dated before `joined_on`** are counted as stored (analytics read the stored data). Working days still
  start at `joined_on`.
- **Unknown department** in the summary or leaderboard filter returns `items: []` (the contract defines no 404 there).
  For the trend, a department exists if it has any employee, even one who has not joined yet; such days have headcount 0 and null rates.
- **Order of checks in PATCH:** body validation (422) → unknown employee (404) → no record for the date (404) →
  rule violations (422) → concurrent modification (409). A concurrent correction that loses gets 409 and should
  retry; this keeps every history entry's `from` equal to the previous entry's `to`.
- **`null` in a PATCH body** for `status`/`punch_in`/`punch_out` is 422; omit the field to leave it unchanged.
- **Leaderboard** considers every log in the month whose employee exists (it has no joined-on rule in the contract).
- Our own 422 responses use the same body shape as FastAPI's validation errors (`detail` is a list of `{loc, msg, type}`).

## Status
All endpoints in `openapi.yaml` are implemented. Nothing is knowingly missing.
