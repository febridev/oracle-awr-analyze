# AWR diff: function signature reference

Source of truth for `scripts/awr_diff_sql.py`. Checked against Oracle documentation on 2026-11-01
(Oracle Database PL/SQL Packages and Types Reference, package `DBMS_WORKLOAD_REPOSITORY`).

## `AWR_DIFF_REPORT_HTML`

Identical in every version checked:

```
DBMS_WORKLOAD_REPOSITORY.AWR_DIFF_REPORT_HTML(
   dbid1     IN NUMBER,
   inst_num1 IN NUMBER,
   bid1      IN NUMBER,
   eid1      IN NUMBER,
   dbid2     IN NUMBER,
   inst_num2 IN NUMBER,
   bid2      IN NUMBER,
   eid2      IN NUMBER)
  RETURN awrdrpt_text_type_table PIPELINED;
```

Order: 1st period (dbid, instance, begin snap, end snap), then 2nd period. No defaults. Output is one
`VARCHAR2(500)` column per row. The docs do not name that column, so the script uses `SELECT *`.
Oracle recommends the interactive `@?/rdbms/admin/awrddrpt.sql` over calling the function directly.

| Version | Signature | URL |
|---|---|---|
| 12.1 | same as above | https://docs.oracle.com/database/121/ARPLS/d_workload_repos.htm |
| 12.2 | same | https://docs.oracle.com/en/database/oracle/oracle-database/12.2/arpls/DBMS_WORKLOAD_REPOSITORY.html |
| 19c | same | https://docs.oracle.com/en/database/oracle/oracle-database/19/arpls/DBMS_WORKLOAD_REPOSITORY.html |
| 21c | same | https://docs.oracle.com/en/database/oracle/oracle-database/21/arpls/DBMS_WORKLOAD_REPOSITORY.html |
| 23ai | same | https://docs.oracle.com/en/database/oracle/oracle-database/23/arpls/DBMS_WORKLOAD_REPOSITORY.html |

The docs list the return type as `awrdrpt_text_type_table` (a text type) even though the function name says HTML.

## How the script uses it

Period 1 is the baseline (`&base_begin_snap`, `&base_end_snap`), period 2 is the current run
(literal values from `awr.json` meta). Both periods use the same dbid and instance number.

## Deferred

RAC / global variant (`AWR_GLOBAL_DIFF_REPORT_HTML`) is out of scope for v1.
