import re

from svg import CATEGORY_COLORS, NEUTRAL, SEVERITY_COLORS, hbar, stacked_bar


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def test_palette_contrast():
    assert list(SEVERITY_COLORS) == ["P0", "P1", "P2", "P3", "P4"]
    assert set(CATEGORY_COLORS) == {"user_app", "monitoring", "maintenance", "oracle_internal", "adhoc", "unknown"}
    for col in [*SEVERITY_COLORS.values(), *CATEGORY_COLORS.values(), NEUTRAL]:
        assert 1.05 / (_lum(col) + 0.05) >= 3.0, col
    ls = [_lum(c) for c in SEVERITY_COLORS.values()]
    assert ls == sorted(ls)  # P0 darkest -> P4 lightest


def test_hbar_labels_and_escaping():
    s = hbar([{"label": "<b>x</b>", "value": 35.6, "tag": "P2", "color": SEVERITY_COLORS["P2"]}], ref_line=1.0, ref_label="capacity")
    assert s.startswith("<svg") and "&lt;b&gt;x&lt;/b&gt;" in s and "P2" in s and "35.6" in s and "capacity" in s
    assert "<title>" in s or 'aria-label' in s


def test_hbar_ref_line_beyond_max_extends_axis():
    s = hbar([{"label": "a", "value": 14.19, "tag": "", "color": NEUTRAL}], ref_line=20, ref_label="cap")
    m = re.search(r'<line class="ref" x1="([\d.]+)"', s)
    assert m and float(m.group(1)) <= 640


def test_stacked_bar_sums_to_100_width():
    for vals in ([60, 30, 10], [50, 20, 10]):  # exact and under 100
        s = stacked_bar([{"label": str(i), "value": v, "color": NEUTRAL} for i, v in enumerate(vals)])
        ws = [float(w) for w in re.findall(r'<rect class="seg"[^>]* width="([\d.]+)"', s)]
        assert abs(sum(ws) - 640) <= 1


def test_empty_rows():
    assert "no data" in hbar([])


def test_none_skipped_and_all_zero():
    s = hbar([{"label": "gone", "value": None, "tag": "", "color": NEUTRAL},
              {"label": "z", "value": 0, "tag": "", "color": NEUTRAL}])
    assert "gone" not in s and ">z<" in s and "nan" not in s
    assert "no data" in hbar([{"label": "q", "value": None, "tag": "", "color": NEUTRAL}])


def test_no_active_content():
    s = hbar([{"label": "a", "value": 1, "tag": "", "color": NEUTRAL}]) + stacked_bar([{"label": "a", "value": 1, "color": NEUTRAL}])
    assert not re.search(r"<script|href=|src=|foreignObject", s)


def test_title_escaped_once_and_outputs_parse():
    from xml.dom.minidom import parseString
    t = 'a<b&"c'
    row = [{"label": "a", "value": 1, "tag": "P1", "color": NEUTRAL}]
    d = parseString(hbar(row, ref_line=2, ref_label="r&", title=t))
    assert d.documentElement.getAttribute("aria-label") == t
    parseString(stacked_bar([{"label": "<x>", "value": 1, "color": NEUTRAL}], title=t))
    parseString(hbar([]))


def test_long_labels_truncated_with_full_title():
    from xml.dom.minidom import parseString
    long = "L" * 60
    for s in (hbar([{"label": long, "value": 1, "tag": "", "color": NEUTRAL}]),
              stacked_bar([{"label": long, "value": 1, "color": NEUTRAL}])):
        d = parseString(s)
        texts = [t for t in d.getElementsByTagName("text") if t.getElementsByTagName("title")]
        assert texts
        shown = texts[0].firstChild.data
        assert len(shown) <= 31 and shown.endswith("…")
        assert long in texts[0].getElementsByTagName("title")[0].firstChild.data


def test_priority_strip_counts_and_escaping():
    from xml.dom.minidom import parseString
    from svg import priority_strip
    s = priority_strip([{"level": "P1", "name": "High & <b>", "action": "Today", "count": 2},
                        {"level": "P4", "name": "Info", "action": "Record only", "count": 0}], title="t")
    parseString(s)
    assert ">2<" in s and ">0<" in s and "High &amp; &lt;b&gt;" in s and SEVERITY_COLORS["P1"] in s
    assert SEVERITY_COLORS["P4"] not in s  # an empty level carries no severity color


def test_domain_grid_cells():
    from xml.dom.minidom import parseString
    from svg import domain_grid
    s = domain_grid([{"label": "<io>", "counts": {"P2": 3}}], title="t")
    parseString(s)
    assert "&lt;io&gt;" in s and ">3<" in s and all(p in s for p in SEVERITY_COLORS)
    assert not re.search(r"<script|href=|src=|foreignObject", s)
    assert "no data" in domain_grid([])


def test_title_is_visible_not_only_on_hover():
    s = hbar([{"label": "a", "value": 1, "tag": "", "color": NEUTRAL}], title="My chart")
    assert re.search(r"<text[^>]*>My chart</text>", s)
    assert 'height="52"' in s  # one row plus the title band
