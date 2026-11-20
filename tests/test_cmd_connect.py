import pytest

from tests.cli_env import CliEnv
from tests.conftest import ScriptedPrompter
from tests.fake_docker import FakeProcess


@pytest.fixture
def env(tmp_path, monkeypatch):
    env = CliEnv(tmp_path, monkeypatch)
    env.write_config(
        {
            "connections": {
                "prod": {
                    "host": "db.example.com",
                    "port": 3307,
                    "user": "app",
                    "password": "keyring",
                    "version": "8.0",
                },
                "stg": {"host": "stg.example.com", "user": "", "password": ""},
            },
            "defaults": {"version": "8.4"},
        }
    )
    env.keychain.set("prod", "TOPSECRET")
    return env


def test_connect_starts_mysql_with_image_of_connection_version(env):
    result = env.invoke(["connect", "prod"])
    assert result.exit_code == 0, result.output
    create = env.docker.call("create")
    assert create[-3:] == ["mysql:8.0", "mysql", "--defaults-extra-file=/tmp/hiramedbc.cnf"]


def test_connect_passes_keychain_password_via_option_file(env):
    env.invoke(["c", "prod"])
    assert env.option_file() == '[client]\nhost="db.example.com"\nport=3307\nuser="app"\npassword="TOPSECRET"\n'


def test_connect_passes_plaintext_config_password_via_option_file_without_asking(env):
    env.write_config({"connections": {"p": {"host": "h", "user": "app", "password": "PLAIN", "version": "8.4"}}})
    prompter = ScriptedPrompter()
    result = env.invoke(["c", "p"], prompter)
    assert result.exit_code == 0, result.output
    assert env.option_file() == '[client]\nhost="h"\nport=3306\nuser="app"\npassword="PLAIN"\n'
    assert prompter.asked == []
    assert all("PLAIN" not in " ".join(call) for call in env.docker.calls)
    assert "PLAIN" not in result.output


def test_connect_does_not_expose_password_in_docker_args_or_output(env):
    result = env.invoke(["c", "prod"])
    assert all("TOPSECRET" not in " ".join(call) for call in env.docker.calls)
    assert "TOPSECRET" not in result.output


def test_connect_version_argument_overrides_connection_version(env):
    result = env.invoke(["c", "5.7", "prod"])
    assert "mysql:5.7" in env.docker.call("create")
    assert "(argument)" in result.output


def test_connect_uses_default_version_and_shows_its_source_when_connection_has_none(env):
    result = env.invoke(["c", "stg"], ScriptedPrompter(texts=["me"], secrets=["pw"]))
    assert "mysql:8.4" in env.docker.call("create")
    assert "(default)" in result.output


def test_connect_explains_how_to_set_version_when_none_is_found(env):
    env.write_config({"connections": {"stg": {"host": "h"}}})
    result = env.invoke(["c", "stg"])
    assert result.exit_code == 1
    assert "No MySQL version specified." in result.output
    assert env.docker.calls == []


def test_connect_works_without_command_name(env):
    result = env.invoke(["prod"])
    assert result.exit_code == 0, result.output
    assert env.docker.subcommands() == ["image", "create", "cp", "start", "rm"]


def test_connect_passes_database_name_to_mysql(env):
    env.invoke(["c", "8.4", "prod", "my_db"])
    assert env.docker.call("create")[-1] == "my_db"


def test_connect_rejects_too_many_arguments(env):
    result = env.invoke(["c", "8.4", "prod", "db", "extra"])
    assert result.exit_code == 1
    assert "Invalid arguments." in result.output


def test_connect_suggests_similar_name_for_unknown_connection(env):
    result = env.invoke(["c", "prd"])
    assert result.exit_code == 1
    assert "prod" in result.output


def test_connect_config_option_works_after_command(env, tmp_path):
    other = tmp_path / "other.json"
    other.write_text(
        '{"connections": {"other": {"host": "o", "user": "u", "password": ""}}, "defaults": {"version": "9.1"}}'
    )
    result = env.invoke(["c", "other", "--config", str(other)], ScriptedPrompter(secrets=["pw"]))
    assert result.exit_code == 0, result.output
    assert "mysql:9.1" in env.docker.call("create")


def test_connect_exits_with_container_exit_code(env):
    env.docker.process = FakeProcess(returncode=2)
    assert env.invoke(["c", "prod"]).exit_code == 2


