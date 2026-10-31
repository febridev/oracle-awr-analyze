-- id: V-03
-- domain: redo
-- min_version: 11.2
-- license: none
-- params:
-- title: Log switch frequency per hour per thread
SELECT TO_CHAR(first_time, 'YYYY-MM-DD HH24') AS jam, thread#, COUNT(*) AS switches
FROM   v$log_history
WHERE  first_time > SYSDATE - 1
GROUP  BY TO_CHAR(first_time, 'YYYY-MM-DD HH24'), thread#
ORDER  BY 1, 2;

-- id: V-10
-- title: Redo log size and members (v$log, v$logfile)
-- domain: redo
-- min_version: 11.2
-- license: none
-- params:
SELECT l.thread#, l.group#, ROUND(l.bytes/1024/1024) AS size_mb, l.members, l.status,
       f.member
FROM   v$log l
JOIN   v$logfile f ON f.group# = l.group#
ORDER  BY l.thread#, l.group#, f.member;
