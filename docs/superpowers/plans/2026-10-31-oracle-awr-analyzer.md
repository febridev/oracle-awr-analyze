# Oracle AWR Analyzer Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `oracle-awr-analyzer/` Claude Skill: Python scripts that turn one Oracle AWR HTML report into `awr.json` → `findings.json` → `report.md` + offline `report.html`, plus the `SKILL.md` that tells a host agent how to run them and write `interpretation.json`.

**Architecture:** One-way pipeline of standalone CLI scripts that talk only through JSON files in a per-run folder. Rule logic lives in Python; every threshold number lives in `references/thresholds.yaml`. The LLM writes prose only (`interpretation.json`), which a validator checks against `awr.json` before rendering.

**Tech Stack:** Python 3.9 (system `python3` is 3.9.6), `beautifulsoup4`, `lxml`, `pyyaml`, `pytest`. No other dependencies. Charts are hand-written inline SVG.

**Spec:** `docs/superpowers/specs/2026-10-31-oracle-awr-analyzer-design.md` (requirements: `PRD.md`)

## Global Constraints

- Python 3.9 compatible: no `match`, no `X | Y` type unions at runtime; use `from __future__ import annotations`.
- Dependencies limited to `beautifulsoup4`, `lxml`, `pyyaml` (+ `pytest` for tests). If `lxml` import fails, use `html.parser` and set `meta.parser = "html.parser (reduced accuracy)"`.
- Scripts compute every number. No script connects to a database or executes SQL.
- Every threshold value comes from `references/thresholds.yaml` (overridable via `--config`); no threshold literals in `analyze.py`.
- Keys carrying units use suffixes: `_ms`, `_s`, `_min`, `_pct`, `_mb`, `_gb`, `_bytes`.
- v1 is single-instance, HTML-only, `lang=en`. RAC → `meta.rac=true` + banner, no RAC analysis. Text input → exit 3.
- Input file is never modified. Run folder `awr-report-<db>-<inst_num>-<begin>_<end>/`; if it exists append `-2`, `-3`, …
- `report.html`: no `<script>`, no `<link>`, no external `src`/`href`; all AWR-derived strings HTML-escaped.
- Shortcuts carry a code comment `shortcut: <limit>, <when to upgrade>`.
- All commands below run from the repo root `/Users/febridev/personal/awr-skill`.

## Review Focus

1. **A second AWR from another day or DB with slightly different table layouts** (an extra column, a renamed header): extractors must look up columns by normalized header name, never by position, and a missing column yields `null` + `missing_sections` entry rather than a crash. Test added in Task 3 (`test_extractor_tolerates_extra_and_missing_columns`).
2. **NLS date/number formats other than the sample's** (`28-Oct-26 00:00:34`, US decimals): unknown date formats keep the raw string with a note instead of raising; comma-decimal files parse correctly. Tests in Task 1 (`test_num_comma_decimal_locale`) and Task 2 (`test_unparseable_date_kept_raw`).
3. **Agent-written `interpretation.json` that is malformed JSON or has wrong types** (string instead of bool for `approval_required`): validator reports an error line, never a traceback. Test in Task 10 (`test_malformed_interpretation_reports_error`).
4. **SQL text containing HTML/script or very long text (the sample has a >10 KB DBMS_STATS statement)**: escaped in HTML and truncated for display in tables while full text stays in `findings.json`. Test in Task 12 (`test_long_sql_truncated_in_tables`).
5. **Running the pipeline twice on the same file**: second run gets a `-2` folder; first run untouched. Test in Task 4 (`test_second_run_gets_suffixed_folder`).

---

## File Structure

```
awr-skill/
├── .gitignore                      # .venv/, __pycache__/, awr-report-*/
├── pytest.ini                      # pythonpath = oracle-awr-analyzer/scripts
├── requirements.txt
└── oracle-awr-analyzer/
    ├── SKILL.md                    # Task 14
    ├── scripts/
    │   ├── common.py               # Task 5: json/yaml IO, config merge, run-dir helpers
    │   ├── parse_awr.py            # Tasks 1-4: num(), table lookup, extractors, CLI
    │   ├── classify_sql.py         # Task 5
    │   ├── lint_verify_sql.py      # Task 6
    │   ├── analyze.py              # Tasks 7-9
    │   ├── validate_findings.py    # Task 10
    │   ├── svg.py                  # Task 11
    │   ├── render_report.py        # Task 12
    │   ├── awr_diff_sql.py         # Task 13
    │   └── tests/
    │       ├── conftest.py         # fixtures: sample_path, sample_awr, run_dir
    │       ├── fixtures/           # hand-written awr.json scenario files (Task 8)
    │       └── test_*.py
    ├── references/
    │   ├── thresholds.yaml         # Tasks 7-9
    │   ├── sql_classification.yaml # Task 5
    │   ├── wait_events.md          # Task 14
    │   ├── awr_diff.md             # Task 13
    │   └── verify_sql/*.sql        # Task 6
    ├── assets/report.html.tpl      # Task 12
    └── examples/
        ├── awrrpt_1_3138_3162.html # moved from ./examples (Task 1)
        └── expected/header.json    # Task 2 golden values
```

---

### Task 1: Scaffold and number normalization

**Files:**
- Move: `examples/awrrpt_1_3138_3162.html` → `oracle-awr-analyzer/examples/awrrpt_1_3138_3162.html` (`git mv` after first `git add`)
- Create: `.gitignore`, `pytest.ini`, `requirements.txt`, `oracle-awr-analyzer/scripts/parse_awr.py`, `oracle-awr-analyzer/scripts/tests/conftest.py`
- Test: `oracle-awr-analyzer/scripts/tests/test_num.py`

**Interfaces:**
- Produces: `num(text: str | None, unit: str | None = None, decimal: str = ".") -> float | None` in `parse_awr.py`. `unit` ∈ {`None`, `"ms"`, `"s"`}: when given, time suffixes (`us`, `ms`, `s`) in `text` are converted to that unit; bare numbers are taken as already in that unit. `slug(label: str) -> str`. conftest fixture `sample_path -> pathlib.Path`.

- [ ] **Step 1: Scaffold**

```bash
python3 -m venv .venv && .venv/bin/pip install beautifulsoup4 lxml pyyaml pytest
mkdir -p oracle-awr-analyzer/{scripts/tests/fixtures,references/verify_sql,assets,examples/expected}
git add examples && git mv examples/awrrpt_1_3138_3162.html oracle-awr-analyzer/examples/
```
`requirements.txt`: `beautifulsoup4`, `lxml`, `pyyaml`, `pytest` (unpinned). `pytest.ini`: `[pytest]` / `pythonpath = oracle-awr-analyzer/scripts` / `testpaths = oracle-awr-analyzer/scripts/tests`. `.gitignore`: `.venv/`, `__pycache__/`, `awr-report-*/`.

- [ ] **Step 2: Write the failing tests**

```python
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
    assert slug("Redo size (bytes):") == "redo_size_bytes"
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest oracle-awr-analyzer/scripts/tests/test_num.py -v` — Expected: FAIL, `ModuleNotFoundError: parse_awr`.

- [ ] **Step 4: Implement `num()` and `slug()` in `parse_awr.py`**

`num`: strip whitespace/`&nbsp;`/non-breaking space; empty → `None`; remove thousands separator (the one that is not `decimal`), normalize decimal to `.`; suffix `K`=1e3, `M`=1e6, `G`=1e9, `T`=1e12; time suffixes `us`/`ms`/`s` converted to `unit`; round result to 10 significant digits to avoid float noise (`0.587`, not `0.5870000001`). Unparseable → `None`. `slug`: lowercase, `%`→`pct`, every run of non-alphanumerics → `_`, strip `_`.

- [ ] **Step 5: Run tests** — Expected: 4 passed.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: scaffold skill and number normalization"`

---

### Task 2: Validation, table lookup, header metadata

**Files:**
- Modify: `oracle-awr-analyzer/scripts/parse_awr.py`
- Create: `oracle-awr-analyzer/examples/expected/header.json`
- Test: `oracle-awr-analyzer/scripts/tests/test_parse_header.py`

**Interfaces:**
- Consumes: `num`, `slug` (Task 1).
- Produces:
  - `class NotAwrError(Exception)`, `class UnsupportedFormatError(Exception)`.
  - `load_doc(path: Path) -> Doc` — reads bytes, detects encoding, validates, builds the soup; raises the two errors above.
  - `Doc.find_table(key: str) -> Tag | None` using module-level `SECTIONS: dict[str, tuple[str, ...]]` (regexes over `summary`, then preceding h2/h3 text).
  - `table_rows(table: Tag) -> list[list[str]]` (cell text, whitespace-collapsed) and `records(table: Tag) -> list[dict[str, str]]` (first row with ≥2 non-empty cells = header, keys `slug(header)`).
  - `to_iso(text: str, year_hint: int | None = None) -> str` — formats `%d-%b-%y %H:%M:%S`, `%d-%b-%y %H:%M`, `%d-%b-%Y %H:%M:%S`, `%d-%b %H:%M:%S` (with `year_hint`); unparseable → returns input unchanged.
  - `extract_meta(doc: Doc) -> dict` returning `meta` keys: `db_name, dbid, unique_name, release, rac, cdb, instance, inst_num, startup_time, host, platform, cpus, cores, sockets, memory_gb, begin_snap, end_snap, begin_time, end_time, sessions_begin, sessions_end, elapsed_min, db_time_min, restart, parser, decimal`.

- [ ] **Step 1: Write golden header file** `examples/expected/header.json` (values read from the sample; DBA confirms at plan review):

