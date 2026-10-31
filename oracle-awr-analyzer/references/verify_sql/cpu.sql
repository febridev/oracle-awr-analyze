-- id: V-04
-- title: Top SQL by CPU in the snapshot range (dba_hist_sqlstat)
-- domain: cpu
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
SELECT *
FROM  (SELECT s.sql_id, s.parsing_schema_name,
              SUM(s.executions_delta)                 AS execs,
              ROUND(SUM(s.cpu_time_delta)/1e6, 1)     AS cpu_s,
              ROUND(SUM(s.elapsed_time_delta)/1e6, 1) AS elapsed_s
       FROM   dba_hist_sqlstat s
       WHERE  s.dbid = &dbid
       AND    s.instance_number = &inst_id
       AND    s.snap_id BETWEEN &begin_snap + 1 AND &end_snap
       GROUP  BY s.sql_id, s.parsing_schema_name
       ORDER  BY cpu_s DESC)
WHERE ROWNUM <= 10;

-- id: V-05
-- title: Host CPU and load per snapshot (dba_hist_osstat deltas for BUSY_TIME, IDLE_TIME, LOAD)
-- domain: cpu
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
WITH o AS (
  SELECT snap_id, stat_name, value,
         value - LAG(value) OVER (PARTITION BY stat_name ORDER BY snap_id) AS delta
  FROM   dba_hist_osstat
  WHERE  dbid = &dbid
  AND    instance_number = &inst_id
  AND    snap_id BETWEEN &begin_snap AND &end_snap
  AND    stat_name IN ('BUSY_TIME', 'IDLE_TIME', 'LOAD')
)
SELECT snap_id,
       ROUND(100 * MAX(CASE WHEN stat_name = 'BUSY_TIME' THEN delta END)
             / NULLIF(MAX(CASE WHEN stat_name = 'BUSY_TIME' THEN delta END)
                    + MAX(CASE WHEN stat_name = 'IDLE_TIME' THEN delta END), 0), 1) AS busy_pct,
       ROUND(MAX(CASE WHEN stat_name = 'LOAD' THEN value END), 2) AS load_avg
FROM   o
WHERE  snap_id > &begin_snap
GROUP  BY snap_id
ORDER  BY snap_id;
