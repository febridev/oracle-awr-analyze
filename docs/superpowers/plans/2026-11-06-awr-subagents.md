# AWR Analyzer Subagents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split step 4 of the AWR skill across writer and verifier subagents routed by severity, with the team visible on the helper desk.

**Architecture:** `team.py` makes routing and merging deterministic. Two plugin agent definitions do the writing and checking, and `SKILL.md` tells the lead how to run them. `helper-desk` stays a separate, display-only mod outside this repository.

**Tech Stack:** Python 3.9+, pytest, Claude Code plugin agents, Claude Code mods (TSX).

**Spec:** `docs/superpowers/specs/2026-11-06-awr-subagents-design.md`

## Global Constraints

- Tier table, as a constant in `team.py`: P0/P1 writer opus, verifier opus, rewrite opus. P2 writer sonnet, verifier sonnet, rewrite opus. P3/P4 writer haiku, verifier sonnet, rewrite sonnet. No findings: sonnet writes the summary, verifier sonnet, rewrite opus.
- No change to steps 1-3, to severities, to `validate_findings.py` or to `render_report.py`.
- Team scripts use exit 0 for success and exit 2 for errors, with the message on stderr and never a traceback.
- The AWR skill must produce a report with no mod present and with no subagents available.
- Never commit, paste or run the restricted AWR sample. `local-samples/` stays out of tests and end-to-end runs.
- Agent descriptions carry only role, model, severity range and `summary`. Never database names, hosts or SQL.
- Do not add `.claude-plugin/plugin.json` or hooks to `oracle-awr-analyzer/`.
- `helper-desk` is edited only at `/Users/febridev/.claude/dev-mods/29a446b9-28ef-498e-b9bb-d7d5c793498f/helper-desk`, and nothing from it enters this repository.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.
- `PY` means `/private/tmp/claude-501/-Users-febridev-personal-awr-skill-feat/29a446b9-28ef-498e-b9bb-d7d5c793498f/scratchpad/venv/bin/python`, a scratch venv that already has the requirements and pytest.

## Review Focus

- A person reruns step 4 in the same `RUN` after changing config: stale part, rewrite and `interpretation.json` files must not leak into the new result (Task 1).
- The lead mistypes an id in `--drop`: merge must fail, not keep the failing text in the report (Task 2).
- A run reaches ten or more rewrites: `rewrite-10` must apply after `rewrite-2` (Task 2).
- A writer emits malformed JSON or the wrong shape: merge exits 2, names the file, and leaves any existing `interpretation.json` untouched (Task 2).
- A verifier's reasons quote `VERDICT: PASS` above a different final line: the desk must not show a verdict (Task 4).

---

### Task 1: `team.py plan`

**Files:**
- Create: `oracle-awr-analyzer/scripts/team.py`
- Create: `oracle-awr-analyzer/scripts/tests/test_team.py`
- Add to the commit: `docs/superpowers/specs/2026-11-06-awr-subagents-design.md`, `docs/superpowers/plans/2026-11-06-awr-subagents.md`

**Interfaces:**
- Consumes: `common.read_json`, `common.write_json`, `classify_sql.main(argv)`, `analyze.main(argv)`.
- Produces:
  - `plan(findings: list[dict]) -> dict`, raising `ValueError` on an unknown severity.
  - `main(argv=None) -> int`, with subcommand `plan RUN`.
  - `RUN/team_plan.json` in the spec's schema: `{"groups": [{writer_model, verifier_model, rewrite_model, findings, summary, part}]}`, ordered opus, sonnet, haiku.

- [ ] **Step 1: Write the failing tests in `test_team.py`**

