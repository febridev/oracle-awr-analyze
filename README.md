# Oracle AWR Analyzer

A skill for AI coding agents (Claude Code, Antigravity and similar) that reads one Oracle AWR report and turns it into a prioritized findings report: severity **P0–P4**, the evidence behind each finding, read-only SQL to verify it, and an offline HTML report you can hand to a non-technical user.

It is built for one DBA serving many databases: answer "our database is slow" in minutes instead of hours.

## What you get

Point your agent at an AWR HTML file and it produces a folder like `awr-report-PRODDB-1-4120_4121/` containing:

| File | Contents |
|---|---|
| `report.html` | Single self-contained file with five inline SVG charts. No scripts, no external requests, so it opens offline and can be emailed |
| `report.md` | The same report as Markdown |
| `findings.json` | Machine-readable findings: severity, evidence, rule ids, verification SQL |
| `awr.json` | Every value extracted from the AWR report |
| `awr_diff_<db>.sql` | Only when a comparison with a baseline period would help: a ready-to-run SQL*Plus script for an AWR Diff report |

The report answers, in order:

1. **Executive summary** in plain language (at most 5 lines).
2. **Database and period**: DB, instance, version, host, CPUs, memory, snapshots, elapsed, DB time.
3. **Load health**: average and peak load (Idle / Normal / Busy / Saturated / Critical), DB CPU share, and the bottleneck. Examples: "Wait-bound: the top wait class (User I/O) sets the direction of the analysis"; "The database is not the problem in this period".
4. **Findings**, P0 first, each with evidence, facts, hypothesis, user impact, recommendation, change risk and approval flag.
5. **Top SQL by category**: application, monitoring, maintenance, Oracle internal, ad-hoc tools, unknown.
6. **Domain detail**, **verification SQL** (with license tags), **AWR diff advice**, and an **appendix** with raw metrics, assumptions, missing sections and limitations.

For reports longer than two hours (for example a daily 24-hour AWR), it reports both the average and the **peak hour**, and recommends an hourly AWR for that peak. A database that looks relaxed on average can still have one saturated hour.

## Severity levels

| Level | Meaning | Action |
|---|---|---|
| P0 Critical | Service disrupted or close to stopping | Act now |
| P1 High | Clear user impact | Today |
| P2 Medium | Measurable degradation that can grow | This week |
| P3 Low | Optimization opportunity | Backlog |
| P4 Info | Configuration note, no measured impact | Record only |

### P0 criteria

A finding is P0 only when one of these rules fires (default thresholds):

| Rule | Condition |
|---|---|
| `cpu.load.p0` | Load ratio ≥ 2.0 (active sessions more than twice the CPU count) and host CPU idle < 5% |
| `event.share.p0` | One wait event takes ≥ 30% of DB time and its latency is past the critical threshold (single-block reads: 8 ms flash, 20 ms HDD/SAN; multi-block: 20 / 40 ms) |
| `conc.combined.p0` | Lock and latch contention events together take ≥ 30% of DB time |
| `redo.ckpt.p0` | `log file switch (checkpoint incomplete)` is the top non-idle wait event |

All four are load-based, so they can be lowered:

- **Quiet period:** if the database is idle (load ratio < 0.10) or the report covers less than 10 minutes, the finding is capped at P3 with low confidence.
- **Volume cap:** a high share of DB time with healthy latency is capped at P2. That is a volume problem, not a storage emergency.

Every threshold lives in `oracle-awr-analyzer/references/thresholds.yaml` and can be overridden per environment. Each finding lists the rule ids that triggered it (`triggered_by`). If a severity was capped or raised, the finding also says why (`severity_adjustments`). For example, a wait event with a high share of DB time but healthy latency is capped at P2 as "a volume problem, not storage".

## Reading the report

The report is written so that someone who is not a DBA can follow it:

- **Priority at a glance:** a P0–P4 strip with finding counts at the top, and a grid showing which area of the system each finding is about.
- **How priority is set:** the Findings section explains the levels, the P0 conditions and the adjustments, using the thresholds of that run. Each finding has a "Why this priority" line that compares the rule's limit with the measured value.
- **Where the numbers come from:** every chart, table and finding names its AWR section by its exact title, so you can search for it in the AWR file. The appendix lists every section used and marks the ones missing from that file. Charts and their captions are in the HTML report only.

## How it works