def test_connect_asks_user_and_password_for_ask_every_time_connection(env):
    prompter = ScriptedPrompter(texts=["me"], secrets=["typed"])
    env.invoke(["c", "stg"], prompter)
    assert 'user="me"\npassword="typed"' in env.option_file()


def test_connect_ask_password_ignores_saved_password(env):
    env.invoke(["c", "--ask-password", "prod"], ScriptedPrompter(secrets=["other"]))
    assert 'password="other"' in env.option_file()


def test_connect_mounts_ssl_cert_read_only_and_writes_it_to_option_file(env, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    env.write_config(
        {
            "connections": {
                "s": {
                    "host": "h",
                    "user": "u",
                    "password": "",
                    "version": "8.4",
                    "ssl": {"mode": "VERIFY_CA", "ca": str(ca)},
                }
            }
        }
    )
    env.invoke(["c", "s"], ScriptedPrompter(secrets=["pw"]))
    create = env.docker.call("create")
    assert (
        create[create.index("--mount") + 1] == f"type=bind,source={ca.resolve()},target=/hiramedbc/ssl/ca.pem,readonly"
    )
    assert 'ssl-mode=VERIFY_CA\nssl-ca="/hiramedbc/ssl/ca.pem"' in env.option_file()


def test_connect_fails_before_connecting_when_ssl_cert_is_missing(env, tmp_path):
    env.write_config({"connections": {"s": {"host": "h", "version": "8.4", "ssl": {"ca": str(tmp_path / "none.pem")}}}})
    result = env.invoke(["c", "s"])
    assert result.exit_code == 1
    assert "not found." in result.output
    assert env.docker.calls == []


def test_connect_to_localhost_suggests_host_docker_internal(env):
    env.write_config({"connections": {"l": {"host": "localhost", "user": "u", "password": "", "version": "8.4"}}})
    result = env.invoke(["c", "l"], ScriptedPrompter(secrets=["pw"]))
    assert "host.docker.internal" in result.output


def test_connect_on_arm64_runs_5x_on_amd64_platform(env):
    env.docker.respond("image inspect", stdout="amd64")
    env.invoke(["c", "5.7", "prod"])
    create = env.docker.call("create")
    assert create[create.index("--platform") + 1] == "linux/amd64"


def test_connect_suggests_init_when_config_file_is_missing(env):
    env.config_path.unlink()
    result = env.invoke(["c", "prod"])
    assert result.exit_code == 1
    assert "hirame init" in result.output


def test_connect_does_not_crash_on_values_with_square_brackets(env):
    env.write_config({"connections": {"b": {"host": "h", "user": "x[/y]", "password": "", "version": "8.4"}}})
    env.docker.respond("image inspect", returncode=1)
    env.docker.respond("pull", returncode=1, stderr="Error: [/tmp] is not a directory")
    result = env.invoke(["c", "b"])
    assert result.exit_code == 1
    assert "[/tmp]" in result.output


# --- M3 review fixes ---


def test_connect_allocates_tty_when_run_from_terminal(env, monkeypatch):
    monkeypatch.setattr("hiramedbc.commands.connect.is_interactive_terminal", lambda: True)
    env.invoke(["c", "prod"])
    assert "-t" in env.docker.call("create")


def test_connect_fails_when_stdin_is_redirected_and_user_is_asked_every_time(env, monkeypatch):
    monkeypatch.setattr("hiramedbc.commands.common.stdin_is_terminal", lambda: False)
    result = env.invoke(["c", "stg"])
    assert result.exit_code == 1
    assert "stdin is redirected" in result.output
    assert env.docker.calls == []


def test_connect_ask_user_connects_with_entered_user_name(env, monkeypatch):
    monkeypatch.setattr("hiramedbc.commands.common.stdin_is_terminal", lambda: True)
    env.invoke(["c", "--ask-user", "prod"], ScriptedPrompter(texts=["other"], secrets=["pw2"]))
    assert 'user="other"\npassword="pw2"' in env.option_file()


def test_connect_exits_with_code_130_when_interrupted_by_ctrl_c(env):
    env.docker.process = FakeProcess(returncode=137, interrupts=2)
    assert env.invoke(["c", "prod"]).exit_code == 130
