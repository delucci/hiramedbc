import io
import subprocess
import tarfile

import pytest

from hiramedbc.docker.option_file import OptionFile
from hiramedbc.docker.runner import (
    MANAGED_LABEL,
    ContainerRunner,
    ContainerSpec,
    DockerCli,
    Mount,
    container_name,
)
from hiramedbc.utils.errors import DockerError
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


def test_runs_create_cp_start_rm_in_order(fake):
    ContainerRunner(DockerCli()).run(_spec())
    assert fake.subcommands() == ["create", "cp", "start", "rm"]


def test_container_name_is_built_from_version_and_unique_id():
    name = container_name("8.4")
    assert name.startswith("hiramedbc-8-4-")
    assert container_name("8.4") != name


def test_create_adds_rm_and_managed_label(fake):
    ContainerRunner(DockerCli()).run(_spec())
    create = fake.call("create")
    assert "--rm" in create
    assert create[create.index("--label") + 1] == MANAGED_LABEL


def test_does_not_put_password_in_docker_arguments(fake):
    ContainerRunner(DockerCli()).run(_spec())
    assert all("TOPSECRET" not in " ".join(call) for call in fake.calls)


def test_injects_option_file_as_tar_via_stdin(fake):
    ContainerRunner(DockerCli()).run(_spec())
    assert fake.call("cp")[2:] == ["-", fake.call("create")[fake.call("create").index("--name") + 1] + ":/tmp"]
    with tarfile.open(fileobj=io.BytesIO(fake.inputs["cp"])) as tar:
        content = tar.extractfile("hiramedbc.cnf").read().decode()
    assert 'password="TOPSECRET"' in content


def test_interactive_connection_adds_it_and_starts_with_ai(fake):
    ContainerRunner(DockerCli()).run(_spec(stdin=True, tty=True))
    create = fake.call("create")
    assert ("-i" in create, "-t" in create) == (True, True)
    assert fake.call("start")[2] == "-ai"


def test_does_not_add_t_without_tty(fake):
    ContainerRunner(DockerCli()).run(_spec(stdin=True, tty=False))
    assert "-t" not in fake.call("create")


def test_run_without_stdin_starts_with_a(fake):
    ContainerRunner(DockerCli()).run(_spec(stdin=False, tty=False))
    assert "-i" not in fake.call("create")
    assert fake.call("start")[2] == "-a"


def test_can_specify_platform_and_mounts(fake):
    ContainerRunner(DockerCli()).run(
        _spec(platform="linux/amd64", mounts=[Mount("/host/ca.pem", "/hiramedbc/ssl/ca.pem")])
    )
    create = fake.call("create")
    assert create[create.index("--platform") + 1] == "linux/amd64"
    assert create[create.index("--mount") + 1] == "type=bind,source=/host/ca.pem,target=/hiramedbc/ssl/ca.pem,readonly"


def test_rejects_mounting_path_with_comma():
    with pytest.raises(DockerError, match="Cannot mount a path that contains a comma"):
        Mount("/a,b/ca.pem", "/x").to_arg()


def test_returns_container_exit_code(fake):
    fake.process = FakeProcess(returncode=3)
    assert ContainerRunner(DockerCli()).run(_spec()).exit_code == 3


def test_removes_container_even_if_cp_fails(fake):
    fake.responders["cp"] = lambda args, kw: subprocess.CompletedProcess(args, 1, b"", b"boom")
    with pytest.raises(DockerError, match="Could not copy the option file into the container"):
        ContainerRunner(DockerCli()).run(_spec())
    assert fake.subcommands()[-1] == "rm"


def test_attempts_rm_even_if_create_fails(fake):
    fake.respond("create", returncode=1, stderr="no such image")
    with pytest.raises(DockerError, match="no such image"):
        ContainerRunner(DockerCli()).run(_spec())
    assert fake.subcommands() == ["create", "rm"]


def test_single_ctrl_c_waits_for_client_to_exit(fake):
    fake.process = FakeProcess(returncode=130, interrupts=1)
    result = ContainerRunner(DockerCli()).run(_spec())
    assert (result.exit_code, result.interrupted, fake.process.killed) == (130, True, False)
    assert fake.subcommands()[-1] == "rm"


def test_second_ctrl_c_kills_docker_cli(fake):
    fake.process = FakeProcess(returncode=137, interrupts=2)
    ContainerRunner(DockerCli()).run(_spec())
    assert fake.process.killed is True


def test_missing_docker_command_raises_error_with_hint(monkeypatch):
    import hiramedbc.docker.runner as runner

    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(runner.subprocess, "run", missing)
    with pytest.raises(DockerError, match="docker command not found") as excinfo:
        DockerCli().runtime_status()
    assert "Docker" in excinfo.value.hint


def test_lists_containers_with_managed_label(fake):
    fake.respond("ps", stdout="hiramedbc-8-4-aa\tExited (0) 2 hours ago\nhiramedbc-5-7-bb\tCreated\n")
    containers = DockerCli().list_managed()
    assert [(c.name, c.status) for c in containers] == [
        ("hiramedbc-8-4-aa", "Exited (0) 2 hours ago"),
        ("hiramedbc-5-7-bb", "Created"),
    ]
    assert f"label={MANAGED_LABEL}" in fake.call("ps")


def test_image_arch_is_none_for_image_not_present_locally(fake):
    fake.respond("image inspect", returncode=1)
    assert DockerCli().image_arch("mysql:8.4") is None


def test_hints_to_start_runtime_when_it_is_stopped(fake):
    fake.respond(
        "pull",
        returncode=1,
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
    )
    with pytest.raises(DockerError) as excinfo:
        DockerCli().pull("mysql:8.4")
    assert "does not seem to be running" in excinfo.value.hint


def test_runtime_is_available_when_server_version_is_returned(fake):
    fake.respond("info", stdout="27.3.1\n")
    status = DockerCli().runtime_status()
    assert (status.available, status.server_version) == (True, "27.3.1")


def test_runtime_is_unavailable_when_server_version_is_empty_despite_exit_code_0(fake):
    fake.respond("info", returncode=0, stdout="\n", stderr="Cannot connect to the Docker daemon at unix:///x.sock.")
    status = DockerCli().runtime_status()
    assert status.available is False
    assert "Cannot connect" in status.error
