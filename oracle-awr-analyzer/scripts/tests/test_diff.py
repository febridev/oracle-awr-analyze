import subprocess
import sys

import pytest
from awr_diff_sql import diff_sql, strip_sqlplus
from common import read_json, write_json
from lint_verify_sql import lint_query
from render_report import render

SCRIPT = "oracle-awr-analyzer/scripts/awr_diff_sql.py"


def test_diff_sql_contents(sample_awr):
    s = diff_sql(sample_awr["meta"])
    assert "SPOOL awr_diff_PRODDB.html" in s and "1234567890" in s
    assert "&base_begin_snap" in s and "&base_end_snap" in s and "3138" in s and "3162" in s
    lines = s.splitlines()
    sp = next(i for i, l in enumerate(lines) if l.startswith("SPOOL "))
    for t in ("SET VERIFY OFF", "SET ECHO OFF", "SET HEADING OFF", "SET FEEDBACK OFF",
              "ACCEPT base_begin_snap NUMBER", "ACCEPT base_end_snap NUMBER"):
        assert any(l.startswith(t) for l in lines[:sp]), t
    assert "SELECT *\n" in s and "SELECT output" not in s
    q = {"id": "diff", "title": "t", "domain": "d", "min_version": "12.1", "license": "diagnostics_pack",
         "sql": strip_sqlplus(s), "params": ["base_begin_snap", "base_end_snap"]}
    assert lint_query(q) == []


def test_hostile_input(sample_awr):
    m = dict(sample_awr["meta"], db_name="a b;\nHOST rm -rf /")
    assert "\nHOST" not in diff_sql(m) and "SPOOL awr_diff_abHOSTrmrf.html" in diff_sql(m)
    with pytest.raises(ValueError):
        diff_sql(dict(sample_awr["meta"], dbid="1 OR 1=1"))


def _run(run):
    return subprocess.run([sys.executable, SCRIPT, str(run)], capture_output=True, text=True)


def test_not_written_when_not_suggested(sample_run, good_run):
    r = _run(good_run)
    assert r.returncode == 0 and "diff not suggested" in r.stdout
    assert not list(good_run.glob("awr_diff_*.sql"))


def test_written_when_suggested(good_run):
    fj = read_json(good_run / "findings.json")
    fj["diff"] = {"suggested": True, "reasons": ["why not"]}
    write_json(good_run / "findings.json", fj)
    assert _run(good_run).returncode == 0
    assert (good_run / "awr_diff_PRODDB.sql").exists()


def test_report_section_8(good_run):
    fj = read_json(good_run / "findings.json")
    fj["diff"] = {"suggested": True, "reasons": ["Saturated but ordinary workload"]}
    write_json(good_run / "findings.json", fj)
    md, _ = render(good_run, "dba")
    sec = md.split("AWR diff")[-1].split("Appendix")[0]
    for t in ("Saturated but ordinary workload", "3138", "3162", "same weekday", "20%", "restart",
              "awr_diff_PRODDB.sql", "baseline snapshot IDs"):
        assert t in sec, t
