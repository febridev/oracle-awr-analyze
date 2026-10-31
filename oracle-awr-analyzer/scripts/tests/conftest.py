import pathlib

import pytest


@pytest.fixture
def sample_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2] / "examples" / "awrrpt_1_3138_3162.html"


def make_ctx(tmp_path, html):
    from parse_awr import Ctx, extract_meta, load_doc
    p = tmp_path / "r.html"
    p.write_text(html)
    doc = load_doc(p)
    return Ctx(doc, extract_meta(doc)["decimal"])


@pytest.fixture(scope="session")
def sample_ctx(tmp_path_factory):
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "examples" / "awrrpt_1_3138_3162.html"
    return make_ctx(tmp_path_factory.mktemp("s"), src.read_text())


@pytest.fixture(scope="session")
def sample_awr():
    from parse_awr import parse
    return parse(pathlib.Path(__file__).resolve().parents[2] / "examples" / "awrrpt_1_3138_3162.html")


def run_parse(path, out_parent):
    """Run parse_awr.py as a subprocess; returns (exitcode, run dir Path or None)."""
    import subprocess
    import sys
    root = pathlib.Path(__file__).resolve().parents[3]
    py = root / ".venv" / "bin" / "python"
    r = subprocess.run([str(py) if py.exists() else sys.executable, str(root / "oracle-awr-analyzer/scripts/parse_awr.py"),
                        str(path), "--out", str(out_parent)], capture_output=True, text=True)
    return r.returncode, (pathlib.Path(r.stdout.strip()) if r.returncode == 0 else None)


def build_padded(src, out_dir, min_bytes):
    """Copy of src whose 'text of the SQL statements' table is padded with copies of its own rows."""
    html = pathlib.Path(src).read_text()
    start = html.index("text of the SQL statements")
    first = html.index("</tr>", start) + len("</tr>")  # skip header row
    end = html.index("</table>", first)
    rows = html[first:end]
    n = max(0, (min_bytes - len(html)) // len(rows.encode()) + 1)
    p = pathlib.Path(out_dir) / "padded.html"
    p.write_text(html[:end] + rows * n + html[end:])
    return p


@pytest.fixture(scope="session")
def cfg():
    from common import load_config
    return load_config(None)


def mini_awr(cpus, elapsed_min, db_time_min, db_cpu_pct, idle_pct):
    return {"meta": {"cpus": cpus, "cores": cpus, "elapsed_min": elapsed_min, "db_time_min": db_time_min},
            "time_model": {"db_cpu": {"time_s": 0, "pct_db_time": db_cpu_pct}},
            "host_cpu": {"idle_pct": idle_pct}, "top_events": [], "addm": [], "os_detail": []}


def fixture(name):
    import json
    return json.loads((pathlib.Path(__file__).resolve().parent / "fixtures" / f"{name}.json").read_text())


def max_by_subject(cands):
    """{(domain, subject): highest severity} — P0 is highest, so the smallest string wins."""
    out = {}
    for c in cands:
        k = (c["domain"], c["subject"])
        out[k] = min(out.get(k, c["severity"]), c["severity"])
    return out
