from parse_awr import num, slug

def test_num_formats():
    assert num("1,439.65") == 1439.65
    assert num("219.2K") == 219200.0
    assert num("3.7E+08") == 370000000.0
    assert num(".7") == 0.7
    assert num("") is None and num(None) is None and num("&#160;") is None

def test_num_time_units():
    assert num("587.00us", unit="ms") == 0.587
    assert num("14.19ms", unit="ms") == 14.19
    assert num("819.19ms", unit="s") == 0.81919
    assert num("2.1s", unit="ms") == 2100.0

def test_num_comma_decimal_locale():
    assert num("1.234,5", decimal=",") == 1234.5
    assert num("0,49ms", unit="ms", decimal=",") == 0.49

def test_slug():
    assert slug("DB Time(s):") == "db_time_s"
    assert slug("Buffer Hit   %:") == "buffer_hit_pct"
    assert slug("% Host Mem used for SGA+PGA:") == "pct_host_mem_used_for_sga_pga"
    assert slug("%Total") == "pct_total" and slug("%User") == "pct_user"
    assert slug("% DB time") == "pct_db_time" and slug("Buffer Hit %:") == "buffer_hit_pct"
    assert slug("Redo size (bytes):") == "redo_size_bytes"
