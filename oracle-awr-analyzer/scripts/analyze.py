from __future__ import annotations

_STATUSES = ("Idle", "Normal", "Busy", "Saturated", "Critical")


def _status(ratio: float, bands: dict) -> str:
    for name, key in zip(_STATUSES, ("idle", "normal", "busy", "saturated")):
        if ratio < bands[key]:  # lower-inclusive: the band's upper bound belongs to the next band
            return name
    return "Critical"


def _peak(awr: dict, t: dict, cpus: float) -> dict | None:
    L, meta, addm = t["load"], awr["meta"], awr.get("addm") or []
    if not addm or meta["elapsed_min"] <= L["long_period_min"]:
        return None
    a = max(addm, key=lambda r: r["avg_active_sessions"])
    aas = round(a["avg_active_sessions"], 2)
    ratio = round(aas / cpus, 2)
    # first OS snapshot at/after the window end closes the window (ISO strings compare chronologically)
    os_row = next((r for r in awr.get("os_detail") or [] if r["snap_time"] >= a["end_time"]), None) or {}
    return {"aas": aas, "load_ratio": ratio, "status": _status(ratio, L["bands"]), "begin": a["begin_time"],
            "end": a["end_time"], "source": "addm", "os_busy_pct": os_row.get("busy_pct"),
            "os_iowait_pct": os_row.get("iowait_pct"), "_idle_pct": os_row.get("idle_pct")}


def load_status(awr: dict, t: dict) -> dict:
    L, meta = t["load"], awr["meta"]
    cpus = meta["cpus"]
    aas = round(meta["db_time_min"] / meta["elapsed_min"], 2)
    ratio = round(aas / cpus, 2)
    share = awr["time_model"]["db_cpu"]["pct_db_time"]
    host_idle = awr["host_cpu"]["idle_pct"]
    peak = _peak(awr, t, cpus)
    peak_idle = peak.pop("_idle_pct") if peak else None
    use_peak = peak is not None and peak["load_ratio"] > ratio
    basis_ratio = peak["load_ratio"] if use_peak else ratio
    idle = peak_idle if use_peak and peak_idle is not None else host_idle
    high = basis_ratio >= L["high_load_ratio"]
    low_idle = idle is not None and idle < L["host_idle_low_pct"]
    if high:
        if share > L["cpu_share_high_pct"] and low_idle:
            bottleneck = "cpu_bound"
        elif share < L["cpu_share_low_pct"]:
            bottleneck = "wait_bound"
        else:
            bottleneck = "mixed"
    else:
        bottleneck = "non_db_host_load" if low_idle else "db_not_problem"
    avg_status = _status(ratio, L["bands"])
    cores = meta.get("cores")
    return {
        "avg": {"aas": aas, "load_ratio": ratio, "status": avg_status, "db_cpu_share_pct": share,
                "host_idle_pct": host_idle},
        "peak": peak, "basis": "peak" if use_peak else "avg", "bottleneck": bottleneck,
        "top_wait_class": next((e["wait_class"] for e in awr.get("top_events") or [] if e.get("wait_class")), None),
        "hyperthreading": bool(cores) and cpus / cores > L["hyperthreading_ratio"],
        "idle": avg_status == "Idle" and (peak is None or peak["status"] in ("Idle", "Normal")),
    }
