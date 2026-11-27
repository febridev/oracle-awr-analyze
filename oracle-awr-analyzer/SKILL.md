---
name: oracle-awr-analyzer
description: Analyze an Oracle AWR report (HTML, single instance) and produce a findings report with severity P0-P4 as Markdown and a self-contained visual HTML file. Use when the user attaches an AWR file, mentions AWR or awrrpt, or reports that an Oracle database is slow and asks for a performance analysis.
---

# Oracle AWR Analyzer

Scripts compute every number and every severity. Your job is to run them in order, then have the
plain-language interpretation (`interpretation.json`) written, by the agent team or the single-agent fallback,
and shown next to the evidence in the report.
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
4. Interpret with a team of agents. Start by running (it deletes old `interpretation.json`, part and rewrite files in `RUN`):
   `python3 <skill>/scripts/team.py plan RUN` -> `RUN/team_plan.json`, a list of `groups`, each with `writer_model`,
   `verifier_model`, `rewrite_model`, `findings` (ids), `summary` (true for one group) and `part` (its output file).
   It also copies `SKILL.md` and `references/wait_events.md` into `RUN/team_refs/`, because agents cannot read plugin paths.
   Agent prompts give `RUN` (absolute), never `<skill>`. `RUN` must be under the working folder, since background agents
   cannot ask for access to other paths. If `RUN` is not under the working folder, use the single-agent fallback.
   If the Agent tool is absent or `oracle-awr-analyzer:awr-writer` is not listed, use the single-agent fallback below.
   - Writers: start one `oracle-awr-analyzer:awr-writer` per group, all in parallel, with `model` set to `writer_model`.
     The agents start cold, so each prompt must give the absolute `RUN`, the ids, whether to write the
     summary, and the output file `RUN/<part>`. Description format: `<writer|verifier|rewriter> <model>: <severity range>[ + summary]`,
     for example `writer opus: P0-P1 + summary`. Never put database names, hosts or SQL in a description.
   - Verifiers: once all writers finish, start one `oracle-awr-analyzer:awr-verifier` per group in parallel, with
     `model` set to `verifier_model`, `RUN`, the ids and the part file. The group with `summary: true` also gets every part file.
     Each ends with `VERDICT: PASS` or `VERDICT: FAIL <ids>`; a reply with neither a `VERDICT:` nor a final `BLOCKED:` line counts as FAIL for that group.
     A verifier that cannot be spawned or replies `BLOCKED:` is not a rejection: keep the text, skip the rewrite and
     drop, and name those entries as unverified in the step 8 reply.
   - Rewrites: for each group with failed ids, start one fresh `awr-writer` on `rewrite_model`, given the failed ids, the reviewer's
     reasons, the file holding the current text (the part file, or the latest rewrite file for those ids; the writer reads it and
     writes each id's complete six-field entry) and the output file `RUN/interpretation.rewrite-<n>.json` (`n` unique per rewrite, so the file name never repeats).
     Set the summary flag when `SUMMARY` failed. Then verify again, once: the re-verify prompt names the rewrite file as the
     text to judge (the summary group also gets the rewrite files, besides every part file).
   - Merge: ids that still fail go to `python3 <skill>/scripts/team.py merge RUN --drop <ids>` (RUN first; `--drop` takes
     every argument after it; `SUMMARY` is a valid id). If nothing failed, run merge with no `--drop`. It writes
     `RUN/interpretation.json` and prints `WARN` lines. Exit 2 is a problem to fix (message on stderr), not to skip.
   - If a model cannot be spawned, a writer fails or a writer replies `BLOCKED:`, write that group yourself under the same rules, save it as that
     group's part file, and verify it as usual.

   Single-agent fallback: read `RUN/findings.json` and `<skill>/references/wait_events.md`. Write `RUN/interpretation.json`
   (schema and rules below). Do not read the raw AWR HTML.
5. Validate, then lint (both must exit 0):
   `python3 <skill>/scripts/validate_findings.py RUN`
   `python3 <skill>/scripts/lint_verify_sql.py --run RUN`
   `validate_findings` prints `ERROR <finding>.<field>: ...` lines; fix exactly those and rerun. At most 3 attempts; after 3 failed attempts, render anyway - findings whose
   interpretation is rejected use the standard text.
   An `ERROR` on `executive_summary` is fixed the same way, with the summary flag set.
   With the team: send the `ERROR` lines to an `awr-writer` on that group's `rewrite_model`, writing a new
   `interpretation.rewrite-<n>.json` with the complete entry of each fixed id (the prompt names the file holding the current
   text; only the fields in the errors change), then rerun `team.py merge RUN` with the same `--drop`
   and validate again. These fixes count toward the 3 attempts and are not re-verified.
   `WARN` lines are not failures. If `lint_verify_sql` reports a problem in the catalog itself (not in `RUN`),
   that is a skill bug: stop and report it, do not edit the SQL files to get past it.
6. Render: `python3 <skill>/scripts/render_report.py RUN [--config FILE]` -> `RUN/report.md`, `RUN/report.html`
   (one file, no scripts, no external resources).
7. AWR diff: `python3 <skill>/scripts/awr_diff_sql.py RUN`. It writes `awr_diff_<db>.sql` when `findings.json`
   has `diff.suggested`; otherwise it prints "diff not suggested" (the reasons are in `findings.json` `diff`). Version signatures live in `<skill>/references/awr_diff.md`.
8. Reply to the user with the executive summary (max 5 lines), the highest-severity findings by id and title,
   and the paths to `report.html`, `report.md`, `findings.json` and the diff script if written.
   Name any entries dropped with `--drop`, entries with a missing part (merge `WARN`) and entries left unverified, and say
   they use standard text or are unverified. If `SUMMARY` was dropped, use the report's summary.
   Mention warnings from `findings.json` (`notes`, load status, comparison warnings) briefly. Relay the report's
   bottleneck sentence and, when a peak exists, its "run an hourly AWR for <peak window>" recommendation.

## Reading findings.json

Each finding has: `id` (F01...), `domain`, `subject`, `title`, `severity` (P0 worst, P4 informational),
`confidence`, `triggered_by` (rule ids), `evidence` (list of `{path, value, ...}`), `verify_sql` (objects with `id`, `title`, `sql`, `license`, `available`, `params`; use `available` before citing one),
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
      "hypothesis": "Possible cause and how V-xx would confirm it. Do not write the label; the report adds 'Hypothesis:'.",
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

The validator checks (errors fail validation):

- Number provenance. Every number in a finding's prose must come from that finding's `evidence` values (rounding to
  the precision you write is fine, 0.587 -> "0.59") or equal a value in `awr.json` exactly. Executive-summary numbers
  may round-match any finding's evidence. A "number not found" error usually means a derived, rounded-too-far or glued
  number (sums, ratios, "twice as much", per-hour conversions, "21.4x"). Describe it in words or use the exact value.
  Write "21.4 ms", not "x21.4"; keep one space between number and unit.
- Allowed identifiers (not treated as numbers): P0-P4, the finding's own V-xx ids, existing Fxx ids, real SQL IDs,
  snap ids, the exact release string.
- `facts` must not contain: because, due to, caused by, likely, probably, suggests. Causes go in `hypothesis`.
- Only the six fields above, with the right types (`approval_required` is a JSON boolean), only existing finding ids.
- `executive_summary` has at most 5 lines.
- No executable change commands in any prose or in the summary: `ALTER SYSTEM/SESSION/INDEX/TABLE/DATABASE ...`,
  `CREATE INDEX x ON ...`, `DROP`, `TRUNCATE TABLE`, `GRANT/REVOKE`, `KILL SESSION`, `EXEC pkg.proc`, `BEGIN ... END;`,
  code fences, backtick-quoted SQL keywords, or any line ending in `;`. Describe changes in prose instead
  ("consider creating an index on the table", "rebuild the index in a maintenance window").
- A finding with no entry is only a WARN: the report falls back to standard text. Write an entry for every finding anyway.

You must self-check (not machine-enforced):

- Do not write the "Hypothesis:" label yourself; the renderer adds it.
- Cite only your own finding's V-xx ids.
- `user_impact` is plain language: what slows down for whom, not event names or internals.
- All fields non-empty. `approval_required` is `true` for any configuration, parameter, index, schema or application
  change; `false` only for read-only checks.
- Keep the story consistent with `severity_adjustments` and `confidence`. If the storage profile was assumed
  (`notes`), say latency judgments rest on that assumption.
- If a section is missing (top-level `missing_sections` in `awr.json`, also summarized in `notes`), say it is absent; do not guess its content.

## Config (optional `--config my.yaml`)

| Key | Meaning |
|---|---|
| `user_schemas` | Schemas owned by the application (SQL classification) |
| (all lists below) | DBA lists are literal names, case-insensitive, not regexes; paste modules as shown in AWR, e.g. `dbspicao11@host (TNS V1-V3)`; empty entries are ignored |
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
