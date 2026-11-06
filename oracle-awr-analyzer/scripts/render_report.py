"""Render report.md and a self-contained report.html from awr.json + findings.json (+ interpretation.json)."""
from __future__ import annotations

import argparse
import fnmatch
import html as _h
import re
import sys
from pathlib import Path
from string import Template

import svg
from common import SKILL_DIR, ConfigError, load_config, read_json
from validate_findings import validate

SECTIONS = ["Executive summary", "Database and period", "Load health", "Findings", "Top SQL by category",
            "Domain detail", "Verification SQL", "AWR diff", "Appendix"]
LICENSE = "AWR and DBA_HIST views require the Oracle Diagnostics Pack license."
LABELS = ("Domain", "Evidence", "Facts", "Hypothesis", "User impact", "How to verify", "Recommendation",
          "Triggered by", "Severity adjusted", "Source", "What this means", "Found in AWR section", "Why this priority")
SQL_CELL = 200
_Q = re.compile(r"[nN]?[qQ]'(?:\[.*?\]|\{.*?\}|\(.*?\)|<.*?>|(?P<d>[^\s\[\]{}()<>']).*?(?P=d))'", re.S)
_STR = re.compile(r"'(?:[^']|'')*'")
_NUM = re.compile(r"(?<![\w:.$#])(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)(?!\w)")


def mask_sql(text: str) -> str:
    return _NUM.sub("?", _STR.sub("'?'", _Q.sub("'?'", text)))


def _n(v, unit="") -> str:
    return "unavailable" if v is None else f"{v:.10g}{unit}" if isinstance(v, float) else f"{v}{unit}"


def _short(text, mask) -> str:
    t = " ".join((mask_sql(text or "") if mask else text or "").split())
    return t if len(t) <= SQL_CELL else t[:SQL_CELL] + "…"


# ---- priority rules and data sources -----------------------------------------------------------

LEVELS = (("P0", "Critical", "Service disrupted or close to stopping", "Act now"),
          ("P1", "High", "Clear user impact", "Today"),
          ("P2", "Medium", "Measurable degradation that can grow", "This week"),
          ("P3", "Low", "Optimization opportunity", "Backlog"),
          ("P4", "Info", "Configuration note, no measured impact", "Record only"))
_LIMITS = {"load_ratio_min": "load ratio is at least {}", "host_idle_max_pct": "host CPU idle is under {}%",
           "pct_min": "its share is at least {}%", "pct_gt": "it uses more than {}% of host memory",
           "ms": "average wait is at least {} ms", "per_hour_gt": "there are more than {} log switches per hour",
           "pct_lt": "soft parse is under {}%", "per_s_min": "there are at least {} hard parses per second",
           "exec_lt": "execute to parse is under {}%", "soft_gt": "soft parse is over {}%",
           "db_time_ratio_max": "a larger SGA would cut estimated DB time to {} times today's or less",
           "needs_latency_crit": "its average wait is past the critical limit"}
_NO_LIMIT = {"mem.swap.p1": "swap-out is at least {swap} MB per hour", "mem.swap.p3": "there is swap-out, below {swap} MB per hour",
             "mem.multipass.p3": "a PGA work area ran in multiple passes", "config.param.p4": "a hidden or watched parameter is set",
             "redo.ckpt.p0": "this event is the top non-idle wait"}
_P0_LEAD = {"cpu.load.p0": "Host CPU is saturated", "event.share.p0": "One wait event dominates",
            "conc.combined.p0": "Sessions block each other (lock and latch waits combined)",
            "redo.ckpt.p0": "Sessions wait for checkpoint completion"}
AREAS = {"cpu": "CPU", "memory": "Memory", "io": "Disk reads", "redo": "Commit and redo logs", "parse": "SQL preparation (parsing)",
         "concurrency": "Sessions blocking each other", "network": "Sending data to clients", "events": "Other wait events",
         "sql": "Individual SQL statements", "monitoring": "Monitoring and maintenance", "config": "Configuration"}