```json
{"db_name": "PRODDB", "dbid": 1234567890, "release": "19.0.0.0.0", "rac": false, "cdb": false,
 "instance": "PRODDB", "inst_num": 1, "startup_time": "2026-07-20T15:40:00",
 "host": "db-host-01", "platform": "Linux x86 64-bit", "cpus": 34, "cores": 34, "sockets": 2,
 "memory_gb": 125.63, "begin_snap": 3138, "end_snap": 3162,
 "begin_time": "2026-10-28T00:00:34", "end_time": "2026-10-29T00:00:13",
 "sessions_begin": 1260, "sessions_end": 1201, "elapsed_min": 1439.65, "db_time_min": 10255.65,
 "restart": false}
```

- [ ] **Step 2: Write failing tests**

```python
import json, pytest
from parse_awr import load_doc, extract_meta, to_iso, NotAwrError, UnsupportedFormatError

def test_header_matches_golden(sample_path):
    meta = extract_meta(load_doc(sample_path))
    golden = json.loads((sample_path.parent / "expected/header.json").read_text())
    assert {k: meta[k] for k in golden} == golden          # A1
    assert meta["decimal"] == "."

def test_not_awr_rejected(tmp_path):
    p = tmp_path / "x.html"; p.write_text("<html><body>hello</body></html>")
    with pytest.raises(NotAwrError): load_doc(p)

def test_text_awr_rejected(tmp_path):
    p = tmp_path / "x.txt"; p.write_text("WORKLOAD REPOSITORY report for\nDB Name ...")
    with pytest.raises(UnsupportedFormatError): load_doc(p)

def test_restart_detected(tmp_path, sample_path):
    html = sample_path.read_text().replace("20-Jul-26 15:40", "28-Oct-26 09:00", 1)
    p = tmp_path / "r.html"; p.write_text(html)
    assert extract_meta(load_doc(p))["restart"] is True

def test_unparseable_date_kept_raw():
    assert to_iso("2026/10/28 x") == "2026/10/28 x"
    assert to_iso("28-Oct 01:00:48", year_hint=2026) == "2026-10-28T01:00:48"
```
(`sample_path` already exists in conftest from Task 1: `Path(__file__).parents[2] / "examples/awrrpt_1_3138_3162.html"`.)

- [ ] **Step 3: Run to verify failure** — `.venv/bin/pytest oracle-awr-analyzer/scripts/tests/test_parse_header.py -v` → FAIL (ImportError).

- [ ] **Step 4: Implement** in `parse_awr.py`.
  - Validation order is from spec §4: "not HTML" = no `<html` or `<table` in the first 64 KB (case-insensitive) → `UnsupportedFormatError`; then no `WORKLOAD REPOSITORY` anywhere (case-insensitive) → `NotAwrError`.
  - `SECTIONS` keys for this task: `db_instance` (`database instance information`, 2 tables: the first has `DB Name`, the second has `Inst Num`; pick the one by header), `host` (`host information`), `snapshot` (`snapshot information`).
  - `decimal`: inspect the raw Elapsed cell `1,439.65 (mins)`: if the last separator is `,` → `","`, else `"."`.
  - `restart`: `startup_time > begin_time`, compared as ISO strings only when both parsed; else `False` + a note in `meta.notes`.

- [ ] **Step 5: Run tests** — Expected: 5 passed.

- [ ] **Step 6: Commit** — `git commit -am "feat(parser): validation, table lookup, header metadata"` (include new files with `git add`).

---

### Task 3: Report-summary extractors, invalid values, missing sections

**Files:**
- Modify: `oracle-awr-analyzer/scripts/parse_awr.py`
- Test: `oracle-awr-analyzer/scripts/tests/test_parse_summary.py`

**Interfaces:**
- Consumes: `Doc`, `records`, `table_rows`, `num`, `slug` (Tasks 1-2).
- Produces: `class Ctx` holding `doc`, `decimal`, `missing: list[dict]`, `invalid: list[dict]`, with `ctx.missing_section(key, reason)` and `ctx.check_pct(path, value) -> float | None` (out of 0–100 → records `{"path","raw","reason"}`, returns `None`). Extractors `extract_<section>(ctx) -> dict | list | None` that return `None` and call `missing_section` when the table is absent. Output shapes (all consumers rely on these):
  - `load_profile: {slug(label): {"per_s": float, "per_txn": float}}` e.g. `db_time_s`, `db_cpu_s`, `redo_size_bytes`, `parses_sql`, `hard_parses_sql`, `executes_sql`, `transactions`, `user_calls`.
  - `efficiency: {slug(label): float | None}` — table has two label/value pairs per row; e.g. `library_hit_pct`, `soft_parse_pct`, `execute_to_parse_pct`.
  - `top_events: [{"event", "waits", "total_wait_s", "avg_wait_ms", "pct_db_time", "wait_class"}]` (`DB CPU` row kept, `wait_class: null`).
  - `time_model: {slug(name): {"time_s", "pct_db_time"}}`.
  - `host_cpu: {"cpus","cores","sockets","load_begin","load_end","user_pct","system_pct","wio_pct","idle_pct"}`.
  - `instance_cpu: {"total_cpu_pct","busy_cpu_pct","db_time_waiting_cpu_rm_pct"}`.
  - `os_stats: {NAME: {"value": float, "end_value": float | None}}` (Oracle names kept, e.g. `VM_OUT_BYTES`).
  - `memory: {slug(label): {"begin","end"}}` e.g. `host_mem_mb`, `pct_host_mem_used_for_sga_pga`.
  - `io_profile: {slug(label): {"rw_per_s","r_per_s","w_per_s"}}`.
  - `iostat_function: [records row with numeric values via num()]`.

- [ ] **Step 1: Write failing tests** (session-scoped fixture `sample_ctx` in conftest builds a `Ctx` from `sample_path`):

```python
from parse_awr import extract_load_profile, extract_efficiency, extract_top_events, extract_time_model, \
    extract_host_cpu, extract_instance_cpu, extract_os_stats, extract_memory, extract_io_profile

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

def test_missing_section_recorded(tmp_path, sample_path):
    html = sample_path.read_text().replace('summary="This table displays load profile"', 'summary="x"')
    ctx = make_ctx(tmp_path, html)                         # conftest helper
    assert extract_load_profile(ctx) is None
    assert ctx.missing[0]["section"] == "load_profile"

def test_extractor_tolerates_extra_and_missing_columns(tmp_path, sample_path):
    html = sample_path.read_text().replace("<th class=\"awrbg\" scope=\"col\">Wait Class</th>",
                                           "<th class=\"awrbg\" scope=\"col\">Wait Klass</th>", 1)
    ev = extract_top_events(make_ctx(tmp_path, html))
    assert ev[0]["wait_class"] is None and ev[0]["pct_db_time"] == 35.6
```
(Before writing the last test, confirm the exact `<th>` markup with `grep -o '<th[^>]*>Wait Class</th>' <sample> | head -1` and use it verbatim.)

- [ ] **Step 2: Run to verify failure** — FAIL (ImportError).
- [ ] **Step 3: Implement the extractors.** Add `SECTIONS` entries using the exact sample summaries: `load_profile` `^This table displays load profile`, `efficiency` `instance efficiency percentages`, `top_events` `top 10 wait events by total wait time`, `time_model` `different time model statistics`, `host_cpu` `system load statistics`, `instance_cpu` `CPU usage and wait statistics`, `os_stats` `operating systems statistics\. For each statistic`, `memory` `^This table displays memory statistics`, `io_profile` `^This table displays IO profile`, `iostat_function` `IO Statistics for different functions`. Columns are looked up by `slug(header)`, never by position; an absent column yields `None`. `check_pct` applies to every `*_pct` value in `efficiency`, `host_cpu`, `instance_cpu`, `memory`.
- [ ] **Step 4: Run tests** — Expected: 6 passed.
- [ ] **Step 5: Commit** — `git commit -am "feat(parser): report summary extractors with invalid/missing tracking"`

---

### Task 4: Remaining extractors, SQL merge, CLI, truncation, benchmark

**Files:**
- Modify: `oracle-awr-analyzer/scripts/parse_awr.py`
- Test: `oracle-awr-analyzer/scripts/tests/test_parse_rest.py`, `oracle-awr-analyzer/scripts/tests/test_parse_cli.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces:
  - `fg_events`, `bg_events: [{"event","waits","timeouts_pct","total_wait_s","avg_wait_ms","waits_per_txn","pct_db_time"}]` (bg uses `% bg time` → key `pct_bg_time`).
  - `sql: [{"sql_id","module","program","client_host","text","by_elapsed": {...}|None, "by_cpu", "by_user_io", "by_gets", "by_reads", "by_executions", "by_parse_calls"}]`. Each `by_*` dict holds that table's numeric columns keyed by `slug(header)` (e.g. `by_elapsed.pct_total`, `by_elapsed.elapsed_time_s`, `by_user_io.user_i_o_time_s`). `program`/`client_host` set only when module matches `^(?P<program>[^@\s]+)@(?P<host>\S+)\s*\(TNS V1-V3\)$`.
  - `services: [{"service_name","db_time_s","db_cpu_s","physical_reads_k","logical_reads_k"}]`.
  - `thread_activity: {"log_switches_total","log_switches_per_hour"}`.
  - `addm: [{"finding","avg_active_sessions","pct_active_sessions","task","begin_time","end_time"}]` (ISO times).
  - `os_detail: [{"snap_time","load","busy_pct","user_pct","sys_pct","idle_pct","iowait_pct"}]` (ISO with `year_hint = begin_time year`).
  - `pga: {"cache_hit_pct","wa_mb_processed","extra_wa_mb","multipass_execs"}` (`multipass_execs` = sum of `M-Pass Execs` in the histogram table).
  - `advisories: {"sga_target": [...], "shared_pool": [...], "buffer_pool": [...], "pga": [...]}` (records with numeric values).
  - `segments: {"by_logical_reads","by_physical_reads","by_row_lock_waits","by_buffer_busy_waits"}` (records).
  - `activity: {slug(statistic): {"total","per_s","per_txn"}}` from "Instance activity statistics" (the full one).
  - `parameters: {name: {"begin","end"}}`.
  - `split_module(module: str | None) -> dict` → `{"program": str|None, "client_host": str|None}` (used by Task 5 tests).
  - `parse(path: Path) -> dict` (full `awr.json` dict incl. `missing_sections`, `invalid_values`), `make_run_dir(parent: Path, meta: dict) -> Path`, and CLI `parse_awr.py <awr.html> [--out PARENT]` printing the run dir path; exit 2 on `NotAwrError`, 3 on `UnsupportedFormatError`.

- [ ] **Step 1: Write failing tests**

```python
def test_sql_merge_and_module_split(sample_awr):
    s = {r["sql_id"]: r for r in sample_awr["sql"]}["mon000000001a"]
    assert s["program"] == "dbspicao11" and s["client_host"] == "db-host-01"
    assert s["by_elapsed"]["pct_total"] == 7.47 and s["by_elapsed"]["executions"] == 2690
    assert s["text"].lower().startswith("select a.file_name")   # full text from Complete List
    assert {r["sql_id"]: r for r in sample_awr["sql"]}["app000000001a"]["by_user_io"]["pct_total"] == 8.37

