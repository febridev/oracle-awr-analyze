from common import read_json


def test_sample_findings(sample_run):
    f = read_json(sample_run / "findings.json")
    sev = {(x["domain"], x["subject"]): x["severity"] for x in f["findings"]}
    assert sev[("io", "db file sequential read")] == "P2"          # 35.63% share, 0.587 ms → volume cap
    assert sev[("io", "direct path read")] == "P2"                 # 16.80% share, 14.19 ms < san warn 20 → cap
    assert sev[("redo", "log file sync")] == "P2"                  # ~12% share, latency fine
    assert sev[("redo", "log switches")] == "P2"                   # 24.13/h > 20
    assert sev[("events", "control file sequential read")] == "P3"
    assert sev[("parse", "execute to parse")] == "P3"              # 2.75 < 50, soft 99.98 > 95
    assert sev[("memory", "swap")] == "P3"                         # VM_OUT 27,025,408 B / 24 h ≈ 1.07 MB/h < 10
    assert sev[("monitoring", "monitoring overhead")] == "P2"
    assert f["sql_summary"]["monitoring_overhead_pct"] >= 11.18    # 85gj 7.47 + 2tc4 1.33 + cmu8 1.29 + 1f8d 1.09
    assert not any(x["severity"] in ("P0", "P1") for x in f["findings"])
    assert f["load"]["avg"]["status"] == "Normal" and f["load"]["peak"]["status"] == "Busy"
    assert f["diff"]["suggested"] is False
    assert all(x["evidence"] and x["verify_sql"] and x["triggered_by"] for x in f["findings"])   # A4
    assert any("storage" in n for n in f["notes"])                 # san assumed
    adj = {x["subject"]: x["severity_adjustments"] for x in f["findings"]}
    for ev in ("db file sequential read", "direct path read"):
        assert adj[ev] and "volume" in adj[ev][0]
    ctrl = next(x for x in f["findings"] if x["subject"] == "control file sequential read")
    assert all(v["available"] for v in ctrl["verify_sql"])
