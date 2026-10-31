# Oracle AWR Analyzer Skill — Design Spec

Date: 2026-10-31 · Source requirements: `PRD.md` (PRD v2) · Status: awaiting review

## 1. Intent

**Outcome.** A Claude Skill (`oracle-awr-analyzer/`) that reads one Oracle AWR HTML report and produces a P0–P4 findings report with evidence, read-only verification SQL, and an offline visual HTML, so one DBA can answer "our database is slow" in minutes.

**Runtime.** The host agent (Claude Code, Antigravity, or any agent that can run Python) is the runtime. We do not build our own agent loop. Scripts are agent-neutral; `SKILL.md` is the agent-facing instruction file.

**Hard rules (from PRD §1).** Scripts compute every number; the LLM only interprets. No database connection, no SQL execution. Verification SQL only from the catalog. Thresholds in YAML.

**Success.** PRD §15 acceptance criteria A1–A10, as adjusted in §10 of this spec, pass on the real PRODDB 19c sample placed in `oracle-awr-analyzer/examples/`.

## 2. Scope decisions for v1 (deviations from PRD)

| Topic | PRD | v1 decision | Reason |
|---|---|---|---|
| RAC | Full RAC analysis | Detect from header, set `meta.rac=true`, banner "RAC sections not analyzed in v1". No `awrgrpt` split, no gc rules, no instance-comparison chart, no global diff variant. | Only sample is single instance; building parsers blind violates PRD "do not invent". Added when a RAC sample is provided. |
| Text AWR (.txt/.lst) | Fallback input | Rejected with exit code 3 and a message to regenerate as HTML. | No text sample. |
| Language | `lang=id` and `en` | `en` only. | DBA decision. |
| `references/report_template.md` | Listed | Dropped. Section order lives in `render_report.py`. | Would duplicate code. |
| A8 visuals | Six | Five (RAC comparison replaced by a note). | Follows from RAC deferral. |

Everything else in the PRD is in scope.

## 3. Architecture

One output folder per run: `awr-report-<db>-<inst>-<snapbegin>_<snapend>/`. If it exists, append `-2`, `-3`, ... Input file is never modified. Scripts communicate only through JSON files in that folder.

```
AWR.html ──parse_awr.py──▶ awr.json ──classify_sql.py──▶ awr.json (sql[] gains category/confidence/reasons)
                                            │
                     thresholds.yaml ──analyze.py──▶ findings.json
                                                          │
                              agent (LLM) ──writes──▶ interpretation.json
                                                          │
            validate_findings.py + lint_verify_sql.py  ◀──┘  (agent fixes, max 3 attempts)
                                                          │
                   render_report.py ──▶ report.md + report.html
                   awr_diff_sql.py  ──▶ awr_diff_<db>.sql   (only if findings.diff.suggested)
```

| File | Sole writer | Content |
|---|---|---|
| `awr.json` | `parse_awr.py`; `classify_sql.py` adds SQL classification fields only | All extracted values, `missing_sections[]`, `invalid_values[]`, `meta` |
| `findings.json` | `analyze.py` | Load status (avg + peak), bottleneck type, findings, filled verify SQL, diff suggestion |
| `interpretation.json` | Agent | Prose fields per finding + executive summary |
| `report.md`, `report.html` | `render_report.py` | Merge of the three above |

**Configuration.** Optional `--config my.yaml` carrying PRD §3 parameters: `user_schemas`, `monitoring_schemas`, `monitoring_modules`, `app_hosts`, `app_programs`, `app_table_prefixes`, `storage_profile` (`hdd`/`san`/`flash`), `audience` (`dba`/`user`/`both`, default `both`), and `thresholds:` overrides. Config values are merged over `references/thresholds.yaml` and `references/sql_classification.yaml` (config wins). If `storage_profile` is absent, `san` thresholds are used and the report states the assumption.

**Dependencies.** Python 3.9+, `beautifulsoup4`, `lxml`, `pyyaml`; `pytest` for tests. If `lxml` is missing, fall back to `html.parser` and record `meta.parser = "html.parser (reduced accuracy)"`. No other dependencies.

## 4. Parser — `parse_awr.py <awr.html> [--out DIR]`

**Validation (in order).**
1. Read bytes; encoding from `<meta charset>`, else try utf-8, else latin-1.
2. Not HTML → exit 3: "text AWR not supported in v1; regenerate with awrrpt.sql in HTML".
3. No "WORKLOAD REPOSITORY" → exit 2 with a description of the expected file.

