import os
import pathlib
import shutil
import subprocess
import sys

import pytest


# The real AWR sample is restricted: it lives outside git, in <repo>/local-samples/ (git-ignored)
# or in $AWR_SAMPLE_DIR. Tests that need it are skipped when it is absent.
SAMPLE_DIR = pathlib.Path(os.environ.get("AWR_SAMPLE_DIR",
                                         pathlib.Path(__file__).resolve().parents[3] / "local-samples"))
SAMPLE_HTML = SAMPLE_DIR / "awrrpt_1_3138_3162.html"


def _require_sample() -> pathlib.Path:
    if not SAMPLE_HTML.exists():
        pytest.skip(f"real AWR sample not available ({SAMPLE_HTML}); set AWR_SAMPLE_DIR")
    return SAMPLE_HTML


@pytest.fixture
def sample_path() -> pathlib.Path:
    return _require_sample()


@pytest.fixture(scope="session")
def sample_meta() -> dict:
    """Real identifiers of the local sample (db name, host, SQL ids...), kept out of the repo."""
    import json
    _require_sample()
    meta = SAMPLE_DIR / "sample.json"
    if not meta.exists():
        pytest.skip(f"{meta} not available")
    return json.loads(meta.read_text())


def make_ctx(tmp_path, html):
    from parse_awr import Ctx, extract_meta, load_doc
    p = tmp_path / "r.html"
    p.write_text(html)
    doc = load_doc(p)
    return Ctx(doc, extract_meta(doc)["decimal"])


@pytest.fixture(scope="session")
def sample_ctx(tmp_path_factory):
    return make_ctx(tmp_path_factory.mktemp("s"), _require_sample().read_text())


@pytest.fixture(scope="session")
def sample_awr():
    from parse_awr import parse
    return parse(_require_sample())


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


@pytest.fixture(scope="session")
def sample_run(tmp_path_factory):
    rc, run = run_parse(_require_sample(), tmp_path_factory.mktemp("g"))
    assert rc == 0
    for s in ("classify_sql.py", "analyze.py"):
        r = subprocess.run([sys.executable, "oracle-awr-analyzer/scripts/" + s, str(run)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    return run


@pytest.fixture
def good_run(sample_run, tmp_path):
    from common import read_json, write_json
    run = tmp_path / "run"
    shutil.copytree(sample_run, run)
    fs = read_json(run / "findings.json")["findings"]
    interp = {"executive_summary": ["The database is Normal on average."], "findings": {}}
    for f in fs:
        e = f["evidence"][0]
        v = e["value"]
        facts = f"{f['title']}: {e['metric']} was {v}." if isinstance(v, (int, float)) else f"{f['title']}."
        interp["findings"][f["id"]] = {"facts": facts, "hypothesis": "", "user_impact": "", "recommendation": "",
                                       "change_risk": "", "approval_required": False}
    write_json(run / "interpretation.json", interp)
    return run
