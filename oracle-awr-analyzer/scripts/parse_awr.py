"""Parse an Oracle AWR HTML report into JSON."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, Tag

try:
    import lxml  # noqa: F401
    _PARSER, _PARSER_NAME = "lxml", "lxml"
except ImportError:
    _PARSER, _PARSER_NAME = "html.parser", "html.parser (reduced accuracy)"

_MULT = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
_TIME_S = {"us": 1e-6, "ms": 1e-3, "s": 1.0}
_NUM_RE = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(us|ms|s|[KMGT])?$")


def num(text: Optional[str], unit: Optional[str] = None, decimal: str = ".") -> Optional[float]:
    """Normalize an AWR number ('1,439.65', '219.2K', '587.00us') to float, or None."""
    if text is None:
        return None
    t = text.replace("&#160;", "").replace("&nbsp;", "").replace("\xa0", "").strip()
    if not t:
        return None
    t = t.replace(".", "") if decimal == "," else t.replace(",", "")
    t = t.replace(",", ".")
    m = _NUM_RE.match(t)
    if not m:
        return None
    val, suf = float(m.group(1)), m.group(2)
    if suf in _MULT:
        val *= _MULT[suf]
    elif suf and unit:
        val = val * _TIME_S[suf] / _TIME_S[unit]
    return float(f"{val:.10g}")


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower().replace("%", " pct ")).strip("_")


class NotAwrError(Exception):
    pass


class UnsupportedFormatError(Exception):
    pass


# key -> regexes over the table summary, then the preceding h2/h3 text
SECTIONS = {
    "db_instance": (r"database instance information",),
    "host": (r"host information",),
    "snapshot": (r"snapshot information",),
    "load_profile": (r"^This table displays load profile",),
    "efficiency": (r"instance efficiency percentages",),
    "top_events": (r"top 10 wait events by total wait time",),
    "time_model": (r"different time model statistics",),
    "host_cpu": (r"system load statistics",),
    "instance_cpu": (r"CPU usage and wait statistics",),
    "os_stats": (r"operating systems statistics\. For each statistic",),
    "memory": (r"^This table displays memory statistics",),
    "io_profile": (r"^This table displays IO profile",),
    "iostat_function": (r"IO Statistics for different functions",),
    "fg_events": (r"Foreground Wait Events and their wait statistics",),
    "bg_events": (r"background wait events statistics",),
    "sql_by_elapsed": (r"top SQL by elapsed time",),
    "sql_by_cpu": (r"top SQL by CPU time",),
    "sql_by_user_io": (r"top SQL by user I/O time",),
    "sql_by_gets": (r"top SQL by buffer gets",),
    "sql_by_reads": (r"top SQL by physical reads",),
    "sql_by_executions": (r"top SQL by number of executions",),
    "sql_by_parse_calls": (r"top SQL by number of parse calls",),
    "sql_text": (r"text of the SQL statements",),
    "services": (r"service statistics\. It displays service names",),
    "thread_activity": (r"thread activity stats",),
    "addm": (r"top ADDM findings",),
    "os_detail": (r"detailed operating systems statistics",),
    "pga_summary": (r"PGA aggregate summary",),
    "pga_hist": (r"PGA aggregate target histograms",),
    "sga_advisory": (r"SGA target advisory",),
    "shared_pool_advisory": (r"shared pool advisory",),
    "buffer_pool_advisory": (r"buffer pool statistics",),
    "pga_advisory": (r"PGA memory advisory",),
    "seg_logical": (r"top segments by logical reads",),
    "seg_physical": (r"top segments by physical reads",),
    "seg_row_lock": (r"top segments by row lock waits",),
    "seg_buffer_busy": (r"top segments by buffer busy waits",),
    "activity": (r"^This table displays Instance activity statistics",),
    "parameters": (r"modified initialization parameters",),
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class Doc:
    def __init__(self, soup: BeautifulSoup, parser: str):
        self.soup, self.parser = soup, parser

    def find_tables(self, key: str) -> list:
        pats = [re.compile(p, re.I) for p in SECTIONS[key]]
        out = []
        for t in self.soup.find_all("table"):
            summ = t.get("summary")
            if summ is not None:  # summary is primary; heading only for tables without one
                hit = any(p.search(summ) for p in pats)
            else:
                head = t.find_previous(["h2", "h3"])
                hit = bool(head) and any(p.search(_clean(head.get_text())) for p in pats)
            if hit:
                out.append(t)
        return out

    def find_table(self, key: str) -> Optional[Tag]:
        tabs = self.find_tables(key)
        return tabs[0] if tabs else None


def load_doc(path: Path) -> Doc:
    raw = Path(path).read_bytes()
    text = None
    m = re.search(rb"charset\s*=\s*[\"']?\s*([\w.:-]+)", raw[:2048], re.I)
    for enc in ([m.group(1).decode("ascii")] if m else []) + ["utf-8", "latin-1"]:
        try:
            text = raw.decode(enc)
            break
        except (LookupError, UnicodeDecodeError):
            pass
    head = text[:65536].lower()
    if "<html" not in head and "<table" not in head:
        raise UnsupportedFormatError("text AWR not supported in v1; regenerate with awrrpt.sql in HTML")
    if "workload repository" not in text.lower():
        raise NotAwrError(("not an Oracle AWR report: expected an HTML file produced by awrrpt.sql / awrgrpt.sql / "
                           "DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_HTML whose title contains 'WORKLOAD REPOSITORY'"))
    return Doc(BeautifulSoup(text, _PARSER), _PARSER_NAME)


def table_rows(table: Tag) -> list:
    return [[_clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]


def records(table: Tag) -> list:
    rows = table_rows(table)
    for i, r in enumerate(rows):
        if sum(1 for c in r if c) >= 2:
            keys, seen = [], {}
            for j, h in enumerate(r):
                k = slug(h) or "col%d" % j
                seen[k] = seen.get(k, 0) + 1
                keys.append(k if seen[k] == 1 else "%s_%d" % (k, seen[k]))
            return [dict(zip(keys, row)) for row in rows[i + 1:]]
    return []


_DATE_FMTS = ("%d-%b-%y %H:%M:%S", "%d-%b-%y %H:%M", "%d-%b-%Y %H:%M:%S")


def to_iso(text: str, year_hint: Optional[int] = None) -> str:
    t = _clean(text)
    fmts = [(f, t) for f in _DATE_FMTS]
    if year_hint:  # "28-Oct 01:00:48" has no year
        fmts.append(("%d-%b-%Y %H:%M:%S", re.sub(r"^(\d+-\w+)", r"\1-%d" % year_hint, t)))
    for f, v in fmts:
        try:
            return datetime.strptime(v, f).isoformat()
        except ValueError:
            pass
    return text


def _int(v: str) -> Optional[int]:
    n = num(v)
    return None if n is None else int(n)


def extract_meta(doc: Doc) -> dict:
    notes = []
    info = {}
    for t in doc.find_tables("db_instance"):
        for r in records(t)[:1]:
            info.update(r)
    host = next(iter(records(doc.find_table("host")) if doc.find_table("host") else []), {})
    snap = {}
    st = doc.find_table("snapshot")
    for r in (table_rows(st) if st else []):
        if r and r[0]:
            snap[r[0].rstrip(":").lower()] = r
    cell = lambda k, i: snap[k][i] if k in snap and len(snap[k]) > i else ""
    elapsed_raw = cell("elapsed", 2)
    m = re.search(r"[.,](?=\d+\D*$)", re.sub(r"\(.*", "", elapsed_raw).strip())
    decimal = "," if m and m.group(0) == "," else "."
    startup = to_iso(info.get("startup_time", ""))
    begin = to_iso(cell("begin snap", 2))
    end = to_iso(cell("end snap", 2))
    restart = False
    if re.match(r"\d{4}-", startup) and re.match(r"\d{4}-", begin):
        restart = startup > begin
    else:
        notes.append("startup/begin time not parsed; restart check skipped")
    mins = lambda k: num(re.sub(r"\(.*", "", cell(k, 2)), decimal=decimal)
    return {
        "db_name": info.get("db_name"), "dbid": _int(info.get("db_id", "")),
        "unique_name": info.get("unique_name"), "release": info.get("release"),
        "rac": info.get("rac", "").upper() == "YES", "cdb": info.get("cdb", "").upper() == "YES",
        "instance": info.get("instance"), "inst_num": _int(info.get("inst_num", "")),
        "startup_time": startup, "host": host.get("host_name"), "platform": host.get("platform"),
        "cpus": _int(host.get("cpus", "")), "cores": _int(host.get("cores", "")),
        "sockets": _int(host.get("sockets", "")),
        "memory_gb": num(host.get("memory_gb", ""), decimal=decimal),
        "begin_snap": _int(cell("begin snap", 1)), "end_snap": _int(cell("end snap", 1)),
        "begin_time": begin, "end_time": end,
        "sessions_begin": _int(cell("begin snap", 3)), "sessions_end": _int(cell("end snap", 3)),
        "elapsed_min": mins("elapsed"), "db_time_min": mins("db time"),
        "restart": restart, "parser": doc.parser, "decimal": decimal, "notes": notes,
    }


class Ctx:
    """Shared extraction state: the parsed doc, number locale, and problems found."""

    def __init__(self, doc: Doc, decimal: str = "."):
        self.doc, self.decimal = doc, decimal
        self.missing: list = []
        self.invalid: list = []

    def missing_section(self, key: str, reason: str) -> None:
        self.missing.append({"section": key, "reason": reason})

    def check_pct(self, path: str, value: Optional[float]) -> Optional[float]:
        if value is not None and not 0 <= value <= 100:
            self.invalid.append({"path": path, "raw": "%g" % value, "reason": "outside 0-100"})
            return None
        return value

    def table(self, key: str) -> Optional[Tag]:
        t = self.doc.find_table(key)
        if t is None:
            self.missing_section(key, "table not found")
        return t

    def val(self, path: str, text: Optional[str], unit: Optional[str] = None) -> Optional[float]:
        """Parse a number; null + invalid entry if negative, or outside 0-100 for percentages."""
        v = num(text, unit=unit, decimal=self.decimal)
        if v is not None and any(g.startswith("pct_") or g.endswith("_pct") for g in path.split(".")):
            return self.check_pct(path, v)
        if v is not None and v < 0:
            self.invalid.append({"path": path, "raw": "%g" % v, "reason": "negative value"})
            return None
        return v

    def expect(self, key: str, recs: list, cols) -> None:
        """Record each expected column absent from the table's header (once per column)."""
        if recs:
            for c in cols:
                if c not in recs[0]:
                    self.missing_section("%s.%s" % (key, c), "column not found")


