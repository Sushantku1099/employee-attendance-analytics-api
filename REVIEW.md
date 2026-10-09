# Phase 1 review — 9 October 2026

Reviewed the current files against PROBLEM_STATEMENT.docx, openapi.yaml and DATA_MODEL.md. No original starter snapshot or Git history exists here, so this report does not invent defects already fixed before this review.

| Where | Defect observed in this pass | Example / verification | Fix |
|---|---|---|---|
| EmployeeIn.joined_on | Arbitrary strings accepted | 2026-02-30 and 20260101 must return 422 | Require YYYY-MM-DD and validate with date.fromisoformat |
| list_attendance.status | Arbitrary status accepted | status=BAD | Use the five contract statuses |
| list_attendance dates | Pydantic date coercion can accept compact dates or timestamps | date_from=20260804 | Validate exact ISO calendar strings before querying |
| list_employees / list_attendance | Empty-string filters ignored | department= and emp_code= | Distinguish None from an explicitly empty filter |
| create_employee | Server instant retained fractional seconds | R1 requires truncated stored/returned instants | Truncate created_at to seconds |
| static_helper_tests.py | Copied helpers could pass while app was broken | Tests did not import app.main | Import production helpers; add serialization and mocked outage checks |
| phase1_tests.py | 2025 epoch values labelled as 2026; weak total checks | Original valid-date expectation could fail on correct app | Compute timestamps from calendar values; use controlled records and exact totals |
| README / submission | Missing .env.example; no ignore rules; existing .venv | Filesystem inspection | Correct setup instructions; add .gitignore |

Readability changes remove decorative dividers, shorten lengthy helper docstrings, remove redundant model configuration, and expand abbreviated shift/filter names. Application code remains in app/main.py.

Already correct: contract email regex (do not replace with stricter EmailStr); decimal half-up rounding; strict integer timestamp bounds; IST overnight cutoff; grace measured from shift start; inclusive 30-minute overtime cutoff; unique-index inserts rather than check-then-insert; database-side pagination; filtered totals; BSON UTC datetimes; response _id exclusion; legacy defaults; nested history conversion. These were retained. No atomic update endpoint exists yet; punch-out and correction concurrency remain future work.

## Actual verification

Existing .venv was reused. No dependencies were installed and no virtual environment was created. MongoDB on localhost:27017 answered ping; Docker daemon was also available. The API used only the separate database candidate_phase1_review_20261009.

Commands executed:

```bash
.venv/bin/python -B static_helper_tests.py
MONGO_DB=candidate_phase1_review_20261009 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m uvicorn app.main:app --port 18000
TEST_BASE_URL=http://localhost:18000 .venv/bin/python -B phase1_tests.py
.venv/bin/python -B - <<'PY'
from pathlib import Path
for name in ('app/main.py', 'sample_seed.py', 'phase1_tests.py', 'static_helper_tests.py'):
    compile(Path(name).read_text(), name, 'exec')
print('Compile: 4 passed, 0 failed')
PY
MONGO_DB=candidate_phase1_review_20261009 .venv/bin/python -B - <<'PY'
import asyncio
from app.main import app, db, lifespan
async def verify():
    async with lifespan(app):
        async with lifespan(app):
            assert db.employees.index_information()['emp_code_1']['unique']
            assert db.attendance_logs.index_information()['emp_code_1_date_1']['unique']
            assert 'date_-1_emp_code_1' in db.attendance_logs.index_information()
            assert 'date_1' in db.attendance_logs.index_information()
    doc = db.attendance_logs.find_one({'emp_code': 'EMP9901'})
    assert doc['punch_in'].utcoffset().total_seconds() == 0
    assert doc['punch_in'].microsecond == 0
    assert db.employees.find_one()['created_at'].utcoffset().total_seconds() == 0
asyncio.run(verify())
PY
```

Results: compile 4/4; production-helper checks 36/36; HTTP integration tests 8/8 (including multiple assertions and subtests); index checks 4/4; storage checks 3/3. Startup completed in the first one-second command window. HTTP tests cover 200, 201, 404, 409 and 422 with bodies, concurrent employee and attendance duplicates, truncation, overnight punches, grace boundaries, pagination, sorting, filtered totals and validation. Overtime is helper-tested only because punch-out is outside Phase 1. Database outage is a mocked direct health-handler check (503 and detail), not a real HTTP outage test.

Initial copied-helper baseline: 30 passed, 0 failed; this was not application verification. Intermediate HTTP run: 7 passed, 1 failed due to concurrency fixture affecting an unfiltered listing. The listing now explicitly filters its date period. A second run had 4 passed, 4 failed because dropping the test database while the server was running removed startup indexes. Restarting against the fresh database restored indexes and all 8 passed. Correct business-rule expectations were retained.

Submission inspection found no .env, Dockerfile, database dump or obvious embedded credential. Existing .venv and .DS_Store are excluded by .gitignore, but still exist locally. No Git repository exists, so tracked-file checks and commit verification are NOT RUN. Do not submit the entire folder as an archive without removing excluded files.

Not verified: real HTTP database outage, 100k-record performance, explain plans, hidden dataset, clean-clone installation, or any Phase 2/3 endpoint. sample_seed.py was read and compiled, not executed, because it deletes existing collection contents. Full assignment remains incomplete.

## Final Phase 1 review — 9 October 2026