**Table identification.** A single `SECTIONS` dict maps each section key to regexes over the table `summary` attribute, with the nearest preceding `h2`/`h3` text as fallback. Exact strings are taken from the real sample at implementation time. Generic `table_rows(table) -> list[dict]` keyed by normalized header names; small per-section extractors select the metrics in PRD §5 (both tables).

**Number normalization — one function `num(text, locale)`.**
- Handles thousands separators, `K`/`M`/`G` suffixes, scientific notation (`3.1E+08`), leading-dot decimals (`.8`), blank → `null`.
- Time values are converted to the unit named by the key suffix: `_ms`, `_s`, `_min`. `486.74us` → `0.48674` in an `_ms` key.
- Percentages stored as numbers in `_pct` keys.
- Locale (decimal separator) is detected once per file from the header Elapsed field (always has decimals), never guessed per value.

**Missing and invalid.**
- Section not found → its value is `null`; append `{"section", "reason"}` to `missing_sections[]`. Parsing continues.
- Out-of-range value (efficiency `%` outside 0–100, negative counts or times) → value becomes `null`; append `{"path", "raw", "reason"}` to `invalid_values[]`. Example: Library Hit % = -433.41.

**Derived metadata.** `meta.restart` (startup time later than begin snap time), `meta.rac`, `meta.cdb`, `meta.parser`, `meta.elapsed_min`, `meta.db_time_min`.

**SQL.** Rows from every "SQL ordered by …" table merged by `sql_id`, each table's metrics kept under its own key. Text from "Complete List of SQL Text". `module` matching `PROGRAM@host (TNS V1-V3)` is split into `program` and `client_host`.

**Peak data.** Top ADDM Findings (name, avg active sessions, % active sessions, start/end) and Operating System Statistics – Detail rows stored as arrays.

**`awr.json` top level.** `meta`, `load_profile`, `efficiency`, `top_events`, `time_model`, `host_cpu`, `instance_cpu`, `os_stats`, `os_detail`, `memory`, `io_profile`, `iostat_function`, `fg_events`, `bg_events`, `sql`, `pga`, `advisories`, `segments`, `activity`, `thread_activity`, `services`, `addm`, `parameters`, `missing_sections`, `invalid_values`. Values are plain numbers or strings, keys follow the unit-suffix convention.

## 5. Analysis

### 5.1 `classify_sql.py <run_dir> [--config]`

All signals are evaluated per SQL (never per module).

- **Category** = highest-priority matching rule, order: (1) DBA lists in config, (2) module patterns in `sql_classification.yaml`, (3) SQL text patterns, (4) object owner/prefix match, (5) `unknown`. Categories: `user_app`, `monitoring`, `maintenance`, `oracle_internal`, `adhoc`, `unknown`.
- **Text patterns.** Only dictionary/fixed objects (`V$`, `GV$`, `DBA_`, `X$`, `SYS.`) → `monitoring`; `DBMS_STATS` or `/* SQL Analyze(` → `maintenance`; `SELECT 1 FROM DUAL` → `monitoring` heartbeat, low; internal tables such as `WRI$_` → `oracle_internal`.
- **Client host signals.** `client_host` equal to the DB host and program not an Oracle process → `monitoring` (medium). `client_host` in `app_hosts` or `program` in `app_programs` → `user_app`.
- **Weak signals.** Generic modules (`JDBC Thin Client`, `SQL*Plus`, `python`) only add confidence when agreeing with another signal; never decide alone.
- **`adhoc`.** Modules SQL Developer, Toad, PL/SQL Developer.
- **Object extraction.** Regex over identifiers after FROM/JOIN/INTO/UPDATE. Marked `shortcut: misses dynamic SQL and quoted identifiers; upgrade if A5 accuracy < 90%`.
- **Confidence.** high = DBA list match or ≥2 agreeing signals; medium = one strong signal; low = weak signal only.
- **Output per SQL.** `category`, `confidence`, `reasons[]`, `verify_sql: "V-01"`.
- **Defaults in `sql_classification.yaml`** (from PRD §8 calibration): `app_table_prefixes: [APP_, ORD_, LCK_]`; `dbspicao11` listed as monitoring program; `Poller` is not a monitoring module; OEM/emagent, Zabbix, Prometheus exporter, Nagios as monitoring modules; RMAN, Data Pump, DBMS_SCHEDULER as maintenance; MMON_SLAVE, MMNL as internal.

### 5.2 `analyze.py <run_dir> [--config] [--comparison-mentioned]`