```
AWR.html ─parse_awr.py─▶ awr.json ─classify_sql.py─▶ awr.json (+SQL categories)
                                          │
                       thresholds.yaml ─analyze.py─▶ findings.json
                                                          │
             team.py plan ─▶ writer agents ─▶ verifier agents ─▶ team.py merge ─▶ interpretation.json
                                                          │
                 validate_findings.py + lint_verify_sql.py (fix and rerun, max 3 times)
                                                          │
                       render_report.py ─▶ report.md + report.html
                       awr_diff_sql.py  ─▶ awr_diff_<db>.sql (only if suggested)
```

Models by severity:

| Findings | Writer | Reviewer | Rewrite |
|---|---|---|---|
| P0, P1 | opus | opus | opus |
| P2 | sonnet | sonnet | opus |
| P3, P4 | haiku | sonnet | sonnet |
| Summary only (no findings) | sonnet | sonnet | opus |

**Scripts compute every number; the AI only interprets.** The agents never read the raw HTML. They write the plain-language text (facts, hypothesis, user impact, recommendation) into `interpretation.json`. In Claude Code the work is split among a small team: `team.py plan` groups findings by severity (and copies the rules into `team_refs/` in the run folder for the agents to read), one writer agent per group writes its part, a second, fresh agent reviews it, and `team.py merge` combines the parts. A failed review gets one rewrite; if it still fails, that entry uses standard text and the reply says so. A validator then checks that text before anything is rendered:

- Every number in the prose must come from the finding's evidence or from `awr.json`. Invented numbers are rejected.
- Causal claims are kept out of `facts` and must be labeled as hypotheses.
- Executable change commands (`ALTER SYSTEM ...`, `CREATE INDEX ... ON ...`, `GRANT ... TO ...`, and so on) are rejected. Changes are described in words and flagged for change approval.

When subagents are unavailable, including a manual `cp -r` copy to `~/.claude/skills/`, the run falls back to one agent writing everything, and the report is the same kind. The optional "helper desk" mod, which shows the team at work, is not part of this plugin; the report is identical without it.

If the AI's text still fails after three attempts, the report uses standard explanations for those findings. The report is always produced, and it is never wrong.

## Safety guarantees

- **No database connection.** The skill reads one file. It never connects to a database or runs SQL.
- **Read-only verification SQL.** Queries come only from the catalog in `references/verify_sql/`. Before any report is produced, a linter checks that each one is:
  - a single `SELECT`/`WITH` statement;
  - free of DML, DDL and other changes;
  - free of package calls outside an allowlist;
  - free of SQL*Plus tricks such as a second statement, `/` or `.` lines, blank lines, or `HOST`.
- **Untrusted input handled as untrusted.** Values taken from the AWR file are validated before they are inserted into verification SQL, and all AWR text is escaped in the HTML.
- **Privacy.** For non-technical readers, string and number literals in SQL text are masked. Full SQL appears only in the DBA appendix and in `findings.json`.
- **Licensing.** AWR and `DBA_HIST_*` require the Oracle Diagnostics Pack. Each verification query carries its license tag (`none`, `diagnostics_pack`, `tuning_pack`).
- **The input file is never modified.** Every run writes to a new folder.

## Installation

### Claude Code (plugin)

This repository is a Claude Code plugin marketplace. In Claude Code (2.1.275 or later):

```
/plugin install oracle-awr-analyzer --marketplace febridev/oracle-awr-analyze
```

On older versions, add the marketplace first, then install:

```
/plugin marketplace add febridev/oracle-awr-analyze
/plugin install oracle-awr-analyzer@oracle-awr-analyze
```

The skill then appears as `oracle-awr-analyzer:oracle-awr-analyzer`. To get new versions, run `/plugin marketplace update oracle-awr-analyze`.

### Python dependencies (one time)

The scripts need Python 3.9 or later and three libraries. A plugin cannot install these for you:

```bash
pip install beautifulsoup4 lxml pyyaml
```

If they are missing, the skill tells the agent to install them from `requirements.txt` in the skill folder.

### Manual install and other agents

- **Claude Code without the plugin system:** copy the skill folder with `cp -r oracle-awr-analyzer ~/.claude/skills/`. A manual copy has no subagents, so it uses the single-agent fallback.
- **Other agents (Antigravity and so on):** the scripts are agent-neutral. Point the agent at `oracle-awr-analyzer/SKILL.md`, which holds the full workflow and rules.

