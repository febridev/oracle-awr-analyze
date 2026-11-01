# Wait event reference

Read this while writing `hypothesis` text. Causes listed here are possibilities to check, never conclusions.
Verify ids are the queries in `references/verify_sql/` (the same ids the rules in `thresholds.yaml` attach).

| Event | Domain | Meaning | Common causes | How to verify |
|---|---|---|---|---|
| db file sequential read | I/O (single block) | A session waits for one block read from disk, usually through an index or by rowid. | Slow storage; index range scans on poorly clustered data; plans that do many single-block lookups; buffer cache too small; many sessions reading at once. | V-07 (top events), V-08, V-09 (I/O by statement and object) |
| db file scattered read | I/O (multiblock) | A session waits for multiblock reads, normally a full table scan or fast full index scan through the buffer cache. | Missing or unusable index; large scans in reporting jobs; stale statistics; high `db_file_multiblock_read_count`; small buffer cache. | V-07, V-08, V-09 |
| direct path read | I/O (multiblock, bypasses cache) | Reads go straight into the session's PGA, skipping the buffer cache. Used for large scans and parallel query. | Large full scans of big tables; parallel query; serial direct reads chosen by size threshold; temp reads of big sorts or hashes. | V-07, V-08, V-09 |
| log file sync | Redo | A session that committed waits until LGWR has written its redo to the online log. | Slow redo disk; very frequent commits (row-by-row commit loops); LGWR starved of CPU; large redo bursts. | V-07, V-10 (redo and LGWR statistics) |
| log file switch (checkpoint incomplete) | Redo / checkpoint | Redo cannot switch to the next log because DBWR has not finished checkpointing it. Sessions stall. | Redo logs too small or too few; DBWR too slow for the write load; slow data file storage. | V-03 (log switch history), V-10 |
| control file sequential read | Control file / background | Reads of the control file by sessions and background processes. Usually harmless background work. | Backup tools (RMAN), archiving, frequent `v$` queries or monitoring tools reading control file based views. | V-07 |
| resmgr:cpu quantum | CPU / Resource Manager | A session is paused by the Resource Manager to enforce a CPU limit of its consumer group. | Active resource plan with CPU limits; a consumer group hitting its cap while the host has idle CPU. | V-05 (CPU by session and group) |
| library cache: mutex X | Parse / mutex | A session waits for exclusive access to a library cache object, often while parsing the same cursor. | Hard parse storms; literals instead of binds; cursor invalidation after statistics or DDL; many versions of one cursor. | V-11 (parse statistics), V-12 (cursor sharing) |
| cursor: pin S wait on X | Parse / mutex | A session wants a shared pin on a cursor that another session holds exclusively, usually during a hard parse. | Same family as library cache mutex: parse storms, version count growth, invalidations. | V-11, V-12 |
| enq: TX - row lock contention | Concurrency / locks | A session waits for a row lock held by another transaction. | Long open transactions; two sessions updating the same rows; missed commits in the application; unindexed foreign keys. | V-13 (blocking and locks) |
| enq: TM* | Concurrency / locks | A session waits for a table-level lock (the `TM` enqueue family). | Unindexed foreign keys on child tables; DDL on a busy table; explicit table locks. | V-13 |
| buffer busy waits | Concurrency / hot block | A session wants a block that another session is reading or changing. | Hot blocks (right-growing index, small hot table); many inserts into the same block; free list or ASSM contention. | V-13 |
| latch: cache buffers chains | Concurrency / hot block | Contention on the hash chains that protect buffer cache blocks. | A few extremely hot blocks read by many sessions; high logical reads from inefficient plans; nested loops over small tables. | V-13 |
| library cache lock | Concurrency / parse | A session waits for a lock on a library cache object, for example during DDL or invalidation. | DDL or grants on objects in use; recompilation of packages; statistics gathering invalidating cursors. | V-13 |
| SQL*Net more data to client | Network | The server waits while sending a result that does not fit in one network packet. | Large result sets; large fetch sizes; slow network or slow client. The database is rarely the bottleneck. | V-14 (network and fetch statistics) |
| SQL*Net message to client | Network | The server waits while sending a message to the client. | Slow or overloaded client or network; very high rate of small round trips. Often a symptom, not a cause. | V-14 |

Notes for the agent:

- Events in `event_share_exclude` (control file read, both mutex events, the five concurrency events and both SQL*Net events) are judged by their own rule or by a sum rule (`conc.combined.*`, `parse.mutex.p1`, `net.client.p3`), not by `event.share.*`.
- Idle events (for example `SQL*Net message from client`) are never findings. If one shows up in a hypothesis, say it is idle time.
- An event name that is not in this table: describe it only from what the finding evidence shows, and say the cause is unknown.
