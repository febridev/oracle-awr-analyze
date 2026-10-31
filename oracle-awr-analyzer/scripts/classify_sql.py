"""Classify each sql[] entry of awr.json (category, confidence, reasons, verify_sql)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import load_config, read_json, write_json

_RANK = ("explicit", "strong", "weak")
# shortcut: misses dynamic SQL, quoted identifiers and comma-joined tables; upgrade if A5 accuracy < 90%
_OBJ_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([\w$#.]+)(?![\w$#.]|\()", re.I)
_VIEW_RE = re.compile(r"\bG?V\$\w*", re.I)


def _any(pats, s):
    return next((p for p in pats if s and re.search(p, s, re.I)), None)


def _prefix(pats, name):
    return next((p for p in pats if re.match(p, name, re.I)), None)


def _objects(text):
    return [o for o in _OBJ_RE.findall(text or "")] + _VIEW_RE.findall(text or "")


def classify(sql: dict, db_host: str, cls: dict) -> dict:
    dba = cls.get("dba", {})
    module, text = sql.get("module") or "", sql.get("text") or ""
    program, chost = sql.get("program"), sql.get("client_host")
    tp = cls["text_patterns"]
    sig = []  # (group, category, strength, reason); lower group number wins

    seen = set()

    def add(group, cat, strength, why, kind=None):
        # one signal per (category, kind): several objects hitting the same rule are not independent evidence
        key = (group, cat, strength, kind or why)
        if key not in seen:
            seen.add(key)
            sig.append((group, cat, strength, why))

    # DBA-supplied lists: explicit
    for key, cat in (("monitoring_modules", "monitoring"),):
        p = _any(dba.get(key, []), module)
        if p:
            add(0, cat, "explicit", f"module matches DBA list {key} ({p})")
    p = _any(dba.get("app_programs", []), program or "")
    if p:
        add(0, "user_app", "explicit", f"program matches DBA app_programs ({p})")
    if chost and _any(dba.get("app_hosts", []), chost):
        add(0, "user_app", "explicit", f"client host {chost} in DBA app_hosts")
    objs = _objects(text)
    for o in objs:
        owner, _, name = o.rpartition(".")
        for key, cat in (("user_schemas", "user_app"), ("monitoring_schemas", "monitoring")):
            if owner and owner.upper() in [s.upper() for s in dba.get(key, [])]:
                add(0, cat, "explicit", f"object {o} owned by DBA {key} entry", "owner-" + key)
        if _prefix(dba.get("app_table_prefixes", []), name):
            add(0, "user_app", "explicit", f"object {o} has DBA app table prefix", "dba-prefix")
    # definitive built-in signals
    p = _any(cls["adhoc_modules"], module)
    if p:
        add(0, "adhoc", "explicit", f"module is an interactive tool ({p})")
    # module patterns
    for key, cat in (("monitoring_modules", "monitoring"), ("maintenance_modules", "maintenance"),
                     ("internal_modules", "oracle_internal")):
        p = _any(cls[key], module)
        if p:
            add(1, cat, "strong", f"module matches {key} ({p})")
    p = _any(cls["monitoring_programs"], program or "")
    if p:
        add(1, "monitoring", "strong", f"program {program} is a monitoring agent")
    p = _any(cls["generic_modules"], module)
    if p:
        add(1, "user_app", "weak", f"generic client module ({p}); proves nothing alone")
    # host
    oracle_prog = _any(cls["oracle_process_programs"], program or "")
    if chost and db_host and chost.lower() == db_host.lower() and not oracle_prog:
        add(2, "monitoring", "strong", f"client host {chost} is the database host (local agent)")
    if chost and _any(cls["app_hosts"], chost):
        add(2, "user_app", "strong", f"client host {chost} in app_hosts")
    if program and any(re.fullmatch(p, program, re.I) for p in cls["app_programs"]):
        add(2, "user_app", "strong", f"program {program} in app_programs")
    # text
    p = _any(tp["maintenance"], text)
    if p:
        add(3, "maintenance", "strong", f"SQL text matches maintenance pattern ({p})")
    p = _any(tp["internal"], text)
    if p:
        add(0, "oracle_internal", "explicit", f"SQL text touches Oracle-owned objects ({p})")
    if any(re.match(p, text, re.I) for p in tp["heartbeat"]):
        add(3, "monitoring", "weak", "heartbeat query (select 1 from dual)")
    if objs and all(_any(tp["dictionary_objects"], o) for o in objs):
        add(4, "monitoring", "strong", "only dictionary/performance views referenced")
    for o in objs:
        if _prefix(cls["app_table_prefixes"], o.rpartition(".")[2]):
            add(4, "user_app", "strong", f"object {o} has an application table prefix", "prefix")
    # internal text hit loses to a monitoring module/host signal
    if any(g in (1, 2) and c == "monitoring" for g, c, _, _ in sig):
        sig = [s for s in sig if s[1] != "oracle_internal" or s[0] != 0]

    firm = [s for s in sig if s[2] != "weak"]
    if not firm:
        weak = [s for s in sig if s[1] != "user_app"]  # generic-only weak user_app stays unknown
        if weak:
            return {"category": weak[0][1], "confidence": "low", "reasons": [w[3] for w in weak], "verify_sql": "V-01"}
        return {"category": "unknown", "confidence": "low",
                "reasons": [s[3] for s in sig] or ["no classification signal found"], "verify_sql": "V-01"}
    top = min(firm, key=lambda s: s[0])
    agree = [s for s in sig if s[1] == top[1]]
    conf = "high" if top[2] == "explicit" or len(agree) >= 2 else "medium"
    return {"category": top[1], "confidence": conf, "reasons": [a[3] for a in agree], "verify_sql": "V-01"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    cls = load_config(Path(a.config) if a.config else None)["classification"]
    f = Path(a.run_dir) / "awr.json"
    awr = read_json(f)
    host = (awr.get("meta") or {}).get("host") or ""
    for s in awr.get("sql", []):
        s.update(classify(s, host, cls))
    write_json(f, awr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
