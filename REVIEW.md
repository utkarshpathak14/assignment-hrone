# REVIEW.md

Defects found in the starter `app/main.py` (helpers and endpoints).

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `MongoClient(...)` (module level) | Created without `tz_aware=True`, so every datetime read back from MongoDB is naive (no time zone). Mixing it with an aware datetime raises `TypeError`, and naive values are silently treated as local time by `datetime` methods. | Read a log and compute `punch_out - datetime.now(timezone.utc)`: `TypeError: can't subtract offset-naive and offset-aware datetimes` → 500. | `MongoClient(uri, tz_aware=True, ...)`; all datetimes in the app are UTC-aware. |
| 2 | `MongoClient(...)` (module level) | No `serverSelectionTimeoutMS`: when MongoDB is down every request blocks for the 30 s default before failing. | Stop MongoDB, call any endpoint: the request hangs about 30 s, then returns 500. | `serverSelectionTimeoutMS=3000`. |
| 3 | `health()` | Always returns 200 without touching MongoDB, but the contract says 200 only when MongoDB answers, else 503. | Stop MongoDB: `/health` still says `ok`, so the runner would start testing a dead app. | `client.admin.command("ping")`; `PyMongoError` → 503 `{"detail": ...}`. Test: `test_health_returns_503_when_mongo_is_down`. |
| 4 | (whole file) | No indexes are created. The grader's DB only has `_id`, so uniqueness is not enforced and every list/analytics query is a collection scan. | `db.attendance_logs.getIndexes()` shows only `_id_`; explain of `/attendance` shows `COLLSCAN`. | `ensure_indexes()` run from the FastAPI `lifespan` at startup (idempotent). Indexes are added per feature. |

## Looked at and decided were not defects
- `load_dotenv()` is called without `override=True`. That is correct: real environment variables keep priority over `.env`, as the contract requires.