Re-read the current source and assignment documents. One additional API defect was reproduced: `PunchInIn(emp_code="EMP0001", punched_at=None)` succeeded. `PunchInRequest.punched_at` references non-nullable `EpochMillis`; only omission defaults to now. `PunchInIn.reject_null_timestamp` now rejects explicitly supplied null with 422 while preserving omission. Both production-model and real HTTP regression checks passed.

Test safety defect: `phase1_tests.py` previously defaulted to localhost:8000 and could write fixtures without explicit authorization. `Phase1Tests.setUpClass` now requires both TEST_BASE_URL and PHASE1_ALLOW_TEST_WRITES=1. Running without them skips before writing. This opt-in does not prove that the target database is disposable. The HTTP suite itself leaves fixtures behind; callers must use and clean a disposable database. README now states this accurately.

This pass kept the previously verified calculations, validation, inserts and serializers. No Phase 2 or Phase 3 work was performed.

Exact verification commands in this final review:

```bash
git status --short
.venv/bin/python -B static_helper_tests.py
.venv/bin/python -B phase1_tests.py
.venv/bin/python -B /tmp/candidate_phase1_final_review.py
.venv/bin/python -B - <<'PY'
from pathlib import Path
for name in ('app/main.py','sample_seed.py','phase1_tests.py','static_helper_tests.py'):
    compile(Path(name).read_text(),name,'exec')
print('Compile: 4 passed, 0 failed')
PY
```

The temporary review harness generated a UUID database name prefixed `candidate_phase1_disposable_`, verified it did not exist, and started `python -B -m uvicorn app.main:app --host 127.0.0.1 --port <allocated-port>` with explicit MONGO_URI=mongodb://localhost:27017 and MONGO_DB=<generated-name>. It ran `python -B phase1_tests.py` with TEST_BASE_URL=http://127.0.0.1:<allocated-port> and PHASE1_ALLOW_TEST_WRITES=1. It then repeated the production lifespan twice; checked all four indexes, BSON UTC storage for employee creation and punch-in, actual HTTP serialization of an inserted legacy record, omitted-timestamp ON_DUTY punch-in, and status-filtered page-two totals. A finally block stopped the server, dropped only its generated database, verified its removal, and closed the temporary log. The harness was outside the submission and removed after use.

Final results:

- Syntax compilation: 4 passed, 0 failed; no bytecode written.
- Actual helper/model checks: 38 passed, 0 failed (previously 36 before adding null/omission regression checks).
- HTTP suite without opt-in: 0 tests run, 1 class skipped, no fixture writes.
- Isolated HTTP suite: 8 passed, 0 failed; explicit null now returns 422.
- Additional readiness/index/storage/HTTP assertions: 11 passed, 0 failed. Readiness: 0.32 seconds.
- Spot checks of dt_to_ms at three timestamp ranges: all 3,000 millisecond values round-tripped, no mismatches; not an exhaustive proof.
- Git status: failed because no repository exists; no repository initialized.

One temporary harness attempt completed all 8 HTTP tests but then failed importing app.main because /tmp was on its import path rather than the project root. Its finally block still removed the database and stopped the server. Adding the working directory to the temporary harness's import path fixed the verification setup; the subsequent complete run passed. No application change was made for this harness issue.

Filesystem listing and a targeted credential-pattern scan found no new submission artifacts or obvious embedded secrets. The existing .venv, .DS_Store and pre-existing caches remain locally and are ignored, not removed. Git tracking remains unverifiable. sample_seed.py was never executed. No existing user collections were deleted.

Limitations at that point: database outages were tested only through a mocked health handler; other routes did not translate outages into explicit API errors. Response schemas were missing and the timestamp schema advertised null (fixed in the schema review below). Large-data performance, hidden fixtures, clean-clone setup, remaining endpoints and analytics/explain indexes were unverified. Half-day endpoint checks await Phase 2.

Files changed during this final review: app/main.py, phase1_tests.py, static_helper_tests.py, README.md, REVIEW.md. DECISIONS.md and assignment source documents were unchanged.

## Narrow generated-OpenAPI review — 9 October 2026

Scope: only the five existing Phase 1 operations. Compared `app.openapi()` and the served `/openapi.json` with the supplied `openapi.yaml`. No punch-out, correction, analytics or explain routes were added.

Concrete schema mismatches and fixes in `app/main.py`:

- `EmployeeIn.joined_on` was a string without `format: date`. Added format metadata while retaining its pattern and real-calendar validator.
- `list_attendance.date_from` and `date_to` lacked `format: date` and advertised null values. Added query format metadata and hid the internal None omission sentinel using Pydantic `SkipJsonSchema`. Parsing remains exact-string validation; compact dates, timestamps, empty dates and impossible dates still return 422.
- `PunchInIn.punched_at` advertised null despite its validator rejecting it. `SkipJsonSchema[None]` now documents a non-null integer while keeping the same omitted-None default and explicit-null rejection. Added `format: int64` to the shared timestamp annotation, retaining its bounds and strict integer validation.
- Optional department, employee-code and status queries advertised null. Their omission sentinels are now excluded from the schema, without changing handler filtering.
- Every success response had an empty schema. Added documentation models for health, employees/pages, attendance/pages and history, including required fields, date formats, nullable values, nested changes and read-only derived fields. These are registered using FastAPI's `responses` metadata, not `response_model`, so existing serializers and HTTP bodies are unchanged.
- Missing 503, 404 and 409 responses are now documented on the applicable routes. Explicit error models document required string `detail`; the 422 model documents required array `detail`, matching the supplied schema.