def test_peak_sources(sample_awr):
    assert max(a["avg_active_sessions"] for a in sample_awr["addm"]) == 31.79
    top = max(sample_awr["addm"], key=lambda a: a["avg_active_sessions"])
    assert top["begin_time"] == "2026-10-28T22:00:00"
    assert sample_awr["os_detail"][1] == {"snap_time": "2026-10-28T01:00:48", "load": 4.52, "busy_pct": 6.22,
        "user_pct": 4.51, "sys_pct": 0.79, "idle_pct": 93.78, "iowait_pct": 1.01}

def test_other_sections(sample_awr):
    assert sample_awr["thread_activity"] == {"log_switches_total": 579.0, "log_switches_per_hour": 24.13}
    assert sample_awr["fg_events"][1]["avg_wait_ms"] == 14.19
    assert sample_awr["bg_events"][0]["event"] == "log file parallel write"
    assert sample_awr["services"][1]["service_name"] == "SYS$USERS"
    assert sample_awr["pga"]["multipass_execs"] == 0
    assert sample_awr["parameters"]["db_block_size"]["begin"] == "8192"
    assert sample_awr["missing_sections"] == []
```
`test_parse_cli.py`:
```python
def test_cli_writes_run_dir(tmp_path, sample_path):   # runs subprocess .venv python parse_awr.py
    run = run_parse(sample_path, tmp_path)               # conftest helper returns (exitcode, Path)
    assert run[0] == 0 and run[1].name == "awr-report-PRODDB-1-3138_3162"
    assert (run[1] / "awr.json").exists()

def test_second_run_gets_suffixed_folder(tmp_path, sample_path):
    run_parse(sample_path, tmp_path); code, second = run_parse(sample_path, tmp_path)
    assert second.name.endswith("3162-2") and (tmp_path / "awr-report-PRODDB-1-3138_3162/awr.json").exists()

def test_exit_codes(tmp_path):     # non-AWR html → 2; .txt → 3
    ...

