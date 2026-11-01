import pytest

from common import read_json, write_json
from validate_findings import main, resolve, validate


def edit_findings(run, fn):
    d = read_json(run / "findings.json")
    fn(d)
    write_json(run / "findings.json", d)


def edit_interp(run, fid, field, val):
    d = read_json(run / "interpretation.json")
    d["findings"][fid][field] = val
    write_json(run / "interpretation.json", d)


def test_good_run_passes(good_run):
    assert validate(good_run) == ([], [])
    assert main([str(good_run)]) == 0


def test_resolve_bad_path(good_run):
    with pytest.raises(KeyError):
        resolve(read_json(good_run / "awr.json"), "fg_events[999].x")


def test_evidence_mismatch(good_run):
    edit_findings(good_run, lambda f: f["findings"][0]["evidence"][0].__setitem__("value", 99.99))
    assert any("evidence" in e for e in validate(good_run)[0])
    assert main([str(good_run)]) == 1


def test_missing_required_field(good_run):
    edit_findings(good_run, lambda f: f["findings"][0].pop("verify_sql"))
    assert any("verify_sql" in e for e in validate(good_run)[0])


def test_unknown_finding_and_extra_field(good_run):
    d = read_json(good_run / "interpretation.json")
    d["findings"]["F99"] = {"facts": ""}
    d["findings"]["F01"]["extra"] = "x"
    write_json(good_run / "interpretation.json", d)
    assert len(validate(good_run)[0]) == 2


def test_fabricated_number(good_run):
    edit_interp(good_run, "F01", "hypothesis", "Latency reached 21.4 ms during the batch.")
    assert any("21.4" in e for e in validate(good_run)[0])


def test_precision_match_passes(good_run):
    edit_interp(good_run, "F01", "hypothesis", "Latency is about 14.2 ms and 14 ms.")
    assert validate(good_run)[0] == []


def test_causal_word_in_facts(good_run):
    edit_interp(good_run, "F01", "facts", "Reads are slow because of storage")
    assert any("F01.facts" in e for e in validate(good_run)[0])
    edit_interp(good_run, "F01", "facts", "ok")
    edit_interp(good_run, "F01", "hypothesis", "Probably storage.")
    assert validate(good_run)[0] == []


def test_summary_too_long(good_run):
    d = read_json(good_run / "interpretation.json")
    d["executive_summary"] = ["line"] * 6
    write_json(good_run / "interpretation.json", d)
    assert any("executive_summary" in e for e in validate(good_run)[0])


def test_missing_interpretation_is_warning(good_run):
    d = read_json(good_run / "interpretation.json")
    del d["findings"]["F01"]
    write_json(good_run / "interpretation.json", d)
    errs, warns = validate(good_run)
    assert errs == [] and any("F01" in w for w in warns)
    assert main([str(good_run)]) == 0


def test_malformed_interpretation_reports_error(good_run):
    (good_run / "interpretation.json").write_text("{not json")
    errs, _ = validate(good_run)
    assert errs and "interpretation.json" in errs[0]
    (good_run / "interpretation.json").write_text("[1]")
    assert "interpretation.json" in validate(good_run)[0][0]


def test_wrong_types(good_run):
    edit_interp(good_run, "F01", "approval_required", "yes")
    edit_interp(good_run, "F01", "facts", 5)
    errs, _ = validate(good_run)
    assert any("approval_required" in e for e in errs) and any("F01.facts" in e for e in errs)


def test_all_verify_unavailable_warns(good_run):
    def f(d):
        for v in d["findings"][0]["verify_sql"]:
            v["available"] = False
    edit_findings(good_run, f)
    errs, warns = validate(good_run)
    assert errs == [] and any("verify_sql" in w for w in warns)


def _flagged(run, text, field="hypothesis"):
    edit_interp(run, "F01", field, text)
    return validate(run)[0]


@pytest.mark.parametrize("text,tok", [
    ("The count is 8123.", "8123"), ("Latency 14.87ms here", "14.87"), ("Delta of -5 seen", "-5"),
    ("Costs $9871 total", "9871"), ("Value 3.1E+08 bytes", "3.1E+08"), ("Elapsed 1,234.56 min", "1,234.56"),
])
def test_number_forms_flagged(good_run, text, tok):
    assert any(tok in e for e in _flagged(good_run, text))


