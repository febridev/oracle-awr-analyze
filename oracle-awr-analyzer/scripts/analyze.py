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
            "os_iowait_pct": os_row.get("iowait_pct"), "os_idle_pct": os_row.get("idle_pct")}


def load_status(awr: dict, t: dict) -> dict:
    L, meta = t["load"], awr["meta"]
    cpus, elapsed, dbt = meta.get("cpus"), meta.get("elapsed_min"), meta.get("db_time_min")
    share = ((awr.get("time_model") or {}).get("db_cpu") or {}).get("pct_db_time")
    host_idle = (awr.get("host_cpu") or {}).get("idle_pct")
    notes = [f"load status unavailable: {n} missing" for n, v, lo in (("cpus", cpus, 0), ("elapsed_min", elapsed, 0),
             ("db_time_min", dbt, -1)) if v is None or v <= lo]  # db_time_min == 0 is valid (an idle window)
    top = next((e["wait_class"] for e in awr.get("top_events") or [] if e.get("wait_class")), None)
    cores = meta.get("cores")
    ht = bool(cores) and bool(cpus) and cpus / cores > L["hyperthreading_ratio"]
    if notes:
        return {"avg": {"aas": None, "load_ratio": None, "status": None, "db_cpu_share_pct": share,
                        "host_idle_pct": host_idle},
                "peak": None, "basis": "avg", "bottleneck": None, "top_wait_class": top, "hyperthreading": ht,
                "idle": False, "notes": notes}
    aas = round(dbt / elapsed, 2)
    ratio = round(aas / cpus, 2)
    peak = _peak(awr, t, cpus)
    peak_idle = peak["os_idle_pct"] if peak else None
    use_peak = peak is not None and peak["load_ratio"] > ratio
    basis_ratio = peak["load_ratio"] if use_peak else ratio
    idle = peak_idle if use_peak and peak_idle is not None else host_idle
    high = basis_ratio >= L["high_load_ratio"]
    if share is None or idle is None:
        bottleneck = None
    else:
        low_idle = idle < L["host_idle_low_pct"]
        if high:
            if share > L["cpu_share_high_pct"] and low_idle:
                bottleneck = "cpu_bound"
            elif share < L["cpu_share_low_pct"] and not low_idle:
                bottleneck = "wait_bound"
            else:
                bottleneck = "mixed"
        else:
            bottleneck = "non_db_host_load" if low_idle else "db_not_problem"
    avg_status = _status(ratio, L["bands"])
    return {
        "avg": {"aas": aas, "load_ratio": ratio, "status": avg_status, "db_cpu_share_pct": share,
                "host_idle_pct": host_idle},
        "peak": peak, "basis": "peak" if use_peak else "avg", "bottleneck": bottleneck,
        "top_wait_class": top, "hyperthreading": ht,
        "idle": avg_status == "Idle" and (peak is None or peak["status"] in ("Idle", "Normal")),
        "notes": notes,
    }


# ---------------------------------------------------------------- domain rules (candidates; Task 9 merges/caps)

def _match(name, pats):  # a pattern ending in * is a prefix match
    return any(name.startswith(p[:-1]) if p.endswith("*") else name == p for p in pats)


def idle_event(name: str, t: dict) -> bool:
    return _match(name, t["idle_events"])


def _idle(awr, name, t):
    # wait_class is absent from the fg events table, so also consult top_events
    return idle_event(name, t) or any(e.get("event") == name and e.get("wait_class") == "Idle"
                                      for e in awr.get("top_events") or [])


def _ev(metric, value, section, path):
    return {"metric": metric, "value": value, "section": section, "path": path}


def _cand(domain, subject, title, rule, t, evidence, pct=None, lat=None):
    r = t["rules"][rule]
    return {"domain": domain, "subject": subject, "title": title, "rule": rule, "severity": r["severity"],
            "load_based": r["load_based"], "evidence": evidence, "pct_db_time": pct,
            "latency_rule": lat is not None, "latency_ok": lat}


def _tier(t, ids, pred):  # ids ordered most severe first; first rule whose predicate holds
    return next((i for i in ids if pred(t["rules"][i])), None)


def _fg(awr, t):  # non-idle fg events as (index, event)
    return [(i, e) for i, e in enumerate(awr.get("fg_events") or []) if not _idle(awr, e["event"], t)]


def _pct(e):
    return e.get("pct_db_time") or 0


