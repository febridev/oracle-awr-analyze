import json, pytest
from parse_awr import load_doc, extract_meta, to_iso, NotAwrError, UnsupportedFormatError

def test_header_matches_golden(sample_path):
    meta = extract_meta(load_doc(sample_path))
    golden = json.loads((sample_path.parent / "expected/header.json").read_text())
    assert {k: meta[k] for k in golden} == golden          # A1
    assert meta["decimal"] == "."

def test_not_awr_rejected(tmp_path):
    p = tmp_path / "x.html"; p.write_text("<html><body>hello</body></html>")
    with pytest.raises(NotAwrError): load_doc(p)

def test_text_awr_rejected(tmp_path):
    p = tmp_path / "x.txt"; p.write_text("WORKLOAD REPOSITORY report for\nDB Name ...")
    with pytest.raises(UnsupportedFormatError): load_doc(p)

def test_restart_detected(tmp_path, sample_path):
    html = sample_path.read_text().replace("20-Jul-26 15:40", "28-Oct-26 09:00", 1)
    p = tmp_path / "r.html"; p.write_text(html)
    assert extract_meta(load_doc(p))["restart"] is True

def test_unparseable_date_kept_raw():
    assert to_iso("2026/10/28 x") == "2026/10/28 x"
    assert to_iso("28-Oct 01:00:48", year_hint=2026) == "2026-10-28T01:00:48"