## Usage

In your agent, attach or name an AWR HTML report, for example:

> Our PRODDB was slow this morning, here is the AWR: `~/awr/awrrpt_1_4120_4121.html`

Generate the report on the database with `@?/rdbms/admin/awrrpt.sql` and choose `html`. Text-format AWR reports are not supported in v1.

If you mention an earlier good period ("yesterday was fine", "since the deploy"), the skill also prepares an AWR diff script against a baseline.

### Running the scripts directly

```bash
S=oracle-awr-analyzer/scripts
RUN=$(python3 $S/parse_awr.py awrrpt.html --out /tmp)   # prints the run folder
python3 $S/classify_sql.py "$RUN"
python3 $S/analyze.py "$RUN" [--config my.yaml] [--comparison-mentioned]
# optional: write $RUN/interpretation.json (see SKILL.md for the schema)
python3 $S/validate_findings.py "$RUN"
python3 $S/lint_verify_sql.py --run "$RUN"
python3 $S/render_report.py "$RUN" [--config my.yaml]
python3 $S/awr_diff_sql.py "$RUN"
```

Without `interpretation.json`, the report uses the standard explanation for each rule.

### Configuration (optional)

Pass `--config my.yaml` to `classify_sql.py`, `analyze.py` and `render_report.py`:

```yaml
storage_profile: flash          # hdd | san | flash (default san; latency thresholds depend on it)
audience: both                  # dba | user | both (default both)

# SQL classification hints: literal names, case-insensitive (paste modules as shown in AWR)
user_schemas: [APP]
app_programs: [APPSRV, APPWORKER]
app_hosts: [app-server-01]
app_table_prefixes: [APP_, ORD_]
monitoring_modules: ["dbspicao11@db-host-01 (TNS V1-V3)"]
monitoring_schemas: [DBSNMP]

thresholds:                     # overrides for references/thresholds.yaml
  swap_out_mb_per_hour: 5
```

Set `storage_profile` correctly. Single-block reads warn at 3/8 ms on flash and 10/20 ms on HDD/SAN, so the same latency can be healthy on one profile and a finding on another.

## Limitations (v1)

- **Single instance only.** RAC reports are detected and flagged; RAC sections are not analyzed.
- **HTML input only.** Text-format AWR is rejected with a message.
- **English reports only.**
- **No comparison tables.** The skill does not build them from two AWR files; it generates the AWR Diff script for the DBA to run.
- **Not analyzed:** PDB-level AWR, Exadata specifics, ASH, ADDM and SQL Monitor reports.
- **Rules of thumb.** Thresholds are common-practice defaults, not Oracle standards. Tune them to your baseline.

## Repository layout

```
.claude-plugin/
└── marketplace.json          # makes this repo installable with /plugin
oracle-awr-analyzer/          # the skill (the plugin's source folder)
├── SKILL.md                  # workflow and rules the agent follows
├── agents/                   # awr-writer and awr-verifier subagents
├── scripts/                  # parse_awr, classify_sql, analyze, team, validate_findings,
│   │                         # lint_verify_sql, render_report, svg, awr_diff_sql, common
│   └── tests/                # pytest suite
├── references/
│   ├── thresholds.yaml       # every threshold and severity rule
│   ├── sql_classification.yaml
│   ├── wait_events.md        # event → meaning → common causes → how to verify
│   ├── awr_diff.md           # AWR_DIFF_REPORT_HTML signature per Oracle version
│   └── verify_sql/           # read-only verification query catalog (V-01 … V-17)
└── assets/report.html.tpl
PRD.md                        # product requirements
docs/superpowers/             # design spec and implementation plan
```

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest
```

The suite covers parsing, classification, the SQL linter's bypass cases, severity rules, the validator, rendering, and an end-to-end golden run.

No AWR report is shipped with this repository; AWR reports contain hostnames, schema names and SQL from real systems. Tests that need a real report read it from a git-ignored folder and are skipped when it is absent:

```
local-samples/                # git-ignored; never commit
├── awrrpt_1_3138_3162.html   # your AWR report
├── expected/header.json      # its expected header values
└── sample.json               # its identifiers used by tests (db name, dbid, host, SQL ids)
```

Point the tests at another location with `AWR_SAMPLE_DIR=/path/to/samples .venv/bin/pytest`.