```python
import json, shutil
from pathlib import Path
import pytest
import analyze, classify_sql, team

FIXTURES = Path(__file__).parent / "fixtures"

def f(id, sev): return {"id": id, "severity": sev}

def fixture_run(tmp_path, name):
    run = tmp_path / name
    run.mkdir()
    shutil.copy(FIXTURES / f"{name}.json", run / "awr.json")
    assert classify_sql.main([str(run)]) == 0 and analyze.main([str(run)]) == 0
    return run

def test_plan_routes_by_severity():
    g = team.plan([f("F01","P0"), f("F02","P1"), f("F03","P2"), f("F04","P3"), f("F05","P4")])["groups"]
    assert [(x["writer_model"], x["verifier_model"], x["rewrite_model"], x["findings"], x["summary"], x["part"]) for x in g] == [
        ("opus", "opus", "opus", ["F01","F02"], True, "interpretation.part-opus.json"),
        ("sonnet", "sonnet", "opus", ["F03"], False, "interpretation.part-sonnet.json"),
        ("haiku", "sonnet", "sonnet", ["F04","F05"], False, "interpretation.part-haiku.json")]

def test_plan_all_p4_starts_no_opus():
    g = team.plan([f("F01","P4")])["groups"]
    assert [(x["writer_model"], x["verifier_model"], x["summary"]) for x in g] == [("haiku", "sonnet", True)]

def test_plan_summary_follows_highest_severity():
    g = team.plan([f("F01","P3"), f("F02","P2")])["groups"]
    assert [(x["writer_model"], x["summary"]) for x in g] == [("sonnet", True), ("haiku", False)]

def test_plan_no_findings_is_sonnet_summary_only():
    assert team.plan([])["groups"] == [{"writer_model": "sonnet", "verifier_model": "sonnet", "rewrite_model": "opus",
        "findings": [], "summary": True, "part": "interpretation.part-sonnet.json"}]

def test_plan_unknown_severity_names_finding():
    with pytest.raises(ValueError, match="F01"):
        team.plan([f("F01","P9")])

@pytest.mark.parametrize("name,model,ids", [("io_bound", "opus", ["F01"]), ("idle", "sonnet", [])])
def test_cli_plan_on_fixtures(tmp_path, name, model, ids):
    run = fixture_run(tmp_path, name)
    assert team.main(["plan", str(run)]) == 0
    g = json.loads((run / "team_plan.json").read_text())["groups"][0]
    assert (g["writer_model"], g["findings"]) == (model, ids)

def test_plan_clears_stale_outputs(tmp_path):
    run = fixture_run(tmp_path, "io_bound")
    stale = ["interpretation.part-haiku.json", "interpretation.rewrite-1.json", "interpretation.json"]
    for n in stale: (run / n).write_text("{}")
    assert team.main(["plan", str(run)]) == 0
    assert not any((run / n).exists() for n in stale) and (run / "findings.json").exists()

def test_cli_plan_missing_findings_exits_2(tmp_path, capsys):
    assert team.main(["plan", str(tmp_path)]) == 2
    assert "error:" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest oracle-awr-analyzer/scripts/tests/test_team.py -q`
Expected: collection error, `No module named 'team'`.

- [ ] **Step 3: Implement `plan` and `main` in `team.py`**

Follow the CLI style of `awr_diff_sql.py`: a module docstring, `argparse` with `plan` as a subparser, `sys.exit(main())`. Catch `OSError, ValueError, KeyError, AttributeError, TypeError` and report `error: ...` on stderr with exit 2. A missing subcommand is argparse's own exit behaviour.

Before writing `team_plan.json`, delete `interpretation.json`, `interpretation.part-*.json` and `interpretation.rewrite-*.json` from `RUN`, and only those. Print the plan as JSON.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PY -m pytest oracle-awr-analyzer/scripts/tests/test_team.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add oracle-awr-analyzer/scripts/team.py oracle-awr-analyzer/scripts/tests/test_team.py docs/superpowers/specs/2026-11-06-awr-subagents-design.md docs/superpowers/plans/2026-11-06-awr-subagents.md
git commit -m "feat: team.py plan routes AWR findings to model tiers"
```

### Task 2: `team.py merge`

**Files:**
- Modify: `oracle-awr-analyzer/scripts/team.py`
- Modify: `oracle-awr-analyzer/scripts/tests/test_team.py`

**Interfaces:**
- Consumes: `team_plan.json` from Task 1, `validate_findings.main(argv)`, `render_report.main(argv)`.
- Produces:
  - `merge(run: Path, team_plan: dict, drop: list[str]) -> tuple[dict, list[str]]`, returning the interpretation and `WARN` lines, raising `ValueError` naming the file.
  - CLI `team.py merge RUN [--drop ID ...]`, which writes `RUN/interpretation.json`, prints `WARN` lines, then the path.

- [ ] **Step 1: Write the failing tests, appended to `test_team.py`**

Use a helper `entry(text="ok")` returning all five prose fields of `interpretation.json` set to `text`, plus `approval_required: True`, and a helper `planned(tmp_path, findings)` that writes `team_plan.json` from `team.plan(findings)` and returns the run folder.

```python
def test_merge_combines_parts_and_summary(tmp_path):
    run = planned(tmp_path, [f("F01","P0"), f("F02","P3")])
    (run/"interpretation.part-opus.json").write_text(json.dumps({"executive_summary": ["s"], "findings": {"F01": entry()}}))
    (run/"interpretation.part-haiku.json").write_text(json.dumps({"findings": {"F02": entry()}}))
    assert team.main(["merge", str(run)]) == 0
    d = json.loads((run/"interpretation.json").read_text())
    assert d["executive_summary"] == ["s"] and set(d["findings"]) == {"F01","F02"}

