import pytest

from hiramedbc.utils.aliases import is_version, resolve_command_name, validate_alias_name
from hiramedbc.utils.errors import ConfigError


@pytest.mark.parametrize(
    ("token", "expected"),
    [("c", "connect"), ("connect", "connect"), ("ls", "list"), ("rm", "remove"), ("dr", "doctor"), ("init", "init")],
)
def test_resolves_command_name_from_full_name_or_alias(token, expected):
    assert resolve_command_name(token) == expected


@pytest.mark.parametrize("token", ["con", "conn", "production", "Connect", ""])
def test_does_not_resolve_prefixes_or_unknown_names_as_commands(token):
    assert resolve_command_name(token) is None


@pytest.mark.parametrize("token", ["8", "8.4", "8.0.36", "10.11.2"])
def test_detects_version_like_tokens(token):
    assert is_version(token)


@pytest.mark.parametrize("token", ["8.", "8.4.0.1", "v8", "8.x", "lts", "8.4-prod", ""])
def test_does_not_treat_non_version_like_tokens_as_versions(token):
    assert not is_version(token)


@pytest.mark.parametrize("name", ["production", "dev-db", "stg_01", "prod.84", "_tmp", "a" * 64])
def test_accepts_valid_connection_names(name):
    validate_alias_name(name)


@pytest.mark.parametrize("name", ["8", "8.4", "8.0.36"])
def test_rejects_version_like_connection_names(name):
    with pytest.raises(ConfigError, match="looks like a version"):
        validate_alias_name(name)


@pytest.mark.parametrize("name", ["init", "connect", "c", "config", "list", "ls", "dr", "LS", "Connect"])
def test_rejects_command_names_and_aliases_case_insensitively(name):
    with pytest.raises(ConfigError, match="clashes with a command name or alias"):
        validate_alias_name(name)


@pytest.mark.parametrize("name", ["", " prod", "prod db", "-prod", "本番", "a" * 65, "prod\n"])
def test_rejects_connection_names_with_invalid_characters_or_length(name):
    with pytest.raises(ConfigError, match="Invalid connection name"):
        validate_alias_name(name)
