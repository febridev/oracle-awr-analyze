---
name: oracle-awr-analyzer
description: Analyze an Oracle AWR report (HTML, single instance) and produce a findings report with severity P0-P4 as Markdown and a self-contained visual HTML file. Use when the user attaches an AWR file, mentions AWR or awrrpt, or reports that an Oracle database is slow and asks for a performance analysis.
---

# Oracle AWR Analyzer

Scripts compute every number and every severity. Your job is to run them in order, then write the
plain-language interpretation (`interpretation.json`) that the report shows next to the evidence.
All paths below are relative to this skill folder (`<skill>`). Run everything with `python3`.

## Setup

Python 3.9+. If an import fails: `pip install -r <skill>/requirements.txt`
(runtime deps: `beautifulsoup4 lxml pyyaml`). Without `lxml` the parser falls back to `html.parser`
and records reduced accuracy in `meta.parser`; tell the user if that happens.

## Workflow

Let `AWR` be the user's file and `RUN` the run folder printed by step 1.

1. Parse (never read the raw HTML yourself):
   `python3 <skill>/scripts/parse_awr.py AWR [--out PARENT]`
   Prints the run folder `awr-report-<db>-<inst>-<begin>_<end>/` (`-2`, `-3` if it exists) holding `awr.json`.
   Exit 2 = not an AWR report; exit 3 = AWR in text format. On either, stop, explain, and ask for the HTML report
   (`@?/rdbms/admin/awrrpt.sql`, choose `html`). Never modify the input file.
2. Classify SQL: `python3 <skill>/scripts/classify_sql.py RUN [--config FILE]`
3. Analyze: `python3 <skill>/scripts/analyze.py RUN [--config FILE] [--comparison-mentioned]`
   - Add `--comparison-mentioned` when the user referred to an earlier good period ("yesterday was fine",
     "since the deploy", "last week it was fast").
   - Pass `--config FILE` if the user gave one or a YAML sits next to the AWR file (see Config).
   - Writes `findings.json`.
4. Interpret. Read `RUN/findings.json` and `<skill>/references/wait_events.md`. Write `RUN/interpretation.json`
   (schema and rules below). Do not read the raw AWR HTML.
5. Validate, then lint (both must exit 0):
   `python3 <skill>/scripts/validate_findings.py RUN`
   `python3 <skill>/scripts/lint_verify_sql.py --run RUN`
   `validate_findings` prints `ERROR <finding>.<field>: ...` lines; fix exactly those and rerun. At most 3 attempts.
   `WARN` lines are not failures. If `lint_verify_sql` reports a problem in the catalog itself (not in `RUN`),
   that is a skill bug: stop and report it, do not edit the SQL files to get past it.
6. Render: `python3 <skill>/scripts/render_report.py RUN [--config FILE]` -> `RUN/report.md`, `RUN/report.html`
   (one file, no scripts, no external resources).
7. AWR diff: `python3 <skill>/scripts/awr_diff_sql.py RUN`. It writes `awr_diff_<db>.sql` when `findings.json`
   has `diff.suggested`; otherwise it says why not. Version signatures live in `<skill>/references/awr_diff.md`.
8. Reply to the user with the executive summary (max 5 lines), the highest-severity findings by id and title,
   and the paths to `report.html`, `report.md`, `findings.json` and the diff script if written.
   Mention warnings from `findings.json` (`notes`, load status, comparison warnings) briefly.

## Reading findings.json

Each finding has: `id` (F01...), `domain`, `subject`, `title`, `severity` (P0 worst, P4 informational),
`confidence`, `triggered_by` (rule ids), `evidence` (list of `{path, value, ...}`), `verify_sql` (V-xx ids),
`fallback` (canned cause/impact/recommendation text used if you write nothing), and
`severity_adjustments` (why the script lowered or capped the severity, for example a volume cap when the
event share is high but latency is fine). Also read the top-level `load`, `notes`, `sql_summary` and `diff`.

Reflect `severity_adjustments` in your prose; never argue the severity is higher or lower than shown.
Do not re-derive severity, thresholds or categories.