Focused regressions are in `openapi_tests.py` (standard library unittest plus existing production dependencies). They inspect production schema generation and exercise the actual request models/date parser. `phase1_tests.py` adds a real HTTP omitted-timestamp test and date_to validation cases.

Commands actually executed:

```bash
.venv/bin/python -B openapi_tests.py
.venv/bin/python -B static_helper_tests.py
.venv/bin/python -B /tmp/phase1_schema_compare.py
.venv/bin/python -B /tmp/phase1_schema_http.py
.venv/bin/python -B - <<'PY'
from pathlib import Path
for name in ('app/main.py', 'phase1_tests.py', 'static_helper_tests.py', 'openapi_tests.py'):
    compile(Path(name).read_text(), name, 'exec')
print('Compile: 4 passed, 0 failed')
PY
git status --short
```

Final results: schema/model regression tests **7 passed, 0 failed**; existing helper checks **38 passed, 0 failed**; HTTP integration tests **9 passed, 0 failed**; additional HTTP/schema checks **13 passed, 0 failed**; compile checks **4 passed, 0 failed**. The first isolated HTTP run, before the permanent omitted-timestamp test was added, passed 8 tests plus the same 13 focused checks. Final rerun passed all 9 plus 13.

The temporary HTTP harness created a fresh UUID-named `candidate_schema_disposable_...` database after checking that it did not exist. It launched uvicorn on an allocated local port with explicit MONGO_URI and MONGO_DB, and ran `python -B phase1_tests.py` with TEST_BASE_URL and PHASE1_ALLOW_TEST_WRITES=1. Additional HTTP checks compared served `/openapi.json` with `app.openapi()`, tested both date parameters, and verified omitted timestamps versus explicit null. The harness stopped its server and dropped only its own disposable database in finally. Temporary logs were closed; no sample_seed execution, user database deletion, schema export, virtual environment or submission artifact was created. Both temporary harness files were removed after verification.

The comparison harness used the already available PyYAML to read the supplied YAML; no dependency was installed or added. It compared **25** Phase 1 request-body, query-parameter, response-code and response-schema groups. After resolving references, ignoring titles/descriptions/examples, sorting enum/required lists and normalizing equivalent allOf, const and null representations, **24 matched and 1 differed**: the generated joined_on request schema retains `pattern: ^\d{4}-\d{2}-\d{2}$` alongside format: date. This pattern explicitly describes the existing YYYY-MM-DD runtime requirement and was retained. This normalized comparison is not a claim of whole-document equality.

Remaining document differences: generated OpenAPI **3.1.0** versus supplied **3.0.3**; different component names, operation IDs, summaries/descriptions, tags and reference structure; const/enum and nullable/anyOf representation; the extra joined_on pattern; all unimplemented Phase 2/3 operations remain absent. No exact document match is claimed. A pre-existing runtime limitation also remains: manually raised date/query HTTPExceptions use string detail for some 422 responses, whereas the supplied 422 schema describes an array. That schema-only pass preserved those runtime error bodies; the readability review below fixes them. Documentation models do not enforce response validation.

During development, an initial import failed because this existing environment did not support a union operator between the Annotated types; using typing.Union fixed the schema annotation without changing behavior. The comparison harness initially mishandled boolean requestBody.required; its normalization was corrected before the reported comparison. These were setup/development failures, not passing tests.

Git status still reports no repository; none was initialized. Files changed in this pass: `app/main.py`, `phase1_tests.py`, new `openapi_tests.py`, and `REVIEW.md`. No business-rule calculation or database write logic changed. Phase 2 remains untouched.

## Readability review — 9 October 2026

Simplified comments and docstrings in `app/main.py`, removed its decorative TODO block (remaining work is still listed in README), and removed banners and repeated explanations from `static_helper_tests.py`. Kept explanations of timezone handling, overnight shifts, grace, rounding, overtime, duplicate protection and older documents. Shortened repeated wording in this review and DECISIONS without removing decisions or test history. README needed no changes.

One verified defect was fixed: `parse_query_date` and `list_attendance` raised 422 with string detail for invalid dates or reversed ranges. The supplied `components.responses.ValidationError` requires an array of objects. The smallest fix keeps the validation rules, status and message, but wraps the message in an error object with `loc`, `msg` and `type`. The parser accepts a parameter name so date_from/date_to errors identify the right query field. No other application logic changed.

`OpenAPITests.test_date_error_bodies` failed against the old string body and passed after the fix. `Phase1Tests.test_query_validation` now checks actual HTTP error arrays and object fields. An initial version of the unit regression incorrectly assumed the new parser signature already existed; it errored, was corrected to call the endpoint function, and then reproduced the actual contract failure. These development failures are not counted as passing tests.

Commands and final results:

```bash
.venv/bin/python -B -m unittest openapi_tests.OpenAPITests.test_date_error_bodies
.venv/bin/python -B openapi_tests.py
.venv/bin/python -B static_helper_tests.py
.venv/bin/python -B /tmp/candidate_readability_checks.py
.venv/bin/python -B phase1_tests.py
.venv/bin/python -B - <<'PY'
from pathlib import Path
from app.main import app
for name in ('app/main.py','sample_seed.py','phase1_tests.py','static_helper_tests.py','openapi_tests.py'):
    compile(Path(name).read_text(),name,'exec')
print('Existing environment compile: 5 passed, 0 failed; app import successful')
PY
python3.11 -B - <<'PY'
from pathlib import Path
for name in ('app/main.py','sample_seed.py','phase1_tests.py','static_helper_tests.py','openapi_tests.py'):
    compile(Path(name).read_text(),name,'exec')
print('Python 3.11 compile: 5 passed, 0 failed')
PY
git status --short
```