def _labeled(ctx: Ctx, key: str, cols) -> Optional[list]:
    """Rows of a header+label-column table as (slug(label), record); label key is col0."""
    t = ctx.table(key)
    if t is None:
        return None
    recs = records(t)
    ctx.expect(key, recs, cols)
    return [(slug(r.get("col0", "")), r) for r in recs if r.get("col0")]


def extract_load_profile(ctx: Ctx) -> Optional[dict]:
    rows = _labeled(ctx, "load_profile", ("per_second", "per_transaction"))
    if rows is None:
        return None
    return {k: {"per_s": ctx.val("load_profile.%s.per_s" % k, r.get("per_second")),
                "per_txn": ctx.val("load_profile.%s.per_txn" % k, r.get("per_transaction"))}
            for k, r in rows}


def extract_efficiency(ctx: Ctx) -> Optional[dict]:
    t = ctx.table("efficiency")
    if t is None:
        return None
    out = {}
    for row in table_rows(t):  # no header row: label, value, label, value
        for i in range(0, len(row) - 1, 2):
            if row[i]:
                k = slug(row[i])
                out[k] = ctx.val("efficiency." + k, row[i + 1])
    return out


def extract_top_events(ctx: Ctx) -> Optional[list]:
    t = ctx.table("top_events")
    if t is None:
        return None
    recs = [r for r in records(t)]
    ctx.expect("top_events", recs, ("event", "waits", "total_wait_time_sec", "avg_wait", "pct_db_time",
                                    "wait_class"))
    out = []
    for r in recs:
        if r.get("event"):
            p = "top_events[%d]." % len(out)
            out.append({"event": r["event"], "waits": ctx.val(p + "waits", r.get("waits")),
                        "total_wait_s": ctx.val(p + "total_wait_s", r.get("total_wait_time_sec"), "s"),
                        "avg_wait_ms": ctx.val(p + "avg_wait_ms", r.get("avg_wait"), "ms"),
                        "pct_db_time": ctx.val(p + "pct_db_time", r.get("pct_db_time")),
                        "wait_class": r.get("wait_class") or None})
    return out


