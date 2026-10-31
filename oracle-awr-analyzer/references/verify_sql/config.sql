-- id: V-15
-- title: Non-default parameters (v$parameter where isdefault = 'FALSE')
-- domain: config
-- min_version: 11.2
-- license: none
-- params:
SELECT name, value, ismodified
FROM   v$parameter
WHERE  isdefault = 'FALSE'
ORDER  BY name;

-- id: V-16
-- title: SGA target advisory (v$sga_target_advice)
-- domain: config
-- min_version: 11.2
-- license: none
-- params:
SELECT sga_size, sga_size_factor, estd_db_time, estd_physical_reads
FROM   v$sga_target_advice
ORDER  BY sga_size;
