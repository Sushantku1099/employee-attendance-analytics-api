# Implementation decisions

1. **Indexes.** Unique emp_code and (emp_code, date) prevent duplicates. (date descending, emp_code ascending) supports listing; date supports report ranges. (emp_code, punch_in descending) finds the latest eligible punch. Joining-date and (department, joining-date) indexes support headcount queries. Reports and explain share index hints. Plans on 100,000 logs showed no collection scans, including lookups.

2. **Races.** Identical punch-ins both attempt insertion; the unique index permits one and the others return 409. Punch-out and corrections match the values and history read earlier. A changed record returns 409. Corrections save fields and append history atomically.

3. **Ties.** MongoDB ranks by late minutes alone. With totals 100, 50, 50, 20, ranks are 1, 2, 2, 4. A limit of 2 returns three rows; employee code orders ties.

4. **Headcount.** Department summaries start from employees who joined by month end, then look up logs. People without logs still count. Trends generate dates and count eligible employees in MongoDB. Decimal arithmetic provides half-up rounding; moving averages use rounded daily rates.

5. **At larger scale.** Measure latency, examined documents and memory before changing indexes. Consider precomputed daily summaries and cursor pagination. Long histories may need a revision counter. Keep raw executionStats available to check changes.