def test_truncated_file(tmp_path, sample_path):
    data = sample_path.read_bytes(); p = tmp_path / "t.html"; p.write_bytes(data[: len(data) // 2])
    code, run = run_parse(p, tmp_path)
    awr = json.loads((run / "awr.json").read_text())
    assert code == 0 and awr["meta"]["db_name"] == "PRODDB"
    assert {"parameters", "pga_hist", "activity"} <= {m["section"] for m in awr["missing_sections"]}   # all three start after the 50% cut

def test_benchmark_20mb(tmp_path, sample_path):        # A9; mark @pytest.mark.slow
    # pad the Complete List of SQL Text table with copies of its own rows until file >= 20 MB
    code, run = run_parse(build_padded(sample_path, tmp_path, 20_000_000), tmp_path)
    assert code == 0 and elapsed < 60
```
(For `test_exit_codes`, write the two asserts in the same style as `test_cli_writes_run_dir`.)

- [ ] **Step 2: Run to verify failure** — FAIL.
- [ ] **Step 3: Implement.** `SECTIONS` additions (exact sample summaries): `fg_events` `Foreground Wait Events and their wait statistics`, `bg_events` `background wait events statistics`, `sql_by_elapsed|cpu|user_io|gets|reads|executions|parse_calls` `top SQL by (elapsed time|CPU time|user I/O time|buffer gets|physical reads|number of executions|number of parse calls)`, `sql_text` `text of the SQL statements`, `services` `service statistics\. It displays service names`, `thread_activity` `thread activity stats`, `addm` `top ADDM findings`, `os_detail` `detailed operating systems statistics`, `pga_summary` `PGA aggregate summary`, `pga_hist` `PGA aggregate target histograms`, `sga_advisory` `SGA target advisory`, `shared_pool_advisory` `shared pool advisory`, `buffer_pool_advisory` `buffer pool statistics`, `pga_advisory` `PGA memory advisory`, `seg_logical|seg_physical|seg_row_lock|seg_buffer_busy` `top segments by (logical reads|physical reads|row lock waits|buffer busy waits)`, `activity` `^This table displays Instance activity statistics`, `parameters` `modified initialization parameters`. The 5 "total number of waits" histogram tables are not used. Missing `sql_text` → keep the truncated text from the `by_*` tables and record missing. Run-dir naming per Global Constraints. Write `awr.json` with `indent=1`, `ensure_ascii=False`.
- [ ] **Step 4: Run tests** — `.venv/bin/pytest oracle-awr-analyzer/scripts/tests -v` — Expected: all passed (benchmark < 60 s).
- [ ] **Step 5: Commit** — `git commit -am "feat(parser): full extraction, CLI, truncation and benchmark tests"`

---

### Task 5: Shared helpers and SQL classification

**Files:**
- Create: `oracle-awr-analyzer/scripts/common.py`, `oracle-awr-analyzer/scripts/classify_sql.py`, `oracle-awr-analyzer/references/sql_classification.yaml`
- Test: `oracle-awr-analyzer/scripts/tests/test_classify.py`

**Interfaces:**
- Consumes: `awr.json` shape from Task 4 (`sql[]`, `meta.host`).
- Produces:
  - `common.SKILL_DIR: Path`, `common.read_json(p) -> dict`, `common.write_json(p, obj) -> None`, `common.load_yaml(p) -> dict`.
  - `common.load_config(path: Path | None) -> dict` returning `{"thresholds": dict, "classification": dict, "storage_profile": str, "storage_assumed": bool, "audience": str, "workload_norms": dict}`; user config keys `user_schemas, monitoring_schemas, monitoring_modules, app_hosts, app_programs, app_table_prefixes` are appended to the classification lists; `thresholds:` is deep-merged over `thresholds.yaml`. `storage_profile` default `"san"` with `storage_assumed: True`; `audience` default `"both"`.
  - `classify(sql: dict, db_host: str, cls: dict) -> dict` returning `{"category", "confidence", "reasons": [str], "verify_sql": "V-01"}`.
  - CLI `classify_sql.py <run_dir> [--config FILE]` updates each `awr.json` `sql[]` entry in place with those four keys.

- [ ] **Step 1: Write `sql_classification.yaml`** with lists (case-insensitive regex strings):
  - `monitoring_modules`: `^OEM`, `emagent`, `zabbix`, `prometheus`, `nagios`, `^dbspicao` · `monitoring_programs`: `dbspicao11`
  - `maintenance_modules`: `^DBMS_SCHEDULER$`, `rman`, `^Data Pump`, `^DW\d+`, `^DM\d+`
  - `internal_modules`: `^MMON_SLAVE`, `^MMNL`, `^MMON`
  - `adhoc_modules`: `SQL Developer`, `^TOAD`, `PL/SQL Developer`
  - `generic_modules` (weak only): `JDBC Thin Client`, `^SQL\*Plus`, `^python`
  - `oracle_process_programs`: `^oracle$`, `^ora_`
  - `app_programs`: `APPSRV`, `APPWORKER`, `APPBATCH` · `app_hosts`: `[]` · `app_table_prefixes`: `APP_`, `ORD_`, `LCK_`
  - `user_schemas`, `monitoring_schemas`: `[]`
  - `text_patterns`: `maintenance`: `DBMS_STATS`, `/\*\s*SQL Analyze\(` · `internal`: `\bWRI\$_`, `\bWRH\$_`, `\bSYS\.\w+\$` · `heartbeat`: `^\s*select\s+1\s+from\s+dual\s*$` · `dictionary_objects`: `^(G?V\$|DBA_|X\$|ALL_|CDB_|SYS\.)`
  - Comment at top: defaults come from PRD §8 calibration; edit or override via `--config`.

- [ ] **Step 2: Write failing tests** — the 8 PRD §8 calibration cases plus 4 real-sample cases:

```python
import pytest
from classify_sql import classify
from common import load_config
CLS = load_config(None)["classification"]; DB = "db-host-01"

@pytest.mark.parametrize("module,text,cat,conf", [
    ("dbspicao11@db-host-01 (TNS V1-V3)", "select a.file_name from DBA_DATA_FILES a, SYS.FILEXT$ b", "monitoring", "high"),
    ("DBMS_SCHEDULER", "/* SQL Analyze(1) */ select /*+ full(t) */ count(*) from T t", "maintenance", "high"),
    ("Poller", "SELECT 1 FROM DUAL", "monitoring", "low"),
    ("Poller", "SELECT ID FROM LCK_SEMAPHORE WHERE ID = :1 FOR UPDATE", "user_app", "medium"),
    ("SQL Developer", "select * from APP_SMS_EVENTS where duration > 0", "adhoc", "high"),
    ("APPSRV@app-host-01 (TNS V1-V3)", "INSERT INTO APP_CALL_EVENTS (A) VALUES (:1)", "user_app", "high"),
    ("", "INSERT INTO wri$_adv_objects (id) values (:1)", "oracle_internal", "high"),
    ("", "WITH MONITOR_DATA AS (SELECT * FROM TABLE(GV$(CURSOR(SELECT * FROM V$SQL_MONITOR))))", "monitoring", "medium"),
    ("APPWORKER@app-host-01 (TNS V1-V3)", "UPDATE ORD_CALL_EVENTS SET REFERENCE = :1", "user_app", "high"),
    ("dbspicao11@db-host-01 (TNS V1-V3)", "select BLOCKS from DBA_FREE_SPACE", "monitoring", "high"),
    ("ReportPortal.exe", 'select SQ_1.F159_1 "F159_1_1" from X SQ_1', "unknown", "low"),
    ("JDBC Thin Client", "select * from orders", "unknown", "low"),
])
def test_calibration(module, text, cat, conf):
    r = classify({"sql_id": "x", "module": module, "text": text, **split_module(module)}, DB, CLS)  # from parse_awr import split_module
    assert (r["category"], r["confidence"]) == (cat, conf)
    assert r["reasons"] and r["verify_sql"] == "V-01"

def test_dba_list_wins():
    cls = load_config(None)["classification"] | {"monitoring_modules": CLS["monitoring_modules"] + ["^Poller$"]}
    assert classify({"module": "Poller", "text": "SELECT ID FROM LCK_SEMAPHORE"}, DB, cls)["category"] == "monitoring"
```

- [ ] **Step 3: Run to verify failure** — FAIL.
- [ ] **Step 4: Implement `classify`.** Collect signals as `(category, strength, reason)` with strength `explicit`/`strong`/`weak`:
  - DBA lists from config → explicit.
  - Module patterns → strong; generic modules → weak toward `user_app`.
  - `client_host == db_host` and program not Oracle → strong `monitoring`; `client_host` in `app_hosts` or `program` in `app_programs` → strong `user_app`.
  - Text: maintenance/internal patterns → strong; heartbeat → weak `monitoring`; all extracted objects dictionary → strong `monitoring`; any object with an app prefix or `user_schemas` owner → strong `user_app`.
  - Object extraction: regex on identifiers after `FROM|JOIN|INTO|UPDATE` plus `GV$`/`V$` inside `CURSOR(`; comment `shortcut: misses dynamic SQL and quoted identifiers; upgrade if A5 accuracy < 90%`.
  - **Definitive signals** (count as `explicit` strength → `high`): `adhoc_modules` match (an interactive tool names itself) and `text_patterns.internal` match (Oracle-owned WRI$/WRH$/SYS.x$ objects) — required by calibration cases `SQL Developer`→adhoc high and `wri$_adv_objects`→oracle_internal high. Exception: an internal-pattern hit loses to a monitoring module/host signal (case 1: `SYS.FILEXT$` under `dbspicao11` stays monitoring).
  - DBA lists: `load_config` keeps user-config lists under `classification["dba"]` (same key names); only those are `explicit`. Built-in YAML lists are `strong`.
  - Category = signal from the highest-priority group in order explicit > module > host > text > object; if only weak signals → that weak category with low, except generic-only → `unknown`.
  - Confidence: explicit, or ≥2 signals agreeing with the category → `high`; one strong → `medium`; weak only → `low`.
- [ ] **Step 5: Run tests** — Expected: 13 passed. If a calibration case disagrees, fix the rule, not the test.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: config loading and SQL classification"`

---

### Task 6: Verification SQL catalog and linter

**Files:**
- Create: `oracle-awr-analyzer/references/verify_sql/{sql_classification,cpu,memory,io,redo,parse,concurrency,network,config}.sql`, `oracle-awr-analyzer/scripts/lint_verify_sql.py`
- Test: `oracle-awr-analyzer/scripts/tests/test_lint.py`

**Interfaces:**
- Produces:
  - `load_catalog(dir: Path) -> dict[str, dict]` → `{id: {"title","domain","min_version","license","params": [str], "sql": str, "file": str}}`.
  - `lint_query(q: dict) -> list[str]` (error messages).
  - `fill(q: dict, values: dict) -> str` substituting `&name` for each declared param (string params quoted already in catalog text, e.g. `'&sql_id'`).
  - CLI `lint_verify_sql.py [--catalog DIR] [--thresholds FILE] [--run RUN_DIR]` exit 0/1, one error per line.

- [ ] **Step 1: Write the catalog.** V-01, V-02, V-03 verbatim from PRD §11 (V-01, V-02 in `sql_classification.sql`, V-03 in `redo.sql`). Add one query per rule domain, all `SELECT`/`WITH`, each with full headers:

| id | file | title | license | min_version | params |
|---|---|---|---|---|---|
| V-04 | cpu | Top SQL by CPU in the snapshot range (`dba_hist_sqlstat`) | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap |
| V-05 | cpu | Host CPU and load per snapshot (`dba_hist_osstat` deltas for BUSY_TIME, IDLE_TIME, LOAD) | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap |
| V-06 | memory | Swap and memory per snapshot (`dba_hist_osstat` VM_IN_BYTES, VM_OUT_BYTES, PHYSICAL_MEMORY_BYTES) | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap |
| V-07 | io | Wait event latency per snapshot (`dba_hist_system_event` deltas; avg ms for one event) | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap, event |
| V-08 | io | Top SQL by user I/O wait (`dba_hist_sqlstat.iowait_delta`) | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap |
| V-09 | io | Top segments by physical reads (`dba_hist_seg_stat` + `dba_hist_seg_stat_obj`) | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap |
| V-10 | redo | Redo log size and members (`v$log`, `v$logfile`) | none | 11.2 | (none) |
| V-11 | parse | Parse and hard-parse counts per snapshot (`dba_hist_sysstat`) | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap |
| V-12 | parse | Statements with high parse-to-execute ratio (`v$sql`: parse_calls vs executions) | none | 11.2 | (none) |
| V-13 | concurrency | Row lock and buffer busy segments (`dba_hist_seg_stat` row_lock_waits_delta, buffer_busy_waits_delta) | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap |
| V-14 | network | SQL*Net events per snapshot (`dba_hist_system_event`, event LIKE 'SQL*Net%') | diagnostics_pack | 11.2 | dbid, inst_id, begin_snap, end_snap |
| V-15 | config | Non-default parameters (`v$parameter` where isdefault = 'FALSE') | none | 11.2 | (none) |
| V-16 | config | SGA target advisory (`v$sga_target_advice`) | none | 11.2 | (none) |
| V-17 | sql_classification | Execution plan from AWR (`DBMS_XPLAN.DISPLAY_AWR('&sql_id')`) | diagnostics_pack | 11.2 | sql_id |

Snapshot ranges always `BETWEEN &begin_snap + 1 AND &end_snap` (PRD §11 note).

- [ ] **Step 2: Write failing tests**

```python
from lint_verify_sql import load_catalog, lint_query, fill
CAT = load_catalog(SKILL_DIR / "references/verify_sql")

def test_catalog_clean():
    assert {k: lint_query(q) for k, q in CAT.items() if lint_query(q)} == {}
    assert {"V-01", "V-02", "V-03"} <= CAT.keys()

def q(sql, params="dbid", license="none"):
    return {"id": "T", "title": "t", "domain": "d", "min_version": "11.2", "license": license,
            "params": params.split(", ") if params else [], "sql": sql}

@pytest.mark.parametrize("sql,needle", [
    ("DELETE FROM t WHERE id = &dbid", "starts with"),
    ("SELECT * FROM t WHERE id = &dbid FOR UPDATE", "FOR UPDATE"),
    ("SELECT DBMS_STATS.GATHER_TABLE_STATS('A','B') FROM dual WHERE 1 = &dbid", "DBMS_STATS"),
    ("WITH x AS (SELECT 1 FROM dual) SELECT * FROM x", "params"),          # declared dbid unused
    ("SELECT * FROM t WHERE a = &dbid AND b = &other", "params"),
])
def test_violations(sql, needle):
    assert any(needle in e for e in lint_query(q(sql)))

def test_keywords_inside_strings_and_comments_ignored():
    assert lint_query(q("SELECT 'delete me' AS x /* update */ FROM t WHERE id = &dbid")) == []

def test_bad_license_and_missing_header():
    assert lint_query(q("SELECT 1 FROM t WHERE a=&dbid", license="free"))
    bad = q("SELECT 1 FROM t WHERE a=&dbid"); del bad["title"]; assert lint_query(bad)

def test_allowed_package():
    assert lint_query(q("SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_AWR('&sql_id'))", params="sql_id")) == []

def test_fill():
    assert "s.sql_id = 'abc'" in fill(CAT["V-01"], {"dbid": 1, "inst_id": 1, "begin_snap": 3138, "end_snap": 3162, "sql_id": "abc"})
```

- [ ] **Step 3: Run to verify failure** — FAIL.
- [ ] **Step 4: Implement** the linter exactly per spec §6.3 rules 1–5 (rules 6–7 are wired in Task 9: `--thresholds` checks every `verify_sql` id in `thresholds.yaml` exists; `--run` checks each filled SQL in `findings.json` equals `fill(catalog[id], values)`). Strip `--` and `/* */` comments and `'…'` literals before keyword checks. Keyword match uses word boundaries. Package allowlist regex: `^DBMS_XPLAN\.DISPLAY_(AWR|CURSOR)$|^DBMS_WORKLOAD_REPOSITORY\.AWR_\w*REPORT\w*$`.
- [ ] **Step 5: Run tests** — Expected: all passed.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: verification SQL catalog and read-only linter"`

---

### Task 7: Load status, bottleneck, peak

**Files:**
- Create: `oracle-awr-analyzer/scripts/analyze.py`, `oracle-awr-analyzer/references/thresholds.yaml` (load section only)
- Test: `oracle-awr-analyzer/scripts/tests/test_load.py`

**Interfaces:**
- Consumes: `awr.json` (`meta`, `time_model`, `host_cpu`, `addm`, `os_detail`), `common.load_config`.
- Produces: `load_status(awr: dict, t: dict) -> dict`:
  ```
  {"avg":  {"aas", "load_ratio", "status", "db_cpu_share_pct", "host_idle_pct"},
   "peak": None | {"aas", "load_ratio", "status", "begin", "end", "source": "addm", "os_busy_pct", "os_iowait_pct"},
   "basis": "avg" | "peak",            # which status load-based severity uses
   "bottleneck": "cpu_bound" | "wait_bound" | "mixed" | "non_db_host_load" | "db_not_problem",
   "top_wait_class": str | None, "hyperthreading": bool, "idle": bool}
  ```
  Status names: `Idle`, `Normal`, `Busy`, `Saturated`, `Critical`. Bands are lower-inclusive: ratio < 0.10 Idle, < 0.50 Normal, < 1.00 Busy, < 2.00 Saturated, ≥ 2.00 Critical (matches PRD §7 P0 `≥ 2,0`).

- [ ] **Step 1: Write the `load:` section of `thresholds.yaml`**

```yaml
load:
  bands: {idle: 0.10, normal: 0.50, busy: 1.00, saturated: 2.00}   # upper bounds; above saturated = Critical
  high_load_ratio: 0.50          # bottleneck table "high" = Busy or worse
  cpu_share_high_pct: 70
  cpu_share_low_pct: 40
  host_idle_low_pct: 10
  long_period_min: 120
  short_period_min: 10
  hyperthreading_ratio: 1.5      # cpus / cores above this → note
```

- [ ] **Step 2: Write failing tests**

```python
def test_sample_load(sample_awr, cfg):
    L = load_status(sample_awr, cfg["thresholds"])
    assert L["avg"]["aas"] == 7.12 and L["avg"]["load_ratio"] == 0.21 and L["avg"]["status"] == "Normal"
    assert L["peak"] == {"aas": 31.79, "load_ratio": 0.93, "status": "Busy", "begin": "2026-10-28T22:00:00",
                         "end": "2026-10-28T23:00:00", "source": "addm", "os_busy_pct": 26.57, "os_iowait_pct": 14.49}  # OS Detail row 28-Oct 23:00:52
    assert L["basis"] == "peak" and L["bottleneck"] == "wait_bound" and L["top_wait_class"] == "User I/O"

@pytest.mark.parametrize("ratio,status", [(0.05, "Idle"), (0.3, "Normal"), (0.7, "Busy"), (1.5, "Saturated"), (2.5, "Critical")])
def test_bands(ratio, status, cfg): ...       # build minimal awr dict: cpus=10, elapsed_min=60, db_time_min=ratio*600

@pytest.mark.parametrize("ratio,share,idle,expect", [
    (1.2, 80, 5, "cpu_bound"), (1.2, 30, 60, "wait_bound"), (1.2, 55, 60, "mixed"),
    (0.2, 50, 5, "non_db_host_load"), (0.2, 50, 80, "db_not_problem")])
def test_bottleneck(ratio, share, idle, expect, cfg): ...
```
Rounding rule: `aas` and `load_ratio` stored rounded to 2 decimals; all later rules compare the rounded stored values so `validate_findings` can match them exactly. (Write the two parametrized test bodies using a `mini_awr(cpus, elapsed_min, db_time_min, db_cpu_pct, idle_pct)` conftest helper.)

- [ ] **Step 3: Run to verify failure** — FAIL.
- [ ] **Step 4: Implement `load_status`.**
  - AAS = `db_time_min / elapsed_min`; load ratio = AAS / `cpus`; DB CPU share = `time_model.db_cpu.pct_db_time`.
  - Peak only when `elapsed_min > long_period_min` and `addm` is non-empty: the ADDM row with max `avg_active_sessions`. Its OS Detail row is the first `os_detail` entry with `snap_time >= peak end` (the snapshot closing the window).
  - Bottleneck uses the `basis` ratio. Host idle comes from the OS Detail peak row (`idle_pct`) when basis is peak, else `host_cpu.idle_pct`.
    - high load and share > high and idle < low → `cpu_bound`;
    - high load and share < low → `wait_bound`;
    - high load otherwise → `mixed`;
    - low load and idle < low → `non_db_host_load`;
    - low load otherwise → `db_not_problem`.
  - `top_wait_class` = wait class of the first `top_events` row with a non-null class.
  - `idle` = avg status `Idle` and no peak above `Normal`.
- [ ] **Step 5: Run tests** — Expected: passed.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(analyze): load status, peak, bottleneck"`

---

### Task 8: Domain rules and fallback text

**Files:**
- Modify: `oracle-awr-analyzer/scripts/analyze.py`, `oracle-awr-analyzer/references/thresholds.yaml`
- Create: `oracle-awr-analyzer/scripts/tests/fixtures/{idle,cpu_bound,io_bound,commit_heavy,parse_heavy,lock_contention}.json`
- Test: `oracle-awr-analyzer/scripts/tests/test_rules.py`

**Interfaces:**
- Consumes: `load_status` (Task 7), classified `awr.json` (Task 5).
- Produces:
  - `Candidate` = dict `{"domain","subject","title","rule": str, "severity": "P0".."P4", "load_based": bool, "evidence": [{"metric","value","section","path"}], "pct_db_time": float | None, "latency_rule": bool, "latency_ok": bool | None}`.
  - Domain functions, each `(awr: dict, L: dict, t: dict, storage: str) -> list[Candidate]`: `rules_cpu`, `rules_memory`, `rules_io`, `rules_redo`, `rules_parse`, `rules_concurrency`, `rules_network`, `rules_events` (generic share + control file), `rules_sql`, `rules_monitoring`, `rules_config`.
  - `idle_event(name: str, t: dict) -> bool`.
  - Evidence `path` syntax: dotted keys with `[i]` list indexes, e.g. `fg_events[1].avg_wait_ms`, `sql[0].by_elapsed.pct_total`, `derived.monitoring_overhead_pct`. `analyze` writes computed values (AAS, load ratio, monitoring overhead, combined concurrency %) into `awr.json["derived"]` so every evidence path resolves.

- [ ] **Step 1: Add rules to `thresholds.yaml`.** Each rule: `severity`, `load_based`, threshold value(s), `verify_sql: [ids]`, `fallback: {cause, impact, recommendation}` (2–3 plain-English sentences each, no numbers in fallback text). Values copied from PRD §7/§9:

| rule id | condition (fields) | sev | load_based | verify |
|---|---|---|---|---|
| cpu.load.p1 | basis load_ratio ≥ 1.0 and host idle < 10 | P1 | yes | V-04, V-05 |
| cpu.load.p0 | basis load_ratio ≥ 2.0 and host idle < 5 | P0 | yes | V-04, V-05 |
| cpu.resmgr.p2 | fg `resmgr:cpu quantum` pct ≥ 5 | P2 | yes | V-05 |
| mem.swap.p1 / .p3 | swap-out rate = `os_stats.VM_OUT_BYTES.value` / 1048576 / (`elapsed_min`/60) MB/h; ≥ `swap_out_mb_per_hour` (default 10) → P1; > 0 but below → P3 (minor swap-out; verify trend with V-06). Rate written to `derived.swap_out_mb_per_hour` (2 decimals) | P1 / P3 | no | V-06 |
| mem.sga_pga.p2 | `memory.pct_host_mem_used_for_sga_pga.end` > 90 | P2 | no | V-06, V-16 |
| mem.multipass.p3 | `pga.multipass_execs` > 0 | P3 | no | V-06 |
| io.seq_read.warn / .crit | `db file sequential read` avg ms ≥ {flash 3, hdd 10, san 10} / {flash 8, hdd 20, san 20} | P2 / P1 | yes | V-07, V-08, V-09 |
| io.multi_read.warn / .crit | `db file scattered read`, `direct path read` avg ms ≥ {flash 10, hdd 20, san 20} / {flash 20, hdd 40, san 40} | P2 / P1 | yes | V-07, V-08, V-09 |
| redo.lfs.warn / .crit | `log file sync` avg ms ≥ 10 / 20 | P2 / P1 | yes | V-07, V-10 |
| redo.switch.warn / .crit | `thread_activity.log_switches_per_hour` > 6 / > 20 | P3 / P2 | no | V-03, V-10 |
| redo.ckpt.p1 / .p0 | `log file switch (checkpoint incomplete)` pct ≥ 1 / is the top non-idle fg event | P1 / P0 | yes | V-03, V-10 |
| parse.soft.warn / .crit | `efficiency.soft_parse_pct` < 90 / < 80 | P3 / P2 | no | V-11, V-12 |
| parse.hard_rate.p2 | `load_profile.hard_parses_sql.per_s` ≥ 100 | P2 | yes | V-11 |
| parse.mutex.p1 | sum pct of `library cache: mutex X`, `cursor: pin S wait on X` ≥ 5 | P1 | yes | V-11, V-12 |
| parse.exec_to_parse.p3 | `execute_to_parse_pct` < 50 and `soft_parse_pct` > 95 | P3 | no | V-12 |
| conc.combined.p2 / .p1 / .p0 | sum pct of `enq: TX - row lock contention`, events starting `enq: TM`, `buffer busy waits`, `latch: cache buffers chains`, `library cache lock` ≥ 5 / 15 / 30 | P2 / P1 / P0 | yes | V-13 |
| net.client.p3 | sum pct of `SQL*Net more data to client`, `SQL*Net message to client` ≥ 10 | P3 | yes | V-14 |
| event.share.p2 / .p1 / .p0 | any non-idle fg event (except `event_share_exclude`) pct ≥ 5 / 15 / 30; p0 additionally needs that event's latency rule at crit | P2 / P1 / P0 | yes | V-07 |
| event.ctrlfile.p3 | fg `control file sequential read` pct ≥ 5 | P3 | yes | V-07, V-01 |
| sql.single.p3 / .p2 / .p1 | `by_elapsed.pct_total` ≥ 10 / 20 / 40 | P3 / P2 / P1 | yes | V-01, V-17 |
| monitoring.overhead.p3 / .p2 | sum `by_elapsed.pct_total` of SQL in `monitoring`+`maintenance` ≥ 5 / 10 | P3 / P2 | no | V-02 |
| config.param.p4 | any modified parameter starting `_` or in `watch_params` | P4 | no | V-15 |
| config.sga_advisory.p4 | SGA advisory row with size factor > 1 and est DB time ≤ 90% of factor-1.0 row | P4 | no | V-16 |

Also in YAML: `swap_out_mb_per_hour: 10`; `event_share_exclude: ["control file sequential read"]` (it has its own P3 rule, per PRD §9 calibration 3); `min_pct_db_time: 1.0`; `watch_params: [optimizer_mode, optimizer_index_cost_adj, optimizer_features_enable, cursor_sharing, db_file_multiblock_read_count]`; `idle_events` (exact names or `prefix*`): `SQL*Net message from client`, `rdbms ipc message`, `pmon timer`, `smon timer`, `jobq slave wait`, `Streams AQ:*`, `class slave wait`, `PL/SQL lock timer`, `Space Manager: slave idle wait`, `wait for unread message on broadcast channel`, `LGWR worker group idle`, `watchdog main loop`, `pipe get`, `heartbeat redo informer`, `lreg timer`, `AQPC idle`, `fbar timer`, `VKTM Logical Idle Wait`, `DIAG idle wait`, `Data Guard: Timer`, `shared server idle wait`, `dispatcher timer`, `virtual circuit next request`; `latency_events: {"db file sequential read": io.seq_read, "db file scattered read": io.multi_read, "direct path read": io.multi_read, "log file sync": redo.lfs}`.

Note: `wait_class` is absent from the 19c fg events table, so idle detection uses `idle_events` plus the class from `top_events` when the event appears there (`wait_class == "Idle"`).

- [ ] **Step 2: Write the 6 scenario fixtures.** Minimal `awr.json` dicts with only the keys rules read: `meta` (cpus 16, elapsed_min 60, db_time_min per scenario), `time_model.db_cpu.pct_db_time`, `host_cpu.idle_pct`, `fg_events`, `top_events`, `efficiency`, `load_profile`, `thread_activity`, `memory`, `os_stats`, `pga`, `sql` (classified), `parameters`, `advisories`, `addm: []`, `os_detail: []`, `missing_sections: []`, `invalid_values: []`. Scenario intent and the key values:
  - `idle`: db_time_min 48 (ratio 0.05), idle 97.
  - `cpu_bound`: db_time_min 1440 (ratio 1.5), cpu share 85, idle 4, one SQL `sql_id` `cpubound0sql1` with `by_elapsed.pct_total` 25.
  - `io_bound`: ratio 1.2, share 20, idle 60; `db file sequential read` 45% at 25 ms (san).
  - `commit_heavy`: ratio 0.8; `log file sync` 22% at 24 ms.
  - `parse_heavy`: ratio 0.9; soft parse 72, hard parse 150/s; `library cache: mutex X` 6%.
  - `lock_contention`: ratio 1.1; `enq: TX - row lock contention` 18%.

- [ ] **Step 3: Write failing tests**

```python
@pytest.mark.parametrize("name,expect", [
    ("cpu_bound", {("cpu", "host cpu"): "P1", ("sql", "cpubound0sql1"): "P2"}),
    ("io_bound", {("io", "db file sequential read"): "P0"}),
    ("commit_heavy", {("redo", "log file sync"): "P1"}),
    ("parse_heavy", {("parse", "soft parse"): "P2", ("parse", "hard parse rate"): "P2", ("parse", "mutex"): "P1"}),
    ("lock_contention", {("concurrency", "concurrency waits"): "P1"}),
])
def test_scenarios_candidates(name, expect, cfg):
    awr = fixture(name); L = load_status(awr, cfg["thresholds"])
    got = max_by_subject(all_candidates(awr, L, cfg))     # conftest helper: {(domain, subject): highest severity}
    for k, sev in expect.items(): assert got[k] == sev

def test_idle_fixture_has_no_load_findings(cfg): ...       # all candidates have load_based False or none at all
def test_idle_events_never_candidates(cfg): ...            # fixture with SQL*Net message from client 80% → no candidate
def test_storage_profile_changes_io(cfg): ...              # sample direct path read 14.19 ms: san → latency_ok True; flash → io.multi_read.warn fires
```
Subject naming is fixed here: event-based findings use the event name; `cpu`→`host cpu`; parse→`soft parse` / `hard parse rate` / `mutex` / `execute to parse`; concurrency→`concurrency waits`; network→`sql*net client`; sql→the `sql_id`; monitoring→`monitoring overhead`; redo switch→`log switches`; memory→`swap` / `sga+pga` / `pga multipass`; config→parameter name or `sga advisory`. Event candidates take the domain of the event's latency rule when it has one (`io` for the three read events, `redo` for `log file sync`), otherwise domain `events`, so generic event-share and latency candidates for the same event share `(domain, subject)` and `finalize` merges them. (Write the three bodies with the same `fixture()` / `all_candidates()` helpers.)

- [ ] **Step 4: Run to verify failure** — FAIL.
- [ ] **Step 5: Implement the domain functions** as table-driven reads of `t[...]`. Event latency rules apply only when the event pct ≥ `min_pct_db_time`. Set `latency_rule`/`latency_ok` on event candidates whose event is in `latency_events` (ok = below warn) so `finalize` can apply the volume cap. `all_candidates(awr, L, cfg)` concatenates all domain lists.
- [ ] **Step 6: Run tests** — Expected: passed.
- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat(analyze): domain rules and thresholds"`

---

### Task 9: finalize, verify SQL fill, diff decision, findings.json, golden run

**Files:**
- Modify: `oracle-awr-analyzer/scripts/analyze.py`, `oracle-awr-analyzer/scripts/lint_verify_sql.py` (rules 6–7), `oracle-awr-analyzer/references/thresholds.yaml` (`diff:` section)
- Test: `oracle-awr-analyzer/scripts/tests/test_finalize.py`, `oracle-awr-analyzer/scripts/tests/test_golden.py`

**Interfaces:**
- Consumes: Tasks 5–8, `lint_verify_sql.load_catalog`, `fill`.
- Produces:
  - `finalize(cands: list, awr: dict, L: dict, cfg: dict, catalog: dict) -> list[Finding]`, where Finding = `{"id","domain","subject","title","severity","triggered_by": [rule ids],"load_based","confidence","evidence","verify_sql": [{"id","title","sql","license","available"}],"fallback": {cause, impact, recommendation}}`. Fallback comes from the highest-severity triggered rule.
  - `diff_decision(findings, L, awr, cfg, comparison_mentioned: bool) -> {"suggested": bool, "reasons": [str]}`.
  - CLI `analyze.py <run_dir> [--config FILE] [--comparison-mentioned]` writes `findings.json`: `{"load", "findings", "sql_summary": {"by_category": {cat: [sql_id...]}, "monitoring_overhead_pct", "monitoring_overhead_cpu_pct"}, "diff", "notes": [str], "storage_profile", "audience"}`; also writes `derived` into `awr.json`.

- [ ] **Step 1: Add `diff:` to thresholds.yaml** — `workload_norms: {executes_per_s: null, txn_per_s: null}`; `duration_diff_pct: 20` (used by Task 13 warnings).

- [ ] **Step 2: Write failing tests**

```python
def test_merge_takes_highest(cfg): ...            # two candidates same (domain,subject) P2+P1 → one finding P1, triggered_by both
def test_below_1pct_dropped(cfg): ...             # event at 0.7% → dropped
def test_idle_cap(cfg): ...                       # idle fixture with a load-based P1 candidate → P3, confidence low; config P4 unchanged
def test_short_elapsed_cap(cfg): ...              # elapsed_min 8 → same cap
def test_volume_cap(cfg): ...                     # share 35% P1 with latency_ok True → P2
def test_sql_escalation(cfg): ...                 # SQL P3 in by_user_io while io.seq_read.crit fired → P2
def test_restart_lowers_confidence(cfg): ...
def test_verify_sql_filled_and_version_gate(cfg): # V-02 min_version 12.1 with release "11.2.0.4.0" → available False
def test_diff_rules(cfg):
    # idle + no mention → False; idle + mention → True; P1 non-config → True; Saturated + norms unset → True
```

`test_golden.py` — full pipeline on the real sample via CLIs (parse → classify → analyze) with default config:
```python
def test_sample_findings(sample_run):
    f = read_json(sample_run / "findings.json")
    sev = {(x["domain"], x["subject"]): x["severity"] for x in f["findings"]}
    assert sev[("io", "db file sequential read")] == "P2"          # 35.63% share, 0.587 ms → volume cap
    assert sev[("io", "direct path read")] == "P2"                 # 16.80% share, 14.19 ms < san warn 20 → cap
    assert sev[("redo", "log file sync")] == "P2"                  # ~12% share, latency fine
    assert sev[("redo", "log switches")] == "P2"                   # 24.13/h > 20
    assert sev[("events", "control file sequential read")] == "P3"
    assert sev[("parse", "execute to parse")] == "P3"              # 2.75 < 50, soft 99.98 > 95
    assert sev[("memory", "swap")] == "P3"                         # VM_OUT 27,025,408 B / 24 h ≈ 1.07 MB/h < 10
    assert sev[("monitoring", "monitoring overhead")] == "P2"
    assert f["sql_summary"]["monitoring_overhead_pct"] >= 11.18    # 85gj 7.47 + 2tc4 1.33 + cmu8 1.29 + 1f8d 1.09
    assert not any(x["severity"] in ("P0", "P1") for x in f["findings"])
    assert f["load"]["avg"]["status"] == "Normal" and f["load"]["peak"]["status"] == "Busy"
    assert f["diff"]["suggested"] is False
    assert all(x["evidence"] and x["verify_sql"] and x["triggered_by"] for x in f["findings"])   # A4
    assert any("storage" in n for n in f["notes"])                 # san assumed
```
`sample_run` = session fixture running the three CLIs into `tmp_path_factory`.

- [ ] **Step 3: Run to verify failure** — FAIL.
- [ ] **Step 4: Implement `finalize`** in spec §5.2 order:
  1. Merge.
  2. Drop idle events and findings with pct < 1, except rules listed under `min_pct_exempt: []` in YAML.
  3. Caps (idle/short → P3 + low; volume → P2).
  4. Escalation.
  5. Restart → low.
  6. Sort and number `F01`… .

  Confidence defaults: `high` when ≥1 evidence value is direct from AWR, `medium` when the finding relies on a classification of medium confidence, `low` per the caps. Fill verify SQL from `meta`: `dbid`, `inst_id`=`inst_num`, `begin_snap`, `end_snap`, `sql_id` (SQL findings), `event` (event findings). Version gate compares the leading `major.minor` of `meta.release` with `min_version`. Then add lint rules 6–7 to `lint_verify_sql.py` with one test each in `test_lint.py` (`test_unknown_rule_verify_id`, `test_run_sql_must_match_catalog`).
- [ ] **Step 5: Run tests** — `.venv/bin/pytest oracle-awr-analyzer/scripts/tests -v` — Expected: all passed. If a golden assertion fails, check the number against the sample before touching either the rule or the test, and report any disagreement with PRD intent instead of tuning silently.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(analyze): finalize, verify SQL, diff decision, golden sample test"`

---

### Task 10: validate_findings.py

**Files:**
- Create: `oracle-awr-analyzer/scripts/validate_findings.py`
- Test: `oracle-awr-analyzer/scripts/tests/test_validate.py`

**Interfaces:**
- Consumes: `awr.json`, `findings.json` (Task 9), optional `interpretation.json` (schema spec §6.1).
- Produces: `validate(run_dir: Path) -> tuple[list[str], list[str]]` (errors, warnings), `resolve(awr: dict, path: str) -> Any` (raises `KeyError` on a bad path); CLI exit 1 if errors, else 0; prints `ERROR <finding>.<field>: …` / `WARN …`.

- [ ] **Step 1: Write failing tests** (fixture `good_run` = copy of `sample_run` + a valid `interpretation.json` built from its findings using only evidence numbers)

```python
def test_good_run_passes(good_run): assert validate(good_run) == ([], [])

def test_evidence_mismatch(good_run):
    edit_findings(good_run, lambda f: f["findings"][0]["evidence"][0].__setitem__("value", 99.99))
    assert any("evidence" in e for e in validate(good_run)[0])

def test_missing_required_field(good_run): ...        # remove verify_sql → error containing "verify_sql"
def test_unknown_finding_and_extra_field(good_run): ... # "F99" key and "extra" field → 2 errors
def test_fabricated_number(good_run):
    edit_interp(good_run, "F01", "hypothesis", "Latency reached 21.4 ms during the batch.")
    assert any("21.4" in e for e in validate(good_run)[0])
def test_precision_match_passes(good_run): ...        # "about 14.2 ms" with evidence 14.19 → no error
def test_identifier_numbers_exempt(good_run): ...     # "P2", "V-07", "F03", "mon000000001a", "19.0.0.0.0", "3138" → no error
def test_causal_word_in_facts(good_run): ...          # facts: "Reads are slow because of storage" → error
def test_summary_too_long(good_run): ...              # 6 lines → error
def test_missing_interpretation_is_warning(good_run): ... # delete F01 entry → warning only, exit 0
def test_malformed_interpretation_reports_error(good_run):
    (good_run / "interpretation.json").write_text("{not json")
    errs, _ = validate(good_run); assert errs and "interpretation.json" in errs[0]
    # also: approval_required = "yes" → type error message
```

- [ ] **Step 2: Run to verify failure** — FAIL.
- [ ] **Step 3: Implement** per spec §6.2. Number extraction regex `(?<![\w.$-])\d+(?:[.,]\d+)?(?![\w.])`. Before extraction, remove identifier tokens: `\bP[0-4]\b`, `\bV-\d+\b`, `\bF\d{2}\b`, 13-char SQL IDs `\b[0-9a-z]{13}\b`, release strings `\b\d+(\.\d+){3,}\b`, and the run's snap ids. A number at precision *d* matches value *v* when `round(v, d) == number`. The candidate value set = this finding's evidence values ∪ every numeric leaf in `awr.json` (collected once). Causal words: `because`, `due to`, `caused by`, `likely`, `probably`, `suggests`, matched as whole words, case-insensitive; comment `shortcut: misses paraphrases; upgrade if A6 review finds leaks`. Allowed interpretation fields: `facts`, `hypothesis`, `user_impact`, `recommendation`, `change_risk` (str) and `approval_required` (bool).
- [ ] **Step 4: Run tests** — Expected: passed.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: findings and interpretation validator"`

---

### Task 11: SVG charts

**Files:**
- Create: `oracle-awr-analyzer/scripts/svg.py`
- Test: `oracle-awr-analyzer/scripts/tests/test_svg.py`

**Interfaces:**
- Produces: `hbar(rows: list[dict], ref_line: float | None = None, ref_label: str = "", unit: str = "", title: str = "") -> str` where each row is `{"label": str, "value": float, "tag": str, "color": str}`; `stacked_bar(parts: list[dict], title: str = "") -> str` with parts `{"label","value","color"}`; module constants `SEVERITY_COLORS: dict[str,str]` (P0–P4), `CATEGORY_COLORS: dict[str,str]` (6 SQL categories), `NEUTRAL: str`.

- [ ] **Step 1: Pick the palette.** Invoke the `dataviz` skill and use its validated default palette and contrast check. Severity uses an ordered sequential ramp (P0 darkest/most saturated → P4 lightest). Categories use categorical slots. Every color must reach ≥ 3:1 contrast against the white background.
- [ ] **Step 2: Write failing tests**

```python
def test_hbar_labels_and_escaping():
    s = hbar([{"label": "<b>x</b>", "value": 35.6, "tag": "P2", "color": SEVERITY_COLORS["P2"]}], ref_line=1.0, ref_label="capacity")
    assert s.startswith("<svg") and "&lt;b&gt;x&lt;/b&gt;" in s and "P2" in s and "35.6" in s and "capacity" in s
    assert "<title>" in s or 'aria-label' in s           # accessible name
def test_hbar_ref_line_beyond_max_extends_axis(): ...    # ref 20 with max value 14.19 → line drawn inside the viewBox
def test_stacked_bar_sums_to_100_width(): ...            # segment widths sum to the bar width (±1px)
def test_empty_rows(): assert "no data" in hbar([])
```
- [ ] **Step 3: Run to verify failure** — FAIL.
- [ ] **Step 4: Implement.** Fixed width 640, row height 28, label column 220 px, value and tag text to the right of each bar. `xml.sax.saxutils.escape` for all text. `role="img"` + `<title>`. Axis max = `max(max(values), ref_line or 0) * 1.1`.
- [ ] **Step 5: Run tests** — passed.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: inline SVG chart helpers"`

---

### Task 12: render_report.py and HTML template

**Files:**
- Create: `oracle-awr-analyzer/scripts/render_report.py`, `oracle-awr-analyzer/assets/report.html.tpl`
- Test: `oracle-awr-analyzer/scripts/tests/test_render.py`

**Interfaces:**
- Consumes: `awr.json`, `findings.json`, optional `interpretation.json`, `validate()` (Task 10; a finding whose interpretation has errors renders with fallback), `svg.*` (Task 11), `common.load_config`.
- Produces: `render(run_dir: Path, audience: str) -> tuple[str, str]` (markdown, html); `mask_sql(text: str) -> str`; CLI `render_report.py <run_dir> [--config FILE]` writing `report.md` and `report.html`.

- [ ] **Step 1: Write failing tests**

```python
SECTIONS = ["Executive summary", "Database and period", "Load health", "Findings", "Top SQL by category",
            "Domain detail", "Verification SQL", "AWR diff", "Appendix"]
def test_sections_in_order(rendered):            # rendered = render(good_run, "both")
    md, html = rendered; idx = [md.index("## " + s) for s in SECTIONS]; assert idx == sorted(idx)
def test_header_line(rendered):
    assert "DB: PRODDB (DBID 1234567890) | Instance 1 (single instance) | 19.0.0.0.0 | CDB: NO" in rendered[0]
def test_five_charts(rendered): assert rendered[1].count("<svg") == 5 and "RAC comparison not available" in rendered[1]
def test_no_external_refs(rendered):
    h = rendered[1]; assert "<script" not in h and "<link" not in h
    assert not re.search(r'(src|href)\s*=\s*["\']?(https?:)?//', h)
def test_sql_text_escaped(good_run): ...         # inject "<script>alert(1)</script>" into a sql text → appears escaped only
def test_long_sql_truncated_in_tables(rendered): # sample has a >10 KB DBMS_STATS SQL; table cells ≤ 200 chars + "…"
    assert max(len(c) for c in re.findall(r"<td class=\"sql\">(.*?)</td>", rendered[1])) <= 260
def test_user_audience_masks_and_omits_dba(good_run):
    md, html = render(good_run, "user")
    assert "Verification SQL" not in md and "'2026-10-27 05:06:28'" not in html
def test_mask_sql(): assert mask_sql("where a = 'x' and b = 42 and c = :1") == "where a = '?' and b = ? and c = :1"
def test_fallback_label(good_run): ...          # remove interpretation → "standard explanation (not database-specific)" per finding
def test_rejected_interpretation_uses_fallback(good_run): ... # fabricated number in F01 → F01 shows fallback + "interpretation rejected by validator"
def test_banners(good_run): ...                 # set meta.rac True → "RAC sections not analyzed in v1" at top; restart → restart banner
def test_severity_text_not_only_color(rendered): assert "[P2]" in rendered[1]
```
- [ ] **Step 2: Run to verify failure** — FAIL.
- [ ] **Step 3: Implement.**
  - **Header block:** PRD §3 three-line format. The instance text is `Instance 1 (single instance)` for v1, `Instance 1 (RAC, not analyzed)` when `meta.rac`.
  - **Executive summary:** `interpretation.executive_summary` if valid; else generated: status line + counts per severity + most urgent finding title.
  - **Findings:** PRD §10 template lines in order. Hypothesis is shown under the label `Hypothesis:`.
  - **Charts:**
    - DB time composition: `stacked_bar` of DB CPU + wait classes from `top_events`.
    - Top 5 non-idle fg events: `hbar`, colored by the severity of their finding, else neutral.
    - Load ratio: avg (+ peak) with `ref_line=1.0`.
    - Top 10 SQL: by `by_elapsed.pct_total`, colored by category.
    - I/O latency: each `latency_events` event present, with `ref_line` = its warn value for the storage profile.
  - **HTML:** `string.Template` fills `$title`, `$banners`, `$body`. The template holds inline CSS only. All text through `html.escape`. SQL text in tables truncated to 200 chars + `…`; full text appears in the DBA verification/appendix sections.
  - **Footer:** "AWR and DBA_HIST views require the Oracle Diagnostics Pack license." `mask_sql` comment: `shortcut: regex masking may miss unusual quoting (q'[...]'); upgrade if review finds leaks`.
- [ ] **Step 4: Run tests** — passed.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: markdown and self-contained HTML report"`

---

### Task 13: AWR diff script and reference

**Files:**
- Create: `oracle-awr-analyzer/scripts/awr_diff_sql.py`, `oracle-awr-analyzer/references/awr_diff.md`
- Modify: `oracle-awr-analyzer/scripts/render_report.py` (section 8 content)
- Test: `oracle-awr-analyzer/scripts/tests/test_diff.py`

**Interfaces:**
- Consumes: `findings.diff`, `meta`.
- Produces: `diff_sql(meta: dict) -> str`; CLI `awr_diff_sql.py <run_dir>` writes `awr_diff_<db_name>.sql` only when `findings.diff.suggested`, else prints `diff not suggested` and exits 0. Linted by `lint_query` rules 2–4 (it's a `SELECT ... FROM TABLE(DBMS_WORKLOAD_REPOSITORY.AWR_DIFF_REPORT_HTML(...))` plus SQL*Plus `SET`/`SPOOL` lines, which are stripped before linting).

- [ ] **Step 1: Write `references/awr_diff.md`.** Fetch the Oracle Database PL/SQL Packages and Types Reference page for `DBMS_WORKLOAD_REPOSITORY.AWR_DIFF_REPORT_HTML` for 19c (and check 12.1, 21c, 23ai). Record the exact parameter list and order per version, with the doc URL and the date checked. If the signature differs across versions, record each. If the docs cannot be fetched, stop and ask the DBA. Don't write it from memory (PRD §12).
- [ ] **Step 2: Write failing tests**

```python
def test_diff_sql_contents(sample_awr):
    s = diff_sql(sample_awr["meta"])
    assert "SPOOL awr_diff_PRODDB.html" in s and "1234567890" in s
    assert "&base_begin_snap" in s and "&base_end_snap" in s and "3138" in s and "3162" in s
    assert lint_query({...header fields..., "sql": strip_sqlplus(s), "params": ["base_begin_snap", "base_end_snap"]}) == []
def test_not_written_when_not_suggested(sample_run): ...   # golden sample → no awr_diff_*.sql file
def test_report_section_8(good_run): ...                    # suggested=True → reasons, current snaps, baseline criteria, ask for baseline snap ids
```
- [ ] **Step 3: Run to verify failure** — FAIL.
- [ ] **Step 4: Implement** using the PRD §12 skeleton, with the argument order from `awr_diff.md`. Current snaps are literal values; the baseline uses `&base_begin_snap`/`&base_end_snap`. In `render_report.py` section 8:
  - when suggested: reasons, current snap ids, baseline criteria (same weekday and hour, similar duration, no restart between), the warning conditions (duration differs > `diff.duration_diff_pct`%, restart, CPU count differs → low confidence), and the `awr_diff_<db>.sql` name;
  - when not suggested: one line saying why not.
- [ ] **Step 5: Run tests** — passed.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: AWR diff script generation"`

---

### Task 14: SKILL.md, wait_events.md, end-to-end run

**Files:**
- Create: `oracle-awr-analyzer/SKILL.md`, `oracle-awr-analyzer/references/wait_events.md`
- Test: `oracle-awr-analyzer/scripts/tests/test_skill_md.py`

**Interfaces:**
- Consumes: every CLI above.

- [ ] **Step 1: Write `wait_events.md`.** One row per event named in `thresholds.yaml` (`latency_events`, concurrency/network/parse/redo event lists, control file read). Columns: event → domain → meaning → common causes → how to verify (verify ids). Plain English, for the agent to read while writing hypotheses.
- [ ] **Step 2: Write `SKILL.md`** (≤ 500 lines). Contents:
  - **Frontmatter:** `name: oracle-awr-analyzer`; description from PRD §13, in English.
  - **Setup:** `pip install -r requirements.txt` if imports fail.
  - **Workflow**, with exact commands using `python3 <skill>/scripts/...`:
    1. `parse_awr.py`. On exit 2 or 3, stop and explain.
    2. `classify_sql.py`.
    3. `analyze.py`. Pass `--comparison-mentioned` when the user referenced an earlier good period ("yesterday was fine", "since the deploy", "last week was fast"); pass `--config` if the user supplied one or one exists next to the AWR file.
    4. Read `findings.json` and `references/wait_events.md` (never the raw HTML), then write `interpretation.json` with the schema from spec §6.1. Rules for the prose:
       - only numbers that appear in evidence;
       - causes only in `hypothesis`;
       - plain language in `user_impact`;
       - no executable change commands; changes are described as text plus `approval_required`.
    5. `validate_findings.py` and `lint_verify_sql.py --run`. Fix and retry at most 3 times; a catalog lint failure is a skill bug, so stop and report it.
    6. `render_report.py`.
    7. `awr_diff_sql.py`.
    8. Reply to the user with the 5-line summary and the paths.
  - **Hard rules:** PRD §13, in English.
  - **v1 limits:** RAC, text, `lang`.
- [ ] **Step 3: Write failing test**

```python
def test_skill_md():
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert text.startswith("---\nname: oracle-awr-analyzer\n") and len(text.splitlines()) <= 500
    for script in ["parse_awr.py", "classify_sql.py", "analyze.py", "validate_findings.py",
                   "lint_verify_sql.py", "render_report.py", "awr_diff_sql.py", "--comparison-mentioned"]:
        assert script in text
```
- [ ] **Step 4: Run the test** — passes after Step 2. Then run the full suite: `.venv/bin/pytest -v` → all green.
- [ ] **Step 5: End-to-end run on the sample, acting as the agent.** Run steps 1–3 of SKILL.md into the scratchpad. Write a real `interpretation.json` for every finding. Run validate and lint (expect exit 0), then render. Open `report.html` with `grep -cE 'https?://' report.html` → only matches inside SQL text or none, and check visually in a browser. Record the output folder path and the findings list for the user's A3/A5 review.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: SKILL.md, wait event reference, end-to-end verified"`

---

## Acceptance trace

| PRD criterion | Covered by |
|---|---|
| A1 header | Task 2 `test_header_matches_golden` |
| A2 numbers match | Task 10 evidence check + Task 9 golden run |
| A3 load status | Task 7 tests + DBA review after Task 14 |
| A4 finding completeness | Task 9 golden assert + Task 10 required-field check |
| A5 classification | Task 5 calibration (12 cases); 50-SQL labeling by DBA after Task 14 |
| A6 hypothesis labeling | Task 10 causal-word check + DBA review |
| A7 lint | Task 6 + Task 9 rules 6–7 |
| A8 report + 5 visuals offline | Task 12 |
| A9 20 MB < 60 s | Task 4 benchmark |
| A10 diff suggestion | Task 9 `test_diff_rules` + Task 13 |