def extract_time_model(ctx: Ctx) -> Optional[dict]:
    t = ctx.table("time_model")
    if t is None:
        return None
    recs = records(t)
    ctx.expect("time_model", recs, ("statistic_name", "time_s", "pct_of_db_time"))
    return {slug(r["statistic_name"]): {
        "time_s": ctx.val("time_model.%s.time_s" % slug(r["statistic_name"]), r.get("time_s")),
        "pct_db_time": ctx.val("time_model.%s.pct_db_time" % slug(r["statistic_name"]), r.get("pct_of_db_time"))}
        for r in recs if r.get("statistic_name")}


def _one_row(ctx: Ctx, key: str, cols: dict) -> Optional[dict]:
    t = ctx.table(key)
    if t is None:
        return None
    recs = records(t)
    ctx.expect(key, recs, cols.values())
    rec = recs[0] if recs else {}
    return {name: ctx.val("%s.%s" % (key, name), rec.get(src)) for name, src in cols.items()}


def extract_host_cpu(ctx: Ctx) -> Optional[dict]:
    return _one_row(ctx, "host_cpu", {
        "cpus": "cpus", "cores": "cores", "sockets": "sockets", "load_begin": "load_average_begin",
        "load_end": "load_average_end", "user_pct": "pct_user", "system_pct": "pct_system",
        "wio_pct": "pct_wio", "idle_pct": "pct_idle"})