# (report part, parser key or None, exact AWR title, what the section tells you)
SOURCES = (("Database and period", None, "Report header (first tables, no section title)", "Database, host, CPU count, snapshot times, Elapsed and DB Time"),
           ("Load health", "host_cpu", "Host CPU", "How busy the whole server was"),
           ("Load health", "time_model", "Time Model Statistics", "How much database working time went to CPU"),
           ("Load health", "top_events", "Top 10 Foreground Events by Total Wait Time", "What the database spent its working time on"),
           ("Load health", "addm", "Top ADDM Findings by Average Active Sessions", "The busiest periods in a long report"),
           ("Findings", "fg_events", "Foreground Wait Events", "What user sessions waited for, and how long each wait took"),
           ("Findings", "load_profile", "Load Profile", "Workload per second, such as hard parses"),
           ("Findings", "efficiency", "Instance Efficiency Percentages (Target 100%)", "How well memory and parsed SQL are reused"),
           ("Findings", "os_stats", "Operating System Statistics", "Server counters, such as memory swapped out"),
           ("Findings", "os_detail", "Operating System Statistics - Detail", "Server CPU use per snapshot"),
           ("Findings", "memory", "Memory Statistics", "Share of server memory the database uses"),
           ("Findings", "pga_summary", "PGA Aggr Summary", "Whether sorts and joins fit in memory"),
           ("Findings", "thread_activity", "Instance Activity Stats - Thread Activity", "How often redo logs switch"),
           ("Findings", "sga_advisory", "SGA Target Advisory", "Estimated effect of more database memory"),
           ("Findings", "parameters", "Modified Parameters", "Settings changed from their defaults"),
           ("Findings", "sql_by_user_io", "SQL ordered by User I/O Wait Time", "Statements that wait most on storage"),
           ("Top SQL by category", "sql_by_elapsed", "SQL ordered by Elapsed Time", "Statements that took the most time"),
           ("Top SQL by category", "sql_text", "Complete List of SQL Text", "Full text of those statements"))
_TITLE = {k: t for _, k, t, _ in SOURCES if k}


def _missing(awr) -> set:
    return {x["section"] if isinstance(x, dict) else str(x) for x in awr.get("missing_sections") or []}


HEADER_CALC = "DB Time, Elapsed and CPUs from the report header (calculated by this tool)"


def _found(awr, *keys, lead=None) -> str:
    gone = _missing(awr)
    return "Found in AWR section: " + "; ".join(([lead] if lead else [])
                                                + [_TITLE[k] + (" (not in this AWR file)" if k in gone else "") for k in keys])


def _limit(v, prof):
    return v.get(prof) if isinstance(v, dict) else v


def _cond(rid, t, prof) -> str:
    """The rule's condition in plain words, with the numbers from the thresholds in use."""
    r = (t.get("rules") or {}).get(rid) or {}
    parts = [_LIMITS[k].format(_limit(v, prof)) for k, v in r.items() if k in _LIMITS]
    if rid in _NO_LIMIT:
        parts.append(_NO_LIMIT[rid].format(swap=t.get("swap_out_mb_per_hour")))
    return " and ".join(parts) or f"rule {rid} fired"


def _why(f, t, prof) -> str:
    rid = f["triggered_by"][0]  # analyze.py sorts these most severe first
    sev = ((t.get("rules") or {}).get(rid) or {}).get("severity", f["severity"])
    measured = "; ".join(f'{e["metric"]} {_n(e["value"])}' for e in f["evidence"])
    return f"Why this priority: the rule gives {sev} when {_cond(rid, t, prof)}. Measured: {measured}."


def _priority_block(fs, t, prof):
    cnt = lambda p: sum(f["severity"] == p for f in fs)  # noqa: E731
    rules, L = t.get("rules") or {}, t.get("load") or {}
    crit = "; ".join(f'{ev} {_limit(rules[b + ".crit"]["ms"], prof)} ms' for ev, b in (t.get("latency_events") or {}).items()
                     if b + ".crit" in rules)
    p0 = [f"{_P0_LEAD.get(rid, rid)}: {_cond(rid, t, prof)}"
          + (f" (critical limits on {prof} storage: {crit})" if r.get("needs_latency_crit") else "")
          for rid, r in rules.items() if r.get("severity") == "P0"]
    return [("h", 3, "How priority is set", None),
            ("p", "Priority comes from fixed rules applied to the numbers in the AWR report, not from opinion. "
                  "Each rule has a limit and a level; a finding takes the level of the most severe rule it meets. "
                  "The limits below are the ones used for this report."),
            ("table", ["Level", "Name", "Meaning", "Action", "Findings here"], [[p, n, m, a, cnt(p)] for p, n, m, a in LEVELS], set()),
            ("p", "A finding is P0 only when one of these holds:"), ("ul", p0),
            ("p", "The level can then change:"),
            ("ul", [f'Quiet period: if the database is idle (load ratio under {(L.get("bands") or {}).get("idle")}) or the report covers less than '
                    f'{L.get("short_period_min")} minutes, load-based findings are capped at P3 with low confidence.',
                    "Volume cap: a large share of database time with healthy latency is capped at P2. There is a lot of work, but storage is not slow.",
                    "Raise: a statement that is a top storage consumer goes up one level while a disk read wait is past its critical limit."])]


