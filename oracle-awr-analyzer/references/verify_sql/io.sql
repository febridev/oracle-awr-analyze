-- id: V-07
-- title: Wait event latency per snapshot (dba_hist_system_event deltas; avg ms for one event)
-- domain: io
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap, event
WITH e AS (
  SELECT snap_id,
         total_waits       - LAG(total_waits)       OVER (ORDER BY snap_id) AS waits,
         time_waited_micro - LAG(time_waited_micro) OVER (ORDER BY snap_id) AS micro
  FROM   dba_hist_system_event
  WHERE  dbid = &dbid
  AND    instance_number = &inst_id
  AND    snap_id BETWEEN &begin_snap AND &end_snap
  AND    event_name = '&event'
)
SELECT snap_id, waits, ROUND(micro/1e6, 1) AS time_s,
       ROUND(micro/1000/NULLIF(waits, 0), 2) AS avg_ms
FROM   e
WHERE  snap_id > &begin_snap
ORDER  BY snap_id;

-- id: V-08
-- title: Top SQL by user I/O wait (dba_hist_sqlstat.iowait_delta)
-- domain: io
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
SELECT *
FROM  (SELECT s.sql_id, s.parsing_schema_name,
              SUM(s.executions_delta)                AS execs,
              ROUND(SUM(s.iowait_delta)/1e6, 1)      AS io_wait_s,
              ROUND(SUM(s.elapsed_time_delta)/1e6, 1) AS elapsed_s
       FROM   dba_hist_sqlstat s
       WHERE  s.dbid = &dbid
       AND    s.instance_number = &inst_id
       AND    s.snap_id BETWEEN &begin_snap + 1 AND &end_snap
       GROUP  BY s.sql_id, s.parsing_schema_name
       ORDER  BY io_wait_s DESC)
WHERE ROWNUM <= 10;

-- id: V-09
-- title: Top segments by physical reads (dba_hist_seg_stat + dba_hist_seg_stat_obj)
-- domain: io
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
SELECT *
FROM  (SELECT o.owner, o.object_name, o.object_type,
              SUM(g.physical_reads_delta) AS physical_reads
       FROM   dba_hist_seg_stat g
       JOIN   dba_hist_seg_stat_obj o
              ON  o.dbid = g.dbid AND o.obj# = g.obj# AND o.dataobj# = g.dataobj#
       WHERE  g.dbid = &dbid
       AND    g.instance_number = &inst_id
       AND    g.snap_id BETWEEN &begin_snap + 1 AND &end_snap
       GROUP  BY o.owner, o.object_name, o.object_type
       ORDER  BY physical_reads DESC)
WHERE ROWNUM <= 10;
