# Employee Attendance & Analytics API

FastAPI + MongoDB service for punch-in/punch-out, manual corrections with an audit trail, and attendance analytics
built on MongoDB aggregation pipelines. All application code lives in `app/main.py`.

## Requirements
- Python 3.11+
- MongoDB 6.0+ (tested with 7.0), e.g. `docker run -d -p 27017:27017 --name mongo mongo:7`

## Run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # or export MONGO_URI / MONGO_DB (real env vars take priority)
python sample_seed.py              # optional: load the nine sample documents
uvicorn app.main:app --port 8000
```

Interactive API docs: http://localhost:8000/docs

## Configuration
| Variable | Default | Meaning |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `attendance_db` | Database name |

## Tests
```bash
pytest -q
```
Tests use a separate database, `attendance_test`, on the same `MONGO_URI`.

## Assumptions
_To be filled in as the endpoints are built._

## Status
Work in progress.
