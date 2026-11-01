import re
import subprocess
import sys

import pytest

from common import read_json, write_json
from render_report import mask_sql, render

SECTIONS = ["Executive summary", "Database and period", "Load health", "Findings", "Top SQL by category",
            "Domain detail", "Verification SQL", "AWR diff", "Appendix"]


@pytest.fixture
def rendered(good_run):
    return render(good_run, "both")


def _edit(run, name, fn):
    d = read_json(run / name)
    fn(d)
    write_json(run / name, d)


def test_sections_in_order(rendered):
    md, html = rendered
    idx = [md.index("## " + s) for s in SECTIONS]
    assert idx == sorted(idx)
    assert all(f"<h2>{s}</h2>" in html for s in SECTIONS)


def test_header_line(rendered):
    assert "DB: PRODDB (DBID 1234567890) | Instance 1 (single instance) | 19.0.0.0.0 | CDB: NO" in rendered[0]


def test_five_charts(rendered):
    assert rendered[1].count("<svg") == 5 and "RAC comparison not available" in rendered[1]


def test_no_external_refs(rendered):
    h = rendered[1]
    assert "<script" not in h and "<link" not in h
    assert not re.search(r'(src|href)\s*=\s*["\']?(https?:)?//', h)


def test_sql_text_escaped(good_run):
    _edit(good_run, "awr.json", lambda d: d["sql"][0].__setitem__("text", "select '<script>alert(1)</script>' from dual"))
    md, html = render(good_run, "dba")  # dba is unmasked, so the text reaches the table and appendix
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "`select '<script>" in md  # markdown keeps it inside a code span


def test_long_sql_truncated_in_tables(rendered):
    cells = re.findall(r"<td class=\"sql\">(.*?)</td>", rendered[1])
    assert cells and max(len(c) for c in cells) <= 260


def test_user_audience_masks_and_omits_dba(good_run):
    md, html = render(good_run, "user")
    assert "Verification SQL" not in md and "'2026-10-27 05:06:28'" not in html
    assert "Triggered by" not in md


def test_mask_sql():
    assert mask_sql("where a = 'x' and b = 42 and c = :1") == "where a = '?' and b = ? and c = :1"
    assert mask_sql("select t1.c2 from t1 where d = 'it''s' and e = 1.5") == "select t1.c2 from t1 where d = '?' and e = ?"


def test_fallback_label(good_run):
    (good_run / "interpretation.json").unlink()
    md, _ = render(good_run, "both")
    n = len(read_json(good_run / "findings.json")["findings"])
    assert md.count("standard explanation (not database-specific)") == n
    assert "None" not in md


def test_rejected_interpretation_uses_fallback(good_run):
    _edit(good_run, "interpretation.json", lambda d: d["findings"]["F01"].__setitem__("hypothesis", "Latency was 77777 ms."))
    md, _ = render(good_run, "both")
    f1 = md[md.index("[P2] db file sequential read takes"):]
    f1 = f1[:f1.index("\n### ")]
    assert "interpretation rejected by validator" in f1 and "77777" not in md
    assert md.count("interpretation rejected by validator") == 1


def test_malformed_interpretation_all_fallback(good_run):
    (good_run / "interpretation.json").write_text("{nope")
    md, _ = render(good_run, "both")
    assert md.count("interpretation rejected by validator") == len(read_json(good_run / "findings.json")["findings"])


def test_banners(good_run):
    _edit(good_run, "awr.json", lambda d: d["meta"].update(rac=True, restart=True))
    md, html = render(good_run, "both")
    assert "RAC sections not analyzed in v1" in md[:md.index("## Executive summary")]
    assert "Instance 1 (RAC, not analyzed)" in md
    assert "restart" in html[:html.index("<h2>")].lower()


def test_severity_text_not_only_color(rendered):
    assert "[P2]" in rendered[1]


def test_severity_adjusted_and_unavailable_verify(good_run):
    def f(d):
        d["findings"][0]["verify_sql"][0].update(available=False, sql=None, reason="needs Diagnostics Pack")
    _edit(good_run, "findings.json", f)
    md, _ = render(good_run, "both")
    assert "- Severity adjusted: capped to P2" in md and "not available (needs Diagnostics Pack)" in md
    assert not re.search(r"\bNone\b|\bnan\b", md)


