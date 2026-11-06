"""Plan which model tier writes, verifies and rewrites each group of findings (writes RUN/team_plan.json)."""
from __future__ import annotations

import argparse
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="write RUN/team_plan.json").add_argument("run_dir")
    run = Path(ap.parse_args(argv).run_dir)
    try:
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
