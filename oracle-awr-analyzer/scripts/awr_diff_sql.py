"""Write awr_diff_<db>.sql (SQL*Plus script for AWR_DIFF_REPORT_HTML) when findings.diff.suggested."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import read_json

# argument order per references/awr_diff.md: dbid, inst, begin, end for period 1 (baseline) then period 2 (current)


def db_label(meta: dict) -> str:
    return re.sub(r"[^A-Za-z0-9_$#]", "", str(meta.get("db_name") or "")) or "db"


def _int(meta: dict, k: str) -> str:
    v = str(meta.get(k)).strip()
    if not re.fullmatch(r"[0-9]+", v):
        raise ValueError(f"meta.{k} must be an integer: {v!r}")
    return v


def diff_sql(meta: dict) -> str:
    d, i, b, e = (_int(meta, k) for k in ("dbid", "inst_num", "begin_snap", "end_snap"))
    return (f"SET LONG 100000000 LONGCHUNKSIZE 100000 PAGESIZE 0 LINESIZE 32767 TRIMSPOOL ON FEEDBACK OFF\n"
            f"SPOOL awr_diff_{db_label(meta)}.html\n"
            f"SELECT output\n"
            f"FROM   TABLE(DBMS_WORKLOAD_REPOSITORY.AWR_DIFF_REPORT_HTML(\n"
            f"         {d}, {i}, &base_begin_snap, &base_end_snap,\n"
            f"         {d}, {i}, {b}, {e}));\n"
            f"SPOOL OFF\n")


def strip_sqlplus(script: str) -> str:
    return "".join(l + "\n" for l in script.splitlines() if not re.match(r"(SET|SPOOL)\s", l)).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir")
    run = Path(ap.parse_args(argv).run_dir)
    if not (read_json(run / "findings.json").get("diff") or {}).get("suggested"):
        print("diff not suggested")
        return 0
    meta = read_json(run / "awr.json")["meta"]
    try:
        out = run / f"awr_diff_{db_label(meta)}.sql"
        out.write_text(diff_sql(meta), encoding="utf-8")
    except (ValueError, OSError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