**Rule logic in Python, values in YAML.** `references/thresholds.yaml` holds each rule as:

```yaml
io.seq_read.crit:
  severity: P1
  load_based: true
  value: {flash: 8, hdd: 20, san: 20}   # ms
  verify_sql: [V-07, V-08]
  fallback:
    cause: "..."
    impact: "..."
    recommendation: "..."
```

`analyze.py` has one function per domain — `cpu`, `memory`, `io`, `redo`, `parse`, `concurrency`, `network`, `sql`, `config`, `monitoring_overhead` — that reads these values and emits candidate findings. All threshold numbers from PRD §7 and §9 (including the sample calibration list in §9) are encoded as rules. RAC rules are not implemented in v1.

**Load status (PRD §6).** AAS = DB time / elapsed; load ratio = AAS / CPUs (logical); DB CPU share = DB CPU / DB time × 100. Status bands and the 4-row bottleneck table as PRD §6; English status names: Idle, Normal, Busy, Saturated, Critical (PRD: idle, normal, sibuk, jenuh, kritis). Hyperthreading (cores much fewer than CPUs) is noted, formula unchanged. If elapsed > 120 min, compute a peak: the ADDM window with highest avg active sessions ÷ CPUs, supported by the OS Detail row for that hour. Load-based severity uses the peak status and names the hour; the report recommends an hourly AWR for the peak. An idle database yields a "no performance problem in this period" statement and no forced findings.

**Finding shape.**

```json
{"id": "F03", "domain": "io", "subject": "direct path read", "title": "...",
 "severity": "P1", "triggered_by": ["io.multi_read.warn", "io.event_share.p1"],
 "load_based": true, "confidence": "high",
 "evidence": [{"metric": "avg_wait_ms", "value": 15.08,
               "section": "Top Foreground Events", "path": "top_events[2].avg_wait_ms"}],
 "verify_sql": [{"id": "V-07", "sql": "...filled...", "license": "diagnostics_pack",
                 "available": true}]}
```

**`finalize()` — applied once, in this order.**
1. Merge candidates by `(domain, subject)`; severity = highest triggered rule; `triggered_by` = all.
2. Drop events in wait class Idle; drop findings under 1% DB time except lost-block and error findings.
3. Caps: load status Idle or elapsed < 10 min → `load_based` findings capped at P3, confidence low. High DB-time share with healthy latency → capped at P2 (volume, not storage); points to SQL ordered by User I/O Wait Time and Segments by Physical Reads.
4. Escalation: a SQL finding is raised one level if that SQL is in SQL ordered by User I/O Wait Time while an I/O event is past its critical threshold.
5. Restart detected → all findings confidence low.
6. Sort P0 → P4, assign ids `F01…`.

**Verify SQL.** Each rule lists catalog ids. Substitution variables `&dbid`, `&inst_id`, `&begin_snap`, `&end_snap`, `&sql_id` are filled from `awr.json`. If the catalog query's `min_version` is above the DB release, `available: false`.

**Diff suggestion (PRD §12).** `diff.suggested = true` when any of: `--comparison-mentioned` passed; a P0/P1 finding not in domain `config`; status Saturated/Critical while executes/s and transactions/s are within the optional `workload_norms` (`executes_per_s`, `txn_per_s` upper bounds) in config/`thresholds.yaml`; when `workload_norms` is unset (default, since one AWR has no baseline), Saturated/Critical status alone fires this criterion. Never suggested for an Idle database without `--comparison-mentioned`. `diff.reasons[]` explains which criterion fired.

**`findings.json` top level.** `load` (avg, peak, bottleneck), `findings[]`, `sql_summary` (top SQL per category, monitoring overhead totals), `diff`, `notes[]` (hyperthreading, storage assumption, restart, RAC).

## 6. Interpretation and validation

### 6.1 `interpretation.json` (agent-written)

```json
{"executive_summary": ["max 5 plain-language lines"],
 "findings": {"F03": {"facts": "", "hypothesis": "", "user_impact": "",
                      "recommendation": "", "change_risk": "",
                      "approval_required": true}}}
```

### 6.2 `validate_findings.py <run_dir>`

Errors → exit 1; warnings only → exit 0. Each message is `<finding>.<field>: <problem>`.

