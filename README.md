# Employee Attendance & Analytics API — Phases 1–3

The assignment requires Python 3.11+ and MongoDB 6.0+ (the grader uses MongoDB 7).
All application code is in `app/main.py`. Implemented endpoints cover employees,
punch-in/out, attendance listing/corrections, all four analytics reports, and real
MongoDB executionStats output.

From the repository root, using a Python 3.11+ environment:

```bash
python -m pip install -r requirements.txt
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=attendance_db
python -m uvicorn app.main:app --port 8000
```

An optional local `.env` may supply MongoDB settings; real environment variables
have priority. Startup creates indexes and `GET /health` checks MongoDB. The
supplied `sample_seed.py` deletes collection contents; it is not needed for tests
and must only be used with an explicitly disposable database.

Run the complete regression suite with an existing environment that has the
requirements installed, Docker running, and the `mongo:7` image already available:

```bash
python -B verify_phase3.py
```

This runs Phase 1/2/3 helper, model, schema and actual HTTP tests, including query
plans on 100,000 synthetic logs. It starts its own MongoDB container and API,
installs nothing, never connects to an existing database, and removes its container
on success or failure. `verify_phase2.py` runs only the earlier phases. For tests,
prefer these isolated runners over manually pointing fixture scripts at a database.

Database-free checks can also run individually:

```bash
python -B static_helper_tests.py
python -B openapi_tests.py
python -B openapi_contract_tests.py
python -B phase2_unit_tests.py
python -B phase3_schema_tests.py
```

## Verification status

The full regression suite passed locally on Python 3.9.6 with MongoDB 7.0.43. It also passed in GitHub Actions on **Python 3.11 and MongoDB 7** on 9 October 2026.

The CI workflow is `.github/workflows/verify.yml`. It runs for pull requests targeting `main` and supports manual execution through GitHub Actions. The successful pull-request run executed `verify_phase3.py`, covering the regression suite, startup and index checks, schema checks, and query-plan verification using 100,000 synthetic attendance logs.

- [CI run](https://github.com/Sushantku1099/employee-attendance-analytics-api/actions/runs/37941164000)
- [CI workflow](https://github.com/Sushantku1099/employee-attendance-analytics-api/blob/main/.github/workflows/verify.yml)

The workflow verifies the current dependency installation against the moving `mongo:7` image tag. Dependencies are not fully locked, so this is not a guarantee of bit-for-bit reproducible builds. MongoDB 6 and the assignment's hidden grading dataset have not been independently verified.

Raw explain output retains internal MongoDB plan fields and uses Extended JSON for BSON values. See `REVIEW.md` for commands, counts and the historical verification record.

This is a Git repository on GitHub. Keep `.env`, virtual environments, caches,
credentials and dumps out of commits; `.gitignore` covers these artifacts. Preserve
`PROBLEM_STATEMENT.docx`, `openapi.yaml`, `DATA_MODEL.md`, and the supplied sample
files for evaluation. Do not include a Dockerfile in the submission.
