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
    assert "s.sql_id = 'abc'" in fill(CAT["V-01"], {"dbid": 1, "inst_id": 1, "begin_snap": 3138, "end_snap": 3162, "sql_id": "abc"})


def test_cli_ok_and_duplicate_id(tmp_path, capsys):
    assert main(["--catalog", str(SKILL_DIR / "references/verify_sql")]) == 0
    hdr = "-- id: V-01\n-- title: t\n-- domain: d\n-- min_version: 11.2\n-- license: none\n-- params:\nSELECT 1 FROM dual;\n"
    (tmp_path / "a.sql").write_text(hdr + hdr)
    assert main(["--catalog", str(tmp_path)]) == 1
    assert "duplicate" in capsys.readouterr().out
