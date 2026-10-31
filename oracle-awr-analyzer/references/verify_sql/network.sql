-- id: V-14
-- title: SQL*Net events per snapshot (dba_hist_system_event, event LIKE 'SQL*Net%')
-- domain: network
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
WITH e AS (
  SELECT snap_id, event_name,
         total_waits       - LAG(total_waits)       OVER (PARTITION BY event_name ORDER BY snap_id) AS waits,
         time_waited_micro - LAG(time_waited_micro) OVER (PARTITION BY event_name ORDER BY snap_id) AS micro
  FROM   dba_hist_system_event
  WHERE  dbid = &dbid
  AND    instance_number = &inst_id
  AND    snap_id BETWEEN &begin_snap AND &end_snap
  AND    event_name LIKE 'SQL*Net%'
)
SELECT snap_id, event_name, waits, ROUND(micro/1e6, 1) AS time_s,
       ROUND(micro/1000/NULLIF(waits, 0), 2) AS avg_ms
FROM   e
WHERE  snap_id > &begin_snap
ORDER  BY snap_id, micro DESC;
