from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Optional

import yaml

SKILL_DIR = Path(__file__).resolve().parents[1]
_PROFILES = ("hdd", "san", "flash")
_AUDIENCES = ("dba", "user", "both")
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


class ConfigError(ValueError):
    pass


def _read_config(path) -> dict:
    try:
        user = load_yaml(path)
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e.strerror or e}")
    except yaml.YAMLError as e:
        raise ConfigError(f"config file {path} is not valid YAML: {e}")
    if not isinstance(user, dict):
        raise ConfigError(f"config file {path} must be a YAML mapping at top level")
    return user


def _as_list(user, key, path) -> list:
    v = user.get(key)
    v = [] if v is None else v if isinstance(v, list) else [v]  # a scalar is a one-item list
    for pat in v:
        try:
            re.compile(str(pat))
        except re.error as e:
            raise ConfigError(f"config file {path}: invalid regex {pat!r} in '{key}': {e}")
    return [str(x) for x in v]


def load_config(path: Optional[Path]) -> dict:
    user = _read_config(path) if path else {}
    over = user.get("thresholds") or {}
    if not isinstance(over, dict):
        raise ConfigError(f"config file {path}: 'thresholds' must be a mapping")
    thr_file = SKILL_DIR / "references" / "thresholds.yaml"
    thresholds = load_yaml(thr_file) if thr_file.exists() else {}
    _deep_merge(thresholds, copy.deepcopy(over))
    cls = load_yaml(SKILL_DIR / "references" / "sql_classification.yaml")
    # User lists live apart from built-ins so classify() can rank them as explicit (DBA-supplied).
    cls["dba"] = {k: v for k in _USER_CLS_KEYS for v in [_as_list(user, k, path)] if v}
    profile = user.get("storage_profile")
    if profile is not None and profile not in _PROFILES:
        raise ConfigError(f"config file {path}: storage_profile must be one of {', '.join(_PROFILES)}, got {profile!r}")
    audience = user.get("audience") or "both"
    if audience not in _AUDIENCES:
        raise ConfigError(f"config file {path}: audience must be one of {', '.join(_AUDIENCES)}, got {audience!r}")
    return {"thresholds": thresholds, "classification": cls, "storage_profile": profile or "san",
            "storage_assumed": not profile, "audience": audience,
            "workload_norms": user.get("workload_norms") or {}}