def _sorted(findings):
    return sorted(findings, key=lambda f: (f["severity"], f["id"]))


# ---- block list -> markdown / html ------------------------------------------------------------
# blocks: ("h", level, text, color|None) ("p", text) ("ul", [str]) ("table", headers, rows, sqlcols) ("svg", s, [caption]) ("pre", text)
# a chart and its caption are HTML only: the markdown report has no charts for the caption to describe

def _md(blocks) -> str:
    out = []
    lt = lambda t: t.replace("<", "&lt;")  # noqa: E731  AWR text must not inject markup into the markdown viewer
    for b in blocks:
        k = b[0]
        if k == "h":
            out.append("#" * b[1] + " " + lt(b[2]))
        elif k == "p":
            out.append(lt(b[1]))
        elif k == "ul":
            out.append("\n".join("- " + lt(i) for i in b[1]))
        elif k == "table":
            cell = lambda c, i: ("`" + str(c).replace("`", "'") + "`" if i in b[3] else lt(str(c))).replace("|", "\\|").replace("\n", " ")  # noqa: E731
            out.append("| " + " | ".join(b[1]) + " |\n|" + "---|" * len(b[1]) + "\n"
                       + "\n".join("| " + " | ".join(cell(c, i) for i, c in enumerate(r)) + " |" for r in b[2]))
        elif k == "pre":
            out.append("```sql\n" + b[1].replace("```", "'''") + "\n```")
    return "\n\n".join(out) + "\n"


def _li(i: str) -> str:
    head, sep, tail = i.partition(": ")
    return f"<li><b>{_e(head)}:</b> {_e(tail)}</li>" if sep and head in LABELS else f"<li>{_e(i)}</li>"


def _e(s) -> str:
    return _h.escape(str(s), quote=False)


def _html(blocks) -> str:
    out = []
    for b in blocks:
        k = b[0]
        if k == "h":
            st = f' style="color:{b[3]}"' if b[3] else ""
            out.append(f"<h{b[1]}{st}>{_e(b[2])}</h{b[1]}>")
        elif k == "p":
            out.append(f"<p>{_e(b[1])}</p>")
        elif k == "ul":
            out.append("<ul>" + "".join(_li(i) for i in b[1]) + "</ul>")
        elif k == "table":
            rows = "".join("<tr>" + "".join(f'<td class="sql">{_e(c)}</td>' if i in b[3] else f"<td>{_e(c)}</td>"
                                            for i, c in enumerate(r)) + "</tr>" for r in b[2])
            out.append('<div class="tw"><table><tr>' + "".join(f"<th>{_e(x)}</th>" for x in b[1]) + f"</tr>{rows}</table></div>")
        elif k == "svg":
            out.append(b[1] + '\n<ul class="cap">' + "".join(_li(i) for i in b[2]) + "</ul>")
        elif k == "pre":
            out.append(f"<pre>{_e(b[1])}</pre>")
    return "\n".join(out)


# ---- interpretation ----------------------------------------------------------------------------

def _interp(run: Path, errs):
    """-> (summary list|None, {fid: entry}, {fid: rejected?}, whole_file_rejected)"""
    try:
        d = read_json(run / "interpretation.json")
    except (OSError, ValueError):
        return None, {}, set(), (run / "interpretation.json").exists()
    whole = any(e.startswith("interpretation.json") and not e.startswith("interpretation.json.executive_summary") for e in errs)
    if whole or not isinstance(d, dict):
        return None, {}, set(), True
    bad = {e.split(".")[0].split(":")[0] for e in errs}
    summ_ok = not any(e.startswith(("executive_summary", "interpretation.json.executive_summary")) for e in errs)
    s = d.get("executive_summary")
    return (s if summ_ok and s else None), d.get("findings") or {}, bad, False


