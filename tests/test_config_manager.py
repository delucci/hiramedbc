import json
import stat
import sys
from pathlib import Path

import pytest

from hiramedbc.config.manager import ConfigLocation, ConfigManager, default_config_path, resolve_config_location
from hiramedbc.config.schema import AppConfig, ConnectionConfig
from hiramedbc.utils.errors import ConfigError, ConfigNotFoundError


def test_config_option_takes_precedence_over_env_var(tmp_path):
    location = resolve_config_location(tmp_path / "opt.json", env={"HIRAMEDBC_CONFIG": str(tmp_path / "env.json")})
    assert (location.path, location.source) == (tmp_path / "opt.json", "option")


def test_uses_env_var_when_no_option(tmp_path):
    location = resolve_config_location(None, env={"HIRAMEDBC_CONFIG": str(tmp_path / "env.json")})
    assert (location.path, location.source) == (tmp_path / "env.json", "env")


def test_uses_default_location_when_neither_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    location = resolve_config_location(None, env={})
    assert (location.path, location.source) == (default_config_path(), "default")


def test_default_config_path_is_dotdir_under_home(tmp_path, monkeypatch):
    # HOME on POSIX, USERPROFILE on Windows
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert default_config_path() == tmp_path / ".hiramedbc" / "config.json"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_save_creates_new_config_directory_as_0700(tmp_path):
    target = tmp_path / ".hiramedbc" / "config.json"
    ConfigManager(ConfigLocation(target, "default")).save(AppConfig())
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_missing_config_at_default_location_hints_init(tmp_path):
    manager = ConfigManager(ConfigLocation(tmp_path / "config.json", "default"))
    with pytest.raises(ConfigNotFoundError) as excinfo:
        manager.load()
    assert "hirame init" in excinfo.value.hint


def test_missing_specified_config_shows_path(tmp_path):
    manager = ConfigManager(ConfigLocation(tmp_path / "x.json", "option"))
    with pytest.raises(ConfigNotFoundError, match="x.json"):
        manager.load()


def test_invalid_json_error_shows_line_and_column(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{\n  "connections": {,}\n}', encoding="utf-8")
    with pytest.raises(ConfigError, match="line 2, column"):
        ConfigManager(ConfigLocation(path, "option")).load()


def test_saved_config_can_be_read_back(tmp_path):
    manager = ConfigManager(ConfigLocation(tmp_path / "sub" / "config.json", "option"))
    config = AppConfig(connections={"prod": ConnectionConfig(host="h", user="app", password="keyring")})
    manager.save(config)
    assert manager.load() == config


def test_does_not_escape_non_ascii_when_saving(tmp_path):
    manager = ConfigManager(ConfigLocation(tmp_path / "config.json", "option"))
    manager.save(AppConfig(connections={"prod": ConnectionConfig(host="ホスト.example")}))
    assert "ホスト.example" in manager.path.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_saved_config_is_readable_and_writable_by_owner_only(tmp_path):
    manager = ConfigManager(ConfigLocation(tmp_path / "config.json", "option"))
    manager.save(AppConfig())
    assert stat.S_IMODE(manager.path.stat().st_mode) == 0o600
    assert not manager.is_readable_by_others()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_detects_config_readable_by_other_users(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    path.chmod(0o644)
    assert ConfigManager(ConfigLocation(path, "option")).is_readable_by_others()


def test_leaves_no_temp_file_after_saving(tmp_path):
    manager = ConfigManager(ConfigLocation(tmp_path / "config.json", "option"))
    manager.save(AppConfig())
    assert [p.name for p in Path(tmp_path).iterdir()] == ["config.json"]


def test_reads_utf8_config_with_bom(tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"connections": {}}).encode())
    assert ConfigManager(ConfigLocation(path, "option")).load().connections == {}


def test_non_utf8_config_hints_encoding(tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes('{"connections": {"本番": {}}}'.encode("cp932"))
    with pytest.raises(ConfigError, match="UTF-8"):
        ConfigManager(ConfigLocation(path, "option")).load()


def test_directory_path_hints_to_specify_a_file(tmp_path):
    with pytest.raises(ConfigError, match="is a directory"):
        ConfigManager(ConfigLocation(tmp_path, "option")).load()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_saving_to_unwritable_location_raises_config_error(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(ConfigError, match="Cannot save config file"):
            ConfigManager(ConfigLocation(locked / "config.json", "option")).save(AppConfig())
    finally:
        locked.chmod(0o700)


@pytest.mark.skipif(sys.platform == "win32", reason="Creating symlinks requires extra privileges")
def test_symlinked_config_updates_link_target(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "config.json"
    link.symlink_to(real)
    ConfigManager(ConfigLocation(link, "option")).save(AppConfig(connections={"prod": ConnectionConfig(host="h")}))
    assert link.is_symlink()
    assert "prod" in real.read_text(encoding="utf-8")


def test_empty_config_option_is_treated_as_unset():
    from hiramedbc.context import AppContext

    assert AppContext(config_option="").location.source != "option"
