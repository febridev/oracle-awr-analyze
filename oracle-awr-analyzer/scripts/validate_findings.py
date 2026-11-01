"""Validate findings.json (against awr.json) and the agent-written interpretation.json."""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Any

from common import read_json

REQUIRED = ("id", "domain", "subject", "title", "severity", "triggered_by", "load_based", "confidence",
            "evidence", "verify_sql", "fallback", "severity_adjustments")
TEXT = ("facts", "hypothesis", "user_impact", "recommendation", "change_risk")
# shortcut: misses paraphrases; upgrade if A6 review finds leaks
CAUSAL = re.compile(r"\b(because|due to|caused by|likely|probably|suggests)\b", re.I)
NUM = re.compile(r"(?<![\w.,])(-?)(\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")
# shortcut: pattern list, not a SQL parser; extend when review finds a command shape that slips through
CMD = re.compile(
    r"\bALTER\s+(TABLE|INDEX)\s+[\w.$\"]+\s+(REBUILD|MOVE|ADD|MODIFY|DROP|RENAME|SHRINK|ENABLE|DISABLE|COALESCE|PARALLEL|NOPARALLEL|LOGGING|NOLOGGING|UNUSABLE|COMPRESS)\b"
    r"|\bALTER\s+DATABASE\s+(DATAFILE|TEMPFILE|ADD|DROP|RENAME|CLEAR|SET|DEFAULT|ARCHIVELOG|NOARCHIVELOG|FLASHBACK|FORCE|OPEN|MOUNT|RECOVER|BACKUP)\b"
    r"|\bALTER\s+(SYSTEM|SESSION)\s+(SET|KILL|FLUSH|SWITCH|CHECKPOINT|RESET)\b"
    r"|\bALTER\s+USER\s+\S+\s+(IDENTIFIED|ACCOUNT|QUOTA|DEFAULT|PROFILE)\b"
    r"|\bCREATE\s+(UNIQUE\s+|BITMAP\s+)?INDEX\s+\w+\s+ON\b"
    r"|\bCREATE\s+(GLOBAL\s+TEMPORARY\s+)?TABLE\s+[\w.$]+\s*(\(|AS\s+SELECT\b)"
    r"|\bDROP\s+(INDEX|TABLE|USER)\s+\w|\bTRUNCATE\s+TABLE\s+\w"
    r"|\b(GRANT|REVOKE)\s+[^\n]*?\bON\s+[\w.$]+\s+(TO|FROM)\s+\w"
    r"|\b(GRANT|REVOKE)\s+(DBA|CONNECT|RESOURCE|SYSDBA|SELECT\s+ANY\s+\w+|CREATE\s+\w+|\w+_ROLE)(\s*,\s*\w+)*\s+(TO|FROM)\s+\w"
    r"|\bKILL\s+SESSION\s+'|\bEXEC(UTE)?\s+\w+\.\w+\s*\(|\bBEGIN\s+[\w.]+\s*\([^\n]*\bEND\s*;|```"
    r"|`\s*(SELECT|INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|GRANT|EXEC\w*)\s+\w|;[ \t]*$", re.I | re.M)
LABEL = re.compile(r"(?<![\w.-])P[0-4](?![\w-]|\.\d)")
_TOK = re.compile(r"[^.\[\]]+|\[\d+\]")


def resolve(awr: dict, path: str) -> Any:
    cur: Any = awr
    for t in _TOK.findall(path):
        try:
            cur = cur[int(t[1:-1])] if t[0] == "[" else cur[t]
        except (KeyError, IndexError, TypeError):
            raise KeyError(path)
    return cur


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _leaves(o, out):
    if _isnum(o):
        out.append(float(o))
    elif isinstance(o, dict):
        for v in o.values():
            _leaves(v, out)
    elif isinstance(o, list):
        for v in o:
            _leaves(v, out)
    return out


def _same(a, b) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) if _isnum(a) and _isnum(b) else a == b


def _unknown_numbers(text: str, known: list, rounded: list, exact: list) -> list:
    """Numbers in text matching no allowed value. known = identifier tokens (snap ids, real sql/verify/finding ids, release)."""
    text = LABEL.sub(" ", text)
    for t in sorted(map(str, known), key=len, reverse=True):
        text = re.sub(rf"(?<![\w.-]){re.escape(t)}(?![\w-]|\.\d)", " ", text)
    bad = []
    for sign, m in NUM.findall(text):
        v = float(m.replace(",", "")) * (-1 if sign else 1)
        d = len(m.split(".")[1]) if "." in m and "e" not in m.lower() else 0
        # shortcut: other awr.json values must match exactly (rounding them admits near-miss inventions); upgrade if A6 review finds legit rounding rejected
        if not (any(abs(round(x, d) - v) < 1e-9 for x in rounded)
                or any(math.isclose(x, v, rel_tol=1e-9, abs_tol=1e-9) for x in rounded + exact)):
            bad.append(sign + m)
    return bad