- Focused regression before the fix: 1 failed; final focused rerun: 1 passed, 0 failed. Also included in the 8 passing schema/model tests.
- Schema/model suite: 8 passed, 0 failed. Production-helper checks: 38 passed, 0 failed.
- Isolated HTTP suite: 9 passed, 0 failed. Additional HTTP/schema/index/storage assertions: 12 passed, 0 failed.
- HTTP suite without explicit test URL/write opt-in: 0 tests run, 1 class skipped; no writes.
- Compile: 5 passed on existing Python 3.9.6 and 5 passed on Python 3.11.16. Existing-environment application import succeeded. Python 3.11 runtime import failed because FastAPI is absent; Python 3.11 runtime tests are NOT RUN. No dependencies or environments were installed.
- Normalized comparison with supplied YAML: 24 of 25 Phase 1 groups match. The only normalized difference is the existing joined_on pattern. The comparison resolves references and normalizes equivalent schema forms, and ignores descriptive metadata. OpenAPI 3.1.0 versus 3.0.3, names, descriptions, operation IDs, tags and missing later-phase routes still differ. No exact document match is claimed. The manual 422 error-body mismatch is now fixed.
- Git status: no repository exists. No repository was initialized.

The temporary harness started `.venv/bin/uvicorn app.main:app --port 57672` with localhost MongoDB and a new UUID database named `candidate_readability_...`, verified absent before use. Port 8000 was unavailable, so an allocated port was used without touching the existing listener. The harness ran the HTTP suite with TEST_BASE_URL and PHASE1_ALLOW_TEST_WRITES=1; checked exact date-error responses, served OpenAPI, indexes, UTC whole-second storage and a legacy response. Readiness took 0.31 seconds. It stopped its server and removed only its disposable database. Its first attempt used a database name exceeding MongoDB's 63-character limit, so startup failed before HTTP tests; shortening the name fixed that harness issue. MongoDB did not create the invalidly named database.

With no Git history, saved copies outside the project were used to inspect the unified diff. An AST comparison ignoring docstrings confirmed helper-test logic was unchanged and all application code outside the two date-error functions was unchanged. Temporary copies, harness and logs were removed after review. No sample seed, user database mutation, bytecode or submission artifact was produced. Existing ignored environment/cache files remain locally.

Still unverified: real HTTP database outage, clean-clone installation on Python 3.11+, large-data performance, hidden fixtures and all later endpoints. Phase 2/3 were not started. Files changed: app/main.py, static_helper_tests.py, openapi_tests.py, phase1_tests.py, REVIEW.md and DECISIONS.md.

## Phase 2 implementation — 9 October 2026

Implemented only `POST /attendance/punch-out` and `PATCH /attendance/{emp_code}/{date}`. The assignment describes Parts A–C, not numbered phases, and does not assign monthly analytics to Phase 2. The agreed project plan keeps all analytics and explain in Phase 3; none was implemented here.

`PunchOutIn` and `RegularizeIn` use the existing strict epoch-millisecond type. Omitted punch-out timestamps use the server clock; explicit null is invalid. Correction fields are optional but cannot be null when supplied. Required reason/by length limits match the supplied schema, and read-only/unknown fields are ignored.

`punch_out` finds the most recent record with punch_in <= the truncated requested time. It includes closed records so a duplicate returns 409 rather than closing an older open day. No eligible record returns 404, including a time before all punch-ins; an equal timestamp on an eligible open record returns 422. Durations must be positive and at most 24 hours. Existing helpers calculate half-up work hours and overtime; half-day uses the rounded hours. Regular punch-out preserves late minutes and history. A new (emp_code ascending, punch_in descending) index supports the lookup.

`regularize_attendance` validates the exact calendar path date, merges only supplied fields and checks the final record. ABSENT/LEAVE clear times and derived fields and reject supplied punch times. Presence needs a punch-in on the addressed attendance date, including the overnight rule. A punch-out must be later and within 24 hours. All derived values are recalculated for corrections. No-op detection compares final values after recalculation: inconsistent stored derived values can be repaired, while a truly unchanged request returns 422. History contains only actual changes, with BSON UTC punch times in storage and milliseconds in responses. Missing legacy fields use the documented defaults.

`update_attendance` matches the natural key plus the fields/history read earlier. It distinguishes missing fields from null. If another request changed the record, it returns 409. A correction's `$set` and `$push` happen in one MongoDB update, so fields and history are saved together. No extra version field, transaction or ObjectId identity was introduced. Identical corrections may return 422 when they arrive after the first correction and are then no-ops; overlapping stale updates return 409.

Added `phase2_unit_tests.py`, `phase2_tests.py` and `verify_phase2.py`. The runner requires Docker and an already available mongo:7 image; it creates its own `--rm` MongoDB container on a localhost-only allocated port, starts the API on another allocated port and runs both phases' suites. Test fixture updates for legacy/overnight cases happen only in that new container. It stops its server and test container in finally. No sample seed was executed and no existing user database was modified or deleted. No dependencies or virtual environments were installed.

Exact main commands executed:

