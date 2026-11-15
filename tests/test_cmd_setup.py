import sys

import pytest

from hiramedbc.secrets.keychain import Keychain
from tests.cli_env import CliEnv
from tests.conftest import ScriptedPrompter


@pytest.fixture
def env(tmp_path, monkeypatch):
    env = CliEnv(tmp_path, monkeypatch)
    env.docker.respond("info", stdout="27.3.1")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    return env


def _write(env, **conn):
    env.write_config({"connections": {"prod": {"host": "db", "user": "app", **conn}}, "defaults": {"version": "8.4"}})
    if sys.platform != "win32":
        env.config_path.chmod(0o600)


# --- doctor ---


def test_doctor_exits_with_code_0_when_no_problems(env):
    _write(env, password="keyring")
    env.keychain.set("prod", "pw")
    result = env.invoke(["doctor"])
    assert result.exit_code == 0, result.output
    assert "Available (server 27.3.1)" in result.output


def test_doctor_exits_with_code_1_when_runtime_is_unavailable(env):
    _write(env)
    env.docker.respond("info", stdout="", stderr="Cannot connect to the Docker daemon")
    result = env.invoke(["dr"])
    assert result.exit_code == 1
    assert "Cannot connect" in result.output


def test_doctor_exits_with_code_1_when_docker_command_is_missing(env, monkeypatch):
    _write(env)
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = env.invoke(["dr"])
    assert result.exit_code == 1
    assert "not found on PATH" in result.output


def test_doctor_detects_plaintext_password_without_showing_its_value(env):
    env.write_config({"connections": {"prod": {"host": "db", "user": "app", "password": "TOPSECRET"}}})
    result = env.invoke(["dr"])
    assert result.exit_code == 1
    assert "plaintext password" in result.output
    assert "TOPSECRET" not in result.output


def test_doctor_suggests_init_when_config_file_is_missing(env):
    result = env.invoke(["dr"])
    assert "hirame init" in result.output


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_doctor_warns_when_config_file_is_readable_by_others(env):
    _write(env)
    env.config_path.chmod(0o644)
    assert "chmod 600" in env.invoke(["dr"]).output


def test_doctor_warns_about_keyring_connection_without_saved_password(env):
    _write(env, password="keyring")
    assert 'Set to "keyring", but no password is saved' in env.invoke(["dr"]).output


def test_doctor_warns_when_keychain_is_unavailable(env):
    import keyring.backends.fail

    _write(env)
    env.keychain = Keychain(keyring.backends.fail.Keyring())
    assert "Keychain: Not available" in env.invoke(["dr"]).output


def test_doctor_warns_about_stopped_leftovers_and_only_shows_names_and_removal_for_running(env):
    _write(env)
    env.docker.respond("ps", stdout="hiramedbc-8-4-aa\tExited (0) 1 hour ago\nhiramedbc-8-4-bb\tUp 3 minutes\n")
    output = env.invoke(["dr"]).output
    assert "1 container(s) left behind by abnormal exits: hiramedbc-8-4-aa" in output
    assert "1 running: hiramedbc-8-4-bb" in output
    assert "docker rm -f" in output


def test_doctor_clean_removes_only_stopped_leftovers(env):
    _write(env)
    env.docker.respond("ps", stdout="hiramedbc-8-4-aa\tExited (1) 1 hour ago\nhiramedbc-8-4-bb\tUp 3 minutes\n")
    env.invoke(["dr", "--clean"])
    removed = [c[-1] for c in env.docker.calls if c[1] == "rm"]
    assert removed == ["hiramedbc-8-4-aa"]


def test_doctor_on_arm64_actually_checks_emulation_when_amd64_image_exists(env):
    env.write_config({"connections": {"old": {"host": "h", "version": "5.7"}}})
    env.docker.respond("image inspect", stdout="amd64")
    env.docker.respond("start", stdout="x86_64\n")
    output = env.invoke(["dr"]).output
    create = env.docker.call("create")
    assert create[create.index("--platform") + 1] == "linux/amd64"
    assert create[-2:] == ["uname", "-m"]
    assert "amd64 emulation works" in output
    assert env.docker.subcommands().count("rm") >= 1


def test_doctor_warns_when_emulation_does_not_work(env):
    env.write_config({"connections": {"old": {"host": "h", "version": "5.7"}}})
    env.docker.respond("image inspect", stdout="amd64")
    env.docker.respond("start", returncode=1, stderr="exec format error")
    assert "Rosetta / QEMU" in env.invoke(["dr"]).output


def test_doctor_pull_pulls_all_versions_in_config(env):
    env.write_config({"connections": {"app": {"host": "h", "version": "8.0"}}, "defaults": {"version": "8.4"}})
    result = env.invoke(["dr", "--pull"])
    assert result.exit_code == 0, result.output
    assert [c[-1] for c in env.docker.calls if c[1] == "pull"] == ["mysql:8.0", "mysql:8.4"]


def test_doctor_pull_pulls_given_versions(env):
    _write(env)
    env.invoke(["dr", "--pull", "5.7", "9.1"])
    pulls = [c for c in env.docker.calls if c[1] == "pull"]
    assert [c[-1] for c in pulls] == ["mysql:5.7", "mysql:9.1"]
    assert pulls[0][2:4] == ["--platform", "linux/amd64"]


