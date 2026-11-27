# AWR analyzer subagents: design

Date: 2026-11-06. Branch: `feat/subagent`.

## Goal

Step 4 of the skill (writing `interpretation.json`) is the only step where a model writes report content. Split it across subagents that check each other, and match the model to how hard each finding is.

Success means:

- Report quality is the same or better than a single agent's.
- Simple findings cost less than they do on the lead's model.
- Every interpretation is checked by an agent that did not write it.
- The person can watch the team work on the helper desk.

## Non-goals

- No change to steps 1-3 or to how numbers and severities are computed.
- No model picks or changes a severity.
- The skill does not require any mod.
- `helper-desk` is not shipped in this plugin, and no second pane is built.
- No per-environment model configuration. The tier table is a constant in `team.py`.
- The report does not show verifier verdicts.

## Flow

```
findings.json
   -> team.py plan                team_plan.json
   -> writers, in parallel        interpretation.part-<model>.json
   -> verifiers, in parallel      VERDICT lines returned to the lead
   -> on FAIL: one rewrite        interpretation.rewrite-<n>.json, then re-verified
   -> still FAIL: drop the entry  the report uses its standard text
   -> team.py merge               interpretation.json
   -> validate_findings.py, lint_verify_sql.py, render_report.py (unchanged)
```

The lead is the agent running the skill. It runs the scripts, starts the subagents, and reads each verdict. It writes no interpretation text unless the fallback applies.

## Routing

| Work | Writer | Verifier | Rewrite model |
|---|---|---|---|
| P0, P1 | opus | opus (a fresh agent) | opus |
| P2 | sonnet | sonnet | opus |
| P3, P4 | haiku | sonnet | sonnet |
| Executive summary | Writer of the highest-severity finding | That writer's verifier | That group's rewrite model |
| No findings at all | sonnet writes the summary only | sonnet | opus |

Tiers with no findings are left out, so a run with only P4 findings starts no Opus agent.

"Work together" means two things:

- Each verifier reviews another agent's text with no context from writing it.
- The summary's verifier reads every part file, so it checks that the summary and findings tell one story.

## Components

### `oracle-awr-analyzer/scripts/team.py`

Two commands, in the same CLI style and with the same exit codes as the other scripts (0 ok, 2 on an error, with the message on stderr).

`team.py plan RUN` reads `findings.json`, writes `RUN/team_plan.json` and prints it:

```json
{
  "groups": [
    {
      "writer_model": "opus",
      "verifier_model": "opus",
      "rewrite_model": "opus",
      "findings": ["F01", "F02"],
      "summary": true,
      "part": "interpretation.part-opus.json"
    }
  ]
}
```

`plan` also copies `SKILL.md` and `references/wait_events.md` into `RUN/team_refs/` (agents cannot read plugin paths outside the working folder). Agents read their rules only from there.

`team.py merge RUN [--drop ID ...]` builds `RUN/interpretation.json`:

- Reads each group's part file, then `interpretation.rewrite-<n>.json` files in numeric order. A later entry replaces an earlier one, so every entry must carry all six fields; a fix writes the complete entry, changing only the fields in question.
- `ID` is a finding id or `SUMMARY`. A dropped entry is left out, so the renderer uses its existing standard text (`_finding` fallback, `_summary`).
- Exit 2, naming the file, on invalid JSON, a finding entry missing any of the six fields, a finding id outside that file's group, or `executive_summary` in a part whose group has `summary: false`.
- A missing part file is a `WARN` line, not an error. Its findings fall back to standard text.

### `oracle-awr-analyzer/agents/awr-writer.md`

- Tools: `Read`, `Write`. Default model: `sonnet`.
- Writes only the part or rewrite file named in its prompt.
- Reads `RUN/findings.json` and `RUN/team_refs/wait_events.md`. Never reads the raw AWR HTML.
- Follows the `interpretation.json` rules in `RUN/team_refs/SKILL.md`. If a required file cannot be read it writes nothing and replies `BLOCKED: <file>`.

### `oracle-awr-analyzer/agents/awr-verifier.md`

