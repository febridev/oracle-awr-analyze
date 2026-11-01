import json
import shutil

import pytest

from common import read_json, write_json
from test_golden import sample_run  # noqa: F401  (session fixture)
from validate_findings import main, resolve, validate


@pytest.fixture
def good_run(sample_run, tmp_path):  # noqa: F811
    run = tmp_path / "run"
    shutil.copytree(sample_run, run)
    fs = read_json(run / "findings.json")["findings"]
    interp = {"executive_summary": ["The database is Normal on average."], "findings": {}}
    for f in fs:
        e = f["evidence"][0]
        v = e["value"]
        facts = f"{f['title']}: {e['metric']} was {v}." if isinstance(v, (int, float)) else f"{f['title']}."
        interp["findings"][f["id"]] = {"facts": facts, "hypothesis": "", "user_impact": "", "recommendation": "",
                                       "change_risk": "", "approval_required": False}
    write_json(run / "interpretation.json", interp)
    return run


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


def test_identifier_numbers_exempt(good_run):
    edit_interp(good_run, "F01", "hypothesis", "P2 V-07 F03 mon000000001a 19.0.0.0.0 3138")
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