| Check | Level |
|---|---|
| Every evidence `path` resolves in `awr.json` and the value is exactly equal | error |
| Every finding has severity, ≥1 evidence, confidence, ≥1 verify SQL, `triggered_by` (A4) | error |
| Interpretation finding ids ⊆ findings ids; only the six allowed fields; executive summary ≤ 5 lines | error |
| Every number in interpretation prose equals a value in that finding's evidence or anywhere in `awr.json`, compared at the precision written (`15.1` matches `15.08`) | error |
| `facts` contains a causal word (because, due to, caused by, likely, probably, suggests) | error |
| Finding without interpretation | warning (fallback used) |

Numbers inside identifiers are exempt: severity labels (`P1`), verify ids (`V-03`), finding ids, SQL IDs, release strings, snap ids. Causal-word list is marked `shortcut: misses paraphrases; upgrade if A6 review finds leaks`.

### 6.3 `lint_verify_sql.py [--catalog DIR] [--run RUN_DIR]`

Catalog: `references/verify_sql/<domain>.sql`, queries separated by `-- id:` headers. Initial catalog includes V-01, V-02, V-03 from the PRD plus queries for each domain rule's verification (ids assigned at implementation; every id referenced in `thresholds.yaml` must exist).

Rules (any violation → exit 1):
1. Headers `id`, `title`, `domain`, `min_version`, `license`, `params` present; `license` ∈ {`none`, `diagnostics_pack`, `tuning_pack`}; ids unique.
2. After stripping comments and string literals, statement starts with `SELECT` or `WITH`.
3. Forbidden tokens: INSERT, UPDATE, DELETE, MERGE, CREATE, ALTER, DROP, TRUNCATE, GRANT, REVOKE, EXEC, EXECUTE, BEGIN, DECLARE, CALL, and `FOR UPDATE`.
4. `DBMS_*` calls limited to `DBMS_XPLAN.DISPLAY_AWR`, `DBMS_XPLAN.DISPLAY_CURSOR`, `DBMS_WORKLOAD_REPOSITORY.AWR_*_REPORT_*`.
5. `&vars` used equal the declared `params`.
6. Every verify id referenced in `thresholds.yaml` exists.
7. With `--run`: each filled SQL in `findings.json` equals its catalog text after substitution.

### 6.4 Fix loop

`SKILL.md` instructs: run both scripts; on failure fix `interpretation.json` and rerun; maximum 3 attempts. After that, render anyway — still-failing findings use fallback text with the note "interpretation rejected by validator". A catalog lint failure is a skill bug: stop and report it.

## 7. Rendering — `render_report.py <run_dir> [--config]`

**Order (PRD §10).** 1 Executive summary · 2 Database and period (header block per PRD §3) · 3 Load health (avg + peak, bottleneck) · 4 Findings P0→P4 (PRD §10 template) · 5 Top SQL per category + monitoring overhead · 6 Domain detail · 7 Verification SQL appendix with license tags · 8 AWR diff suggestion · 9 Appendix: raw metrics, assumptions, missing sections, invalid values, limitations.

If `interpretation.json` is absent, the executive summary is generated from load status and severity counts.

**Banners** (top of both files): restart between snaps; elapsed < 10 min; RAC not analyzed in v1; reduced parser accuracy.

**Fallback text.** Missing or rejected interpretation → rule `fallback` text, labeled "standard explanation (not database-specific)".

**Charts — `scripts/svg.py`, inline SVG.**
- `hbar(rows, ref_line=None)`: top 5 wait events (color by severity), top SQL (color by category), I/O latency vs threshold line, load ratio vs 1.0 line (avg and peak bars when elapsed > 120 min).
- `stacked_bar(parts)`: DB time composition (DB CPU + wait classes).
- Every bar has a text label (severity, category, value); color is never the only carrier.
- RAC comparison: a text note in place of the chart.

**HTML constraints.** Single file from `assets/report.html.tpl` filled via `string.Template`. No `<script>`, no `<link>`, no external `src`/`href`. All AWR-derived strings HTML-escaped.

**Audience.** `both` (default): user layer, then DBA layer. `user`: user layer only. `dba`: DBA layer only. User layer masks SQL text: string literals → `'?'`, numeric literals → `?` (`shortcut: regex masking may miss unusual quoting; upgrade if review finds leaks`). Full SQL only in DBA layer and `findings.json`.

**Footer.** Diagnostics Pack license note.

## 8. AWR diff — `awr_diff_sql.py <run_dir>`

