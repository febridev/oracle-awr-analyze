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