```bash
.venv/bin/python -B openapi_tests.py
.venv/bin/python -B static_helper_tests.py
.venv/bin/python -B phase2_unit_tests.py
.venv/bin/python -B /tmp/candidate_phase2_verify.py
.venv/bin/python -B verify_phase2.py
.venv/bin/python -B phase1_tests.py
.venv/bin/python -B phase2_tests.py
.venv/bin/python -B - <<'PY'
from pathlib import Path
for filename in ('app/main.py','sample_seed.py','static_helper_tests.py','openapi_tests.py','phase1_tests.py','phase2_unit_tests.py','phase2_tests.py','verify_phase2.py'):
    compile(Path(filename).read_text(), filename, 'exec')
print('Existing runtime compile: 8 passed, 0 failed')
PY
python3.11 -B - <<'PY'
from pathlib import Path
for filename in ('app/main.py','sample_seed.py','static_helper_tests.py','openapi_tests.py','phase1_tests.py','phase2_unit_tests.py','phase2_tests.py','verify_phase2.py'):
    compile(Path(filename).read_text(), filename, 'exec')
print('Python 3.11 compile: 8 passed, 0 failed')
PY
python3.11 -B -c 'import fastapi'
git status --short
docker ps -a --filter name=candidate-phase2- --format '{{.Names}}'
```

The permanent runner executes the following exact child test commands with TEST_BASE_URL and both write opt-ins pointing to its own isolated API. TEST_MONGO_URI and TEST_MONGO_DB point to that same container/database:

```bash
.venv/bin/python -B openapi_tests.py
.venv/bin/python -B static_helper_tests.py
.venv/bin/python -B phase2_unit_tests.py
.venv/bin/python -B phase1_tests.py
.venv/bin/python -B phase2_tests.py
```

Final executed results on MongoDB **7.0.43**, using the existing Python **3.9.6** environment:

| Check | Actual result |
|---|---|
| Existing schema/model suite | 8 passed, 0 failed |
| Existing production-helper checks | 38 passed, 0 failed |
| New Phase 2 unit/model/schema suite | 7 passed, 0 failed |
| Unchanged Phase 1 HTTP suite | 9 passed, 0 failed |
| Phase 2 HTTP suite | 21 passed, 0 failed |
| Startup/index assertions | 5 passed; repeated production lifespan succeeded |
| Served OpenAPI equals generated schema | 1 passed |
| Representative punch-out query plan on small fixtures | IXSCAN present, no COLLSCAN |
| In-memory syntax compilation | 8 passed on each of Python 3.9 and Python 3.11 |
| Direct HTTP-suite invocation without write opt-ins | 0 tests run, 1 class skipped in each suite; no writes |
| Python 3.11 runtime dependency check | Failed: FastAPI is not installed; Python 3.11 runtime tests NOT RUN |
| Git status | No Git repository exists; none initialized |
| Test container cleanup check | No candidate-phase2 containers remained |

The final API startup took **0.42 seconds**. HTTP tests cover success responses, 404/409/422 errors, timestamp validation/truncation, omitted server time, normal/overnight dates, latest eligible/closed-record selection, zero and 24-hour duration boundaries, 29:59 versus 30:00 overtime, and 4.49 versus 4.50 rounded half-day boundaries. Correction tests cover all statuses, clearing/reinstating presence, recalculation, ignored derived input, no-ops, history preservation and BSON/nested timestamp types. Six simultaneous punch-outs produced one 200 and five 409s. Concurrent differing and identical corrections, plus a correction racing punch-out, preserved successful writes/history. Deterministic unit tests inspect the conditional update filter, missing/null handling and 409 on a stale update.

Intermediate runs were also real passes: the first temporary-container run passed 9 Phase 1 and 17 Phase 2 tests; added edge cases increased Phase 2 to 20, then 21. The permanent runner's final run passed every suite above. No expected business-rule result was changed to make a failing test pass.

A read-only comparison using the already available PyYAML inspected all seven implemented operations against supplied openapi.yaml. After reference expansion, sorting required/enum lists, normalizing nullable/anyOf, const/enum and single allOf wrappers, and ignoring descriptive metadata, **38 of 39 groups matched**; both new endpoints matched **14 of 14 groups**. The existing extra joined_on pattern is the remaining normalized difference. Generated OpenAPI 3.1 versus supplied 3.0.3, component names, descriptions, tags and operation IDs still differ; no exact whole-document match is claimed. PyYAML was used only for that inspection and was not added as a production dependency.

Actual unified diffs were inspected using pre-edit copies outside the project. An AST comparison confirmed every pre-existing function and model was unchanged except lifespan's added index; the three Phase 1 test files were byte-for-byte unchanged. README now lists Phase 2 and reproducible safe testing, and DECISIONS records the new index and conditional atomic writes within its five required answers. A targeted credential-pattern scan found no obvious embedded secrets. Existing ignored .venv, .DS_Store and caches remain locally; no new generated submission artifact or Dockerfile was created. Temporary snapshots and the prototype harness were removed after review; the useful permanent verification runner remains.

Remaining limits: no 100,000-record load test, long-history stress test, real HTTP outage test, hidden dataset, clean-clone installation or Python 3.11 runtime test. Conditional updates compare the full history and may need a revision approach at larger scale. MongoDB outages on normal routes are not specially translated, consistent with the existing Phase 1 limitation. All analytics (including employee monthly) and explain remain unimplemented. Changed files: app/main.py, README.md, DECISIONS.md, REVIEW.md, new phase2_unit_tests.py, phase2_tests.py and verify_phase2.py.

## Phase 3 — analytics and explain (2026-10-09)

Read PROBLEM_STATEMENT.docx, openapi.yaml, DATA_MODEL.md, the current application,
README/DECISIONS/REVIEW and existing tests before implementation. No Git repository
exists (`git status --short` returned exit 128); none was initialized. A temporary
copy outside the project was used for the diff review.