Runs only when `findings.diff.suggested`. Writes `awr_diff_<db>.sql` using the PRD §12 skeleton, current snaps filled from `awr.json`, baseline as `&base_begin_snap` / `&base_end_snap`. The `DBMS_WORKLOAD_REPOSITORY.AWR_DIFF_REPORT_HTML` signature is recorded in `references/awr_diff.md` from Oracle documentation for 12.1–23ai, verified at implementation time. Report section 8 gives the reasons, current snap ids, baseline criteria (same weekday/hour, similar duration, no restart between), comparison warnings (duration differs > 20%, restart, CPU count differs → low confidence), and asks the DBA for baseline snap ids. Global (RAC) variant deferred.

## 9. Error handling

| Situation | Behavior |
|---|---|
| Not an AWR | `parse_awr.py` exit 2; agent stops and explains |
| Text AWR | exit 3; agent stops and explains |
| Missing section / truncated file | `null` + `missing_sections[]`; partial analysis; appendix lists gaps |
| Invalid value | nulled + `invalid_values[]`; never used in a finding |
| Restart, elapsed < 10 min, RAC, lxml missing | banner + caps/lower confidence |
| Validator fails after 3 attempts | fallback text, labeled; report produced |
| Catalog lint failure | hard stop (skill bug) |
| Output folder exists | new folder with numeric suffix |

## 10. Testing and acceptance

pytest suite in `oracle-awr-analyzer/scripts/tests/`.

| Area | Tests |
|---|---|
| Parser (real sample) | Header golden values (extracted, then confirmed by DBA) — A1; table detection count; 19c number formats; Library Hit % in `invalid_values`; ADDM and OS Detail arrays present |
| Parser (units) | `num()` cases from PRD §3 (`153.2K`, `3.1E+08`, `.8`, `486.74us`, `15.08ms`, both locales); non-AWR → exit 2; text → exit 3 |
| Truncated file | Real sample cut at ~50%: runs, lists missing sections |
| Analysis scenarios | Hand-written `awr.json` fixtures: idle, CPU-bound, I/O-bound, commit-heavy, parse-heavy, lock contention — expected status, bottleneck, findings |
| End-to-end golden (real sample) | Expected: direct path read finding, monitoring overhead P2, control file sequential read P3, Execute to Parse P3, log switch P3, avg Normal / peak Busy 22:00–23:00 |
| Classification | The 8 PRD §8 calibration cases |
| Validator | fabricated number, unknown finding id, extra field, causal word in `facts`, precision match passes |
| Linter | DML, `FOR UPDATE`, disallowed DBMS call, param mismatch, missing header, missing referenced id |
| Render | no external refs; `<script>` in SQL text escaped; user-layer masking; 9 sections; 5 charts |
| Benchmark (A9) | 20 MB file made by padding the sample's SQL text section; full pipeline < 60 s |

**Acceptance mapping.** A1, A2, A4, A7, A8 (five visuals), A9, A10 automated. A3 (load status judgment), A5 (≥ 90% on 50 DBA-labeled SQL), A6 (review) require DBA review after the first real run.

## 11. Repository layout

```
awr-skill/                       # git repo
├── PRD.md
├── docs/superpowers/specs/
└── oracle-awr-analyzer/
    ├── SKILL.md                 # ≤ 500 lines
    ├── scripts/
    │   ├── parse_awr.py
    │   ├── classify_sql.py
    │   ├── analyze.py
    │   ├── validate_findings.py
    │   ├── lint_verify_sql.py
    │   ├── render_report.py
    │   ├── svg.py
    │   ├── awr_diff_sql.py
    │   └── tests/
    ├── references/
    │   ├── thresholds.yaml
    │   ├── sql_classification.yaml
    │   ├── wait_events.md
    │   ├── awr_diff.md
    │   └── verify_sql/
    ├── assets/report.html.tpl
    └── examples/                # real sample (DBA-provided) + expected outputs
```

**`SKILL.md` content.** Frontmatter from PRD §13. Workflow: validate → parse → classify → analyze → write `interpretation.json` (schema from §6.1) → validate + lint (max 3 attempts) → render → diff script if suggested. When to pass `--comparison-mentioned` (user mentions "yesterday was fine", "since deploy", "last week was fast"). Where `--config` comes from. Hard rules from PRD §13.

## 12. Open items (do not block implementation)

- PRD A5: Diagnostics Pack license on all target DBs — affects only the license footer wording.
- PRD A6/A8/A9: app hosts, programs, prefixes, `dbspicao11`, `ReportPortal.exe` — defaults in §5.1; DBA edits config.
- PRD A7: storage profile per environment — default `san` with stated assumption.
- Real sample must be placed in `oracle-awr-analyzer/examples/` before parser work starts.