def _find(awr, t, name):
    return next(((i, e) for i, e in _fg(awr, t) if e["event"] == name), (None, None))


def _sum(awr, t, pats):
    hits = [(i, e) for i, e in _fg(awr, t) if _match(e["event"], pats)]
    return round(sum(_pct(e) for _, e in hits), 2), hits


def _ms(rule, storage):
    return rule["ms"][storage] if isinstance(rule["ms"], dict) else rule["ms"]


def _lat_state(t, storage, name, e):
    """(has latency rule, below warn, at crit) for an fg event."""
    base = t["latency_events"].get(name)
    if not base:
        return False, None, False
    avg = e.get("avg_wait_ms")
    return True, avg is None or avg < _ms(t["rules"][base + ".warn"], storage), \
        avg is not None and avg >= _ms(t["rules"][base + ".crit"], storage)


def _latency(awr, t, storage, domain):
    out = []
    for name, base in t["latency_events"].items():
        i, e = _find(awr, t, name)
        if e is None or base.split(".")[0] != domain or _pct(e) < t["min_pct_db_time"] or e.get("avg_wait_ms") is None:
            continue
        rid = _tier(t, [base + ".crit", base + ".warn"], lambda r: e["avg_wait_ms"] >= _ms(r, storage))
        if rid:
            out.append(_cand(domain, name, f"{name} latency is high", rid, t,
                             [_ev("Average wait (ms)", e["avg_wait_ms"], "Foreground Wait Events", f"fg_events[{i}].avg_wait_ms"),
                              _ev("% DB time", _pct(e), "Foreground Wait Events", f"fg_events[{i}].pct_db_time")],
                             _pct(e), lat=False))
    return out


def _sum_cand(awr, t, domain, subject, title, ids, key, pats, pred):
    pct, hits = _sum(awr, t, pats)
    awr.setdefault("derived", {})[key] = pct
    rid = _tier(t, ids, lambda r: pct >= r["pct_min"] and pred(r))
    if not rid:
        return []
    ev = [_ev(e["event"], _pct(e), "Foreground Wait Events", f"fg_events[{i}].pct_db_time") for i, e in hits]
    ev.append(_ev("Combined % DB time", pct, "Foreground Wait Events", f"derived.{key}"))
    return [_cand(domain, subject, title, rid, t, ev, pct)]


def rules_cpu(awr, L, t, storage):
    out, d = [], awr.setdefault("derived", {})
    basis = L["peak"] if L["basis"] == "peak" else L["avg"]
    ratio, idle = basis["load_ratio"], (awr.get("host_cpu") or {}).get("idle_pct")
    idle_ev = _ev("Host idle %", idle, "Host CPU", "host_cpu.idle_pct")
    if L["basis"] == "peak" and L["peak"].get("os_idle_pct") is not None:  # peak window: idle from its OS Detail row
        k = next(i for i, r in enumerate(awr["os_detail"]) if r["snap_time"] >= L["peak"]["end"])
        idle = L["peak"]["os_idle_pct"]
        idle_ev = _ev("Host idle % (peak window)", idle, "OS Detail", f"os_detail[{k}].idle_pct")
    if ratio is not None:
        d["aas"], d["load_ratio"] = basis["aas"], ratio
        rid = idle is not None and _tier(t, ["cpu.load.p0", "cpu.load.p1"], lambda r: ratio >= r["load_ratio_min"] and idle < r["host_idle_max_pct"])
        if rid:
            out.append(_cand("cpu", "host cpu", "Host CPU is saturated", rid, t,
                             [_ev("Load ratio (AAS / CPUs)", ratio, "Load", "derived.load_ratio"), idle_ev]))
    r = t["rules"]["cpu.resmgr.p2"]
    i, e = _find(awr, t, r["event"])
    if e and _pct(e) >= r["pct_min"]:
        out.append(_cand("events", r["event"], "Resource Manager is throttling CPU", "cpu.resmgr.p2", t,
                         [_ev("% DB time", _pct(e), "Foreground Wait Events", f"fg_events[{i}].pct_db_time")], _pct(e)))
    return out