def test_merge_rewrites_apply_in_numeric_order(tmp_path):
    run = planned(tmp_path, [f("F01","P0")])
    for n, t in [(10, "ten"), (2, "two")]:
        (run/f"interpretation.rewrite-{n}.json").write_text(json.dumps({"findings": {"F01": entry(t)}}))
    (run/"interpretation.part-opus.json").write_text(json.dumps({"findings": {"F01": entry("first")}}))
    assert team.main(["merge", str(run)]) == 0
    assert json.loads((run/"interpretation.json").read_text())["findings"]["F01"]["facts"] == "ten"

def test_merge_drop_finding_and_summary(tmp_path):
    run = planned(tmp_path, [f("F01","P0"), f("F02","P0")])
    (run/"interpretation.part-opus.json").write_text(json.dumps({"executive_summary": ["s"], "findings": {"F01": entry(), "F02": entry()}}))
    assert team.main(["merge", str(run), "--drop", "F01", "SUMMARY"]) == 0
    d = json.loads((run/"interpretation.json").read_text())
    assert "executive_summary" not in d and set(d["findings"]) == {"F02"}

@pytest.mark.parametrize("name,body,needle", [
    ("interpretation.part-opus.json", "{not json", "part-opus"),
    ("interpretation.part-opus.json", "[]", "part-opus"),
    ("interpretation.part-opus.json", '{"findings": []}', "part-opus"),
    ("interpretation.part-opus.json", '{"findings": {"F02": {}}}', "F02"),
    ("interpretation.part-haiku.json", '{"executive_summary": ["s"]}', "part-haiku"),
    ("interpretation.rewrite-1.json", '{"findings": {"F99": {}}}', "F99"),
    ("interpretation.rewrite-x.json", "{}", "rewrite-x"),
])
def test_merge_bad_input_exits_2_and_keeps_output(tmp_path, capsys, name, body, needle):
    run = planned(tmp_path, [f("F01","P0"), f("F02","P3")])
    (run/"interpretation.json").write_text("KEEP")
    (run/name).write_text(body)
    assert team.main(["merge", str(run)]) == 2
    assert needle in capsys.readouterr().err and (run/"interpretation.json").read_text() == "KEEP"

def test_merge_unknown_drop_id_exits_2(tmp_path, capsys):
    run = planned(tmp_path, [f("F01","P0")])
    assert team.main(["merge", str(run), "--drop", "F99"]) == 2
    assert "F99" in capsys.readouterr().err and not (run/"interpretation.json").exists()

def test_merge_missing_part_warns(tmp_path, capsys):
    run = planned(tmp_path, [f("F01","P0"), f("F02","P3")])
    (run/"interpretation.part-opus.json").write_text(json.dumps({"findings": {"F01": entry()}}))
    assert team.main(["merge", str(run)]) == 0
    assert "WARN" in capsys.readouterr().out

def test_merge_without_plan_exits_2(tmp_path):
    assert team.main(["merge", str(tmp_path)]) == 2

def test_merged_output_validates_and_dropped_entries_fall_back(tmp_path):
    run = fixture_run(tmp_path, "io_bound")
    assert team.main(["plan", str(run)]) == 0
    (run/"interpretation.part-opus.json").write_text(json.dumps({"executive_summary": ["TEAM SUMMARY LINE"], "findings": {"F01": entry("TEAM FACT")}}))
    assert team.main(["merge", str(run)]) == 0
    assert validate_findings.main([str(run)]) == 0 and render_report.main([str(run)]) == 0
    md = (run/"report.md").read_text()
    assert "TEAM SUMMARY LINE" in md and "TEAM FACT" in md
    assert team.main(["merge", str(run), "--drop", "F01", "SUMMARY"]) == 0
    assert render_report.main([str(run)]) == 0
    md = (run/"report.md").read_text()
    assert "TEAM SUMMARY LINE" not in md and "standard explanation" in md
```

Add `validate_findings, render_report` to the test module's imports.

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest oracle-awr-analyzer/scripts/tests/test_team.py -q`
Expected: the new tests fail, and Task 1's tests still pass.

