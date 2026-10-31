import json
import time

import pytest

from conftest import build_padded, run_parse


def test_cli_writes_run_dir(tmp_path, sample_path):
    run = run_parse(sample_path, tmp_path)
    assert run[0] == 0 and run[1].name == "awr-report-PRODDB-1-3138_3162"
    assert (run[1] / "awr.json").exists()


def test_second_run_gets_suffixed_folder(tmp_path, sample_path):
    run_parse(sample_path, tmp_path)
    code, second = run_parse(sample_path, tmp_path)
    assert second.name.endswith("3162-2") and (tmp_path / "awr-report-PRODDB-1-3138_3162/awr.json").exists()


def test_exit_codes(tmp_path):
    bad = tmp_path / "x.html"
    bad.write_text("<html><body><table><tr><td>hello</td></tr></table></body></html>")
    assert run_parse(bad, tmp_path)[0] == 2
    txt = tmp_path / "x.txt"
    txt.write_text("WORKLOAD REPOSITORY report for\nplain text")
    assert run_parse(txt, tmp_path)[0] == 3


def test_truncated_file(tmp_path, sample_path):
    data = sample_path.read_bytes()
    p = tmp_path / "t.html"
    p.write_bytes(data[: len(data) // 2])
    code, run = run_parse(p, tmp_path)
    awr = json.loads((run / "awr.json").read_text())
    assert code == 0 and awr["meta"]["db_name"] == "PRODDB"
    assert {"parameters", "pga_hist", "activity"} <= {m["section"] for m in awr["missing_sections"]}


@pytest.mark.slow
def test_benchmark_20mb(tmp_path, sample_path):
    padded = build_padded(sample_path, tmp_path, 20_000_000)
    assert padded.stat().st_size >= 20_000_000
    t0 = time.time()
    code, run = run_parse(padded, tmp_path)
    elapsed = time.time() - t0
    print("benchmark seconds: %.1f" % elapsed)
    assert code == 0 and elapsed < 60
