import pytest
from classify_sql import classify
from common import load_config
from parse_awr import split_module

CLS = load_config(None)["classification"]
DB = "db-host-01"


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
    r = classify({"sql_id": "x", "module": module, "text": text, **split_module(module)}, DB, CLS)
    assert (r["category"], r["confidence"]) == (cat, conf)
    assert r["reasons"] and r["verify_sql"] == "V-01"


def test_dba_list_wins():
    cls = dict(CLS, dba={"monitoring_modules": ["Poller"]})
    assert classify({"module": "Poller", "text": "SELECT ID FROM LCK_SEMAPHORE"}, DB, cls)["category"] == "monitoring"


def test_sample_known_sqls(sample_awr):
    cat = {s["sql_id"]: classify(s, DB, CLS)["category"] for s in sample_awr["sql"]}
    for i in ("mon000000001a", "mon000000002b", "mon000000003c", "mon000000004d"):
        assert cat[i] == "monitoring", i
    assert cat["app000000001a"] == "user_app"


def test_same_rule_on_two_objects_is_one_signal():
    r = classify({"module": "X", "text": "SELECT * FROM LCK_A JOIN LCK_B ON 1=1"}, DB, CLS)
    assert (r["category"], r["confidence"]) == ("user_app", "medium")


def test_prefix_anchored_to_object_name():
    assert classify({"module": "X", "text": "SELECT * FROM SHOP_ORDERS"}, DB, CLS)["category"] == "unknown"
    assert classify({"module": "X", "text": "SELECT * FROM APP.OP_X"}, DB, CLS)["category"] == "user_app"
    dba = dict(CLS, dba={"app_table_prefixes": ["ORD_"]})
    assert classify({"module": "X", "text": "SELECT * FROM DROP_X"}, DB, dba)["category"] == "unknown"
