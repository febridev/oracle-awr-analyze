"""Inline SVG chart helpers (no scripts, no external refs)."""
from __future__ import annotations

from xml.sax.saxutils import escape

# Palette: dataviz default hues where they reach 3:1 on white; yellow/aqua/magenta slots
# replaced by darker steps. Severity = one-hue ramp, P0 darkest -> P4 lightest.
SEVERITY_COLORS = {"P0": "#7f1d1d", "P1": "#b42318", "P2": "#c2410c", "P3": "#d4631f", "P4": "#dc7a3c"}
CATEGORY_COLORS = {
    "user_app": "#2a78d6", "monitoring": "#6250d6", "maintenance": "#008300",
    "oracle_internal": "#a8590b", "adhoc": "#c2306a", "unknown": "#6b6b6b",
}
NEUTRAL = "#6b6b6b"

W, ROW, LABEL_W, INK = 640, 28, 220, "#1f1f1f"


def _fmt(v: float) -> str:
    return f"{v:g}" if abs(v) < 1e6 else f"{v:.0f}"


def _svg(h: int, title: str, body: str) -> str:
    t = escape(title or "chart")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" '
            f'role="img" aria-label="{escape(t, {chr(34): "&quot;"})}" font-family="sans-serif" font-size="12">'
            f'<title>{t}</title>{body}</svg>')


def hbar(rows, ref_line=None, ref_label="", unit="", title=""):
    rows = [r for r in rows if r.get("value") is not None]  # None rows are skipped
    if not rows:
        return _svg(ROW, title, f'<text x="4" y="18" fill="{INK}">no data</text>')
    top = 20 if ref_line is not None else 0
    axis = max(max(r["value"] for r in rows), ref_line or 0) * 1.1 or 1  # all-zero -> 1, no div by 0
    x0, bar_max = LABEL_W, W - LABEL_W - 110
    out = []
    for i, r in enumerate(rows):
        y = top + i * ROW
        w = max(r["value"], 0) / axis * bar_max
        txt = f'{_fmt(r["value"])}{escape(unit)}' + (f' {escape(r["tag"])}' if r.get("tag") else "")
        out.append(f'<text x="{LABEL_W - 6}" y="{y + 18}" text-anchor="end" fill="{INK}">{escape(str(r["label"]))}</text>'
                   f'<rect x="{x0}" y="{y + 5}" width="{w:.1f}" height="18" fill="{r["color"]}"/>'
                   f'<text x="{x0 + w + 6:.1f}" y="{y + 18}" fill="{INK}">{txt}</text>')
    h = top + len(rows) * ROW
    if ref_line is not None:
        rx = x0 + ref_line / axis * bar_max
        out.append(f'<line class="ref" x1="{rx:.1f}" y1="{top - 4}" x2="{rx:.1f}" y2="{h}" stroke="{INK}" stroke-dasharray="4 3"/>'
                   f'<text x="{rx:.1f}" y="12" text-anchor="middle" fill="{INK}">{escape(ref_label)}</text>')
    return _svg(h, title, "".join(out))


def stacked_bar(parts, title=""):
    parts = [p for p in parts if p.get("value") and p["value"] > 0]
    total = sum(p["value"] for p in parts)
    if not total:
        return _svg(ROW, title, f'<text x="4" y="18" fill="{INK}">no data</text>')
    x, segs, legend = 0.0, [], []
    for i, p in enumerate(parts):
        w = p["value"] / total * W  # scale to parts' total so the bar always fills
        lab = f'{escape(str(p["label"]))} {_fmt(round(p["value"], 2))}'
        segs.append(f'<rect class="seg" x="{x:.2f}" y="0" width="{w:.2f}" height="24" fill="{p["color"]}"><title>{lab}</title></rect>')
        # legend text carries identity so color is never the only cue
        legend.append(f'<rect x="{(i % 3) * 213}" y="{34 + i // 3 * 18}" width="10" height="10" fill="{p["color"]}"/>'
                      f'<text x="{(i % 3) * 213 + 14}" y="{43 + i // 3 * 18}" fill="{INK}">{lab}</text>')
        x += w
    h = 34 + ((len(parts) + 2) // 3) * 18
    return _svg(h, title, "".join(segs) + "".join(legend))
