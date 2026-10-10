# Employee Attendance & Analytics API

FastAPI + PyMongo assignment implementation. Requires Python 3.11+ and MongoDB
6.0+; all application code lives in `app/main.py`.

**Do not run `sample_seed.py` against a valuable database: it deletes collection
contents. It is never needed for verification.**

Start the API from the repository root, using a Python 3.11+ environment and your
chosen MongoDB database:

```bash
python -m pip install -r requirements.txt
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=attendance_db
python -m uvicorn app.main:app --port 8000
```

A local `.env` is optional; real environment variables take priority. Startup
creates indexes idempotently. `/health` returns 200 when MongoDB answers a ping.

Run the complete isolated regression suite with the same installed requirements,
Docker running and the `mongo:7` image already available:

```bash
python -B verify_phase3.py
```

This is the authoritative test command. It starts its own API and fresh MongoDB
container with a dynamic loopback port and unique database. It overrides database
settings and removes only its own process/container, including on test failure.
It installs nothing and never uses your application database. To check MongoDB 6,
use `python -B verify_phase3.py --mongo-image mongo:6.0` with that image available.
CI uses `pip install -r requirements.txt` and this runner for both images.

The suite covers helpers/models, real HTTP requests, startup/indexes, OpenAPI and
executionStats on 100,000 synthetic logs. Database-free checks can run separately:

```bash
python -B static_helper_tests.py
python -B openapi_tests.py
python -B openapi_contract_tests.py
python -B phase2_unit_tests.py
python -B phase3_schema_tests.py
```

## Design and contract

`employees` stores people identified by `emp_code`; `attendance_logs` stores one
record per employee and attendance date. Unique indexes on `emp_code` and
`(emp_code, date)` prevent duplicate inserts. Conditional updates compare the
record read earlier; stale punch-outs/corrections return 409. Corrections update
fields and append history atomically. Other indexes support listing and reports;
see `DECISIONS.md`.

Instants use strict integer epoch milliseconds in the API and UTC BSON datetimes
in MongoDB, truncated to whole seconds. Calendar dates and shifts are IST strings,
including the overnight-shift rule. Half-up rounding, grace and overtime boundaries
follow `openapi.yaml`. `joined_on` requires a real date in exact YYYY-MM-DD form.

Four MongoDB aggregation reports provide employee monthly attendance, department
summaries including employees without logs, competition-ranked late employees,
and daily department trends including gaps and a seven-day moving average.
Analytics read stored derived metrics. Explain runs executionStats on the shared
query builders and preserves raw BSON values using Extended JSON.

The independent OpenAPI comparator requires zero normalized structural differences,
without field exceptions. Documentation metadata and equivalent schema forms are
normalized; exact OpenAPI document equality and runtime behavior are separate checks.
Only `/health` has a contract-defined 503. Other routes do not translate database
connectivity failures; this remains a limitation rather than adding undocumented
response codes or hiding programming errors.

## Verification and limitations

[Verified merged-main matrix run](https://github.com/Sushantku1099/employee-attendance-analytics-api/actions/runs/38027764363)
passed 66 unittest tests and 38 helper checks per Python 3.11.17 job on MongoDB 6.0.28 and 7.0.43 for commit
`fe58ce949d71eef6365edd57d46118a3b8cd7265`. The current date/schema change passed
67 unittest tests and 38 helper checks locally on Python 3.9.6 with each database version; current PR checks
provide separate Python 3.11 evidence. See `REVIEW.md` for exact commands, package
versions, results and historical failures.

[CI workflow](.github/workflows/verify.yml) runs for PRs targeting `main` and manual
execution. Requirements use lower bounds, not a lock file, and Docker image tags
remain mutable. No clean Python 3.11 lock was generated locally; its installed
interpreter lacks the application dependencies. Fresh CI installations test the
current resolution, not bit-for-bit reproducibility.

Hidden grader data, long-history stress, cold index creation on 100,000 preseeded
logs and all deployment/filter combinations remain unverified. Keep `.env`, secrets,
environments, caches, dumps and Dockerfiles out of the public submission. Preserve
the supplied assignment documents and samples. `REVIEW.md` retains dated history;
earlier limitations there may be superseded by later entries.