### Implementation

- `employee_monthly_pipeline`: employee-first lookup; MongoDB counts weekdays from
  the joining date and aggregates stored values. Weekend presence is excluded from
  present_days, but weekend late/overtime and leave records remain in their totals.
  Unknown employee is 404; zero working days gives null attendance_pct.
- `department_summary_pipeline`: start with employees joined by month end, so people
  without logs count. Sum qualifying work hours and record counts separately for a
  weighted record average. Departments without eligible employees are omitted.
- `late_leaderboard_pipeline`: group month logs, discard orphan employee references,
  filter department, use `$rank` on total late minutes alone, apply rank cutoff,
  then sort ties by employee code. Tested ranks 1,2,2,4 and 1,000 tied rank-1 rows.
- `department_trend_pipeline`: `$range`/`$dateAdd` generate calendar rows in MongoDB,
  indexed lookups count eligible employees and daily records. Weekends remain in
  the response with null rates. The seven-row window averages already-rounded,
  non-null rates inside the requested range. Inclusive ranges over 92 days are 422.
- `mongo_half_up`: decimal arithmetic implements half-up because MongoDB `$round`
  uses ties-to-even. Tests cover 8.125 -> 8.13, .03125 -> .0313, and a moving-average
  midpoint. No punch-derived values are recalculated for analytics.
- Reports and explain share `analytics_operation`; attendance listing and explain
  share `attendance_find_operation`, including filters, sort, pagination and hints.
  Explain runs MongoDB's real executionStats command and returns the full envelope.
  BSON values use Extended JSON; raw plans retain internal MongoDB fields, including
  `_id` references where MongoDB emits them. Ordinary API records still omit `_id`.
- Added employees.joined_on and employees.(department, joined_on) indexes for the
  actual headcount queries. Existing log indexes cover month/date/employee lookups.
  Hints are used by the real operations as well as explain, never explain alone.
- Removed only the obsolete Phase 2 schema assertion that analytics paths must be
  absent. All existing Phase 1/2 business functions and validation models remain
  unchanged. AST comparison found changes only to existing lifespan and
  list_attendance definitions; no prior definitions were removed.
- New schema test caught a missing generated pattern for explain's optional month.
  Added the explicit schema annotation while preserving runtime validation.

### Commands and actual results

Final full run:

```bash
.venv/bin/python -B verify_phase3.py > /tmp/candidate-phase3-final.log 2>&1
```

The runner starts an already-installed mongo:7 image with a unique container name,
`--rm` and an ephemeral localhost port; starts `python -B -m uvicorn app.main:app
--port <allocated-port>` with explicit MONGO_URI/MONGO_DB; then executes these exact
script commands using the same interpreter and isolated database settings:

| Command | Actual result |
|---|---|
| `.venv/bin/python -B openapi_tests.py` | 8 passed |
| `.venv/bin/python -B phase3_schema_tests.py` | 2 passed |
| `.venv/bin/python -B static_helper_tests.py` | 38 checks passed |
| `.venv/bin/python -B phase2_unit_tests.py` | 7 passed |
| `.venv/bin/python -B phase1_tests.py` | 9 HTTP tests passed |
| `.venv/bin/python -B phase2_tests.py` | 21 HTTP tests passed |
| `.venv/bin/python -B phase3_tests.py` | 13 HTTP/plan tests passed |

Total: 98 suite tests/checks passed, zero failed or skipped in the final run.
MongoDB was 7.0.43; API health became ready in 0.43 seconds on the initially empty
instance. Two truly empty-database HTTP checks passed before fixtures were inserted.
Seven index assertions passed, including both unique constraints; repeated lifespan
startup passed after the large fixture existed. Served OpenAPI equalled app.openapi().
The representative Phase 2 punch-out plan also retained IXSCAN without COLLSCAN.

Phase 3 tests inspected seven small-fixture and seven 100,000-log executionStats
operations through real HTTP explain requests. Root plans had IXSCAN and no COLLSCAN;
lookup execution statistics reported zero collection scans. Small-fixture commands
were compared with the shared production builders, including pagination and pipeline
contents. Large-fixture responses also checked counts, record-weighted averages,
all rank-1 ties, trends, and unchanged document counts after report/explain requests.
These are concrete fixture checks, not a guarantee for every future data distribution.

Development-run failures were test issues: the first run's Phase 3 setup used a code
outside the existing EMP pattern (zero tests ran, one setup error); the next run had
10/11 Phase 3 tests pass with one incorrect expected average. The original 7.58 was
correct: (8.12+3.51+9.12+8.13+9)/5 = 7.576. It was restored after recalculation.
A standalone Phase 3 schema run initially had one error across two tests because
explain's optional month pattern was absent; both passed after the annotation fix.
The initial temporary comparison script also required fixes to its normalization;
only its corrected results below are counted.

Syntax checks executed with both `.venv/bin/python -B -` (Python 3.9.6) and
`python3.11 -B -` (Python 3.11.16), each using this script:

```python
from pathlib import Path
files = [Path('app/main.py'), *Path('.').glob('*tests.py'),
         Path('verify_phase2.py'), Path('verify_phase3.py')]
for path in files:
    compile(path.read_text(), str(path), 'exec')
```

Both compiled all 10 files without writing caches. Import and real API startup were
verified with the existing .venv interpreter; dependencies were not installed.

Read-only supplied/generated OpenAPI comparison executed:

