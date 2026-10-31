-- id: V-13
-- title: Row lock and buffer busy segments (dba_hist_seg_stat row_lock_waits_delta, buffer_busy_waits_delta)
-- domain: concurrency
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
SELECT *
FROM  (SELECT o.owner, o.object_name, o.object_type,
              SUM(g.row_lock_waits_delta)     AS row_lock_waits,
              SUM(g.buffer_busy_waits_delta)  AS buffer_busy_waits
       FROM   dba_hist_seg_stat g
       JOIN   dba_hist_seg_stat_obj o
              ON  o.dbid = g.dbid AND o.obj# = g.obj# AND o.dataobj# = g.dataobj#
       WHERE  g.dbid = &dbid
       AND    g.instance_number = &inst_id
       AND    g.snap_id BETWEEN &begin_snap + 1 AND &end_snap
       GROUP  BY o.owner, o.object_name, o.object_type
       ORDER  BY row_lock_waits + buffer_busy_waits DESC)
WHERE ROWNUM <= 10;