- [ ] **Step 3: Implement `merge` and the `merge` subcommand**

Rules, in order:

1. Read every planned part that exists. A missing one adds `WARN <part>: missing, its findings use standard text`.
2. In a part, a finding id outside its group is an error, and so is `executive_summary` when the group has `summary: false`.
3. Apply `interpretation.rewrite-<n>.json` in numeric order, using `re.fullmatch(r"interpretation\.rewrite-(\d+)\.json", name)`. Any other `interpretation.rewrite-*.json` name is an error. A rewrite may hold any planned finding id and `executive_summary`, and its entries replace earlier ones.
4. `--drop` accepts planned finding ids and `SUMMARY`. Anything else is an error, and dropped entries are removed last.
5. Write `interpretation.json` only after all of this succeeds. Omit `executive_summary` when there is none.

Files must be JSON objects, and `findings` must be an object. Do not check entry contents, because `validate_findings.py` owns that.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PY -m pytest oracle-awr-analyzer/scripts/tests/test_team.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add oracle-awr-analyzer/scripts/team.py oracle-awr-analyzer/scripts/tests/test_team.py
git commit -m "feat: team.py merge builds interpretation.json from agent parts"
```

### Task 3: Agents, skill workflow and README

**Files:**
- Create: `oracle-awr-analyzer/agents/awr-writer.md`
- Create: `oracle-awr-analyzer/agents/awr-verifier.md`
- Modify: `oracle-awr-analyzer/SKILL.md` (steps 4, 5 and 8)
- Modify: `README.md` ("How it works", install notes, "Repository layout")
- Test: `oracle-awr-analyzer/scripts/tests/test_skill_md.py`

**Interfaces:**
- Consumes: the `team.py plan` and `team.py merge` CLI, and the `team_plan.json` fields from Tasks 1-2.
- Produces:
  - Agent types `oracle-awr-analyzer:awr-writer` and `oracle-awr-analyzer:awr-verifier`.
  - The verifier's final line, exactly `VERDICT: PASS` or `VERDICT: FAIL <ids>`.
  - The description format `<writer|verifier|rewriter> <model>: <severity range>[ + summary]`, for example `writer opus: P0-P1 + summary`.

- [ ] **Step 1: Write the failing tests, appended to `test_skill_md.py`**

```python
def _front(name):
    lines = (SKILL_DIR / "agents" / f"{name}.md").read_text().split("---")[1].strip().splitlines()
    return dict(l.split(": ", 1) for l in lines)

def test_agent_definitions():
    w, v = _front("awr-writer"), _front("awr-verifier")
    assert (w["name"], w["tools"], w["model"]) == ("awr-writer", "Read, Write", "sonnet")
    assert (v["name"], v["tools"], v["model"]) == ("awr-verifier", "Read", "sonnet")
    assert "VERDICT: PASS" in (SKILL_DIR / "agents/awr-verifier.md").read_text()

def test_skill_md_team_workflow_and_fallback():
    text = (SKILL_DIR / "SKILL.md").read_text()
    for s in ["team.py plan", "team.py merge", "oracle-awr-analyzer:awr-writer", "oracle-awr-analyzer:awr-verifier",
              "VERDICT:", "--drop", "writer opus: P0-P1 + summary", "Single-agent fallback"]:
        assert s in text