def test_number_forms_in_awr_pass(good_run):
    assert _flagged(good_run, "Elapsed 1,439.65 min, also 1439.65.") == []
    d = read_json(good_run / "findings.json")
    d["findings"][0]["evidence"].append({"metric": "x", "value": 310000000, "section": "s", "path": "meta.elapsed_min"})
    write_json(good_run / "findings.json", d)  # path mismatch error expected; only check number errors
    assert not any("3.1E+08" in e for e in _flagged(good_run, "Value 3.1E+08 bytes"))


def test_identifier_smuggling_flagged(good_run):
    for text, tok in [("1234567890abc seen", "1234567890"), ("V-9871 seen", "9871"), ("V-4417.4 seen", "4417.4"),
                      ("7717.1.2.3 seen", "7717.1")]:
        assert any(tok in e for e in _flagged(good_run, text)), text


def test_real_identifiers_exempt(good_run):
    awr = read_json(good_run / "awr.json")
    f = read_json(good_run / "findings.json")["findings"][0]
    sid = next(s["sql_id"] for s in awr["sql"] if any(c.isdigit() for c in s["sql_id"]))
    t = f"P2 {f['verify_sql'][0]['id']} {sid} {awr['meta']['release']} F01"
    assert _flagged(good_run, t) == []


def test_summary_rounds_to_evidence_only(good_run):
    ev = [e["value"] for f in read_json(good_run / "findings.json")["findings"] for e in f["evidence"]]
    assert 35.63 in ev
    def summ(s):
        d = read_json(good_run / "interpretation.json")
        d["executive_summary"] = [s]
        write_json(good_run / "interpretation.json", d)
        return validate(good_run)[0]
    assert summ("about 35.6% of DB time") == []
    assert any("41.2" in e for e in summ("about 41.2% of DB time"))


REJECT = ["ALTER INDEX ix_a REBUILD;", "alter system set cursor_sharing=force", "```sql\nselect 1\n```",
          "Rebuild it.\nDone;", "alter system set sga_target=60G scope=spfile", "ALTER INDEX ord_call_events_ix1 REBUILD ONLINE",
          "alter user app identified by secret", "ALTER SESSION SET optimizer_mode = ALL_ROWS", "CREATE INDEX ix1 ON t(c)",
          "create table t_bak as select * from t", "DROP INDEX ix1", "TRUNCATE TABLE t", "GRANT SELECT ON app.t TO monitor",
          "grant dba to scott", "REVOKE SELECT ON app.t FROM monitor", "ALTER SYSTEM KILL SESSION '12,345'",
          "exec dbms_stats.gather_table_stats('A','B')", "BEGIN dbms_stats.gather_schema_stats('APP'); END;",
          "`ALTER SYSTEM SET x=1`"]
ACCEPT = ["grant time to the batch window", "Grant access to users is not needed; ask the DBA to revoke rights from batch",
          "Begin the review at the end; done.", "Alter table statistics were stale before the batch",
          "consider alter index rebuild during a maintenance window", "Alter the redo log size after change approval",
          "the index is dropped and recreated nightly by the job", "begin by checking V-07; then compare with V-03",
          "consider creating an index on APP_CALL_EVENTS", "rebuild the index during a maintenance window"]


@pytest.mark.parametrize("text", REJECT)
def test_change_command_rejected(good_run, text):
    edit_interp(good_run, "F01", "recommendation", text)
    assert any("F01.recommendation" in e and "executable change command" in e for e in validate(good_run)[0])
    d = read_json(good_run / "interpretation.json")
    d["executive_summary"] = [text]
    write_json(good_run / "interpretation.json", d)
    assert any("executive_summary" in e and "executable change command" in e for e in validate(good_run)[0])


@pytest.mark.parametrize("text", ACCEPT)
def test_change_prose_accepted(good_run, text):
    edit_interp(good_run, "F01", "recommendation", text)
    assert validate(good_run)[0] == []
