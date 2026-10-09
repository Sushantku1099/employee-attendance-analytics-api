# Employee Attendance & Analytics API — Phases 1–3

Python 3.11+, MongoDB 6.0+ (grader uses 7.0).

```bash
pip install -r requirements.txt
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=attendance_db
uvicorn app.main:app --port 8000
```

An optional local .env may set MONGO_URI and MONGO_DB; real environment variables take priority. Startup creates indexes. GET /health checks MongoDB. Optional `python sample_seed.py` **deletes both collections' contents** before loading samples; use only a disposable database.

Implemented: health, employee creation/listing, punch-in/out, attendance listing/corrections, all four analytics reports and the explain endpoint. Analytics run in MongoDB; explain returns actual executionStats. See REVIEW.md for verification and limitations.

Unit checks: `python -B static_helper_tests.py`, `python -B openapi_tests.py`, and `python -B phase2_unit_tests.py` test the actual application helpers, models and schema without database reads or writes.

HTTP integration checks require a **fresh isolated database**, with no sample seed:

```bash
MONGO_DB=candidate_phase1_test uvicorn app.main:app --port 18000
# In another terminal:
TEST_BASE_URL=http://localhost:18000 PHASE1_ALLOW_TEST_WRITES=1 python -B phase1_tests.py
```

Stop the server before resetting the test database, then restart so indexes are recreated. Tests require an explicit write opt-in and test URL. They insert records and reject duplicate fixture codes; that is not proof that the database is disposable. Remove only your disposable test database after stopping the server. Never run them against valuable data.

Do not submit .env, virtual environments, caches, dumps or secrets. .gitignore excludes these, but this folder is not yet a Git repository. All application code remains in app/main.py.

Phase 2 HTTP tests also check stored BSON timestamps and deliberately create legacy fixtures. Start an API against a fresh disposable MongoDB instance, then point the tests at that same instance and database:

```bash
TEST_BASE_URL=http://localhost:18000 PHASE2_ALLOW_TEST_WRITES=1 \
TEST_MONGO_URI=mongodb://localhost:27017 TEST_MONGO_DB=candidate_phase2_test \
python -B phase2_tests.py
```

The API in this example must have MONGO_URI=mongodb://localhost:27017 and MONGO_DB=candidate_phase2_test. These tests leave fixtures in the disposable database; they do not delete databases. Never use a database containing valuable data. Tests in this review ran against a separate temporary MongoDB 7 container, which was removed afterward.

To reproduce the isolated checks, use `python -B verify_phase2.py` from the project root. Docker must be running and the mongo:7 image must already be available. The runner starts its own MongoDB container and API, runs both phases' tests, checks startup/indexes, then removes only its own test container. It never uses an existing database or runs sample_seed.py.

For the full regression suite, run `python -B verify_phase3.py`. It requires Docker and an already available mongo:7 image; it installs nothing. It runs all earlier suites, Phase 3 schema/HTTP tests, and indexed-plan checks on 100,000 synthetic logs in its own disposable container. Fixtures never touch an existing database, and the runner removes its container afterward. Raw explain documents retain MongoDB's internal plan fields and encode BSON values as Extended JSON.
