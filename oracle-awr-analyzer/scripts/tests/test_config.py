import pytest
from common import ConfigError, load_config


def cfgfile(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(body)
    return p


def test_scalar_list_is_one_item(tmp_path):
    assert load_config(cfgfile(tmp_path, "app_programs: APPSRV\n"))["classification"]["dba"]["app_programs"] == ["APPSRV"]


def test_top_level_not_mapping(tmp_path):
    with pytest.raises(ConfigError, match="mapping"):
        load_config(cfgfile(tmp_path, "- a\n- b\n"))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="nope.yaml"):
        load_config(tmp_path / "nope.yaml")


def test_malformed_yaml(tmp_path):
    with pytest.raises(ConfigError, match="YAML"):
        load_config(cfgfile(tmp_path, "a: [1,\n"))


def test_dba_lists_are_literal_and_drop_empties(tmp_path):
    from classify_sql import classify
    c = load_config(cfgfile(tmp_path, "app_hosts: ''\nmonitoring_modules: ['C++ agent', 'dbspicao11@host (TNS V1-V3)', '  ']\n"))["classification"]
    assert "app_hosts" not in c["dba"] and len(c["dba"]["monitoring_modules"]) == 2
    mod = "dbspicao11@host (TNS V1-V3)"
    assert classify({"module": mod, "text": "select 1 from t"}, "h", c)["reasons"][0].startswith("module matches DBA")
    assert classify({"module": "C++ agent", "text": "select 1 from t"}, "h", c)["category"] == "monitoring"
    assert classify({"module": "x", "client_host": "anything", "text": "select 1 from t"}, "h", c)["category"] != "user_app"


@pytest.mark.parametrize("body,key", [("storage_profile: tape\n", "storage_profile"), ("audience: boss\n", "audience")])
def test_invalid_enum(tmp_path, body, key):
    with pytest.raises(ConfigError, match=key):
        load_config(cfgfile(tmp_path, body))