def extract_instance_cpu(ctx: Ctx) -> Optional[dict]:
    return _one_row(ctx, "instance_cpu", {
        "total_cpu_pct": "pct_total_cpu", "busy_cpu_pct": "pct_busy_cpu",
        "db_time_waiting_cpu_rm_pct": "pct_db_time_waiting_for_cpu_resource_manager"})


def extract_os_stats(ctx: Ctx) -> Optional[dict]:
    t = ctx.table("os_stats")
    if t is None:
        return None
    recs = records(t)
    ctx.expect("os_stats", recs, ("statistic", "value", "end_value"))
    return {r["statistic"]: {"value": ctx.val("os_stats.%s.value" % r["statistic"], r.get("value")),
                             "end_value": ctx.val("os_stats.%s.end_value" % r["statistic"], r.get("end_value"))}
            for r in recs if r.get("statistic")}


def extract_memory(ctx: Ctx) -> Optional[dict]:
    rows = _labeled(ctx, "memory", ("begin", "end"))
    if rows is None:
        return None
    return {k: {"begin": ctx.val("memory.%s.begin" % k, r.get("begin")),
                "end": ctx.val("memory.%s.end" % k, r.get("end"))} for k, r in rows}


def extract_io_profile(ctx: Ctx) -> Optional[dict]:
    rows = _labeled(ctx, "io_profile", ("read_write_per_second", "read_per_second", "write_per_second"))
    if rows is None:
        return None
    return {k: {"rw_per_s": ctx.val("io_profile.%s.rw_per_s" % k, r.get("read_write_per_second")),
                "r_per_s": ctx.val("io_profile.%s.r_per_s" % k, r.get("read_per_second")),
                "w_per_s": ctx.val("io_profile.%s.w_per_s" % k, r.get("write_per_second"))} for k, r in rows}


# header slug (repeated headers get _2 from records()) -> output key
_IOSTAT_COLS = {"reads_data": "reads_data", "reqs_per_sec": "reads_reqs_per_s",
                "data_per_sec": "reads_data_per_s", "writes_data": "writes_data",
                "reqs_per_sec_2": "writes_reqs_per_s", "data_per_sec_2": "writes_data_per_s",
                "waits_count": "waits_count", "avg_time": "avg_time_ms"}


