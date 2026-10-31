-- id: V-11
-- title: Parse and hard-parse counts per snapshot (dba_hist_sysstat)
-- domain: parse
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
WITH s AS (
  SELECT snap_id, stat_name,
         value - LAG(value) OVER (PARTITION BY stat_name ORDER BY snap_id) AS delta
  FROM   dba_hist_sysstat
  WHERE  dbid = &dbid
  AND    instance_number = &inst_id
  AND    snap_id BETWEEN &begin_snap AND &end_snap
  AND    stat_name IN ('parse count (total)', 'parse count (hard)', 'execute count')
)
SELECT snap_id,
       MAX(CASE WHEN stat_name = 'parse count (total)' THEN delta END) AS parses,
       MAX(CASE WHEN stat_name = 'parse count (hard)'  THEN delta END) AS hard_parses,
       MAX(CASE WHEN stat_name = 'execute count'       THEN delta END) AS executes
FROM   s
WHERE  snap_id > &begin_snap
GROUP  BY snap_id
ORDER  BY snap_id;

-- id: V-12
-- title: Statements with high parse-to-execute ratio (v$sql: parse_calls vs executions)
-- domain: parse
-- min_version: 11.2
-- license: none
-- params:
SELECT *
FROM  (SELECT sql_id, parsing_schema_name, parse_calls, executions,
              ROUND(parse_calls / NULLIF(executions, 0), 2) AS parse_per_exec
       FROM   v$sql
       WHERE  parse_calls > 100
       ORDER  BY parse_calls DESC)
WHERE ROWNUM <= 20;
