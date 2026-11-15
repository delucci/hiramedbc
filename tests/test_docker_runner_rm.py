"""Cleanup docker rm (addresses final review items #2 #3 #4 #11)."""

import subprocess
import sys

import pytest

from hiramedbc.docker.option_file import OptionFile
from hiramedbc.docker.runner import REMOVE_TIMEOUT, ContainerRunner, ContainerSpec, DockerCli
from tests.fake_docker import FakeDocker, FakeProcess


@pytest.fixture
def fake(monkeypatch):
    return FakeDocker().install(monkeypatch)


def _spec():
    return ContainerSpec(
        image="mysql:8.4",
        version="8.4",
        command=["mysql"],
        option_file=OptionFile(host="h", port=3306, user="u", password="p"),
    )


def _capture_rm_kwargs(fake):
    captured = {}

    def rm(args, kw):
        captured.update(kw)
        return fake.default_result(args, kw)

    fake.responders["rm"] = rm
    return captured


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX session separation")
def test_rm_starts_in_separate_session_unreachable_by_terminal_ctrl_c(fake):
    captured = _capture_rm_kwargs(fake)
    ContainerRunner(DockerCli()).run(_spec())
    assert captured.get("start_new_session") is True


def test_rm_has_timeout(fake):
    captured = _capture_rm_kwargs(fake)
    ContainerRunner(DockerCli()).run(_spec())
    assert captured.get("timeout") == REMOVE_TIMEOUT


def test_warns_and_returns_when_rm_times_out(fake, capsys):
    def rm(args, kw):
        raise subprocess.TimeoutExpired(args, REMOVE_TIMEOUT)

    fake.responders["rm"] = rm
    ContainerRunner(DockerCli()).run(_spec())
    assert "did not finish within" in capsys.readouterr().err


def test_does_not_warn_when_removal_is_already_in_progress(fake, capsys):
    fake.respond("rm", returncode=1, stderr="Error response from daemon: removal of container x is already in progress")
    assert DockerCli().remove("x") is True
    assert "Could not remove container" not in capsys.readouterr().err


def test_rm_without_force_omits_f(fake):
    DockerCli().remove("x", force=False)
    assert fake.call("rm") == ["docker", "rm", "-v", "x"]


def test_keeps_waiting_until_exit_after_wait_timeout(fake):
    class SlowProcess(FakeProcess):
        def __init__(self):
            super().__init__(returncode=0)
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits < 3:
                raise subprocess.TimeoutExpired("docker", timeout)
            return self.returncode

    fake.process = SlowProcess()
    result = ContainerRunner(DockerCli()).run(_spec())
    assert (result.exit_code, fake.process.waits) == (0, 3)