def extract_iostat_function(ctx: Ctx) -> Optional[list]:
    t = ctx.table("iostat_function")
    if t is None:
        return None
    recs = records(t)
    ctx.expect("iostat_function", recs, ("function_name",) + tuple(_IOSTAT_COLS))
    out = []
    for r in recs:
        if r.get("function_name"):
            row = {"function_name": r["function_name"]}
            for src, name in _IOSTAT_COLS.items():
                # shortcut: num() treats K/M/G/T as decimal (1e3..), AWR means binary; fine for ranking, fix if exact bytes needed
                row[name] = ctx.val("iostat_function[%d].%s" % (len(out), name), r.get(src),
                                    "ms" if name == "avg_time_ms" else None)
            out.append(row)
    return out


def _events(ctx: Ctx, key: str, pct_col: str, pct_key: str) -> Optional[list]:
    t = ctx.table(key)
    if t is None:
        return None
    recs = records(t)
    ctx.expect(key, recs, ("event", "waits", "pct_time_outs", "total_wait_time_s", "avg_wait", "waits_txn", pct_col))
    out = []
    for r in recs:
        if r.get("event"):
            p = "%s[%d]." % (key, len(out))
            out.append({"event": r["event"], "waits": ctx.val(p + "waits", r.get("waits")),
                        "timeouts_pct": ctx.val(p + "timeouts_pct", r.get("pct_time_outs")),
                        "total_wait_s": ctx.val(p + "total_wait_s", r.get("total_wait_time_s"), "s"),
                        "avg_wait_ms": ctx.val(p + "avg_wait_ms", r.get("avg_wait"), "ms"),
                        "waits_per_txn": ctx.val(p + "waits_per_txn", r.get("waits_txn")),
                        pct_key: ctx.val(p + pct_key, r.get(pct_col))})
    return out


def extract_fg_events(ctx: Ctx) -> Optional[list]:
    return _events(ctx, "fg_events", "pct_db_time", "pct_db_time")


def extract_bg_events(ctx: Ctx) -> Optional[list]:
    return _events(ctx, "bg_events", "pct_bg_time", "pct_bg_time")


_MODULE_RE = re.compile(r"^(?P<program>[^@\s]+)@(?P<host>\S+)\s*\(TNS V1-V3\)$")


def split_module(module: Optional[str]) -> dict:
    m = _MODULE_RE.match((module or "").strip())
    return {"program": m.group("program") if m else None, "client_host": m.group("host") if m else None}


_SQL_TABLES = (("by_elapsed", "sql_by_elapsed"), ("by_cpu", "sql_by_cpu"), ("by_user_io", "sql_by_user_io"),
               ("by_gets", "sql_by_gets"), ("by_reads", "sql_by_reads"),
               ("by_executions", "sql_by_executions"), ("by_parse_calls", "sql_by_parse_calls"))
_SQL_TEXT_COLS = ("sql_id", "sql_module", "sql_text")


def extract_sql(ctx: Ctx) -> list:
    """Merge the seven top-SQL tables by sql_id; full text from the Complete List when present."""
    sqls: dict = {}
    for name, key in _SQL_TABLES:
        t = ctx.table(key)
        if t is None:
            continue
        recs = records(t)
        ctx.expect(key, recs, _SQL_TEXT_COLS)
        for r in recs:
            sid = r.get("sql_id")
            if not sid:
                continue
            e = sqls.setdefault(sid, dict({"sql_id": sid, "module": r.get("sql_module") or None, "text": r.get("sql_text") or None},
                                          **{n: None for n, _ in _SQL_TABLES}))
            e["module"] = e["module"] or r.get("sql_module") or None
            e["text"] = e["text"] or r.get("sql_text") or None
            e[name] = {k: ctx.val("sql[%s].%s.%s" % (sid, name, k), v) for k, v in r.items()
                       if k not in _SQL_TEXT_COLS}
    tt = ctx.table("sql_text")
    full: dict = {}
    if tt is not None:
        recs = records(tt)
        ctx.expect("sql_text", recs, ("sql_id", "sql_text"))
        for r in recs:
            full.setdefault(r.get("sql_id"), r.get("sql_text"))
    for sid, e in sqls.items():
        e["text"] = full.get(sid) or e["text"]
        e.update(split_module(e["module"]))
    return list(sqls.values())


