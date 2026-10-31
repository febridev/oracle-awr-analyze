import pytest

from analyze import all_candidates, idle_event, load_status
from conftest import fixture, max_by_subject, mini_awr


def run(awr, cfg):
    L = load_status(awr, cfg["thresholds"])
    return all_candidates(awr, L, cfg)


@pytest.mark.parametrize("name,expect", [
    ("cpu_bound", {("cpu", "host cpu"): "P1", ("sql", "cpubound0sql1"): "P2"}),
    ("io_bound", {("io", "db file sequential read"): "P0"}),
    ("commit_heavy", {("redo", "log file sync"): "P1"}),
    ("parse_heavy", {("parse", "soft parse"): "P2", ("parse", "hard parse rate"): "P2", ("parse", "mutex"): "P1"}),
    ("lock_contention", {("concurrency", "concurrency waits"): "P1"}),
])
def test_scenarios_candidates(name, expect, cfg):
    got = max_by_subject(run(fixture(name), cfg))
    for k, sev in expect.items():
        assert got[k] == sev


def test_idle_fixture_has_no_load_findings(cfg):
    assert not [c for c in run(fixture("idle"), cfg) if c["load_based"]]


def test_idle_events_never_candidates(cfg):
    awr = fixture("idle")
    awr["fg_events"][0]["pct_db_time"] = 80
    assert run(awr, cfg) == []
    assert idle_event("Streams AQ: waiting", cfg["thresholds"]) and not idle_event("log file sync", cfg["thresholds"])
    awr["fg_events"] = [{"event": "weird wait", "pct_db_time": 50, "avg_wait_ms": 1.0}]
    awr["top_events"] = [{"event": "weird wait", "wait_class": "Idle"}]
    assert run(awr, cfg) == []


def test_storage_profile_changes_io(sample_awr, cfg):
    def multi(profile):
        c = dict(cfg, storage_profile=profile)
        got = [x for x in run(sample_awr, c) if x["subject"] == "direct path read" and x["rule"].startswith("io.multi_read")]
        return got
    assert multi("san") == []
    assert [x["rule"] for x in multi("flash")] == ["io.multi_read.warn"]
    share = [x for x in run(sample_awr, cfg) if x["subject"] == "direct path read" and x["rule"].startswith("event.share")]
    assert share[0]["latency_rule"] is True and share[0]["latency_ok"] is True and share[0]["domain"] == "io"


def test_sample_candidates(sample_awr, cfg):
    import classify_sql
    for s in sample_awr["sql"]:
        s.update(classify_sql.classify(s, sample_awr["meta"]["host"], cfg["classification"]))
    got = run(sample_awr, cfg)
    d = sample_awr["derived"]
    assert d["swap_out_mb_per_hour"] == 1.07
    assert max_by_subject(got)[("memory", "swap")] == "P3"
    assert max_by_subject(got)[("redo", "log switches")] == "P2"
    assert [c["rule"] for c in got if c["subject"] == "control file sequential read"] == ["event.ctrlfile.p3"]
    assert max_by_subject(got)[("io", "db file sequential read")] == "P1"


def test_evidence_paths_resolve(sample_awr, cfg):
    import re
    for c in run(sample_awr, cfg):
        for e in c["evidence"]:
            cur = sample_awr
            for key, idx in re.findall(r"([^.\[\]]+)|\[(\d+)\]", e["path"]):
                cur = cur[int(idx)] if idx else cur[key]
            assert cur == e["value"], e


def test_zero_db_time_is_idle(cfg):
    L = load_status(mini_awr(10, 60, 0, 50, 50), cfg["thresholds"])
    assert L["avg"]["status"] == "Idle" and L["notes"] == []


def test_cpu_load_uses_peak_window_idle(cfg):
    a = mini_awr(10, 300, 300, 80, 80)  # avg ratio 0.1, avg idle 80
    a["addm"] = [{"avg_active_sessions": 15.0, "begin_time": "2026-01-01T01:00:00", "end_time": "2026-01-01T02:00:00"}]
    a["os_detail"] = [{"snap_time": "2026-01-01T02:00:00", "busy_pct": 95, "iowait_pct": 1, "idle_pct": 5}]
    L = load_status(a, cfg["thresholds"])
    assert L["basis"] == "peak" and L["peak"]["os_idle_pct"] == 5
    from analyze import rules_cpu
    c = [x for x in rules_cpu(a, L, cfg["thresholds"], "san") if x["subject"] == "host cpu"]
    assert [x["rule"] for x in c] == ["cpu.load.p1"] and c[0]["evidence"][1]["path"] == "os_detail[0].idle_pct"


def test_resmgr_shares_event_subject(cfg):
    a = fixture("idle")
    a["fg_events"] = [{"event": "resmgr:cpu quantum", "pct_db_time": 8, "avg_wait_ms": 5.0}]
    got = [c for c in run(a, cfg) if "resmgr" in c["subject"]]
    assert {c["rule"] for c in got} == {"cpu.resmgr.p2", "event.share.p2"}
    assert {(c["domain"], c["subject"]) for c in got} == {("events", "resmgr:cpu quantum")}


def test_config_param_fires_on_presence(cfg):
    a = fixture("idle")
    a["parameters"] = {"_fix_control": {"begin": "x", "end": None}, "cursor_sharing": {"begin": "FORCE", "end": None},
                       "db_block_size": {"begin": "8192", "end": None}}
    got = [c for c in run(a, cfg) if c["domain"] == "config"]
    assert sorted(c["subject"] for c in got) == ["_fix_control", "cursor_sharing"]
    assert got[0]["evidence"][0]["path"].endswith(".begin")


def test_sum_rules_exclude_generic_share(cfg):
    got = run(fixture("lock_contention"), cfg)
    assert not [c for c in got if c["subject"].startswith("enq:")]
    ev = [c for c in got if c["domain"] == "concurrency"][0]["evidence"]
    assert ev[0]["path"] == "fg_events[0].pct_db_time" and ev[-1]["path"] == "derived.concurrency_pct"
    a = fixture("idle")
    a["fg_events"] = [{"event": "SQL*Net message to client", "pct_db_time": 12, "avg_wait_ms": 0.1}]
    assert [(c["domain"], c["subject"], c["severity"]) for c in run(a, cfg)] == [("network", "sql*net client", "P3")]
