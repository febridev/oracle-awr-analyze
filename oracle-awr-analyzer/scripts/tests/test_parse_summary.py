from conftest import make_ctx
from parse_awr import extract_load_profile, extract_efficiency, extract_top_events, extract_time_model, \
    extract_host_cpu, extract_instance_cpu, extract_os_stats, extract_memory, extract_io_profile, \
    extract_iostat_function


def test_load_profile(sample_ctx):
    lp = extract_load_profile(sample_ctx)
    assert lp["db_time_s"]["per_s"] == 7.1 and lp["transactions"]["per_s"] == 604.0
    assert lp["redo_size_bytes"]["per_txn"] == 37067.6


def test_library_hit_invalid(sample_ctx):
    eff = extract_efficiency(sample_ctx)
    assert eff["library_hit_pct"] is None and eff["soft_parse_pct"] == 99.98
    assert eff["execute_to_parse_pct"] == 2.75
    assert {"path": "efficiency.library_hit_pct", "raw": "-51.96",
            "reason": "outside 0-100"} in sample_ctx.invalid


def test_top_events(sample_ctx):
    ev = extract_top_events(sample_ctx)
    assert ev[0] == {"event": "db file sequential read", "waits": 370000000.0, "total_wait_s": 219200.0,
                     "avg_wait_ms": 0.587, "pct_db_time": 35.6, "wait_class": "User I/O"}
    assert ev[1]["event"] == "DB CPU" and ev[1]["wait_class"] is None


def test_cpu_memory_io(sample_ctx):
    assert extract_host_cpu(sample_ctx)["idle_pct"] == 91.6
    assert extract_instance_cpu(sample_ctx)["db_time_waiting_cpu_rm_pct"] == 0.0
    assert extract_time_model(sample_ctx)["db_cpu"]["pct_db_time"] == 32.82
    assert extract_os_stats(sample_ctx)["LOAD"] == {"value": 3.0, "end_value": 6.0}
    assert extract_memory(sample_ctx)["pct_host_mem_used_for_sga_pga"]["end"] == 47.80
    assert extract_io_profile(sample_ctx)["total_mb"]["rw_per_s"] == 1214.6
    io = extract_iostat_function(sample_ctx)
    assert io[0]["function_name"] == "Others" and io[0]["avg_time"] == 0.5141


def test_missing_section_recorded(tmp_path, sample_path):
    html = sample_path.read_text().replace('summary="This table displays load profile"', 'summary="x"')
    ctx = make_ctx(tmp_path, html)
    assert extract_load_profile(ctx) is None
    assert ctx.missing[0]["section"] == "load_profile"


def test_extractor_tolerates_extra_and_missing_columns(tmp_path, sample_path):
    html = sample_path.read_text().replace('<th class="awrbg" scope="col">Wait Class</th>',
                                           '<th class="awrbg" scope="col">Wait Klass</th>', 1)
    ev = extract_top_events(make_ctx(tmp_path, html))
    assert ev[0]["wait_class"] is None and ev[0]["pct_db_time"] == 35.6
