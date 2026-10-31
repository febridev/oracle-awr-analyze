import json, pytest
from bs4 import BeautifulSoup
from parse_awr import Doc, records, load_doc, extract_meta, to_iso, NotAwrError, UnsupportedFormatError

def test_header_matches_golden(sample_path):
    meta = extract_meta(load_doc(sample_path))
    golden = json.loads((sample_path.parent / "expected/header.json").read_text())
    assert {k: meta[k] for k in golden} == golden          # A1
    assert meta["decimal"] == "."

def test_not_awr_rejected(tmp_path):
    p = tmp_path / "x.html"; p.write_text("<html><body>hello</body></html>")
    with pytest.raises(NotAwrError, match="expected an HTML file produced by awrrpt.sql"): load_doc(p)

def test_text_awr_rejected(tmp_path):
    p = tmp_path / "x.txt"; p.write_text("WORKLOAD REPOSITORY report for\nDB Name ...")
    with pytest.raises(UnsupportedFormatError, match="text AWR not supported in v1; regenerate with awrrpt.sql in HTML"): load_doc(p)

def test_restart_detected(tmp_path, sample_path):
    html = sample_path.read_text().replace("20-Jul-26 15:40", "28-Oct-26 09:00", 1)
    p = tmp_path / "r.html"; p.write_text(html)
    assert extract_meta(load_doc(p))["restart"] is True

def test_unparseable_date_kept_raw():
    assert to_iso("2026/10/28 x") == "2026/10/28 x"
    assert to_iso("28-Oct 01:00:48", year_hint=2026) == "2026-10-28T01:00:48"

def test_meta_charset_sniffed(tmp_path):
    p = tmp_path / "c.html"
    p.write_bytes(b'<html><head><meta charset="windows-1252"></head><body><table>WORKLOAD REPOSITORY \x92</table>')
    assert "\u2019" in str(load_doc(p).soup)
    p.write_bytes(b'<html><head><meta content="text/html; charset=bogus-xx"></head><body><table>WORKLOAD REPOSITORY \xe9</table>')
    assert "\xe9" in str(load_doc(p).soup)

def test_heading_is_fallback_only():
    h = ('<h2>Host Information</h2><table summary="This table displays snapshot information"><tr><td>a</td></tr></table>'
         '<table summary="This table displays host information"><tr><td>b</td></tr></table>')
    d = Doc(BeautifulSoup(h, "html.parser"), "x")
    assert [t["summary"] for t in d.find_tables("host")] == ["This table displays host information"]
    assert len(d.find_tables("snapshot")) == 1

def test_records_dedupes_header_slugs():
    t = BeautifulSoup("<table><tr><th></th><th>Snap Id</th><th>% Total</th><th>% Total</th></tr>"
                      "<tr><td>a</td><td>1</td><td>2</td><td>3</td></tr></table>", "html.parser").table
    assert records(t) == [{"col0": "a", "snap_id": "1", "pct_total": "2", "pct_total_2": "3"}]
