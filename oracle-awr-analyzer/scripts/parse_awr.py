"""Parse an Oracle AWR HTML report into JSON."""
from __future__ import annotations

import re
from typing import Optional

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
    return re.sub(r"[^a-z0-9]+", "_", label.lower().replace("%", "pct")).strip("_")
