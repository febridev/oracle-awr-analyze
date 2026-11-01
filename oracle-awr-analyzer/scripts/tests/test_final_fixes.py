import json
import subprocess
import sys
from pathlib import Path

import pytest

from common import read_json, write_json
from conftest import fixture, run_parse
from render_report import render

SCRIPTS = Path(__file__).resolve().parents[1]


def _py(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True)


def _pipeline(tmp_path, html):
    src = tmp_path / "in.html"
    src.write_text(html)
    rc, run = run_parse(src, tmp_path / "out")
    assert rc == 0
    for s in ("classify_sql.py", "analyze.py"):
        assert _py(s, run).returncode == 0
    return run


# --- item 2: PRD sentences
def test_sample_report_has_bottleneck_and_peak_lines(sample_run):
    md, _ = render(sample_run, "both")
    assert "Wait-bound: the top wait class (" in md
    assert "Recommendation: run an hourly AWR for 2026-10-28 22:00:00 – 2026-10-28 23:00:00 (peak) to analyse its cause separately." in md


@pytest.mark.parametrize("b,text", [
    ("cpu_bound", "CPU-bound: look for the SQL with the most CPU and excessive parsing."),
    ("mixed", "Mixed: both CPU and waits contribute significantly."),
    ("non_db_host_load", "Non-database load on the host: another process is using CPU; check at OS level."),
    ("db_not_problem", "The database is not the problem in this period."),
    (None, "Bottleneck could not be determined (load inputs unavailable).")])
def test_bottleneck_sentences(good_run, b, text):
    d = read_json(good_run / "findings.json")
    d["load"]["bottleneck"] = b
    write_json(good_run / "findings.json", d)
    assert text in render(good_run, "both")[0]


def test_idle_report_says_no_problem(good_run):
    d = read_json(good_run / "findings.json")
    d["load"]["idle"], d["load"]["peak"], d["findings"] = True, None, []
    write_json(good_run / "findings.json", d)
    (good_run / "interpretation.json").unlink()
    md = render(good_run, "both")[0]
    assert md.count("No performance problem in this period.") >= 2
    assert "at peak" not in md and "unavailable" not in md.split("## Findings")[0].split("## Load health")[0]
    assert md.split("## Executive summary\n\n")[1].startswith("- No performance problem in this period.")


# --- item 3
def test_volume_capped_findings_have_v08_v09_and_wait_evidence(sample_run):
    fs = {f["subject"]: f for f in read_json(sample_run / "findings.json")["findings"]}
    for ev in ("db file sequential read", "direct path read"):
        v = {x["id"]: x for x in fs[ev]["verify_sql"]}
        assert v["V-08"]["available"] and v["V-09"]["available"]
        assert any(e["path"].endswith(".avg_wait_ms") for e in fs[ev]["evidence"])
    assert "V-10" in [x["id"] for x in fs["log file sync"]["verify_sql"]]


# --- item 4
def test_missing_sections_and_peak_notes_visible(tmp_path, sample_path):
    html = sample_path.read_text()
    for s in ("top ADDM findings", "Foreground Wait Events"):
        assert 'summary="This table displays ' + s in html
        html = html.replace('summary="This table displays ' + s, 'summary="This table displays zzz')
    run = _pipeline(tmp_path, html)
    notes = read_json(run / "findings.json")["notes"]
    assert any(n.startswith("Sections missing from this AWR:") for n in notes)
    assert "Peak unavailable: Top ADDM Findings absent; severity uses the period average." in notes
    md = render(run, "both")[0]
    head = md.split("## Findings")[0]
    assert "Sections missing from this AWR:" in head and "Peak unavailable" in head


# --- item 5
def test_classify_cli_config_error_clean(sample_run, tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("storage_profile: tape\n")
    r = _py("classify_sql.py", sample_run, "--config", cfg)
    assert r.returncode == 2 and "Traceback" not in r.stderr and "storage_profile" in r.stderr


# --- item 6
def test_addm_row_without_aas_ignored(sample_awr, cfg):
    import copy
    from analyze import load_status
    awr = copy.deepcopy(sample_awr)
    awr["addm"][0]["avg_active_sessions"] = None
    assert load_status(awr, cfg["thresholds"])["avg"]["aas"] is not None
    for r in awr["addm"]:
        r["avg_active_sessions"] = None
    assert load_status(awr, cfg["thresholds"])["peak"] is None


# --- item 7
def test_diff_duration_override_used(good_run):
    d = read_json(good_run / "findings.json")
    d["diff"] = {"suggested": True, "reasons": ["x"]}
    write_json(good_run / "findings.json", d)
    from common import load_config
    c = load_config(None)
    c["thresholds"]["diff"]["duration_diff_pct"] = 37
    assert "by more than 37%" in render(good_run, "both", c)[0]


# --- item 8
def test_cli_errors_are_clean(tmp_path):
    r = _py("parse_awr.py", tmp_path / "nope.html")
    assert r.returncode == 1 and "cannot read" in r.stderr and "Traceback" not in r.stderr
    empty, bad = tmp_path / "empty", tmp_path / "bad"
    empty.mkdir()
    bad.mkdir()
    for n in ("awr.json", "findings.json"):
        (bad / n).write_text("{not json")
    for s in ("classify_sql.py", "analyze.py", "validate_findings.py", "render_report.py", "awr_diff_sql.py"):
        for d in (empty, bad):
            r = _py(s, d)
            assert r.returncode not in (0, None) and "Traceback" not in r.stderr, (s, d, r.stderr)


# --- item 9
def test_module_in_code_span(good_run):
    d = read_json(good_run / "awr.json")
    d["sql"][0]["module"] = "x<script>`y"
    write_json(good_run / "awr.json", d)
    md, html = render(good_run, "both")
    assert "`x<script>'y`" in md and "<script>" not in html


# --- item 10
def test_unknown_latency_not_volume_capped(sample_awr, cfg):
    import copy
    from analyze import all_candidates, finalize, load_status
    from lint_verify_sql import load_catalog
    from common import SKILL_DIR
    awr = copy.deepcopy(sample_awr)
    for e in awr["fg_events"]:
        if e["event"] == "db file sequential read":
            e["avg_wait_ms"] = None
    L = load_status(awr, cfg["thresholds"])
    fs = finalize(all_candidates(awr, L, cfg), awr, L, cfg, load_catalog(SKILL_DIR / "references" / "verify_sql"))
    f = next(x for x in fs if x["subject"] == "db file sequential read")
    assert not any("volume" in a for a in f["severity_adjustments"])
