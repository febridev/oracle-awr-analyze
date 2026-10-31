import copy

from analyze import diff_decision, finalize, load_status
from conftest import fixture
from lint_verify_sql import load_catalog
from common import SKILL_DIR

CAT = load_catalog(SKILL_DIR / "references/verify_sql")


def awr(**meta):
    m = {"dbid": 7, "inst_num": 1, "begin_snap": 10, "end_snap": 11, "release": "19.0.0.0.0", "elapsed_min": 60,
         "restart": False, "cpus": 8, "cores": 8, "db_time_min": 600}
    m.update(meta)
    return {"meta": m, "fg_events": [{"event": "db file sequential read"}], "sql": [], "top_events": []}


def cand(rule="io.seq_read.crit", sev="P1", subject="db file sequential read", domain="io", pct=20.0, lb=True, lat=None):
    return {"domain": domain, "subject": subject, "title": "t", "rule": rule, "severity": sev, "load_based": lb,
            "evidence": [{"metric": "m", "value": 1, "section": "s", "path": f"p.{rule}"}], "pct_db_time": pct,
            "latency_rule": lat is not None, "latency_ok": lat}


def L_for(a):
    return {"idle": False, "avg": {"status": "Busy"}, "peak": None, "basis": "avg", "notes": []}


def fin(cands, cfg, a=None, L=None):
    a = a or awr()
    return finalize(cands, a, L or L_for(a), cfg, CAT)


def test_merge_takes_highest(cfg):
    f = fin([cand("io.seq_read.warn", "P2"), cand("event.share.p1", "P1")], cfg)
    assert len(f) == 1 and f[0]["severity"] == "P1" and f[0]["id"] == "F01"
    assert set(f[0]["triggered_by"]) == {"io.seq_read.warn", "event.share.p1"}


def test_below_1pct_dropped(cfg):
    assert fin([cand("event.share.p2", "P2", pct=0.7)], cfg) == []


def test_idle_cap(cfg):
    a = fixture("idle")
    a["meta"].update({k: v for k, v in awr()["meta"].items() if k not in a["meta"]})
    L = load_status(a, cfg["thresholds"])
    assert L["idle"]
    f = fin([cand("io.seq_read.crit", "P1"), cand("config.param.p4", "P4", "x", "config", None, False)], cfg, a, L)
    by = {x["subject"]: x for x in f}
    assert (by["db file sequential read"]["severity"], by["db file sequential read"]["confidence"]) == ("P3", "low")
    assert by["x"]["severity"] == "P4" and by["x"]["confidence"] != "low"


def test_short_elapsed_cap(cfg):
    f = fin([cand()], cfg, awr(elapsed_min=8))
    assert (f[0]["severity"], f[0]["confidence"]) == ("P3", "low")


def test_volume_cap(cfg):
    f = fin([cand("event.share.p1", "P1", pct=35, lat=True)], cfg)
    assert f[0]["severity"] == "P2"


def test_sql_escalation(cfg):
    a = awr()
    a["sql"] = [{"sql_id": "abcdefghij123", "by_user_io": {"pct_total": 5}, "category": "user_app", "confidence": "high"}]
    sq = cand("sql.single.p3", "P3", "abcdefghij123", "sql")
    f = fin([sq, cand("io.seq_read.crit", "P1")], cfg, a)
    assert {x["subject"]: x["severity"] for x in f}["abcdefghij123"] == "P2"
    f = fin([sq], cfg, a)  # no I/O event past critical → no escalation
    assert f[0]["severity"] == "P3"


def test_restart_lowers_confidence(cfg):
    assert fin([cand()], cfg, awr(restart=True))[0]["confidence"] == "low"


def test_verify_sql_filled_and_version_gate(cfg):
    c = cand("monitoring.overhead.p2", "P2", "monitoring overhead", "monitoring", 12.0, False)
    c["evidence"][0]["path"] = "x"
    f = fin([c], cfg, awr(release="11.2.0.4.0"))[0]
    v = {q["id"]: q for q in f["verify_sql"]}
    assert v["V-02"]["available"] is False and v["V-02"]["sql"] is None and v["V-02"]["reason"]
    f = fin([cand()], cfg)[0]
    v07 = next(q for q in f["verify_sql"] if q["id"] == "V-07")
    assert v07["available"] and "&" not in v07["sql"] and "db file sequential read" in v07["sql"]
    assert f["fallback"]["cause"]


def test_fill_failure_marks_unavailable(cfg):
    a = awr(dbid="x; drop")
    q = fin([cand()], cfg, a)[0]["verify_sql"]
    assert all(not x["available"] and x["sql"] is None and x["reason"] for x in q)


def test_diff_rules(cfg):
    idle = {"idle": True, "avg": {"status": "Idle"}, "peak": None, "basis": "avg"}
    a = awr()
    assert diff_decision([], idle, a, cfg, False)["suggested"] is False
    assert diff_decision([], idle, a, cfg, True)["suggested"] is True
    busy = {"idle": False, "avg": {"status": "Busy"}, "peak": None, "basis": "avg"}
    p1 = [{"severity": "P1", "domain": "io"}]
    assert diff_decision(p1, busy, a, cfg, False)["suggested"] is True
    assert diff_decision([{"severity": "P1", "domain": "config"}], busy, a, cfg, False)["suggested"] is False
    sat = dict(busy, avg={"status": "Saturated"})
    assert diff_decision([], sat, a, cfg, False)["suggested"] is True
    c2 = copy.deepcopy(cfg)
    c2["workload_norms"] = {"executes_per_s": 100}
    a["load_profile"] = {"executes_sql": {"per_s": 500}}
    assert diff_decision([], sat, a, c2, False)["suggested"] is False


def test_quiet_period_blocks_escalation_and_lowers_all_load_based(cfg):
    a = fixture("idle")
    a["meta"].update({k: v for k, v in awr()["meta"].items() if k not in a["meta"]})
    a["sql"] = [{"sql_id": "abcdefghij123", "by_user_io": {"pct_total": 5}, "category": "user_app", "confidence": "high"}]
    L = load_status(a, cfg["thresholds"])
    f = fin([cand("sql.single.p3", "P3", "abcdefghij123", "sql"), cand("io.seq_read.crit", "P1"),
             cand("event.share.p2", "P3", "other", "events")], cfg, a, L)
    by = {x["subject"]: x for x in f}
    assert (by["abcdefghij123"]["severity"], by["abcdefghij123"]["confidence"]) == ("P3", "low")
    assert by["other"]["confidence"] == "low"
    assert by["db file sequential read"]["severity_adjustments"]


def test_severity_adjustments_recorded(cfg):
    f = fin([cand("event.share.p1", "P1", pct=35, lat=True)], cfg)[0]
    assert "volume" in f["severity_adjustments"][0]
    assert fin([cand("config.param.p4", "P4", "x", "config", None, False)], cfg)[0]["severity_adjustments"] == []