def _finding(f, ent, rejected, inst, show_dba, t, prof):
    fb = f.get("fallback") or {}
    use = ent is not None and not rejected
    ent = ent if use else {}
    ev = "; ".join(f'{e["metric"]} {_n(e["value"])} ({e["section"]})' for e in f["evidence"])
    ids = ", ".join(v["id"] for v in f["verify_sql"])
    src = ("database-specific interpretation" if use else
           ("interpretation rejected by validator; " if rejected else "") + "standard explanation (not database-specific)")
    approval = ent.get("approval_required")
    items = [f'Domain: {f["domain"]} | Instance: {inst} | Confidence: {f["confidence"]}',
             f"Evidence: {ev}",
             "Found in AWR section: " + "; ".join(dict.fromkeys(e["section"] for e in f["evidence"])),
             _why(f, t, prof)]
    if f.get("severity_adjustments"):
        items.append("Severity adjusted: " + "; ".join(f["severity_adjustments"]))
    items += [
             "Facts: " + (ent.get("facts") or "see the measured values under Evidence"),
             "Hypothesis: " + (re.sub(r"^\s*hypothesis:\s*", "", ent.get("hypothesis") or "", flags=re.I) or fb.get("cause") or "not provided"),
             "User impact: " + (ent.get("user_impact") or fb.get("impact") or "not provided")]
    if show_dba:
        items.append(f"How to verify: see SQL {ids} in the Verification SQL section")
    items.append("Recommendation: " + (ent.get("recommendation") or fb.get("recommendation") or "not provided")
                 + f' | Change risk: {ent.get("change_risk") or "not assessed"}'
                 + f' | Change approval required: {"yes" if approval is None or approval else "no"}')
    if show_dba:
        items.append("Triggered by: " + ", ".join(f["triggered_by"]))
    items.append(f"Source: {src}")
    return [("h", 3, f'[{f["severity"]}] {f["title"]}', svg.SEVERITY_COLORS.get(f["severity"])), ("ul", items)]


# ---- charts ------------------------------------------------------------------------------------

def _idle(name, cfg):
    return any(fnmatch.fnmatchcase(name, p) for p in cfg["thresholds"].get("idle_events", []))