def test_doctor_rejects_versions_without_pull(env):
    _write(env)
    result = env.invoke(["dr", "8.4"])
    assert result.exit_code == 1
    assert "Versions can only be given together with --pull." in result.output


# --- init ---


def _init_prompter(register=True, pull=True):
    # default version -> add a connection? -> add form (user/password asked every time) -> continue? -> pull?
    texts = ["8.4"]
    confirms = [register]
    if register:
        texts += ["prod", "db.example.com", "3306", ""]
        confirms += [False, False, False]  # save user? / SSL? / continue?
    confirms.append(pull)
    return ScriptedPrompter(texts=texts, confirms=confirms)


def test_init_creates_config_adds_connection_and_pulls_image(env):
    result = env.invoke(["init"], _init_prompter())
    assert result.exit_code == 0, result.output
    config = env.read_config()
    assert config["defaults"] == {"version": "8.4"}
    assert list(config["connections"]) == ["prod"]
    assert [c[-1] for c in env.docker.calls if c[1] == "pull"] == ["mysql:8.4"]
    assert "hirame c prod" in result.output


def test_init_does_not_pull_when_declined(env):
    env.invoke(["init"], _init_prompter(pull=False))
    assert "pull" not in env.docker.subcommands()


def test_init_creates_config_even_when_runtime_is_unavailable(env):
    env.docker.respond("info", stdout="")
    prompter = ScriptedPrompter(texts=["8.4"], confirms=[False])
    result = env.invoke(["init"], prompter)
    assert result.exit_code == 0, result.output
    assert env.config_path.exists()
    assert "pull" not in env.docker.subcommands()


def test_init_resumes_from_adding_connections_without_overwriting_existing_config(env):
    _write(env)
    before = env.read_config()["connections"]["prod"]
    prompter = ScriptedPrompter(confirms=[True, False])  # resume / add no more (pull is not asked if none added)
    env.invoke(["init"], prompter)
    assert env.read_config()["connections"]["prod"] == before


def test_init_does_nothing_when_resume_is_declined_for_existing_config(env):
    _write(env)
    result = env.invoke(["init"], ScriptedPrompter(confirms=[False]))
    assert result.exit_code == 0
    assert "pull" not in env.docker.subcommands()


def test_init_warns_and_completes_when_pull_fails(env):
    env.docker.respond("pull", returncode=1, stderr="network error")
    result = env.invoke(["init"], _init_prompter())
    assert result.exit_code == 0
    assert "doctor --pull" in result.output


# --- config ---


def test_config_path_prints_path_in_use_to_stdout(env):
    _write(env)
    result = env.invoke(["config", "path"])
    assert result.exit_code == 0
    assert str(env.config_path) in result.stdout


def test_config_path_notes_when_file_is_not_created_yet(env):
    assert "Not created yet" in env.invoke(["config", "path"]).output


def test_config_edit_opens_config_file_in_editor(env, monkeypatch):
    _write(env)
    opened = []
    monkeypatch.setenv("VISUAL", "myeditor")
    monkeypatch.setattr("hiramedbc.utils.platform.subprocess.run", lambda cmd, check: opened.append(cmd))
    result = env.invoke(["config", "edit"])
    assert result.exit_code == 0, result.output
    assert opened == [["myeditor", str(env.config_path)]]


def test_config_edit_suggests_init_when_config_file_is_missing(env):
    result = env.invoke(["config", "edit"])
    assert result.exit_code == 1
    assert "hirame init" in result.output


def test_doctor_clean_runs_rm_without_force_and_keeps_running_containers(env):
    _write(env)
    env.docker.respond(
        "ps", stdout="hiramedbc-8-4-aa\tExited (0) 1 hour ago\nhiramedbc-8-4-bb\tRestarting (1) 2 seconds ago\n"
    )
    env.invoke(["dr", "--clean"])
    rm_calls = [c for c in env.docker.calls if c[1] == "rm"]
    assert rm_calls == [["docker", "rm", "-v", "hiramedbc-8-4-aa"]]


def test_init_pulls_only_versions_of_connections_added_in_this_run(env):
    env.write_config({"connections": {"old": {"host": "h", "version": "5.7"}}, "defaults": {"version": "8.4"}})
    prompter = ScriptedPrompter(
        texts=["prod", "db", "3306", ""],
        confirms=[True, True, False, False, False, True],  # resume / add more / save user? / SSL? / continue? / pull
    )
    env.invoke(["init"], prompter)
    assert [c[-1] for c in env.docker.calls if c[1] == "pull"] == ["mysql:8.4"]


def test_config_path_prints_path_with_square_brackets_as_is(env, tmp_path, monkeypatch):
    path = tmp_path / "[x]" / "config.json"
    path.parent.mkdir()
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HIRAMEDBC_CONFIG", str(path))
    assert str(path) in env.invoke(["config", "path"]).stdout


def test_doctor_clean_does_not_remove_just_created_containers(env):
    _write(env)
    env.docker.respond("ps", stdout="hiramedbc-8-4-cc\tCreated\n")
    env.invoke(["dr", "--clean"])
    assert "rm" not in env.docker.subcommands()
