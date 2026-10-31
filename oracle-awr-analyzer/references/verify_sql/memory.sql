-- id: V-06
-- title: Swap and memory per snapshot (dba_hist_osstat VM_IN_BYTES, VM_OUT_BYTES, PHYSICAL_MEMORY_BYTES)
-- domain: memory
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
  AND    stat_name IN ('VM_IN_BYTES', 'VM_OUT_BYTES', 'PHYSICAL_MEMORY_BYTES')
)
SELECT snap_id,
       ROUND(MAX(CASE WHEN stat_name = 'VM_IN_BYTES' THEN delta END)/1024/1024, 1)  AS swap_in_mb,
       ROUND(MAX(CASE WHEN stat_name = 'VM_OUT_BYTES' THEN delta END)/1024/1024, 1) AS swap_out_mb,
       ROUND(MAX(CASE WHEN stat_name = 'PHYSICAL_MEMORY_BYTES' THEN value END)/1024/1024/1024, 1) AS phys_mem_gb
FROM   o
WHERE  snap_id > &begin_snap
GROUP  BY snap_id
ORDER  BY snap_id;