def extract_services(ctx: Ctx) -> Optional[list]:
    t = ctx.table("services")
    if t is None:
        return None
    recs = records(t)
    cols = ("db_time_s", "db_cpu_s", "physical_reads_k", "logical_reads_k")
    ctx.expect("services", recs, ("service_name",) + cols)
    return [dict({"service_name": r["service_name"]},
                 **{c: ctx.val("services[%d].%s" % (i, c), r.get(c)) for c in cols})
            for i, r in enumerate(x for x in recs if x.get("service_name"))]


def extract_thread_activity(ctx: Ctx) -> Optional[dict]:
    t = ctx.table("thread_activity")
    if t is None:
        return None
    recs = records(t)
    ctx.expect("thread_activity", recs, ("statistic", "total", "per_hour"))
    r = next((x for x in recs if x.get("statistic", "").lower().startswith("log switches")), None)
    if r is None:
        ctx.missing_section("thread_activity.log_switches", "row not found")
        r = {}
    return {"log_switches_total": ctx.val("thread_activity.log_switches_total", r.get("total")),
            "log_switches_per_hour": ctx.val("thread_activity.log_switches_per_hour", r.get("per_hour"))}


def extract_addm(ctx: Ctx) -> Optional[list]:
    t = ctx.table("addm")
    if t is None:
        return None
    recs = records(t)
    ctx.expect("addm", recs, ("finding_name", "avg_active_sessions_of_the_task",
                              "percent_active_sessions_of_finding", "task_name", "begin_snap_time", "end_snap_time"))
    out = []
    for r in recs:
        if r.get("finding_name"):
            p = "addm[%d]." % len(out)
            out.append({"finding": r["finding_name"],
                        "avg_active_sessions": ctx.val(p + "avg_active_sessions", r.get("avg_active_sessions_of_the_task")),
                        "pct_active_sessions": ctx.val(p + "pct_active_sessions", r.get("percent_active_sessions_of_finding")),
                        "task": r.get("task_name"),
                        "begin_time": to_iso(r.get("begin_snap_time", "")), "end_time": to_iso(r.get("end_snap_time", ""))})
    return out


def extract_os_detail(ctx: Ctx, year_hint: Optional[int] = None) -> Optional[list]:
    t = ctx.table("os_detail")
    if t is None:
        return None
    recs = records(t)
    cols = {"load": "load", "busy_pct": "pct_busy", "user_pct": "pct_user", "sys_pct": "pct_sys",
            "idle_pct": "pct_idle", "iowait_pct": "pct_iowait"}
    ctx.expect("os_detail", recs, ("snap_time",) + tuple(cols.values()))
    out = []
    for r in recs:
        if r.get("snap_time"):
            p = "os_detail[%d]." % len(out)
            out.append(dict({"snap_time": to_iso(r["snap_time"], year_hint)},
                            **{k: ctx.val(p + k, r.get(src)) for k, src in cols.items()}))
    return out


def extract_pga(ctx: Ctx) -> Optional[dict]:
    s = _one_row(ctx, "pga_summary", {"cache_hit_pct": "pga_cache_hit_pct", "wa_mb_processed": "w_a_mb_processed",
                                      "extra_wa_mb": "extra_w_a_mb_read_written"})
    out = s or {"cache_hit_pct": None, "wa_mb_processed": None, "extra_wa_mb": None}
    out["multipass_execs"] = None
    t = ctx.table("pga_hist")
    if t is not None:
        recs = records(t)
        ctx.expect("pga_hist", recs, ("m_pass_execs",))
        vals = [ctx.val("pga_hist.m_pass_execs", r.get("m_pass_execs")) for r in recs]
        out["multipass_execs"] = sum(v for v in vals if v is not None) if recs else None
    return out


