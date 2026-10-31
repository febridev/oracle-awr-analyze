-- id: V-01
-- title: Actual parsing schema for one SQL ID
-- domain: sql_classification
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap, sql_id
SELECT s.sql_id, s.parsing_schema_name, s.module, s.action,
       SUM(s.executions_delta)                 AS execs,
       ROUND(SUM(s.elapsed_time_delta)/1e6, 1) AS elapsed_s,
       ROUND(SUM(s.cpu_time_delta)/1e6, 1)     AS cpu_s
FROM   dba_hist_sqlstat s
WHERE  s.dbid = &dbid
AND    s.instance_number = &inst_id
AND    s.snap_id BETWEEN &begin_snap + 1 AND &end_snap
AND    s.sql_id = '&sql_id'
GROUP  BY s.sql_id, s.parsing_schema_name, s.module, s.action;

-- id: V-02
-- domain: sql_classification
-- min_version: 12.1
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
-- title: Load per parsing schema (to confirm the user vs monitoring classification)
SELECT s.parsing_schema_name,
       ROUND(SUM(s.elapsed_time_delta)/1e6) AS elapsed_s,
       ROUND(SUM(s.cpu_time_delta)/1e6)     AS cpu_s,
       SUM(s.executions_delta)              AS execs
FROM   dba_hist_sqlstat s
WHERE  s.dbid = &dbid AND s.instance_number = &inst_id
AND    s.snap_id BETWEEN &begin_snap + 1 AND &end_snap
GROUP  BY s.parsing_schema_name
ORDER  BY elapsed_s DESC
FETCH FIRST 10 ROWS ONLY;

-- id: V-17
-- title: Execution plan from AWR (DBMS_XPLAN.DISPLAY_AWR)
-- domain: sql_classification
-- min_version: 11.2
-- license: diagnostics_pack
-- params: sql_id
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_AWR('&sql_id'));