## interpretation.json

```json
{
  "executive_summary": ["up to 5 plain-language lines"],
  "findings": {
    "F01": {
      "facts": "What the evidence shows, numbers only from this finding.",
      "hypothesis": "Possible cause. Say 'Hypothesis:' and name how V-xx would confirm it.",
      "user_impact": "What an application user would notice, no jargon.",
      "recommendation": "What to look at or change, in words.",
      "change_risk": "What could go wrong if the change is made.",
      "approval_required": true
    }
  }
}
```

Write an entry for every finding id. Use only these six fields, and only ids that exist in `findings.json`
(extra fields, unknown ids and wrong types are errors; `approval_required` is a JSON boolean).
Output valid JSON only (no comments, no trailing commas).

Rules the validator enforces:

- Every number in a finding's prose must come from that finding's `evidence` values (rounding to the precision you
  write is fine, 0.587 -> "0.59") or equal a value in `awr.json` exactly. Executive-summary numbers may round-match
  any finding's evidence. Do not compute new numbers (no sums, ratios, "twice as much", per-hour conversions).
  If a number is not in the evidence, describe it in words instead.
- Do not glue numbers to words or symbols: write "21.4 ms", not "x21.4" or "21.4x". Spell units out and keep
  one space between number and unit. Avoid ranges like "5-10" and counts you invented ("three sessions").
- Allowed identifiers (not treated as numbers): P0-P4, the finding's own V-xx ids, existing Fxx ids, real SQL IDs,
  snap ids, the exact release string.
- `facts` must not contain: because, due to, caused by, likely, probably, suggests. Causes go in `hypothesis`
  and are labeled as hypotheses. Use `wait_events.md` for common causes.
- `user_impact` is plain language: what slows down for whom, not event names or internals.
- No executable change commands (no `ALTER`, `DROP`, `EXEC`, `GRANT`, shell lines, hints to paste). Describe the
  change in prose and set `approval_required: true` for anything that changes the database, schema, parameters or
  application. Use `false` only for read-only checks.
- Keep the severity story consistent with `severity_adjustments` and `confidence`.
  If the storage profile was assumed (`notes`), say latency judgments rest on that assumption.
- If a section is missing from the report (`meta.missing_sections`), say it is absent; do not guess its content.

## Config (optional `--config my.yaml`)

| Key | Meaning |
|---|---|
| `user_schemas` | Schemas owned by the application (SQL classification) |
| `monitoring_schemas`, `monitoring_modules` | Monitoring/maintenance schemas and module names |
| `app_hosts`, `app_programs`, `app_table_prefixes` | Hints that mark application SQL |
| `storage_profile` | `hdd`, `san` or `flash` (default `san`, stated as an assumption in `notes`) |
| `audience` | `dba`, `user` or `both` (default `both`) |
| `thresholds` | Overrides merged over `references/thresholds.yaml` |

## Hard rules

- All numbers come from `awr.json` / `findings.json`. Never compute or invent numbers.
- Never connect to a database and never run SQL. The scripts do not either.
- Verification SQL comes only from `references/verify_sql/` (via the V-xx ids). Do not write your own SQL for the user.
- If a section is absent, say so; do not guess.
- Label every cause as a hypothesis. State facts and hypotheses separately.
- Never modify the input AWR file.
- Do not read the raw AWR HTML; use `awr.json` and `findings.json`.
- Do not execute or suggest running change commands; changes need the user's approval.
- If the period is under 10 minutes or over 120 minutes, or comparison periods differ by more than 20% in
  duration, restarted in between, or have a different CPU count, mark conclusions low confidence.

## v1 limits

- Single instance only. RAC reports set `meta.rac=true` and the report shows a banner; no RAC analysis is done.
- HTML input only (text AWR exits 3).
- English reports only (`lang=en`).
- Two-report comparison tables are not built; `awr_diff_sql.py` only generates the script for the DBA to run.
- Reports of about 20 MB parse in under a minute; larger files may be slower.