def _numrecs(ctx: Ctx, key: str, path: str) -> Optional[list]:
    """All columns of a table as slug-keyed records: numbers where numeric, else the raw text (or None)."""
    t = ctx.table(key)
    if t is None:
        return None
    out = []
    for r in records(t):
        if any(r.values()):
            p = "%s[%d]." % (path, len(out))
            out.append({k: (ctx.val(p + k, v) if num(v, decimal=ctx.decimal) is not None else (v or None))
                        for k, v in r.items()})
    return out


def extract_advisories(ctx: Ctx) -> dict:
    return {n: _numrecs(ctx, k, "advisories." + n) for n, k in (
        ("sga_target", "sga_advisory"), ("shared_pool", "shared_pool_advisory"),
        ("buffer_pool", "buffer_pool_advisory"), ("pga", "pga_advisory"))}


def extract_segments(ctx: Ctx) -> dict:
    return {n: _numrecs(ctx, k, "segments." + n) for n, k in (
        ("by_logical_reads", "seg_logical"), ("by_physical_reads", "seg_physical"),
        ("by_row_lock_waits", "seg_row_lock"), ("by_buffer_busy_waits", "seg_buffer_busy"))}


def extract_activity(ctx: Ctx) -> Optional[dict]:
    t = ctx.table("activity")
    if t is None:
        return None
    recs = records(t)
    ctx.expect("activity", recs, ("statistic", "total", "per_second", "per_trans"))
    return {slug(r["statistic"]): {"total": ctx.val("activity.%s.total" % slug(r["statistic"]), r.get("total")),
                                   "per_s": ctx.val("activity.%s.per_s" % slug(r["statistic"]), r.get("per_second")),
                                   "per_txn": ctx.val("activity.%s.per_txn" % slug(r["statistic"]), r.get("per_trans"))}
            for r in recs if r.get("statistic")}


def extract_parameters(ctx: Ctx) -> Optional[dict]:
    t = ctx.table("parameters")
    if t is None:
        return None
    recs = records(t)
    ctx.expect("parameters", recs, ("parameter_name", "begin_value", "end_value_if_different"))
    return {r["parameter_name"]: {"begin": r.get("begin_value") or None, "end": r.get("end_value_if_different") or None}
            for r in recs if r.get("parameter_name")}


_EXTRACTORS = ("load_profile", "efficiency", "top_events", "time_model", "host_cpu", "instance_cpu", "os_stats",
               "memory", "io_profile", "iostat_function", "fg_events", "bg_events", "sql", "services",
               "thread_activity", "addm", "pga", "advisories", "segments", "activity", "parameters")


def parse(path: Path) -> dict:
    doc = load_doc(path)
    meta = extract_meta(doc)
    ctx = Ctx(doc, meta["decimal"])
    out = {"meta": meta}
    for name in _EXTRACTORS:
        out[name] = globals()["extract_" + name](ctx)
    m = re.match(r"(\d{4})-", meta.get("begin_time") or "")
    out["os_detail"] = extract_os_detail(ctx, int(m.group(1)) if m else None)  # shortcut: ignores year rollover inside one report, fix if reports span New Year
    out["missing_sections"], out["invalid_values"] = ctx.missing, ctx.invalid
    return out


def make_run_dir(parent: Path, meta: dict) -> Path:
    safe = lambda v: re.sub(r"[^\w.-]", "_", str(v if v is not None else "unknown"))
    base = "awr-report-%s-%s-%s_%s" % (safe(meta.get("db_name")), safe(meta.get("inst_num")),
                                       safe(meta.get("begin_snap")), safe(meta.get("end_snap")))
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        d = parent / (base if n == 1 else "%s-%d" % (base, n))
        try:
            d.mkdir()
            return d
        except FileExistsError:
            n += 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Parse an Oracle AWR HTML report into awr.json")
    ap.add_argument("awr_html")
    ap.add_argument("--out", default=".", help="parent folder for the run directory")
    a = ap.parse_args(argv)
    try:
        awr = parse(Path(a.awr_html))
    except NotAwrError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except UnsupportedFormatError as e:
        print("error: %s" % e, file=sys.stderr)
        return 3
    run = make_run_dir(Path(a.out), awr["meta"])
    (run / "awr.json").write_text(json.dumps(awr, indent=1, ensure_ascii=False), encoding="utf-8")
    print(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
