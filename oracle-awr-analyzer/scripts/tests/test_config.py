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


def test_bad_regex(tmp_path):
    with pytest.raises(ConfigError, match="app_hosts"):
        load_config(cfgfile(tmp_path, "app_hosts: ['(']\n"))


@pytest.mark.parametrize("body,key", [("storage_profile: tape\n", "storage_profile"), ("audience: boss\n", "audience")])
def test_invalid_enum(tmp_path, body, key):
    with pytest.raises(ConfigError, match=key):
        load_config(cfgfile(tmp_path, body))
