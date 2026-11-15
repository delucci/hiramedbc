import stat
import sys

import pytest

from tests.cli_env import CliEnv
from tests.conftest import ScriptedPrompter
from tests.fake_docker import FakeProcess


@pytest.fixture
def env(tmp_path, monkeypatch):
    env = CliEnv(tmp_path, monkeypatch)
    env.write_config(
        {"connections": {"prod": {"host": "db", "user": "app", "password": "keyring"}}, "defaults": {"version": "8.4"}}
    )
    env.keychain.set("prod", "pw")
    env.docker.output = b"-- MySQL dump\n"
    return env


def test_dump_writes_dump_to_output_file(env, tmp_path):
    out = tmp_path / "backup.sql"
    result = env.invoke(["dump", "prod", "my_db", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"-- MySQL dump\n"


def test_dump_starts_mysqldump_without_tty_and_passes_database_name(env, tmp_path):
    env.invoke(["d", "8.0", "prod", "my_db", str(tmp_path / "o.sql")])
    create = env.docker.call("create")
    assert ("-i" in create, "-t" in create) == (False, False)
    assert create[-4:] == ["mysql:8.0", "mysqldump", "--defaults-extra-file=/tmp/hiramedbc.cnf", "my_db"]
    assert env.docker.call("start")[2] == "-a"


def test_dump_passes_args_after_double_dash_as_extra_mysqldump_options(env, tmp_path):
    env.invoke(["d", "prod", "my_db", str(tmp_path / "o.sql"), "--", "--no-data", "--single-transaction"])
    assert env.docker.call("create")[-3:] == ["my_db", "--no-data", "--single-transaction"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_dump_file_is_readable_and_writable_only_by_owner(env, tmp_path):
    out = tmp_path / "o.sql"
    env.invoke(["d", "prod", "my_db", str(out)])
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_dump_failure_keeps_existing_file_and_removes_temp_file(env, tmp_path):
    out = tmp_path / "o.sql"
    out.write_bytes(b"old")
    env.docker.process = FakeProcess(returncode=2)
    result = env.invoke(["d", "prod", "my_db", str(out), "--force"])
    assert result.exit_code == 1
    assert "exit code 2" in result.output
    assert [p.name for p in tmp_path.iterdir() if p.name != "config.json"] == ["o.sql"]
    assert out.read_bytes() == b"old"


def test_dump_interrupted_exits_with_code_130_without_creating_output_file(env, tmp_path):
    out = tmp_path / "o.sql"
    env.docker.process = FakeProcess(returncode=130, interrupts=1)
    result = env.invoke(["d", "prod", "my_db", str(out)])
    assert result.exit_code == 130
    assert not out.exists()


def test_dump_does_nothing_when_overwrite_is_declined(env, tmp_path):
    out = tmp_path / "o.sql"
    out.write_bytes(b"old")
    result = env.invoke(["d", "prod", "my_db", str(out)], ScriptedPrompter(confirms=[False]))
    assert result.exit_code == 1
    assert (out.read_bytes(), env.docker.calls) == (b"old", [])


def test_dump_force_overwrites_without_confirmation(env, tmp_path):
    out = tmp_path / "o.sql"
    out.write_bytes(b"old")
    prompter = ScriptedPrompter()
    env.invoke(["d", "prod", "my_db", str(out), "-f"], prompter)
    assert (out.read_bytes(), prompter.asked) == (b"-- MySQL dump\n", [])


def test_dump_fails_when_output_directory_does_not_exist(env, tmp_path):
    result = env.invoke(["d", "prod", "my_db", str(tmp_path / "none" / "o.sql")])
    assert result.exit_code == 1
    assert "does not exist." in result.output


def test_dump_shows_usage_when_arguments_are_missing(env):
    result = env.invoke(["d", "prod", "my_db"])
    assert result.exit_code == 1
    assert "hirame dump" in result.output


# --- M3 review fixes ---


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks requires privileges")
def test_dump_does_not_follow_symlink_placed_at_temp_file_path(env, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"keep")
    (tmp_path / ".o.sql.hiramedbc-tmp").symlink_to(victim)
    out = tmp_path / "o.sql"
    result = env.invoke(["d", "prod", "my_db", str(out)])
    assert result.exit_code == 0, result.output
    assert victim.read_bytes() == b"keep"
    assert not out.is_symlink()


def test_dump_fails_before_connecting_when_output_directory_is_not_writable(env, tmp_path, monkeypatch):
    monkeypatch.setattr("hiramedbc.commands.dump.os.access", lambda path, mode: False)
    result = env.invoke(["d", "prod", "my_db", str(tmp_path / "o.sql")])
    assert result.exit_code == 1
    assert "is not writable." in result.output
    assert env.docker.calls == []


def test_dump_rejects_extra_arguments_without_double_dash(env, tmp_path):
    result = env.invoke(["d", "prod", "db1", str(tmp_path / "o.sql"), "db2"])
    assert result.exit_code == 1
    assert "--" in result.output
    assert env.docker.calls == []


def test_dump_with_version_passes_args_after_double_dash_as_extra_options(env, tmp_path):
    env.invoke(["d", "8.0", "prod", "my_db", str(tmp_path / "o.sql"), "--", "--no-data"])
    assert env.docker.call("create")[-2:] == ["my_db", "--no-data"]


def test_dump_writes_to_stdout_when_output_is_hyphen(env):
    result = env.invoke(["d", "prod", "my_db", "-"])
    assert result.exit_code == 0, result.output
    assert b"-- MySQL dump" in result.stdout_bytes


def test_dump_interrupted_keeps_existing_file(env, tmp_path):
    out = tmp_path / "o.sql"
    out.write_bytes(b"old")
    env.docker.process = FakeProcess(returncode=-9, interrupts=1)
    result = env.invoke(["d", "prod", "my_db", str(out), "-f"])
    assert result.exit_code == 130
    assert out.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir() if p.name != "config.json"] == ["o.sql"]


def test_dump_fails_for_unknown_connection_before_asking_to_overwrite(env, tmp_path):
    out = tmp_path / "o.sql"
    out.write_bytes(b"old")
    prompter = ScriptedPrompter()
    result = env.invoke(["d", "none", "my_db", str(out)], prompter)
    assert result.exit_code == 1
    assert prompter.asked == []


def test_dump_places_table_names_after_double_dash_after_database_name(env, tmp_path):
    env.invoke(["d", "prod", "my_db", str(tmp_path / "o.sql"), "--", "--no-data", "users"])
    assert env.docker.call("create")[-3:] == ["my_db", "--no-data", "users"]
