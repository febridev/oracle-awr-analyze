import pytest
from common import SKILL_DIR
from lint_verify_sql import load_catalog, lint_query, fill, main

CAT = load_catalog(SKILL_DIR / "references/verify_sql")


def test_catalog_clean():
    assert {k: lint_query(q) for k, q in CAT.items() if lint_query(q)} == {}
    assert {"V-01", "V-02", "V-03"} <= CAT.keys()
    assert {f"V-{i:02d}" for i in range(1, 18)} == set(CAT)


def q(sql, params="dbid", license="none"):
    return {"id": "T", "title": "t", "domain": "d", "min_version": "11.2", "license": license,
            "params": params.split(", ") if params else [], "sql": sql}


@pytest.mark.parametrize("sql,needle", [
    ("DELETE FROM t WHERE id = &dbid", "starts with"),
    ("SELECT * FROM t WHERE id = &dbid FOR UPDATE", "FOR UPDATE"),
    ("SELECT DBMS_STATS.GATHER_TABLE_STATS('A','B') FROM dual WHERE 1 = &dbid", "DBMS_STATS"),
    ("WITH x AS (SELECT 1 FROM dual) SELECT * FROM x", "params"),
    ("SELECT * FROM t WHERE a = &dbid AND b = &other", "params"),
])
def test_violations(sql, needle):
    assert any(needle in e for e in lint_query(q(sql)))


def test_keywords_inside_strings_and_comments_ignored():
    assert lint_query(q("SELECT 'delete me' AS x /* update */ FROM t WHERE id = &dbid")) == []


def test_bad_license_and_missing_header():
    assert lint_query(q("SELECT 1 FROM t WHERE a=&dbid", license="free"))
    bad = q("SELECT 1 FROM t WHERE a=&dbid")
    del bad["title"]
    assert lint_query(bad)


def test_allowed_package():
    assert lint_query(q("SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_AWR('&sql_id'))", params="sql_id")) == []


def test_fill():
    assert "s.sql_id = 'abcdefghij123'" in fill(CAT["V-01"], {"dbid": 1, "inst_id": 1, "begin_snap": 3138, "end_snap": 3162, "sql_id": "abcdefghij123"})


def test_cli_ok_and_duplicate_id(tmp_path, capsys):
    assert main(["--catalog", str(SKILL_DIR / "references/verify_sql")]) == 0
    hdr = "-- id: V-01\n-- title: t\n-- domain: d\n-- min_version: 11.2\n-- license: none\n-- params:\nSELECT 1 FROM dual;\n"
    (tmp_path / "a.sql").write_text(hdr + hdr)
    assert main(["--catalog", str(tmp_path)]) == 1
    assert "duplicate" in capsys.readouterr().out


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM dual; RENAME t TO t_old",
    "SELECT 1 FROM dual; LOCK TABLE t IN EXCLUSIVE MODE",
    "SELECT 1 FROM dual; PURGE DBA_RECYCLEBIN",
    "SELECT 1 FROM dual; NOAUDIT ALL",
    "SELECT 1 FROM dual\n/\nCOMMENT ON TABLE t IS NULL",
    "SELECT 1 FROM dual\n\nHOST rm -rf ~",
    "SELECT 1 FROM dual\r\n/\r\nCOMMENT ON TABLE t IS NULL",
    "SELECT 1 FROM dual\r\rHOST ls",
    "SELECT 1 FROM dual\n.\nHOST ls",
    "SELECT 1\n#HOST ls\nFROM dual;",
    "SELECT q'[']' FROM dual; DELETE FROM t WHERE x = '",
    "SELECT \"DBMS_PIPE\".\"SEND_MESSAGE\"('p') FROM dual",
    "SELECT sys.\"DBMS_LOCK\".SLEEP(1) FROM dual",
    "SELECT UTL_HTTP.REQUEST('http://x') FROM dual",
])
def test_bypasses_rejected(sql):
    assert lint_query(q(sql, params="")) != []


def test_legit_forms_accepted():
    assert lint_query(q("SELECT q'[it's delete]' FROM dual;", params="")) == []
    assert lint_query(q("SELECT 1 -- c\n/* x */\nFROM dual;", params="")) == []
    assert lint_query(q("SELECT DBMS_XPLAN.DISPLAY_AWR('x') FROM dual", params="")) == []


@pytest.mark.parametrize("key,val", [("sql_id", "x'; DROP TABLE t; --"), ("dbid", "1 OR 1=1")])
def test_fill_rejects_injection(key, val):
    vals = {"dbid": 1, "inst_id": 1, "begin_snap": 1, "end_snap": 2, "sql_id": "abcdefghij123", key: val}
    with pytest.raises(ValueError):
        fill(CAT["V-01"], vals)


def test_fill_event_quote_doubled():
    vals = {"dbid": 1, "inst_id": 1, "begin_snap": 1, "end_snap": 2, "event": "db file x"}
    assert "event_name = 'db file x'" in fill(CAT["V-07"], vals)
    assert "event_name = 'a''b'" in fill(CAT["V-07"], dict(vals, event="a'b"))


@pytest.mark.parametrize("ev", ["a\n\nHOST rm -rf ~ #", "a\n/\nb", "a;\nHOST x", "a\x7fb", "a\rb"])
def test_fill_rejects_control_chars(ev):
    vals = {"dbid": 1, "inst_id": 1, "begin_snap": 1, "end_snap": 2, "event": ev}
    with pytest.raises(ValueError):
        fill(CAT["V-07"], vals)


def test_fill_rejects_unicode_digits():
    vals = {"dbid": "\u0661\u0662", "inst_id": 1, "begin_snap": 1, "end_snap": 2, "sql_id": "abcdefghij123"}
    with pytest.raises(ValueError):
        fill(CAT["V-01"], vals)