def validate(run_dir: Path):
    run = Path(run_dir)
    errs: list = []
    warns: list = []
    awr, fj = read_json(run / "awr.json"), read_json(run / "findings.json")
    findings = fj.get("findings", [])
    allv = _leaves(awr, [])
    meta = awr.get("meta", {})
    ids = [meta.get(k) for k in ("begin_snap", "end_snap", "dbid", "release") if meta.get(k) is not None]
    ids += [x["sql_id"] for x in awr.get("sql") or [] if x.get("sql_id")] + [f["id"] for f in findings if "id" in f]
    vids = lambda f: [v["id"] for v in f.get("verify_sql") or [] if isinstance(v, dict) and v.get("id")]  # noqa: E731
    all_ev = [e["value"] for f in findings for e in f.get("evidence") or [] if isinstance(e, dict) and _isnum(e.get("value"))]
    by_id = {}
    for f in findings:
        fid = f.get("id", "?")
        by_id[fid] = f
        for k in REQUIRED:
            if k not in f:
                errs.append(f"{fid}.{k}: required field missing")
        for k in ("evidence", "verify_sql", "triggered_by"):
            if k in f and not f[k]:
                errs.append(f"{fid}.{k}: must not be empty")
        for e in f.get("evidence") or []:
            try:
                got = resolve(awr, e["path"])
                if not _same(got, e["value"]):
                    errs.append(f"{fid}.evidence: {e['path']} is {got!r} in awr.json but finding says {e['value']!r}")
            except (KeyError, TypeError):
                errs.append(f"{fid}.evidence: path {e.get('path') if isinstance(e, dict) else e!r} does not resolve")
        v = f.get("verify_sql") or []
        if v and not any(x.get("available") for x in v):
            warns.append(f"{fid}.verify_sql: no verify query is available for this finding")

    ip = run / "interpretation.json"
    if not ip.exists():
        warns.append("interpretation.json: not found; report will use fallback text")
        return errs, warns
    try:
        interp = read_json(ip)
        assert isinstance(interp, dict)
    except Exception as ex:  # malformed JSON or wrong top-level type: report, never traceback
        errs.append(f"interpretation.json: cannot read as a JSON object ({ex.__class__.__name__})")
        return errs, warns
    summ = interp.get("executive_summary", [])
    if not isinstance(summ, list) or not all(isinstance(s, str) for s in summ):
        errs.append("interpretation.json.executive_summary: must be a list of strings")
    else:
        if len(summ) > 5:
            errs.append(f"executive_summary: {len(summ)} lines, max 5")
        for s in summ:
            if CMD.search(s):
                errs.append("executive_summary: contains an executable change command")
            for n in _unknown_numbers(s, ids + [v for f in findings for v in vids(f)], all_ev, allv):
                errs.append(f"executive_summary: number {n} not found in the report data")
    fi = interp.get("findings", {})
    if not isinstance(fi, dict):
        errs.append("interpretation.json.findings: must be an object")
        return errs, warns
    for fid, ent in fi.items():
        if fid not in by_id:
            errs.append(f"{fid}: unknown finding id")
            continue
        if not isinstance(ent, dict):
            errs.append(f"{fid}: entry must be an object")
            continue
        evv = [e["value"] for e in by_id[fid].get("evidence") or [] if isinstance(e, dict) and _isnum(e.get("value"))]
        for k, val in ent.items():
            if k == "approval_required":
                if not isinstance(val, bool):
                    errs.append(f"{fid}.{k}: must be true or false, got {type(val).__name__}")
            elif k not in TEXT:
                errs.append(f"{fid}.{k}: unknown field")
            elif not isinstance(val, str):
                errs.append(f"{fid}.{k}: must be a string, got {type(val).__name__}")
            else:
                if CMD.search(val):
                    errs.append(f"{fid}.{k}: contains an executable change command")
                for n in _unknown_numbers(val, ids + vids(by_id[fid]), evv, allv):
                    errs.append(f"{fid}.{k}: number {n} not found in the report data")
                if k == "facts" and (m := CAUSAL.search(val)):
                    errs.append(f"{fid}.facts: causal wording '{m.group(0)}' belongs in hypothesis")
    for fid in by_id:
        if fid not in fi:
            warns.append(f"{fid}: no interpretation entry; fallback text will be used")
    return errs, warns


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate findings.json and interpretation.json")
    ap.add_argument("run_dir")
    run = Path(ap.parse_args(argv).run_dir)
    try:
        errs, warns = validate(run)
    except (OSError, ValueError, KeyError, AttributeError) as ex:
        print(f"error: cannot read findings.json/awr.json in {run}: {ex}", file=sys.stderr)
        return 2
    for e in errs:
        print(f"ERROR {e}")
    for w in warns:
        print(f"WARN {w}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