def _charts(awr, fj, cfg):
    cols = list(svg.CATEGORY_COLORS.values())
    parts, tot = {}, 0.0
    for e in awr.get("top_events") or []:
        if e.get("pct_db_time") is not None and not _idle(e["event"], cfg):
            k = "DB CPU" if e["event"] == "DB CPU" else e.get("wait_class") or "Other"
            parts[k] = parts.get(k, 0) + e["pct_db_time"]
            tot += e["pct_db_time"]
    if tot < 100:
        parts["Rest"] = parts.get("Rest", 0) + round(100 - tot, 2)
    comp = svg.stacked_bar([{"label": k, "value": v, "color": cols[i % len(cols)]} for i, (k, v) in enumerate(parts.items())],
                           "DB time composition (% of DB time, top events)")
    sev = {f["subject"]: f["severity"] for f in fj["findings"]}
    fg = [e for e in awr.get("fg_events") or [] if not _idle(e["event"], cfg)][:5]
    events = svg.hbar([{"label": e["event"], "value": e["pct_db_time"], "tag": sev.get(e["event"], ""),
                        "color": svg.SEVERITY_COLORS.get(sev.get(e["event"]), svg.NEUTRAL)} for e in fg],
                      unit="%", title="Top foreground events (% of DB time; color and tag show finding severity)")
    L, rows = fj["load"], []
    for name, key in (("Average", "avg"), ("Peak", "peak")):
        if L.get(key):
            rows.append({"label": name, "value": L[key].get("load_ratio"), "tag": L[key].get("status") or "", "color": svg.NEUTRAL})
    load = svg.hbar(rows, ref_line=1.0, ref_label="1.0 = all CPUs busy", title="Load ratio (active sessions / CPUs)")
    top = sorted([s for s in awr.get("sql") or [] if (s.get("by_elapsed") or {}).get("pct_total") is not None],
                 key=lambda s: -s["by_elapsed"]["pct_total"])[:10]
    sqlc = svg.hbar([{"label": s["sql_id"], "value": s["by_elapsed"]["pct_total"], "tag": s.get("category", ""),
                      "color": svg.CATEGORY_COLORS.get(s.get("category"), svg.NEUTRAL)} for s in top],
                    unit="%", title="Top 10 SQL by elapsed time (% of total, colored by category)")
    prof, rules = fj.get("storage_profile") or "san", cfg["thresholds"].get("rules") or {}
    lat = []
    for ev, pre in (cfg["thresholds"].get("latency_events") or {}).items():
        ms = _limit((rules.get(pre + ".warn") or {}).get("ms"), prof)
        row = next((e for e in awr.get("fg_events") or [] if e["event"] == ev and e.get("avg_wait_ms") is not None), None)
        if row:
            if ms:
                lat.append({"label": ev, "value": round(row["avg_wait_ms"] / ms * 100, 2), "color": svg.NEUTRAL,
                            "tag": f'{row["avg_wait_ms"]:g} ms / warn {ms:g} ms'})
    io = svg.hbar(lat, ref_line=100, ref_label="warning threshold", unit="%",
                  title=f"Wait latency as % of its warning threshold (storage profile {prof}); 100% = threshold")
    # these percentages can sum past 100 in AWR (%WIO overlaps the others), so they stay separate bars, never a stack
    hc = awr.get("host_cpu") or {}
    cpu = [{"label": n, "value": hc.get(k), "tag": "", "color": svg.NEUTRAL} for n, k in
           (("User", "user_pct"), ("System", "system_pct"), ("Waiting for I/O", "wio_pct"), ("Idle", "idle_pct"))]
    eff = awr.get("efficiency") or {}
    ratios = [{"label": n, "value": eff.get(k), "tag": "", "color": svg.NEUTRAL} for n, k in
              (("Buffer hit", "buffer_hit_pct"), ("Library hit", "library_hit_pct"), ("Soft parse", "soft_parse_pct"),
               ("Execute to parse", "execute_to_parse_pct"), ("Parse CPU to parse elapsed", "parse_cpu_to_parse_elapsd_pct"),
               ("Latch hit", "latch_hit_pct"))]
    has = lambda rs: any(r["value"] is not None for r in rs)  # noqa: E731
    cpu = svg.hbar(cpu, ref_line=100, ref_label="100%", unit="%", title="Host CPU use (% of server CPU time)") if has(cpu) else None
    ratios = svg.hbar(ratios, ref_line=100, ref_label="target 100%", unit="%", title="Efficiency ratios (%)") if has(ratios) else None
    return comp, events, load, sqlc, io, cpu, ratios


# ---- main --------------------------------------------------------------------------------------

NO_PROBLEM = "No performance problem in this period."
BOTTLENECK = {
    "cpu_bound": "CPU-bound: look for the SQL with the most CPU and excessive parsing.",
    "wait_bound": "Wait-bound: the top wait class ({top}) sets the direction of the analysis.",
    "mixed": "Mixed: both CPU and waits contribute significantly.",
    "non_db_host_load": "Non-database load on the host: another process is using CPU; check at OS level.",
    "db_not_problem": "The database is not the problem in this period. If users still complain, check the snapshot period or the application and network layers.",
}


def _bottleneck(L) -> str:
    b = L.get("bottleneck")
    return BOTTLENECK[b].format(top=_n(L.get("top_wait_class"))) if b in BOTTLENECK else \
        "Bottleneck could not be determined (load inputs unavailable)."


def _summary(fj, L):
    fs = _sorted(fj["findings"])
    cnt = {}
    for f in fs:
        cnt[f["severity"]] = cnt.get(f["severity"], 0) + 1
    peak = f'; at peak: {_n(L["peak"].get("status"))}' if L.get("peak") else ""
    out = [NO_PROBLEM] if L.get("idle") or not fs else []
    out += [f'Load on average: {_n((L.get("avg") or {}).get("status"))}{peak}.',
           "Findings: " + (", ".join(f"{k}: {v}" for k, v in sorted(cnt.items())) if cnt else "none") + "."]
    if fs:
        out.append(f'Most urgent: [{fs[0]["severity"]}] {fs[0]["title"]}.')
    return out


