import subprocess

import pytest

from hiramedbc.docker.image import AMD64, ImageManager, image_ref, is_known_amd64_only
from hiramedbc.docker.runner import DockerCli
from hiramedbc.utils.errors import DockerError
from tests.fake_docker import FakeDocker


@pytest.fixture
def fake(monkeypatch):
    return FakeDocker().install(monkeypatch)


def _pulls(fake):
    return [c for c in fake.calls if c[1] == "pull"]


def test_image_ref_is_mysql_with_version_tag():
    assert image_ref("8.4") == "mysql:8.4"


@pytest.mark.parametrize(
    ("version", "expected"),
    [("5.7", True), ("5", True), ("5.7.44", True), ("8.0", False), ("8.4", False), ("9.1", False)],
)
def test_detects_5_x_as_amd64_only(version, expected):
    assert is_known_amd64_only(version) is expected


def test_pulls_5_x_with_amd64_platform_on_arm64(fake):
    local_arch = {"value": ""}

    def inspect(args, kw):
        found = bool(local_arch["value"])
        return subprocess.CompletedProcess(args, 0 if found else 1, local_arch["value"], "")

    def pull(args, kw):
        local_arch["value"] = "amd64"
        return subprocess.CompletedProcess(args, 0, "", "")

    fake.responders["image inspect"] = inspect
    fake.responders["pull"] = pull
    assert ImageManager(DockerCli(), arch="arm64").ensure("5.7") == AMD64
    assert _pulls(fake)[0][2:] == ["--platform", AMD64, "mysql:5.7"]


def test_does_not_specify_platform_for_5_x_on_x64(fake):
    fake.respond("image inspect", stdout="amd64")
    assert ImageManager(DockerCli(), arch="amd64").ensure("5.7") is None


def test_does_not_pull_image_present_locally(fake):
    fake.respond("image inspect", stdout="arm64")
    assert ImageManager(DockerCli(), arch="arm64").ensure("8.4") is None
    assert _pulls(fake) == []


def test_runs_with_emulation_when_local_image_is_amd64_on_arm64(fake):
    fake.respond("image inspect", stdout="amd64")
    assert ImageManager(DockerCli(), arch="arm64").ensure("9.9") == AMD64


def test_retries_with_amd64_when_manifest_missing_for_unlisted_version(fake):
    attempts = []

    def pull(args, kw):
        attempts.append(args)
        if "--platform" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(
            args, 1, "", "no matching manifest for linux/arm64/v8 in the manifest list entries"
        )

    fake.responders["pull"] = pull
    assert ImageManager(DockerCli(), arch="arm64").pull("6.0") == AMD64
    assert [("--platform" in a) for a in attempts] == [False, True]


def test_non_manifest_pull_failure_raises_without_retry(fake):
    fake.respond("pull", returncode=1, stderr="manifest unknown")
    with pytest.raises(DockerError, match="Could not pull image mysql:99.9"):
        ImageManager(DockerCli(), arch="arm64").pull("99.9")
    assert len(_pulls(fake)) == 1


def test_does_not_retry_on_x64_even_if_manifest_missing(fake):
    fake.respond("pull", returncode=1, stderr="no matching manifest")
    with pytest.raises(DockerError):
        ImageManager(DockerCli(), arch="amd64").pull("6.0")
    assert len(_pulls(fake)) == 1


@pytest.mark.parametrize(
    "stderr",
    [
        "no matching manifest for linux/arm64/v8 in the manifest list entries",
        "image with reference mysql:6.0 was found but does not provide the specified platform (linux/arm64)",
        "no match for platform in manifest: not found",
    ],
)
def test_retries_with_amd64_for_each_message_indicating_no_arm64_image(fake, stderr):
    fake.respond("pull", returncode=1, stderr=stderr)
    attempts = []
    original = fake.responders["pull"]

    def pull(args, kw):
        attempts.append(args)
        if "--platform" in args:
            fake.respond("pull")
            return fake.responders["pull"](args, kw)
        return original(args, kw)

    fake.responders["pull"] = pull
    assert ImageManager(DockerCli(), arch="arm64").pull("6.0") == AMD64


def test_runs_as_amd64_after_amd64_retry_even_if_inspect_fails(fake):
    fake.respond("image inspect", returncode=1)
    calls = []

    def pull(args, kw):
        calls.append(args)
        if "--platform" not in args:
            return original_fail(args, kw)
        return ok(args, kw)

    fake.respond("pull", returncode=1, stderr="no matching manifest")
    original_fail = fake.responders["pull"]
    fake.respond("pull")
    ok = fake.responders["pull"]
    fake.responders["pull"] = pull
    assert ImageManager(DockerCli(), arch="arm64").ensure("6.0") == AMD64


def test_prefers_docker_daemon_architecture(fake, monkeypatch):
    monkeypatch.setattr("hiramedbc.docker.image.host_arch", lambda: "amd64")
    fake.respond("info", stdout="aarch64\n")
    assert ImageManager(DockerCli()).arch == "arm64"


def test_falls_back_to_python_architecture_when_daemon_is_unavailable(fake, monkeypatch):
    monkeypatch.setattr("hiramedbc.docker.image.host_arch", lambda: "amd64")
    fake.respond("info", stdout="", stderr="Cannot connect to the Docker daemon")
    assert ImageManager(DockerCli()).arch == "amd64"
