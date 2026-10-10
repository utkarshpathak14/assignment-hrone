# DECISIONS.md

1. **Indexes.** `attendance_logs`: unique `{emp_code, date}` (punch-in race, PATCH lookup, monthly, every per-employee
   `$lookup`); `{emp_code, punch_in: -1}` (punch-out finds the latest punch-in); `{date: -1, emp_code}` (default list
   sort, month scans); `{status, date: -1, emp_code}` (status filter). `employees`: unique `emp_code` (duplicate
   creates, joins); `{department, emp_code}` (filtered list); `{joined_on}` and `{department, joined_on}` (headcount).
   I rejected an index on `late_minutes`: the month's date range already narrows the leaderboard scan.

2. **Punch-in race.** Both requests compute the same `(emp_code, date)` and call `insert_one`. There is no read
   first. The unique index lets exactly one insert succeed; the other gets `DuplicateKeyError`, returned as 409.

3. **Ties.** `$rank` gives tied employees the same rank and skips the next one. `limit` filters `rank <= limit`
   after ranking, so everyone tied at the cutoff is returned, ordered by `emp_code`.

4. **Headcount.** The pipeline starts from `employees` joined by month end and attaches logs with `$lookup`.
   Headcount is `$sum: 1` per employee document, logs or not.

5. **At 100x.** Maintain per-day department rollups on write, so analytics read a small summary collection instead
   of joining raw logs.