- Tools: `Read` only, so it cannot edit what it reviews. Default model: `sonnet`.
- Checks the "You must self-check" list in `RUN/team_refs/SKILL.md`, and that each claim in `facts` traces to the finding's evidence.
- Does not repeat the number checks that `validate_findings.py` enforces.
- The final line of its answer is exactly `VERDICT: PASS` or `VERDICT: FAIL <ids>`, where ids may include `SUMMARY`. Reasons go above that line.

The lead sets each agent's model with the Agent tool's `model` parameter, using `team_plan.json`.

### `oracle-awr-analyzer/SKILL.md`

- Step 4 gains the team workflow, and the current single-agent text stays as the fallback.
- Step 8 tells the person which entries were dropped.
- The file stays within the 500-line limit that `test_skill_md.py` enforces.

### No manifest or mod in the plugin

`agents/*.md` are discovered without `.claude-plugin/plugin.json` (tested on Claude Code 2.1.319). Updates keep following commits, and the README's 2.1.275 minimum needs no change for a mod. Whether plugin agents work on 2.1.275 itself is not tested.

### `helper-desk` (separate mod, not in this repository)

It already shows each subagent's type, description, status, current tool, tool count and elapsed time. The lead gives agents descriptions such as `writer opus: P0-P1 + summary`, which carry no database names, hosts or SQL.

Two additions, both display only:

- Store the model from the `agent.spawn` result next to the desk, so a wrong route is visible.
- Read the final `VERDICT:` line from `classic.SubagentStop`'s `last_assistant_message` and show `PASS` or `FAIL`.

Both follow the mod's existing rule that a failure in the view never affects the agent. The AWR skill must produce the same report with this mod absent.

## Failure handling

| Failure | Behaviour |
|---|---|
| Verifier returns FAIL | One rewrite by a fresh writer on the rewrite model, then one re-verify |
| Still FAIL after the rewrite | `--drop` the entry. Report uses standard text, and the final reply names it |
| Verifier answer has neither a `VERDICT:` nor a `BLOCKED:` line | Treated as FAIL |
| Validator `ERROR` lines | A writer on that group's rewrite model fixes only those fields, writing the complete entry, in a new rewrite file, then merge and validate rerun. The existing limit of 3 attempts still applies. These fixes are not re-verified |
| Agent tool absent, or `oracle-awr-analyzer:awr-writer` not listed | Single-agent step 4, as today |
| `RUN` outside the working folder | Single-agent fallback (agents would be denied the files) |
| A verifier cannot be spawned, or replies `BLOCKED: <file>` with no `VERDICT:` | Not a rejection: keep the text, skip rewrite and drop, and the final reply names those entries as unverified |
| A model cannot be spawned, or a writer fails | The lead writes that group itself under the single-agent rules, and a verifier checks it unless that verifier is itself unavailable (then unverified, previous row) |
| Session forces one model on all subagents | Accepted. Routing has no effect, and the workflow still runs |

## Trust boundaries

- **Enforced by tools:** the verifier cannot write.
- **Instruction only:** the writer's single output file and the rule against reading raw HTML, because the agent's tool list cannot limit paths.
- **Enforced by scripts:** `team.py merge` rejects out-of-scope ids, and `validate_findings.py` remains the hard check on numbers and change commands.
- **Restricted data:** AWR content stays with the same model provider as today. No agent or the helper desk adds a new destination. Tests and end-to-end runs use only synthetic fixtures.

## Testing

- `scripts/tests/test_team.py`:
  - Routing on existing fixtures: `io_bound` (one P0 finding) and `idle` (no findings).
  - Synthetic findings: an all-P4 run, and mixed tiers.
  - Merge: rewrite precedence, `--drop`, `SUMMARY`, out-of-scope id, invalid JSON, missing part.
  - The merged output of a valid plan passes `validate_findings.py`, and the render falls back correctly for dropped entries.
- `test_skill_md.py`: `team.py` and the fallback are documented.
- `helper-desk`: extend `register.test.tsx` for model and verdict, then run `claude plugin test` and `claude plugin validate`.
- One end-to-end run on a synthetic fixture, with subagents and the helper desk open. This uses model tokens, and shows routing and the rewrite path by observation, not by automated test.

## Documentation

The README's "How it works" section and install notes describe the team, the fallback, and that the helper desk is optional.
