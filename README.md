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
python -B phase2_unit_tests.py
python -B phase3_schema_tests.py
```

Verification status: the local full suite passed on the existing Python 3.9.6
environment with MongoDB 7.0.43. Python 3.11 syntax checks passed, but Python 3.11
runtime tests have **not** run: the inspected 3.11 environment lacks the required
dependencies. This does not establish compliance with the required runtime. See
`REVIEW.md` for exact commands, counts, findings and remaining limits. Raw explain
output retains internal MongoDB plan fields and uses Extended JSON for BSON values.

This is a Git repository on GitHub. Keep `.env`, virtual environments, caches,
credentials and dumps out of commits; `.gitignore` covers these artifacts. Preserve
`PROBLEM_STATEMENT.docx`, `openapi.yaml`, `DATA_MODEL.md`, and the supplied sample
files for evaluation. Do not include a Dockerfile in the submission.

A proposed Python 3.11/MongoDB 7 CI check is below. It is documentation only: no
workflow has been enabled and no CI pass is claimed. Once approved, save it as
`.github/workflows/verify.yml`. The GitHub-hosted Ubuntu runner provides Docker;
installation and image pulling happen only in that fresh CI environment.

```yaml
name: Verify assignment
on:
  pull_request:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: python -m pip install -r requirements.txt
      - run: docker pull mongo:7
      - run: python -B verify_phase3.py
```

This repeats the verification procedure, not a fully locked dependency environment:
requirements use lower bounds and `mongo:7` is a moving image tag. Pin tested package
versions and an image digest if exact build reproducibility is needed.