def test_plugin_has_no_manifest_or_hooks():
    assert not (SKILL_DIR / ".claude-plugin").exists() and not (SKILL_DIR / "hooks").exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest oracle-awr-analyzer/scripts/tests/test_skill_md.py -q`
Expected: the three new tests fail, and the existing ones pass.

- [ ] **Step 3: Write `agents/awr-writer.md`**

Frontmatter: `name`, a one-line `description`, `tools: Read, Write`, `model: sonnet`. The body says:

- The prompt supplies the skill folder, `RUN`, the finding ids, whether to write the summary, and the output file.
- Read `RUN/findings.json`, `<skill>/SKILL.md` ("Reading findings.json" and "interpretation.json") and `<skill>/references/wait_events.md`.
- Never read the raw AWR HTML. Never change another file.
- Write only `{"findings": {...}}`, plus `executive_summary` when asked, to the named output file.
- On a rewrite, the prompt also gives the reasons or validator `ERROR` lines to fix.

- [ ] **Step 4: Write `agents/awr-verifier.md`**

Frontmatter: `name`, a one-line `description`, `tools: Read`, `model: sonnet`. The body says:

- The prompt supplies the skill folder, `RUN`, the ids, the file to review and, when the group has the summary, every part file.
- Check the "You must self-check" list in `SKILL.md`, and that each `facts` claim traces to that finding's evidence.
- For the summary, check that it and the findings tell one story.
- Do not repeat the validator's number checks. Do not rewrite anything.
- State reasons by id, then end with a final line that is exactly `VERDICT: PASS` or `VERDICT: FAIL <ids>` (ids may include `SUMMARY`).

- [ ] **Step 5: Update `SKILL.md`**

Keep the current step 4 text under a `Single-agent fallback` heading. Use the fallback when the Agent tool is absent or `oracle-awr-analyzer:awr-writer` is not listed. New step 4:

1. `python3 <skill>/scripts/team.py plan RUN`.
2. Start one `awr-writer` per group in parallel, with `model` set to `writer_model`, and the description format above. Never put database names, hosts or SQL in a description.
3. Once all writers finish, start one `awr-verifier` per group in parallel, with `model` set to `verifier_model`. The group with `summary: true` also gets every part file.
4. A missing `VERDICT:` line counts as FAIL.
5. For each failed id, start one fresh `awr-writer` per group on `rewrite_model`, writing `interpretation.rewrite-<n>.json` with `n` unique per rewrite, then verify again once.
6. Ids that still fail are passed to `python3 <skill>/scripts/team.py merge RUN --drop <ids>`. Otherwise run merge with no `--drop`. Exit 2 is a problem to fix, not to skip.
7. If a model cannot be spawned or a writer fails, write that group yourself under the same rules and write its part file, then verify it as usual.

Step 5 gains: validator `ERROR` lines go to a writer on that group's `rewrite_model` in a new rewrite file, then rerun merge with the same `--drop` and validate. These fixes count toward the existing 3 attempts and are not re-verified.

Step 8 gains: name any dropped entries and say they use standard text.

- [ ] **Step 6: Update `README.md`**

- "How it works": replace the single AI-writes line with the team flow, and include the tier table.
- Say a run falls back to one agent when subagents are unavailable, including a manual `cp -r` copy to `~/.claude/skills/`.
- Say the helper desk is an optional mod that is not part of this plugin, and the report is the same without it.
- "Repository layout": add `agents/` and `team`.

- [ ] **Step 7: Run all tests and the validators**

Run: `PY -m pytest -q`, then `claude plugin validate .`
Expected: no test failures (skips are the restricted-sample tests), and `✔ Validation passed`.

- [ ] **Step 8: Commit**

```bash
git add oracle-awr-analyzer/agents oracle-awr-analyzer/SKILL.md oracle-awr-analyzer/scripts/tests/test_skill_md.py README.md
git commit -m "feat: writer and verifier agents with team workflow in SKILL.md"
```

### Task 4: Model and verdict on the helper desk

Everything in this task happens in `/Users/febridev/.claude/dev-mods/29a446b9-28ef-498e-b9bb-d7d5c793498f/helper-desk`, written `$DESK` below. It is not in this repository, so there is no commit.

**Files:**
- Modify: `$DESK/types/index.d.ts` (`Desk`)
- Modify: `$DESK/hooks/register.tsx` (`doing`, the `agent.spawn` hook, a new `classic.SubagentStop` hook)
- Test: `$DESK/hooks/register.test.tsx`

**Interfaces:**
- Consumes: `AgentSpawnResult.model` and `.agentId`, and `classic.SubagentStop`'s `agent_id` and `last_assistant_message`. The verdict format comes from Task 3.
- Produces: `Desk.model?: string` and `Desk.verdict?: string`, shown by `doing(desk)`.

- [ ] **Step 1: Write the failing tests, appended to `register.test.tsx`**

```tsx
const stop = (last: string) => ({ agent_id: 'a1', agent_type: 'x', stop_hook_active: false, agent_transcript_path: '/t', last_assistant_message: last })

test('a desk shows its model and a final-line verdict', async ($, on) => {
  on('clock.now', () => ({ value: 5000 }))
  on('agent.list', () => ({ value: [AGENT] }))
  on('agent.spawn', () => ({ model: 'haiku', agentId: 'a1' }))
  on('classic.SubagentStop', () => ({}))
  await $.agent.spawn({ prompt: 'go', description: AGENT.description, subagentType: 'Explore' })
  await $.classic.SubagentStop(stop('F03 is off\nVERDICT: FAIL F03 SUMMARY\n'))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const row = await ui.find({ type: 'Text', text: /Explore/ })
  expect(row?.text).toContain('haiku')
  expect(row?.text).toContain('FAIL F03 SUMMARY')
  await ui.unmount()
})