def test_cli_writes_files(good_run):
    r = subprocess.run([sys.executable, "oracle-awr-analyzer/scripts/render_report.py", str(good_run)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (good_run / "report.md").exists() and (good_run / "report.html").exists()


# ---- fix round 1 ----
def _top(md):
    return md[:md.index("## Appendix")]


def test_header_lines_prd(rendered):
    md = rendered[0]
    assert "Host: db-host-01 | 34 CPU, 34 core, 125.63 GB | Linux x86 64-bit\n" in md
    assert ("Period: snap 3138-3162 | 2026-10-28 00:00:34 - 2026-10-29 00:00:13 | "
            "Elapsed 1439.65 min | DB Time 10255.65 min\n") in md


def test_short_period_and_parser_banners(good_run):
    _edit(good_run, "awr.json", lambda d: (d["meta"].update(elapsed_min=5, parser="html.parser (reduced accuracy)")))
    md, html = render(good_run, "both")
    top = md[:md.index("## Executive summary")]
    assert "short" in top.lower() and "reduced accuracy" in top
    assert html.count('class="banner"') == 2


def test_license_tag_per_query(good_run):
    _edit(good_run, "findings.json", lambda d: d["findings"][0]["verify_sql"][0].update(available=False, sql=None, reason="x"))
    md, _ = render(good_run, "both")
    assert "(license: diagnostics_pack)" in md[md.index("## Verification SQL"):md.index("## AWR diff")]
    assert "Diagnostics Pack license" in md


@pytest.mark.parametrize("sql,gone", [("select q'[it's secret]' from t", "secret"), ("x = q'{O'Brien}'", "Brien"),
                                      ("x = nq'#abc#'", "abc"), ("a = .5 and b = 1e5 and c = 0x1F", "5")])
def test_mask_sql_probes(sql, gone):
    out = mask_sql(sql)
    assert gone not in out and "?" in out


def test_mask_sql_exact():
    assert mask_sql("select q'[it's secret]' from t") == "select '?' from t"
    assert mask_sql("a = .5 and b = 1e5 and c = 0x1F and d = t1") == "a = ? and b = ? and c = ? and d = t1"


def test_none_sql_text(good_run):
    _edit(good_run, "awr.json", lambda d: d["sql"][0].__setitem__("text", None))
    for aud in ("user", "both", "dba"):
        render(good_run, aud)


def test_masking_by_audience(good_run):
    probe = "ORD_VERSION = 2"
    u, uh = render(good_run, "user")
    b, bh = render(good_run, "both")
    d, _ = render(good_run, "dba")
    assert probe not in u and probe not in uh and probe not in _top(b) and probe not in bh[:bh.index("<h2>Appendix")]
    assert probe in b[b.index("## Appendix"):] and probe in d and "ORD_VERSION = ?" in _top(b)
    assert d != b
    assert "## Executive summary" not in d


def test_latency_chart_pct_of_warn(rendered):
    h = rendered[1]
    i = h.index("direct path read<title>")
    seg = h[i:i + 400]
    assert "70.95" in h and "14.19 ms / warn 20 ms" in h and "warning threshold" in h and seg


def test_db_cpu_share_in_load_health(rendered):
    sec = rendered[0][rendered[0].index("## Load health"):rendered[0].index("## Findings")]
    assert "DB CPU share: 32.82 %" in sec


def test_appendix_contents(rendered):
    app = rendered[0][rendered[0].index("## Appendix"):]
    for t in ("Key metrics", "DB Time per second: 7.1", "Executes (SQL) per second", "Transactions per second",
              "Redo size (bytes) per second: 22388907.1", "Hard parses (SQL) per second", "Assumptions", "storage profile: san (assumed",
              "Logical CPUs: 34", "Hyperthreading", "Limitations", "single instance only", "RAC, PDB and Exadata are not analyzed",
              "HTML input only", "English only", "efficiency.library_hit_pct"):
        assert t in app, t
