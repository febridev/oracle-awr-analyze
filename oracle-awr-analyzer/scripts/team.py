"""Plan which model tier writes, verifies and rewrites each group of findings (writes RUN/team_plan.json)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import read_json, write_json

# severity -> writer; writer -> (verifier, rewrite). Groups are ordered opus, sonnet, haiku.
WRITER = {"P0": "opus", "P1": "opus", "P2": "sonnet", "P3": "haiku", "P4": "haiku"}
REVIEW = {"opus": ("opus", "opus"), "sonnet": ("sonnet", "opus"), "haiku": ("sonnet", "sonnet")}
STALE = ("interpretation.json", "interpretation.part-*.json", "interpretation.rewrite-*.json")


def plan(findings: list[dict]) -> dict:
    by: dict[str, list[str]] = {}
    for f in findings:
        if f["severity"] not in WRITER:
            raise ValueError(f"finding {f['id']}: unknown severity {f['severity']!r}")
        by.setdefault(WRITER[f["severity"]], []).append(f["id"])
    by = {w: by[w] for w in REVIEW if w in by} or {"sonnet": []}  # no findings: sonnet writes the summary only
    groups = [{"writer_model": w, "verifier_model": REVIEW[w][0], "rewrite_model": REVIEW[w][1], "findings": ids,
               "summary": False, "part": f"interpretation.part-{w}.json"} for w, ids in by.items()]
    groups[0]["summary"] = True
    return {"groups": groups}


def _load(run: Path, name: str, ids: set[str], summary: bool) -> dict:
    try:
        d = read_json(run / name)
    except (OSError, ValueError) as ex:
        raise ValueError(f"{name}: cannot read as JSON ({ex})")
    if not isinstance(d, dict) or not isinstance(d.get("findings", {}), dict):
        raise ValueError(f"{name}: must be an object with an object 'findings'")
    for fid in d.get("findings", {}):
        if fid not in ids:
            raise ValueError(f"{name}: finding {fid} is not allowed here")
    if "executive_summary" in d and not summary:
        raise ValueError(f"{name}: executive_summary is not allowed here")
    return d


def merge(run: Path, team_plan: dict, drop: list[str]) -> tuple[dict, list[str]]:
    groups, warns, out = team_plan["groups"], [], {"findings": {}}
    planned = {i for g in groups for i in g["findings"]}
    for g in groups:
        if not (run / g["part"]).exists():
            warns.append(f"WARN {g['part']}: missing, its findings use standard text")
            continue
        d = _load(run, g["part"], set(g["findings"]), g["summary"])
        out["findings"].update(d.get("findings", {}))
        if "executive_summary" in d:
            out["executive_summary"] = d["executive_summary"]
    rewrites = []
    for p in run.glob("interpretation.rewrite-*.json"):
        m = re.fullmatch(r"interpretation\.rewrite-(\d+)\.json", p.name)
        if not m:
            raise ValueError(f"{p.name}: rewrite files must be named interpretation.rewrite-<n>.json")
        rewrites.append((int(m[1]), p.name))
    for _, name in sorted(rewrites):
        d = _load(run, name, planned, True)
        out["findings"].update(d.get("findings", {}))
        if "executive_summary" in d:
            out["executive_summary"] = d["executive_summary"]
    for i in drop:
        if i == "SUMMARY":
            out.pop("executive_summary", None)
        elif i in planned:
            out["findings"].pop(i, None)
        else:
            raise ValueError(f"--drop {i}: not a planned finding id or SUMMARY")
    return out, warns


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="write RUN/team_plan.json").add_argument("run_dir")
    mp = sub.add_parser("merge", help="write RUN/interpretation.json from agent parts")
    mp.add_argument("run_dir")
    mp.add_argument("--drop", nargs="*", default=[], metavar="ID")
    args = ap.parse_args(argv)
    run = Path(args.run_dir)
    try:
        if args.cmd == "merge":
            result, warns = merge(run, read_json(run / "team_plan.json"), args.drop)
            write_json(run / "interpretation.json", result)
            print(*warns, run / "interpretation.json", sep="\n")
            return 0
        result = plan(read_json(run / "findings.json").get("findings", []))
        for pat in STALE:
            for p in run.glob(pat):
                p.unlink()
        write_json(run / "team_plan.json", result)
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    print((run / "team_plan.json").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