test('a verdict quoted above the final line is not a verdict', async ($, on) => {
  on('clock.now', () => ({ value: 5000 }))
  on('agent.list', () => ({ value: [AGENT] }))
  on('agent.spawn', () => ({ model: 'haiku', agentId: 'a1' }))
  on('classic.SubagentStop', () => ({}))
  await $.agent.spawn({ prompt: 'go', description: AGENT.description, subagentType: 'Explore' })
  await $.classic.SubagentStop(stop('VERDICT: PASS\nbut I changed my mind'))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /Explore/ }))?.text).not.toContain('PASS')
  await ui.unmount()
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `claude plugin test $DESK`
Expected: the two new tests fail, and the 2 existing tests pass.

- [ ] **Step 3: Implement**

- Add `model?: string` and `verdict?: string` to `Desk`. They are optional, so the stored `desks-1` shape stays valid.
- In `agent.spawn`, after `sync`, when `started` has a `model` and an `agentId`, store the model on that desk.
- Add `on('classic.SubagentStop', ...)`. Take the last non-empty line of `last_assistant_message`, match `/^VERDICT: (PASS|FAIL\b.*)$/`, and store capture group 1 as the verdict. Always `return next(e)`.
- Wrap both stores in `.catch(() => {})`, as the mod's other hooks do.
- `doing(desk)` joins status or tool, tool count, model and verdict, leaving out empty values.

- [ ] **Step 4: Run tests and validate**

Run: `claude plugin test $DESK`, then `claude plugin validate $DESK`
Expected: 4 pass, 0 fail, and `✔ Validation passed`. The mod reloads by itself when the turn ends.

### Task 5: End-to-end run on a synthetic fixture

This uses model tokens and changes no repository code unless it finds a defect. A defect found here goes back to the task that owns it, with a test.

**Files:**
- Scratch only: `/private/tmp/claude-501/-Users-febridev-personal-awr-skill-feat/29a446b9-28ef-498e-b9bb-d7d5c793498f/scratchpad/e2e`

**Interfaces:**
- Consumes: everything above.
- Produces: a report on the verified result, for the person.

- [ ] **Step 1: Build the run**

In the scratch `e2e` folder, copy `tests/fixtures/cpu_bound.json` to `awr.json`, then run `classify_sql.py` and `analyze.py`. In this scratch `findings.json` only, change `F03`'s severity from `P2` to `P3`, so all three tiers run.
Expected: `F01` P1, `F02` P2, `F03` P3.

- [ ] **Step 2: Run the workflow headless**

With the venv's `bin` first on `PATH`, from the scratch folder:

`claude --plugin-dir /Users/febridev/personal/awr-skill-feat/oracle-awr-analyzer --model sonnet --permission-mode acceptEdits --allowedTools "Bash(python3:*)" --output-format stream-json --verbose -p "<run skill steps 4 to 6 on e2e, whose steps 1 to 3 are done>" > e2e.jsonl`

- [ ] **Step 3: Verify the result**

From `e2e.jsonl`, list each Agent call's `subagent_type`, `model` and `description`.
Expected:
- Writers on opus, sonnet and haiku.
- Verifiers on opus and sonnet, with the haiku group's verifier on sonnet.
- No database names, hosts or SQL in descriptions.
- `validate_findings.py e2e` exits 0, and `report.html` and `report.md` exist.

Report any rewrite or dropped entry. Do not force a failure to exercise that path. If none occurs, say that path was covered only by Task 2's tests.

- [ ] **Step 4: Run the final suite**

Run: `PY -m pytest -q`, `claude plugin validate .`, `claude plugin test $DESK`, then `git status --short`
Expected: no failures, and a clean working tree. Changes in `$DESK` don't appear because it's outside this repository.

- [ ] **Step 5: Ask the person to watch it on the desk**

Give them this command to run in a separate terminal from the scratch folder, because this session's installed plugin is not the branch version:

`claude --plugin-dir /Users/febridev/personal/awr-skill-feat/oracle-awr-analyzer --plugin-dir /Users/febridev/.claude/dev-mods/29a446b9-28ef-498e-b9bb-d7d5c793498f/helper-desk`

Then ask them to request skill steps 4 to 6 on `e2e`, and confirm that the desk shows each agent's model and verdict. The AWR parser needs HTML, so a JSON fixture starts from step 4.
