from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Optional

import yaml

SKILL_DIR = Path(__file__).resolve().parents[1]
_USER_CLS_KEYS = ("user_schemas", "monitoring_schemas", "monitoring_modules", "app_hosts", "app_programs",
                  "app_table_prefixes")


def read_json(p) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write_json(p, obj) -> None:
    Path(p).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def load_yaml(p) -> dict:
    return yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        base[k] = _deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return base


def load_config(path: Optional[Path]) -> dict:
    user = load_yaml(path) if path else {}
    thr_file = SKILL_DIR / "references" / "thresholds.yaml"
    thresholds = load_yaml(thr_file) if thr_file.exists() else {}
    _deep_merge(thresholds, copy.deepcopy(user.get("thresholds") or {}))
    cls = load_yaml(SKILL_DIR / "references" / "sql_classification.yaml")
    # User lists live apart from built-ins so classify() can rank them as explicit (DBA-supplied).
    cls["dba"] = {k: list(user[k]) for k in _USER_CLS_KEYS if user.get(k)}
    profile = user.get("storage_profile")
    return {"thresholds": thresholds, "classification": cls, "storage_profile": profile or "san",
            "storage_assumed": not profile, "audience": user.get("audience") or "both",
            "workload_norms": user.get("workload_norms") or {}}
