import json, shutil
from pathlib import Path
import pytest
from common import write_json
import analyze, classify_sql, render_report, team, validate_findings

FIXTURES = Path(__file__).parent / "fixtures"

def f(id, sev): return {"id": id, "severity": sev}

def entry(text="ok"):
    return {k: text for k in ("facts", "hypothesis", "user_impact", "recommendation", "change_risk")} | {"approval_required": True}

def planned(tmp_path, findings):
    write_json(tmp_path / "team_plan.json", team.plan(findings))
    return tmp_path

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
    awr = json.loads((run/"awr.json").read_text())  # the fixture's meta is too thin for the renderer
    awr["meta"] |= {"db_name": "X", "dbid": 1, "inst_num": 1, "rac": False, "restart": False, "cdb": False, "begin_snap": 1, "end_snap": 2, "begin_time": "t0", "end_time": "t1", "host": "h", "memory_gb": 64, "platform": "p"}
    write_json(run/"awr.json", awr)
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