def render(run_dir: Path, audience: str, cfg=None):
    run = Path(run_dir)
    cfg = cfg or load_config(None)
    awr, fj = read_json(run / "awr.json"), read_json(run / "findings.json")
    m, L = awr["meta"], fj["load"]
    show_dba, mask = audience != "user", audience != "dba"
    errs, _ = validate(run)
    summ, ients, bad, whole = _interp(run, errs)
    fs = _sorted(fj["findings"])
    inst = f'Instance {m["inst_num"]} (' + ("RAC, not analyzed" if m["rac"] else "single instance") + ")"
    title = f'AWR report: {m["db_name"]} snapshots {m["begin_snap"]}-{m["end_snap"]}'
    banners = []
    if m["rac"]:
        banners.append("RAC sections not analyzed in v1. Only this instance's report content was read.")
    if m["restart"]:
        banners.append("The instance restarted during this period; counters are incomplete and confidence is lowered.")
    short = cfg["thresholds"].get("load", {}).get("short_period_min")
    if m.get("elapsed_min") is not None and short and m["elapsed_min"] < short:
        banners.append(f'Short period ({m["elapsed_min"]:g} min, under {short} min): averages may not represent normal load.')
    if "reduced accuracy" in str(m.get("parser")):
        banners.append("The report was read with the fallback parser (reduced accuracy); check key values by hand.")
    t, prof = cfg["thresholds"], fj.get("storage_profile") or "san"
    strip = ("svg", svg.priority_strip([{"level": p, "name": n, "action": a, "count": sum(f["severity"] == p for f in fs)} for p, n, _, a in LEVELS],
                                       "Findings by priority"),
             ["What this means: how many problems this report found at each priority, and how soon each level should be handled.",
              "Found in AWR section: not an AWR section; counted by this tool from the findings in this report"])
    B = [("h", 2, "Executive summary", None), ("ul", summ or _summary(fj, L)), strip] if audience != "dba" else []
    B += [("h", 2, "Database and period", None),
         ("p", f'DB: {m["db_name"]} (DBID {m["dbid"]}) | {inst} | {m["release"]} | CDB: {"YES" if m["cdb"] else "NO"}'),
         ("p", f'Host: {m["host"]} | {_n(m["cpus"])} CPU, {_n(m["cores"])} core, {_n(m["memory_gb"], " GB")} | {m["platform"]}'),
         ("p", f'Period: snap {m["begin_snap"]}-{m["end_snap"]} | {str(m["begin_time"]).replace("T", " ")} - {str(m["end_time"]).replace("T", " ")} | '
               f'Elapsed {_n(m["elapsed_min"], " min")} | DB Time {_n(m["db_time_min"], " min")}'),
         ("h", 2, "Load health", None)]
    comp, events, load, sqlc, io, cpu, ratios = _charts(awr, fj, cfg)
    load_keys = ("addm",) if L.get("peak") else ()
    row = lambda n, x: [n, _n(x.get("aas")), _n(x.get("load_ratio")), _n(x.get("status")), _n(x.get("host_idle_pct", x.get("os_idle_pct")), " %")]  # noqa: E731
    B += [("p", "Load ratio is the average number of active sessions divided by the number of CPUs; 1.0 means all CPUs are fully used."),
          ("table", ["Basis", "Active sessions", "Load ratio", "Status", "Host CPU idle"],
           [row("Average", L["avg"])] + ([row(f'Peak ({L["peak"].get("begin")} to {L["peak"].get("end")})', L["peak"])] if L.get("peak") else []), set()),
          ("ul", [_found(awr, *load_keys, "host_cpu", "time_model", "top_events", lead=HEADER_CALC)]),
          ("p", f'DB CPU share: {_n(L["avg"].get("db_cpu_share_pct"), " %")} of DB time | Top wait class: {_n(L.get("top_wait_class"))} | Status basis: {_n(L.get("basis"))}'),
          ("p", _bottleneck(L))]
    if L.get("idle"):
        B.append(("p", NO_PROBLEM))
    if L.get("peak"):
        pk = lambda x: str(L["peak"].get(x)).replace("T", " ")  # noqa: E731
        B.append(("p", f'Recommendation: run an hourly AWR for {pk("begin")} – {pk("end")} (peak) to analyse its cause separately.'))
    B += [("svg", load, ["What this means: above the dashed line, the database needs more CPU than the server has, so sessions queue.",
                         _found(awr, *load_keys, lead=HEADER_CALC)]),
          ("svg", comp, ["What this means: where the database spent its working time. A large share is not a problem by itself; the findings say when it is.",
                         _found(awr, "top_events")])]
    B += [("p", n) for n in fj.get("notes") or []]  # includes load notes; shown here so they are not buried in the appendix
    B.append(("h", 2, "Findings", None))
    if audience == "dba":
        B.append(strip)
    if fs:
        areas = sorted({f["domain"] for f in fs}, key=lambda d: min(f["severity"] for f in fs if f["domain"] == d))
        grid = svg.domain_grid([{"label": AREAS.get(d, d), "counts": {p: sum(f["domain"] == d and f["severity"] == p for f in fs) for p, *_ in LEVELS}}
                                for d in areas], "Findings by area and priority")
        B.append(("svg", grid, ["What this means: which part of the system the findings are about. The number in a cell is the count of findings.",
                                "Found in AWR section: not an AWR section; counted by this tool from the findings in this report"]))
    B += _priority_block(fs, t, prof)
    B.append(("h", 3, "Findings, most urgent first", None))
    if not fs:
        B.append(("p", "No findings were raised for this period."))
    for f in fs:
        B += _finding(f, ients.get(f["id"]) if not whole else None, f["id"] in bad or whole, m["inst_num"], show_dba, t, prof)
    B.append(("h", 2, "Top SQL by category", None))
    by = {s["sql_id"]: s for s in awr.get("sql") or []}
    rows = []
    for cat, ids in (fj["sql_summary"].get("by_category") or {}).items():
        for s in sorted((by[i] for i in ids if i in by), key=lambda s: -((s.get("by_elapsed") or {}).get("pct_total") or 0))[:5]:
            rows.append([cat, s["sql_id"], s.get("confidence", ""), _n((s.get("by_elapsed") or {}).get("pct_total"), " %"),
                         s.get("module") or "", _short(s.get("text"), mask)])
    B += [("svg", sqlc, ["What this means: the statements that took the most database time. The category is assigned by this tool, not by Oracle.",
                         _found(awr, "sql_by_elapsed")]),
          ("p", f'Monitoring and maintenance SQL together: {_n(fj["sql_summary"].get("monitoring_overhead_pct"), " %")} of elapsed time.'),
          ("table", ["Category", "SQL ID", "Confidence", "% elapsed", "Module", "SQL text (truncated)"], rows, {4, 5}),
          ("ul", [_found(awr, "sql_by_elapsed", "sql_text")])]
    B += [("h", 2, "Domain detail", None),
          ("svg", events, ["What this means: what user sessions waited for most. A colored bar has a finding; a gray bar does not.", _found(awr, "fg_events")]),
          ("svg", io, ["What this means: how long each wait took compared with its warning limit. Past the dashed line, that wait is slower than it should be.",
                       _found(awr, "fg_events")])]
    if cpu:
        B.append(("svg", cpu, ["What this means: how busy the whole server was, including work outside the database. "
                               "Waiting for I/O can overlap the other values, so together they can add up to more than 100%.", _found(awr, "host_cpu")]))
    if ratios:
        B.append(("svg", ratios, ["What this means: how well memory and parsed SQL are reused. A low value is only a problem when a finding says so.",
                                  _found(awr, "efficiency")]))
    B += [
          ("p", "RAC comparison not available: v1 analyzes a single instance only.")]
    for dom in sorted({f["domain"] for f in fs}):
        B.append(("p", f"{dom}: " + "; ".join(f'{f["id"]} [{f["severity"]}] {f["title"]}' for f in fs if f["domain"] == dom)))
    B.append(("h", 2, "Verification SQL", None) if show_dba else None)
    if show_dba:
        B.append(("p", "Run these read-only queries yourself; the tool never connects to a database. " + LICENSE))
        for f in fs:
            for v in f["verify_sql"]:
                B.append(("h", 4, f'{f["id"]} {v["id"]}: {v.get("title") or ""} (license: {v.get("license") or "unknown"})', None))
                B.append(("pre", v["sql"]) if v.get("available") and v.get("sql") else
                         ("p", f'{v["id"]}: not available ({v.get("reason") or "no reason given"})'))
    B.append(("h", 2, "AWR diff", None))
    d = fj.get("diff") or {}
    if d.get("suggested"):
        pct = (cfg["thresholds"].get("diff") or {}).get("duration_diff_pct", 20)
        from awr_diff_sql import db_label
        B += [("p", "A comparison with a baseline report is suggested for these reasons:"), ("ul", d.get("reasons") or ["not given"]),
              ("p", f'Current period: snapshots {m["begin_snap"]}-{m["end_snap"]} (instance {m["inst_num"]}).'),
              ("p", "A good baseline is the same weekday and hour in a previous week, with a similar duration and no instance restart in between. "
                    "Please give the baseline snapshot IDs (begin and end)."),
              ("p", f"The comparison gets low confidence if the two periods differ in duration by more than {pct}%, "
                    "if the instance restarted between them, or if the CPU count differs."),
              ("p", f'Run awr_diff_{db_label(m)}.sql in SQL*Plus; it asks for base_begin_snap and base_end_snap.')]
    else:
        B.append(("p", "No AWR diff is suggested for this period."))
    B.append(("h", 2, "Appendix", None))
    lp = awr.get("load_profile") or {}
    km = [(k, n) for k, n in (("db_time_s", "DB Time"), ("db_cpu_s", "DB CPU"), ("executes_sql", "Executes (SQL)"),
                              ("transactions", "Transactions"), ("redo_size_bytes", "Redo size (bytes)"),
                              ("hard_parses_sql", "Hard parses (SQL)"))]
    km = [f'{n} per second: {_n((lp.get(k) or {}).get("per_s"))}' for k, n in km]
    A = L.get("avg") or {}
    km += [f'Average active sessions: {_n(A.get("aas"))}', f'Load ratio: {_n(A.get("load_ratio"))}']
    gone = _missing(awr)
    B += [("h", 3, "Where this report gets its data", None),
          ("p", "Open the AWR HTML file and search for the section name to see the original numbers."),
          ("table", ["Report part", "AWR section", "What it tells you", "Status"],
           [[p, s, w, "not in this AWR file" if k in gone else "read"] for p, k, s, w in SOURCES], set()),
          ("h", 3, "Key metrics", None), ("ul", km), ("h", 3, "Assumptions", None),
          ("ul", [f'storage profile: {fj.get("storage_profile")}' + (" (assumed; set storage_profile in the config to change it)" if
                  any("assumed" in x for x in fj.get("notes") or []) else " (set in the config)"),
                  f'Logical CPUs: {_n(m.get("cpus"))} (cores {_n(m.get("cores"))})',
                  "Hyperthreading: " + ("detected, CPU count is logical" if L.get("hyperthreading") else "not detected")]),
          ("h", 3, "Data quality", None),
          ("ul", [f'Parser: {m.get("parser")}'] + list(m.get("notes") or [])
           + [f'Invalid value ignored: {i["path"]} = {i["raw"]} ({i["reason"]})' for i in awr.get("invalid_values") or []]
           + [f"Section missing from the AWR report: {x}" for x in awr.get("missing_sections") or []]),
          ("h", 3, "Limitations", None),
          ("ul", ["v1 supports a single instance only; RAC, PDB and Exadata are not analyzed",
                  "HTML input only (text AWR reports are not read)", "English only"])]
    if show_dba:
        for s in sorted(by.values(), key=lambda s: -((s.get("by_elapsed") or {}).get("pct_total") or 0))[:10]:
            B += [("h", 4, f'SQL {s["sql_id"]} ({s.get("category")}; {"; ".join(s.get("reasons") or [])})', None), ("pre", s.get("text") or "")]
    B.append(("p", LICENSE))
    B = [b for b in B if b]
    md = "\n".join(f"> {b}" for b in banners) + ("\n\n" if banners else "")
    md = f"# {title}\n\n" + md + _md(B)
    bn = "".join(f'<div class="banner">{_e(b)}</div>' for b in banners)
    page = Template((SKILL_DIR / "assets" / "report.html.tpl").read_text(encoding="utf-8"))
    return md, page.substitute(title=_e(title), banners=bn, body=_html(B))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render report.md and report.html")
    ap.add_argument("run_dir")
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    run = Path(a.run_dir)
    try:
        cfg = load_config(Path(a.config) if a.config else None)
        audience = cfg["audience"] if a.config else read_json(run / "findings.json").get("audience", "both")
        md, html = render(run, audience, cfg)
    except (ConfigError, OSError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    (run / "report.md").write_text(md, encoding="utf-8")
    (run / "report.html").write_text(html, encoding="utf-8")
    print(run / "report.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
