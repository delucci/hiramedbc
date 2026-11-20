import pytest

from hiramedbc.config.schema import AppConfig, ConnectionConfig
from hiramedbc.config.validator import parse_config
from hiramedbc.utils.errors import ConfigError


def _raw(**conn):
    return {"connections": {"prod": {"host": "db.example.com", **conn}}}


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_user_and_password_as_null_or_empty_string_mean_ask_every_time(empty):
    config = parse_config(_raw(user=empty, password=empty))
    conn = config.connections["prod"]
    assert (conn.user_fixed, conn.password_store) == (False, "ask")


def test_missing_user_and_password_keys_mean_ask_every_time():
    conn = parse_config(_raw()).connections["prod"]
    assert (conn.user, conn.password) == (None, None)


def test_port_defaults_to_3306_when_omitted():
    assert parse_config(_raw()).connections["prod"].port == 3306


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_rejects_out_of_range_port(port):
    with pytest.raises(ConfigError, match="connections.prod.port"):
        parse_config(_raw(port=port))


def test_accepts_plaintext_password_in_config_file():
    conn = parse_config(_raw(user="app", password="s3cr3t!")).connections["prod"]
    assert (conn.password_store, conn.plaintext_password, conn.password_in_keychain) == ("config", "s3cr3t!", False)


def test_keeps_leading_and_trailing_spaces_of_plaintext_password():
    conn = parse_config(_raw(user="app", password=" pw ")).connections["prod"]
    assert conn.plaintext_password == " pw "


def test_rejects_non_string_password_without_including_value_in_error():
    with pytest.raises(ConfigError) as excinfo:
        parse_config(_raw(user="app", password=1234))
    assert "connections.prod.password: Must be a string" in excinfo.value.message
    assert "1234" not in excinfo.value.message


def test_does_not_include_plaintext_password_in_repr():
    conn = parse_config(_raw(user="app", password="s3cr3t!")).connections["prod"]
    assert "s3cr3t!" not in repr(conn)


def test_rejects_plaintext_password_with_ask_every_time_user():
    with pytest.raises(ConfigError, match="A saved password requires a saved user name"):
        parse_config(_raw(user="", password="s3cr3t!"))


def test_json_dict_for_saving_keeps_plaintext_password():
    data = ConnectionConfig(host="h", user="app", password="s3cr3t!").to_json_dict()
    assert data["password"] == "s3cr3t!"


def test_rejects_saved_password_with_ask_every_time_user():
    with pytest.raises(ConfigError, match="A saved password requires a saved user name"):
        parse_config(_raw(user="", password="keyring"))


def test_accepts_saved_user_with_keyring_password():
    conn = parse_config(_raw(user="app", password="keyring")).connections["prod"]
    assert (conn.user_fixed, conn.password_store, conn.plaintext_password) == (True, "keychain", None)


def test_rejects_empty_host():
    with pytest.raises(ConfigError, match="connections.prod.host"):
        parse_config(_raw(host=""))


def test_rejects_unknown_key():
    with pytest.raises(ConfigError, match="pasword"):
        parse_config(_raw(pasword="x"))


def test_rejects_reserved_connection_name():
    with pytest.raises(ConfigError, match="clashes with a command name or alias"):
        parse_config({"connections": {"list": {"host": "h"}}})


def test_rejects_ssh_settings_as_unsupported():
    with pytest.raises(ConfigError, match="SSH tunnels are not supported yet"):
        parse_config(_raw(ssh={"host": "bastion"}))


def test_treats_empty_ssl_settings_as_none():
    assert parse_config(_raw(ssl={"ca": ""})).connections["prod"].ssl is None


def test_normalizes_ssl_mode_to_uppercase():
    assert parse_config(_raw(ssl={"mode": "required"})).connections["prod"].ssl.mode == "REQUIRED"


def test_rejects_invalid_ssl_mode():
    with pytest.raises(ConfigError, match="connections.prod.ssl.mode"):
        parse_config(_raw(ssl={"mode": "ON"}))


@pytest.mark.parametrize("version", ["8.4", "8.0.36", "lts", "5.7"])
def test_accepts_versions_valid_as_image_tags(version):
    assert parse_config(_raw(version=version)).connections["prod"].version == version


@pytest.mark.parametrize("version", ["8.4 ", "mysql:8.4", "../x"])
def test_rejects_versions_invalid_as_image_tags(version):
    with pytest.raises(ConfigError, match="version"):
        parse_config(_raw(version=version))


def test_rejects_top_level_that_is_not_an_object():
    with pytest.raises(ConfigError, match="must be a JSON object"):
        parse_config([])


def test_treats_null_connections_and_defaults_as_empty():
    config = parse_config({"connections": None, "defaults": None})
    assert (config.connections, config.defaults.version) == ({}, None)


def test_lists_versions_used_in_config_without_duplicates():
    config = AppConfig(
        connections={
            "a": ConnectionConfig(host="h", version="8.0"),
            "b": ConnectionConfig(host="h"),
            "c": ConnectionConfig(host="h", version="8.4"),
        },
        defaults={"version": "8.4"},
    )
    assert config.used_versions() == ["8.0", "8.4"]


def test_json_dict_for_saving_outputs_empty_credentials_as_empty_strings():
    data = ConnectionConfig(host="h").to_json_dict()
    assert data == {"host": "h", "port": 3306, "user": "", "password": ""}


def test_numeric_version_hints_to_write_it_as_string():
    with pytest.raises(ConfigError, match=r'Version must be a string \(for example "8\.4"\)'):
        parse_config(_raw(version=8.4))


def test_reports_missing_required_field_in_readable_message():
    with pytest.raises(ConfigError, match="connections.prod.host: Required field"):
        parse_config({"connections": {"prod": {}}})


def test_reports_unknown_key_in_readable_message():
    with pytest.raises(ConfigError, match=r"Unknown key \(check the spelling\)"):
        parse_config(_raw(pasword="x"))


@pytest.mark.parametrize("host", ["db\nexample.com", "db example.com", "db\x00"])
def test_rejects_host_with_whitespace_or_control_characters(host):
    with pytest.raises(ConfigError, match="Host name must not contain whitespace or control characters"):
        parse_config(_raw(host=host))


def test_rejects_boolean_port():
    with pytest.raises(ConfigError, match="Port must be an integer"):
        parse_config(_raw(port=True))


def test_example_config_file_satisfies_schema():
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).parents[1] / "config" / "config.example.json").read_text(encoding="utf-8"))
    assert set(parse_config(raw).connections) == {"development", "staging", "production"}
