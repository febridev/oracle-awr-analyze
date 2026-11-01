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
