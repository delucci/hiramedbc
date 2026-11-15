"""Cleanup, signals, and discarding credentials (addresses M2 review items #1 #2 #3 #12 #13 #15 #16 #17 #18)."""

import os
import signal
import subprocess
import sys

import pytest

from hiramedbc.docker.option_file import OptionFile
from hiramedbc.docker.runner import ContainerRunner, ContainerSpec, DockerCli
from hiramedbc.utils.errors import ConfigError, DockerError
from tests.fake_docker import FakeDocker, FakeProcess


@pytest.fixture
def fake(monkeypatch):
    return FakeDocker().install(monkeypatch)


def _spec(**kwargs):
    defaults = dict(
        image="mysql:8.4",
        version="8.4",
        command=["mysql", "--defaults-extra-file=/tmp/hiramedbc.cnf"],
        option_file=OptionFile(host="h", port=3306, user="u", password="TOPSECRET"),
    )
    return ContainerSpec(**{**defaults, **kwargs})


def _signal_during_rm(fake, sig):
    seen = {}

    def rm(args, kw):
        seen["handler"] = signal.getsignal(sig)
        return fake.default_result(args, kw)

    fake.responders["rm"] = rm
    ContainerRunner(DockerCli()).run(_spec())
    return seen["handler"]


def test_ignores_sigint_during_cleanup_rm(fake):
    assert _signal_during_rm(fake, signal.SIGINT) == signal.SIG_IGN


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is not handled on Windows (SIGBREAK is used instead)")
def test_ignores_sigterm_during_cleanup_rm(fake):
    assert _signal_during_rm(fake, signal.SIGTERM) == signal.SIG_IGN


def test_restores_signal_handlers_after_cleanup(fake):
    before_int = signal.getsignal(signal.SIGINT)
    before_term = signal.getsignal(signal.SIGTERM)
    ContainerRunner(DockerCli()).run(_spec())
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == (before_int, before_term)


@pytest.mark.skipif(sys.platform == "win32", reason="Sending SIGTERM is POSIX only")
def test_sigterm_while_running_stops_docker_cli_removes_container_and_exits(fake):
    class TerminatedProcess(FakeProcess):
        def wait(self, timeout=None):
            if not self.killed:
                os.kill(os.getpid(), signal.SIGTERM)
            return self.returncode

    fake.process = TerminatedProcess()
    with pytest.raises(SystemExit) as excinfo:
        ContainerRunner(DockerCli()).run(_spec())
    assert excinfo.value.code == 128 + signal.SIGTERM
    assert fake.process.killed is True
    assert fake.subcommands()[-1] == "rm"


def test_first_ctrl_c_with_tty_does_not_stop_docker_cli(fake):
    fake.process = FakeProcess(returncode=0, interrupts=1)
    result = ContainerRunner(DockerCli()).run(_spec(stdin=True, tty=True))
    assert (fake.process.killed, result.interrupted) == (False, True)


def test_first_ctrl_c_without_tty_stops_docker_cli(fake):
    fake.process = FakeProcess(returncode=-9, interrupts=1)
    result = ContainerRunner(DockerCli()).run(_spec(stdin=False, tty=False))
    assert (fake.process.killed, result.interrupted) == (True, True)


def test_exit_code_after_signal_is_128_plus_signal_number(fake):
    fake.process = FakeProcess(returncode=-9, interrupts=1)
    result = ContainerRunner(DockerCli()).run(_spec(stdin=False, tty=False))
    assert result.exit_code == 128 + 9


def test_rm_adds_v_to_remove_anonymous_volumes(fake):
    ContainerRunner(DockerCli()).run(_spec())
    assert fake.call("rm")[2:4] == ["-f", "-v"]


def test_warns_when_rm_fails(fake, capsys):
    fake.respond("rm", returncode=1, stderr="permission denied")
    ContainerRunner(DockerCli()).run(_spec())
    assert "Could not remove container" in capsys.readouterr().err


def test_does_not_warn_when_container_is_already_removed(fake, capsys):
    fake.respond("rm", returncode=1, stderr="Error response from daemon: No such container: x")
    ContainerRunner(DockerCli()).run(_spec())
    assert "Could not remove container" not in capsys.readouterr().err


def test_discards_option_file_password_after_injection(fake):
    spec = _spec()
    ContainerRunner(DockerCli()).run(spec)
    assert spec.option_file.password == ""


def test_unrepresentable_value_fails_before_creating_container(fake):
    spec = _spec(option_file=OptionFile(host="h", port=3306, user="u", password="a'b\"c"))
    with pytest.raises(ConfigError, match="A value containing both double quotes"):
        ContainerRunner(DockerCli()).run(spec)
    assert "create" not in fake.subcommands()


def test_pull_progress_goes_to_stderr(fake):
    captured = {}

    def pull(args, kw):
        captured["stdout"] = kw.get("stdout")
        return subprocess.CompletedProcess(args, 0, "", "")

    fake.responders["pull"] = pull
    DockerCli().pull("mysql:8.4")
    assert captured["stdout"] is sys.stderr


def test_pull_error_output_is_shown_once_in_exception_message(fake, capsys):
    fake.respond("pull", returncode=1, stderr="manifest unknown")
    with pytest.raises(DockerError) as excinfo:
        DockerCli().pull("mysql:x")
    assert "manifest unknown" in excinfo.value.message
    assert "manifest unknown" not in capsys.readouterr().err