def rules_memory(awr, L, t, storage):
    out, d = [], awr.setdefault("derived", {})
    v = ((awr.get("os_stats") or {}).get("VM_OUT_BYTES") or {}).get("value")
    elapsed = awr["meta"].get("elapsed_min")
    if v and v > 0 and elapsed and elapsed > 0:
        d["swap_out_mb_per_hour"] = rate = round(v / 1048576 / (elapsed / 60), 2)
        rid = "mem.swap.p1" if rate >= t["swap_out_mb_per_hour"] else "mem.swap.p3"
        out.append(_cand("memory", "swap", "Memory is being swapped out", rid, t,
                         [_ev("Swap-out (MB/h)", rate, "OS Statistics", "derived.swap_out_mb_per_hour")]))
    pct = ((awr.get("memory") or {}).get("pct_host_mem_used_for_sga_pga") or {}).get("end")
    if pct is not None and pct > t["rules"]["mem.sga_pga.p2"]["pct_gt"]:
        out.append(_cand("memory", "sga+pga", "SGA and PGA use most host memory", "mem.sga_pga.p2", t,
                         [_ev("% host memory used", pct, "Memory Statistics", "memory.pct_host_mem_used_for_sga_pga.end")]))
    mp = (awr.get("pga") or {}).get("multipass_execs")
    if mp and mp > 0:
        out.append(_cand("memory", "pga multipass", "PGA work areas spill in multiple passes", "mem.multipass.p3", t,
                         [_ev("Multipass executions", mp, "PGA Aggr Summary", "pga.multipass_execs")]))
    return out


def rules_io(awr, L, t, storage):
    return _latency(awr, t, storage, "io")


def rules_redo(awr, L, t, storage):
    out = _latency(awr, t, storage, "redo")
    sw = (awr.get("thread_activity") or {}).get("log_switches_per_hour")
    rid = sw is not None and _tier(t, ["redo.switch.crit", "redo.switch.warn"], lambda r: sw > r["per_hour_gt"])
    if rid:
        out.append(_cand("redo", "log switches", "Redo logs switch too often", rid, t,
                         [_ev("Log switches per hour", sw, "Thread Activity", "thread_activity.log_switches_per_hour")]))
    r = t["rules"]["redo.ckpt.p1"]
    i, e = _find(awr, t, r["event"])
    if e and _pct(e) >= r["pct_min"]:
        top = max(_fg(awr, t), key=lambda x: _pct(x[1]))[1] is e
        out.append(_cand("events", r["event"], "Sessions wait for checkpoint completion",
                         "redo.ckpt.p0" if top else "redo.ckpt.p1", t,
                         [_ev("% DB time", _pct(e), "Foreground Wait Events", f"fg_events[{i}].pct_db_time")], _pct(e)))
    return out


def rules_parse(awr, L, t, storage):
    out, eff = [], awr.get("efficiency") or {}
    soft = eff.get("soft_parse_pct")
    rid = soft is not None and _tier(t, ["parse.soft.crit", "parse.soft.warn"], lambda r: soft < r["pct_lt"])
    if rid:
        out.append(_cand("parse", "soft parse", "Too many hard parses", rid, t,
                         [_ev("Soft parse %", soft, "Instance Efficiency", "efficiency.soft_parse_pct")]))
    rate = ((awr.get("load_profile") or {}).get("hard_parses_sql") or {}).get("per_s")
    if rate is not None and rate >= t["rules"]["parse.hard_rate.p2"]["per_s_min"]:
        out.append(_cand("parse", "hard parse rate", "Hard parse rate is very high", "parse.hard_rate.p2", t,
                         [_ev("Hard parses per second", rate, "Load Profile", "load_profile.hard_parses_sql.per_s")]))
    out += _sum_cand(awr, t, "parse", "mutex", "Library cache mutex contention", ["parse.mutex.p1"], "mutex_pct",
                     t["mutex_events"], lambda r: True)
    ex = eff.get("execute_to_parse_pct")
    r = t["rules"]["parse.exec_to_parse.p3"]
    if ex is not None and soft is not None and ex < r["exec_lt"] and soft > r["soft_gt"]:
        out.append(_cand("parse", "execute to parse", "Cursors are not reused between executions", "parse.exec_to_parse.p3", t,
                         [_ev("Execute to parse %", ex, "Instance Efficiency", "efficiency.execute_to_parse_pct"),
                          _ev("Soft parse %", soft, "Instance Efficiency", "efficiency.soft_parse_pct")]))
    return out


def rules_concurrency(awr, L, t, storage):
    return _sum_cand(awr, t, "concurrency", "concurrency waits", "Sessions wait on each other", ["conc.combined.p0",
                     "conc.combined.p1", "conc.combined.p2"], "concurrency_pct", t["concurrency_events"], lambda r: True)


