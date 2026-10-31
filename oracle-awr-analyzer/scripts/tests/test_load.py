import pytest

from analyze import load_status
from conftest import mini_awr


def test_sample_load(sample_awr, cfg):
    L = load_status(sample_awr, cfg["thresholds"])
    assert L["avg"]["aas"] == 7.12 and L["avg"]["load_ratio"] == 0.21 and L["avg"]["status"] == "Normal"
    assert L["peak"] == {"aas": 31.79, "load_ratio": 0.93, "status": "Busy", "begin": "2026-10-28T22:00:00",
                         "end": "2026-10-28T23:00:00", "source": "addm", "os_busy_pct": 26.57, "os_iowait_pct": 14.49, "os_idle_pct": 73.43}
    assert L["basis"] == "peak" and L["bottleneck"] == "wait_bound" and L["top_wait_class"] == "User I/O"


@pytest.mark.parametrize("ratio,status", [(0.05, "Idle"), (0.3, "Normal"), (0.7, "Busy"), (1.5, "Saturated"), (2.5, "Critical")])
def test_bands(ratio, status, cfg):
    L = load_status(mini_awr(10, 60, ratio * 600, 50, 50), cfg["thresholds"])
    assert L["avg"]["status"] == status and L["peak"] is None and L["basis"] == "avg"


@pytest.mark.parametrize("ratio,share,idle,expect", [
    (1.2, 80, 5, "cpu_bound"), (1.2, 30, 60, "wait_bound"), (1.2, 30, 5, "mixed"), (1.2, 55, 60, "mixed"),
    (0.2, 50, 5, "non_db_host_load"), (0.2, 50, 80, "db_not_problem")])
def test_bottleneck(ratio, share, idle, expect, cfg):
    assert load_status(mini_awr(10, 60, ratio * 600, share, idle), cfg["thresholds"])["bottleneck"] == expect


def test_missing_inputs_no_crash(cfg):
    for key, val in (("cpus", None), ("elapsed_min", 0), ("db_time_min", None)):
        a = mini_awr(10, 60, 100, 50, 50)
        a["meta"][key] = val
        L = load_status(a, cfg["thresholds"])
        assert L["avg"]["aas"] is None and L["avg"]["status"] is None and L["bottleneck"] is None
        assert L["peak"] is None and L["basis"] == "avg" and L["notes"] == [f"load status unavailable: {key} missing"]
    a = mini_awr(10, 60, 600, 50, 50)
    a["time_model"] = {}
    L = load_status(a, cfg["thresholds"])
    assert L["bottleneck"] is None and L["avg"]["status"] == "Saturated" and L["notes"] == []