```bash
PYTHONPATH=. .venv/bin/python -B /tmp/compare_phase3_openapi.py
```

Using existing PyYAML only for this temporary inspection, it expanded references,
normalized equivalent nullable/const/allOf representations, sorted required/enum
lists, and ignored titles/descriptions/examples. It compared parameters, status-code
sets, request schemas and each response schema for all 12 operations: **60/61 groups
matched**. All Phase 3 groups matched. The sole remaining structural difference is
Phase 1's extra joined_on request regex alongside format:date. OpenAPI versions
(3.0.3 supplied, 3.1.0 generated), component names and descriptive metadata differ;
this is not an exact document match. The persistent Phase 3 schema tests use only
installed application dependencies and the standard library, not PyYAML.

### Safety, diff and limitations

All fixture writes went to this run's new MongoDB container. No sample_seed.py,
existing collection deletion, dependency installation or new virtual environment
was used. The runner stopped its API and removed its own container on success and
failure. `docker ps --filter name=candidate-phase3 --format '{{.Names}}'` then showed
no remaining Phase 3 container. Logs, diff snapshots and the comparison script were
outside the submission folder. Existing .venv, caches and .DS_Store are excluded by
.gitignore; without Git, tracked/submitted-file exclusion cannot be verified. No new
secrets, dumps or generated schema files were added.

Reviewed the actual unified diff against the external snapshot and inspected all
three new files. Changed files: app/main.py, phase2_unit_tests.py, README.md,
DECISIONS.md, REVIEW.md; added phase3_tests.py, phase3_schema_tests.py,
verify_phase3.py. requirements.txt and the supplied contract files were unchanged.

Remaining verification limits: runtime tests used Python 3.9.6, not the assignment's
Python 3.11+ (3.11 syntax passed; its application dependencies are unavailable).
MongoDB 6 and the hidden grader data were not tested. Startup timing was measured
on an empty database; repeated startup on the large fixture reused existing indexes,
so cold index creation on 100,000 preseeded logs is unverified. Plan/latency checks are
fixture-specific, not a full benchmark. No public Git repository/submission was
created or verified. No claim is made that all submission requirements are complete.

## Pre-merge repository review (2026-10-09)

Reviewed `Sushantku1099/employee-attendance-analytics-api` on the existing development
branch. After `git fetch origin`, development and origin/development had zero
commits of divergence. The starting working tree and staging area were clean.
The GitHub repository is public, its default branch is main, and there was no open
development-to-main PR. Earlier statements in this review about Git being absent
record the state at those earlier reviews; they are no longer the current state.

Changes are limited to README.md, .gitignore and this review. README now states that
this is a Git repository, uses one interpreter for pip/uvicorn, recommends the safe
full disposable runner, includes the Phase 3 database-free schema check, and clearly
distinguishes Python 3.11 syntax checks from unexecuted runtime tests. Its proposed
Python 3.11/MongoDB 7 workflow is documentation only; it was not enabled or executed.
Ignore rules now also cover common lint/type/test environment caches, BSON/archive
dumps and private-key/credential file formats. No application rewrite was justified.

### Repository and author checks

Executed `git status --short --branch`, `git diff --cached --stat`, `git ls-files`,
`git ls-tree -r --name-only HEAD`, `git status --ignored --short`, and `git diff
origin/main...development --stat`. There are 22 tracked files, including all three
assignment contract files and the supplied sample data/script. No prohibited artifact
is tracked or was initially staged. Existing .venv and .DS_Store remain ignored.
A targeted scan of all tracked file contents found no GitHub-token, AWS-access-key,
private-key-header or credential-bearing MongoDB URI patterns; this is a targeted
check, not a comprehensive secret-scanner guarantee. A `git check-ignore --stdin`
check passed for 20 representative prohibited paths. Required evaluation files
remain tracked and unchanged. The PR includes the branch's existing assignment,
test and verification files; the application code was already identical in main
and development before this review.

`git config --show-origin --get user.name` and the corresponding user.email query
reported global configuration `sushant-ji <sushant@pods365.com>`. The starting latest
commit was 9e0d36bc4cfbe5c3756b121b1bc1238883609157. GitHub's commit API associated
both its author and committer with `sushant-ji`, although the active GitHub CLI
account is Sushantku1099. No identity configuration or existing commit was changed.
No credential/token values were included in the review.

### Actual contract review

Reread PROBLEM_STATEMENT.docx, openapi.yaml, DATA_MODEL.md, current app/main.py and
the tests. Examined every endpoint's path, parameters, response fields/statuses,
strict timestamp validation, truncation/storage, IST/overnight rules, rounding and
conditional updates, not just the test summaries.

- `punch_out`: the specific contract selects the latest punch at or before the
  requested instant, including closed records. Before the earliest punch there is
  no eligible record (404); exactly at the punch duration validation gives 422.
  This explains the apparent conflict with the broader "at or before" error prose;
  the existing selection and tests follow the specific query definition.
- `update_attendance`/`regularize_attendance`: matching the old values/history and
  atomically appending history prevents lost corrections; legacy missing fields
  are distinguished from null in the conditional update.
- `department_summary_pipeline`: eligible employees are the input, not logs;
  no-log people count, future joiners do not, and hours/counts preserve record
  weighting. `employee_monthly_pipeline` counts weekdays from the joining date.
- `late_leaderboard_pipeline`: department filtering precedes `$rank`, ranking uses
  only total minutes, and the cutoff precedes deterministic display sorting.