def rules_network(awr, L, t, storage):
    return _sum_cand(awr, t, "network", "sql*net client", "Time spent sending data to clients", ["net.client.p3"],
                     "network_pct", t["network_events"], lambda r: True)


def rules_events(awr, L, t, storage):
    out = []
    for i, e in _fg(awr, t):
        name, pct = e["event"], _pct(e)
        has_lat, ok, crit = _lat_state(t, storage, name, e)
        if name == t["rules"]["event.ctrlfile.p3"]["event"]:
            if pct >= t["rules"]["event.ctrlfile.p3"]["pct_min"]:
                out.append(_cand("events", name, "Control file reads take a visible share", "event.ctrlfile.p3", t,
                                 [_ev("% DB time", pct, "Foreground Wait Events", f"fg_events[{i}].pct_db_time")], pct))
            continue
        if _match(name, t["event_share_exclude"]):
            continue
        rid = _tier(t, ["event.share.p0", "event.share.p1", "event.share.p2"],
                    lambda r: pct >= r["pct_min"] and (crit or not r.get("needs_latency_crit")))
        if rid:
            dom = t["latency_events"][name].split(".")[0] if has_lat else "events"
            out.append(_cand(dom, name, f"{name} takes a large share of DB time", rid, t,
                             [_ev("% DB time", pct, "Foreground Wait Events", f"fg_events[{i}].pct_db_time")], pct,
                             lat=ok if has_lat else None))
    return out


def rules_sql(awr, L, t, storage):
    out = []
    for i, s in enumerate(awr.get("sql") or []):
        pct = (s.get("by_elapsed") or {}).get("pct_total")
        rid = pct is not None and _tier(t, ["sql.single.p1", "sql.single.p2", "sql.single.p3"], lambda r: pct >= r["pct_min"])
        if rid:
            out.append(_cand("sql", s["sql_id"], "One statement dominates elapsed time", rid, t,
                             [_ev("% of total elapsed", pct, "SQL ordered by Elapsed Time", f"sql[{i}].by_elapsed.pct_total")], pct))
    return out


def rules_monitoring(awr, L, t, storage):
    pct = round(sum((s.get("by_elapsed") or {}).get("pct_total") or 0 for s in awr.get("sql") or []
                    if s.get("category") in t["monitoring_categories"]), 2)
    awr.setdefault("derived", {})["monitoring_overhead_pct"] = pct
    rid = _tier(t, ["monitoring.overhead.p2", "monitoring.overhead.p3"], lambda r: pct >= r["pct_min"])
    return [_cand("monitoring", "monitoring overhead", "Monitoring and maintenance SQL use database time", rid, t,
                  [_ev("Monitoring + maintenance % elapsed", pct, "SQL ordered by Elapsed Time",
                       "derived.monitoring_overhead_pct")], pct)] if rid else []


def rules_config(awr, L, t, storage):
    out = []
    for name, v in (awr.get("parameters") or {}).items():
        if name.startswith("_") or name in t["watch_params"]:  # section lists only non-default params
            side = "end" if v.get("end") else "begin"
            out.append(_cand("config", name, f"Parameter {name} is set", "config.param.p4", t,
                             [_ev("Value", v[side], "Init.ora Parameters", f"parameters.{name}.{side}")]))
    rows = (awr.get("advisories") or {}).get("sga_target") or []
    r = t["rules"]["config.sga_advisory.p4"]
    cur = next((x for x in rows if x["sga_size_factor"] == 1), None)
    if cur and cur.get("est_db_time_s"):
        for i, x in enumerate(rows):
            if x["sga_size_factor"] > r["factor_gt"] and x["est_db_time_s"] <= r["db_time_ratio_max"] * cur["est_db_time_s"]:
                out.append(_cand("config", "sga advisory", "A larger SGA would cut database time", "config.sga_advisory.p4", t,
                                 [_ev("Estimated DB time (s)", x["est_db_time_s"], "SGA Target Advice",
                                      f"advisories.sga_target[{i}].est_db_time_s")]))
                break
    return out


_DOMAINS = (rules_cpu, rules_memory, rules_io, rules_redo, rules_parse, rules_concurrency, rules_network,
            rules_events, rules_sql, rules_monitoring, rules_config)


def all_candidates(awr: dict, L: dict, cfg: dict) -> list:
    t, storage = cfg["thresholds"], cfg["storage_profile"]
    return [c for f in _DOMAINS for c in f(awr, L, t, storage)]
