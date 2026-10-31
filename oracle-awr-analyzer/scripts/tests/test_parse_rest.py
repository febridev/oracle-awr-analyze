from parse_awr import split_module


def test_sql_merge_and_module_split(sample_awr):
    s = {r["sql_id"]: r for r in sample_awr["sql"]}["mon000000001a"]
    assert s["program"] == "dbspicao11" and s["client_host"] == "db-host-01"
    assert s["by_elapsed"]["pct_total"] == 7.47 and s["by_elapsed"]["executions"] == 2690
    assert s["text"].lower().startswith("select a.file_name")   # full text from Complete List
    assert {r["sql_id"]: r for r in sample_awr["sql"]}["app000000001a"]["by_user_io"]["pct_total"] == 8.37


def test_split_module():
    assert split_module("APPSRV@h1 (TNS V1-V3)") == {"program": "APPSRV", "client_host": "h1"}
    assert split_module("DBMS_SCHEDULER") == {"program": None, "client_host": None}
    assert split_module(None) == {"program": None, "client_host": None}


def test_peak_sources(sample_awr):
    assert max(a["avg_active_sessions"] for a in sample_awr["addm"]) == 31.79
    top = max(sample_awr["addm"], key=lambda a: a["avg_active_sessions"])
    assert top["begin_time"] == "2026-10-28T22:00:00"
    assert sample_awr["os_detail"][1] == {"snap_time": "2026-10-28T01:00:48", "load": 4.52, "busy_pct": 6.22,
        "user_pct": 4.51, "sys_pct": 0.79, "idle_pct": 93.78, "iowait_pct": 1.01}


def test_other_sections(sample_awr):
    assert sample_awr["thread_activity"] == {"log_switches_total": 579.0, "log_switches_per_hour": 24.13}
    assert sample_awr["fg_events"][1]["avg_wait_ms"] == 14.19
    assert sample_awr["bg_events"][0]["event"] == "log file parallel write"
    assert sample_awr["services"][1]["service_name"] == "SYS$USERS"
    assert sample_awr["pga"]["multipass_execs"] == 0
    assert sample_awr["parameters"]["db_block_size"]["begin"] == "8192"
    assert sample_awr["missing_sections"] == []