- `department_trend_pipeline`: MongoDB generates every calendar date, counts daily
  joining-date headcount, and windows the rounded rates within the requested range.
- `explain_endpoint`: real executionStats uses the same query/pipeline and hints.
  The general no-_id convention and raw-explain requirement overlap: raw explain
  may contain internal MongoDB field references. It remains unsimplified as the
  explain-specific contract requires; ordinary API documents still exclude _id.

No new business-rule defect was established in this review. The existing extra
joined_on request regex is still a generated-schema difference, not a demonstrated
runtime rejection of a valid YYYY-MM-DD calendar date. Re-executed the temporary
read-only comparison with `PYTHONPATH=. .venv/bin/python -B
/tmp/compare_phase3_openapi.py`: 60/61 normalized groups matched, including all
Phase 3 groups. OpenAPI version/descriptive metadata also differ; no exact document
match is claimed.

### Executed verification and runtime availability

```bash
.venv/bin/python -B verify_phase3.py > /tmp/candidate-premerge-verification.log 2>&1
```

Exit 0. The runner executed `.venv/bin/python -B <script>` for each script below
with its own disposable MongoDB settings:

| Script | Actual result |
|---|---|
| openapi_tests.py | 8 passed |
| phase3_schema_tests.py | 2 passed |
| static_helper_tests.py | 38 checks passed |
| phase2_unit_tests.py | 7 passed |
| phase1_tests.py | 9 passed |
| phase2_tests.py | 21 passed |
| phase3_tests.py | 13 passed |

98 suite tests/checks passed, zero failures or skips. MongoDB 7.0.43; API ready in
0.43 seconds. Two empty-database HTTP checks, seven index assertions, repeated
lifespan and served-schema equality checks passed. Seven large-fixture explain plans
on 100,000 synthetic logs had IXSCAN and no root or lookup collection scans. The
small-fixture explain checks and representative punch-out plan also passed. The
runner terminated its API and removed its own container; no user database was
accessed and sample_seed.py was not run.

Compilation via `.venv/bin/python -B -` and `python3.11 -B -` each passed all ten
application/test/runner files using the compile script documented in the Phase 3
section; application import passed under .venv. `git diff --check` passed.
Existing runtime versions: Python 3.9.6, FastAPI 0.128.8, Uvicorn 0.39.0, PyMongo
4.18.3, Pydantic 2.13.5, python-dotenv 1.2.1.

Checked `python3.11 -B -c 'import fastapi, uvicorn, pymongo, pydantic, dotenv'`:
failed with ModuleNotFoundError for fastapi. The UV-managed Python 3.11.16 path
likewise lacks all five packages. The bundled desktop runtime is Python 3.12.14,
also without all application requirements. No suitable existing Python 3.11
environment was found in the inspected locations. No dependencies were installed
and no environment was created. Proposed CI in README uses setup-python 3.11,
requirements.txt, docker pull mongo:7 and the same complete runner on a fresh
GitHub-hosted Ubuntu machine. CI execution remains pending approval; dependency
versions/image tags are not locked.

Remaining risks: Python 3.11 runtime and clean-install verification are still
pending; MongoDB 6, hidden grader data, long-history stress and cold index creation
on preseeded 100,000-log data are unverified. Existing normal-route database-outage
handling is unchanged. Passing these fixtures does not prove every contract case.


## Python 3.11 CI verification confirmed (2026-10-09)

The earlier Python 3.11 runtime limitation was resolved by GitHub Actions, not by
installing dependencies locally. Verified the successful pull-request run:
[37941164000](https://github.com/Sushantku1099/employee-attendance-analytics-api/actions/runs/37941164000).
It tested head commit 1855e7471309de1cd8dc2db12f2a9a317a319ea3 using Python 3.11.17
and MongoDB 7.0.43. The current workflow is present on main at
[.github/workflows/verify.yml](https://github.com/Sushantku1099/employee-attendance-analytics-api/blob/main/.github/workflows/verify.yml),
with pull_request targeting main and workflow_dispatch triggers.

Read-only verification commands executed:

```bash
gh run view 37941164000 --repo Sushantku1099/employee-attendance-analytics-api --json conclusion,status,headSha,event,createdAt,workflowName,jobs,url
gh run view 37941164000 --repo Sushantku1099/employee-attendance-analytics-api --log > /tmp/candidate-python311-ci.log
gh api 'repos/Sushantku1099/employee-attendance-analytics-api/contents/.github/workflows/verify.yml?ref=main' --jq .content | base64 --decode
```

Run conclusion: success. The recorded command `python -B verify_phase3.py` passed
98 suite tests/checks (8 existing schema, 2 Phase 3 schema, 38 helpers, 7 Phase 2
unit, 9 Phase 1 HTTP, 21 Phase 2 HTTP, 13 Phase 3 HTTP/plan tests). API readiness
was 0.41 seconds. Startup/index, served-schema and query-plan checks passed,
including seven indexed plans on 100,000 synthetic logs with no root or lookup
collection scans. The runner removed its disposable container. These results were
verified from the existing CI log; tests were not rerun during this documentation
update. README now reflects completed CI rather than a proposed workflow.

The earlier local Python 3.9.6/MongoDB 7.0.43 results remain historical facts.
Requirements are not fully locked and mongo:7 is a moving tag. MongoDB 6, hidden
grader data, long-history stress and cold index creation on the preseeded large
dataset remain unverified. No application, test or workflow code changed in this
update; no dependencies were installed and no branch, commit or remote was changed.
