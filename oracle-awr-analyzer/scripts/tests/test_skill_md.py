from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]


def test_skill_md():
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert text.startswith("---\nname: oracle-awr-analyzer\n") and len(text.splitlines()) <= 500
    for script in ["parse_awr.py", "classify_sql.py", "analyze.py", "validate_findings.py",
                   "lint_verify_sql.py", "render_report.py", "awr_diff_sql.py", "--comparison-mentioned"]:
        assert script in text


def test_skill_md_validator_sections():
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert "The validator checks" in text and "You must self-check" in text and "Say 'Hypothesis:'" not in text


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


def test_agents_read_rules_from_team_refs():
    for n in ("awr-writer", "awr-verifier"):
        t = (SKILL_DIR / "agents" / f"{n}.md").read_text()
        assert "RUN/team_refs/" in t and "<skill>" not in t
    assert "team_refs" in (SKILL_DIR / "SKILL.md").read_text()
