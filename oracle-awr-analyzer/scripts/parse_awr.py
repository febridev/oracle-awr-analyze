"""Parse an Oracle AWR HTML report into JSON."""
from __future__ import annotations

import re
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
