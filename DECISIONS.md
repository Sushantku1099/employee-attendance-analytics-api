# Implementation decisions

> Five design choices for the live walkthrough. These describe the implementation in [`app/main.py`](app/main.py); verification evidence is in [`REVIEW.md`](REVIEW.md).

## 1. Indexes follow the queries

Unique `emp_code` and `(emp_code, date)` indexes prevent duplicates. `(date descending, emp_code ascending)` supports attendance listing; `date` supports report ranges. `(emp_code, punch_in descending)` finds the latest eligible punch. Joining-date and `(department, joining-date)` indexes support headcount queries. Reports and explain share index hints. Plans on 100,000 logs showed no collection scans, including lookups.

## 2. MongoDB resolves competing writes

Identical punch-ins both attempt insertion; the unique index permits one and the others return `409`. Punch-out and corrections match the values and history read earlier. A changed record returns `409`. Corrections save fields and append history atomically.

## 3. Ties share a competition rank

MongoDB ranks by late minutes alone. With totals `100, 50, 50, 20`, ranks are `1, 2, 2, 4`. A limit of `2` returns three rows; employee code orders ties.

## 4. Headcount starts with employees

Department summaries start from employees who joined by month end, then look up logs. People without logs still count. Trends generate dates and count eligible employees in MongoDB. Decimal arithmetic provides half-up rounding; moving averages use rounded daily rates.

## 5. Measure before scaling the design

Measure latency, examined documents and memory before changing indexes. Consider precomputed daily summaries and cursor pagination. Long histories may need a revision counter. Keep raw executionStats available to check changes.

---

## Additional implementation notes

| Choice | Reason |
|---|---|
| **MongoDB 6 and 7 verification** | CI runs the full suite on both versions. The runner uses unique database/container names and defaults to MongoDB 7. |
| **Closed-record punch-out selection** | Choose the latest eligible record even when closed. Return `409` instead of accidentally closing an older shift. |
| **Exact dates without a schema exception** | `joined_on` has a date-format schema. Runtime validation parses the calendar date and checks its exact ISO representation. The comparator has no field exceptions. |
| **Contract-defined failures** | Only health declares `503`; other connectivity failures remain a documented limitation rather than introducing new response codes. |
| **Dependency locking deferred** | Requirements remain unlocked until a Python 3.11 lock can be generated and its clean installation verified. No untested pins are presented as reproducible. |

These choices support the assignment's current scale. Hidden-grader behavior, long-history stress and cold indexing on preseeded large data remain unverified.
