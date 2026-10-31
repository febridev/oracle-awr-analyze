"""Lint the verification SQL catalog: read-only, allowlisted packages, declared params."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import SKILL_DIR, load_yaml, read_json

REQUIRED = ("id", "title", "domain", "min_version", "license", "params")
LICENSES = {"none", "diagnostics_pack", "tuning_pack"}
FORBIDDEN = ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE", "GRANT",
             "REVOKE", "EXEC", "EXECUTE", "BEGIN", "DECLARE", "CALL")
PKG_OK = re.compile(r"^DBMS_XPLAN\.DISPLAY_(AWR|CURSOR)$|^DBMS_WORKLOAD_REPOSITORY\.AWR_\w*REPORT\w*$")
# one pass so `--` inside a string or `'` inside a comment is not misread; q-quotes before plain strings
_Q = r"(?<![\w$#])[nN]?[qQ]'(?:\[.*?\]|\(.*?\)|\{.*?\}|<.*?>|([^\s\[({<])(?:(?!\1').)*\1)'"
_COMMENT_OR_STR = re.compile(r"--[^\n]*|/\*.*?\*/|" + _Q + r"|'(?:[^']|'')*'", re.S)
_PKG_CALL = re.compile(r"\b(?:SYS\s*\.\s*)?((?:DBMS|UTL)_\w+)\s*\.\s*(\w+)")
_INT_PARAMS = ("dbid", "inst_id", "begin_snap", "end_snap")


def load_catalog(dir: Path) -> dict:
    cat: dict = {}
    for f in sorted(Path(dir).glob("*.sql")):
        for block in re.split(r"(?m)^(?=-- id:)", f.read_text()):
            if not block.strip():
                continue
            q: dict = {"file": f.name}
            lines = block.strip().splitlines()
            i = 0
            while i < len(lines) and lines[i].startswith("--"):
                m = re.match(r"--\s*(\w+):\s*(.*)$", lines[i])
                if m:
                    q[m.group(1)] = m.group(2).strip()
                i += 1
            q["sql"] = "\n".join(lines[i:]).strip()
            if "params" in q:
                q["params"] = [p.strip() for p in q["params"].split(",") if p.strip()]
            # duplicates are reported by the CLI, so keep them under a distinct key
            key = q.get("id", f"?{f.name}:{len(cat)}")
            while key in cat:
                key += "+dup"
            cat[key] = q
    return cat


def lint_query(q: dict) -> list:
    errs = [f"missing header: {h}" for h in REQUIRED if h not in q]
    if q.get("license", "none") not in LICENSES:
        errs.append(f"bad license: {q['license']}")
    sql = q.get("sql", "").replace("\r\n", "\n").replace("\r", "\n")  # SQL*Plus treats \r as a line break
    no_comments = _COMMENT_OR_STR.sub(lambda m: " " if m.group(0)[:2] in ("--", "/*") else m.group(0), sql)
    bare = _COMMENT_OR_STR.sub(" ", sql)
    marked = _COMMENT_OR_STR.sub(" x ", sql)  # placeholder keeps comment-only lines non-blank
    if any("\n" in m.group(0) and m.group(0)[:2] not in ("--", "/*") for m in _COMMENT_OR_STR.finditer(sql)):
        errs.append("newline inside a string literal is forbidden")
    body = bare.rstrip()
    if body.count(";") > (1 if body.endswith(";") else 0):
        errs.append("more than one statement (';' only allowed at the end)")
    if re.search(r"(?m)^[ \t]*/[ \t]*$", bare):
        errs.append("line consisting only of '/' is forbidden")
    if re.search(r"(?m)^[ \t]*(\.[ \t]*|#.*)$", bare):
        errs.append("line that is only '.' or starts with '#' is forbidden")
    if re.search(r"\n[ \t\r]*\n", marked.strip()):
        errs.append("blank line inside statement is forbidden")
    if not re.match(r"\s*(SELECT|WITH)\b", bare, re.I):
        errs.append("statement starts with something other than SELECT or WITH")
    if re.search(r"\bFOR\s+UPDATE\b", bare, re.I):
        errs.append("FOR UPDATE is forbidden")
    for kw in FORBIDDEN:
        if re.search(rf"\b{kw}\b", bare, re.I):
            errs.append(f"forbidden token: {kw}")
    for pkg, fn in _PKG_CALL.findall(bare.replace('"', "").upper()):
        if not PKG_OK.match(f"{pkg}.{fn}"):
            errs.append(f"package call not allowed: {pkg}.{fn}")
    used = set(re.findall(r"&(\w+)", no_comments))
    declared = set(q.get("params", []))
    if used != declared:
        errs.append(f"params mismatch: declared {sorted(declared)}, used {sorted(used)}")
    return errs


def fill(q: dict, values: dict) -> str:
    sql = q["sql"]
    for p in q["params"]:
        v = str(values[p])
        if p in _INT_PARAMS:
            if not re.fullmatch(r"[0-9]+", v.strip()):
                raise ValueError(f"{p} must be an integer: {v!r}")
            v = v.strip()
        elif p == "sql_id":
            if not re.fullmatch(r"[0-9a-z]{13}", v):
                raise ValueError(f"sql_id must match [0-9a-z]{{13}}: {v!r}")
        else:
            if re.search(r"[\x00-\x1f\x7f]", v):
                raise ValueError(f"{p} contains control characters")
            v = v.replace("'", "''")
        sql = re.sub(rf"&{p}\b", lambda _m, v=v: v, sql)
    errs = lint_query(dict(q, sql=sql, params=[]))
    if errs:
        raise ValueError("; ".join(errs))
    return sql


def lint_thresholds(thr: dict, catalog: dict) -> list:
    ids = {q.get("id") for q in catalog.values()}
    return [f"rule {rid}: unknown verify_sql id {v}" for rid, r in (thr.get("rules") or {}).items()
            for v in r.get("verify_sql") or [] if v not in ids]


def lint_run(findings: dict, catalog: dict) -> list:
    """Every available verify query in findings.json must be its catalog query with only parameters substituted."""
    by_id = {q.get("id"): q for q in catalog.values()}
    errs = []
    for f in findings.get("findings") or []:
        for v in f.get("verify_sql") or []:
            if not v.get("available"):
                continue
            q = by_id.get(v["id"])
            if q is None:
                errs.append(f"{f['id']}: {v['id']} not in catalog")
                continue
            pat = re.sub(r"\\&(\w+)", "(?s:.+?)", re.escape(q["sql"]))  # shortcut: parameter text may contain anything; fill() already validated it
            if not re.fullmatch(pat, v.get("sql") or ""):
                errs.append(f"{f['id']}: {v['id']} SQL does not match the catalog")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", type=Path, default=SKILL_DIR / "references" / "verify_sql")
    ap.add_argument("--thresholds", type=Path)
    ap.add_argument("--run", type=Path, help="run folder whose findings.json is checked against the catalog")
    args = ap.parse_args(argv)
    errors = []
    cat = load_catalog(args.catalog)
    if args.thresholds:
        errors += lint_thresholds(load_yaml(args.thresholds), cat)
    if args.run:
        errors += lint_run(read_json(args.run / "findings.json"), cat)
    for key, q in cat.items():
        if key.endswith("+dup"):
            errors.append(f"{q.get('id')}: duplicate id")
        errors += [f"{q.get('id', key)}: {e}" for e in lint_query(q)]
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
