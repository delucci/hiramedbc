"""Calling the docker CLI and running disposable containers.

- subprocess is called with an argument list, never through a shell.
- Credentials are never passed as CLI arguments or environment variables; an option file is injected with
  `docker cp -` instead.
- On top of `--rm`, the Python side also runs `docker rm -f` in a finally block.
"""

import secrets
import shutil
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import IO, Any

from hiramedbc import APP_NAME
from hiramedbc.docker.option_file import CONTAINER_DIR, OptionFile, to_tar
from hiramedbc.utils import console as out
from hiramedbc.utils.errors import DockerError
from hiramedbc.utils.platform import normalize_arch

MANAGED_LABEL = f"{APP_NAME}.managed=true"


_DAEMON_DOWN_MARKERS = ("cannot connect to the docker daemon", "error during connect", "is the docker daemon running")


def daemon_hint(output: str) -> str | None:
    """Return a hint if docker's error output indicates that the runtime is not running."""
    if any(marker in output.lower() for marker in _DAEMON_DOWN_MARKERS):
        return (
            "The Docker-compatible runtime does not seem to be running. "
            "Start it and try again (run `hirame doctor` to check)."
        )
    return None


class DockerCommandError(DockerError):
    def __init__(self, message: str, *, output: str = "", hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.output = output


@dataclass(frozen=True)
class Mount:
    source: str
    target: str

    def to_arg(self) -> str:
        if "," in self.source or "," in self.target:
            raise DockerError(f"Cannot mount a path that contains a comma: {self.source}")
        return f"type=bind,source={self.source},target={self.target},readonly"


REMOVE_TIMEOUT = 30
"""Seconds to wait for the cleanup docker rm (so we do not hang if the daemon stops responding)."""


def _detached() -> dict[str, Any]:
    """Arguments that start a child process in a separate group that terminal signals (Ctrl+C) do not reach."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    return {"start_new_session": True}


@dataclass(frozen=True)
class RuntimeStatus:
    available: bool
    server_version: str
    error: str


@dataclass(frozen=True)
class ManagedContainer:
    name: str
    status: str


class DockerCli:
    """Thin wrapper around the docker CLI (system boundary)."""

    def __init__(self, executable: str = "docker") -> None:
        self.executable = executable

    def is_installed(self) -> bool:
        return shutil.which(self.executable) is not None

    def _run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        try:
            return subprocess.run([self.executable, *args], check=False, **kwargs)
        except FileNotFoundError:
            raise DockerError(
                "docker command not found.",
                hint="Install a Docker-compatible runtime (Docker Desktop, OrbStack, Colima, Rancher Desktop, etc.).",
            ) from None

    def _check(self, result: subprocess.CompletedProcess[Any], message: str) -> None:
        if result.returncode != 0:
            output = (result.stderr or "").strip() if isinstance(result.stderr, str) else ""
            raise DockerCommandError(f"{message}\n{output}".rstrip(), output=output, hint=daemon_hint(output))

    def runtime_status(self) -> RuntimeStatus:
        """Whether the runtime is usable.

        `docker info --format` exits with code 0 even when it cannot connect to the daemon,
        so this checks whether ServerVersion was returned.
        """
        result = self._run(["info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
        version = (result.stdout or "").strip()
        if result.returncode == 0 and version:
            return RuntimeStatus(True, version, "")
        error = (result.stderr or "").strip() or "Could not get the server version"
        return RuntimeStatus(False, "", error.splitlines()[0])

    def server_arch(self) -> str | None:
        """CPU architecture of the Docker daemon (arm64 / amd64), or None if unavailable."""
        result = self._run(["info", "--format", "{{.Architecture}}"], capture_output=True, text=True)
        arch = (result.stdout or "").strip()
        if result.returncode != 0 or not arch or " " in arch:
            return None
        return normalize_arch(arch)

    def image_arch(self, image: str) -> str | None:
        """Architecture of a local image, or None if the image is not present."""
        result = self._run(["image", "inspect", "--format", "{{.Architecture}}", image], capture_output=True, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def pull(self, image: str, platform: str | None = None) -> None:
        """Pull an image. Progress goes to stderr (stdout is reserved for dump output)."""
        args = ["pull"]
        if platform:
            args += ["--platform", platform]
        result = self._run([*args, image], stdout=sys.stderr, stderr=subprocess.PIPE, text=True)
        # Include the error output in the exception message so it is shown only once
        self._check(result, f"Could not pull image {image}.")

    def create(
        self,
        name: str,
        image: str,
        command: list[str],
        *,
        tty: bool,
        stdin: bool,
        platform: str | None = None,
        mounts: list[Mount] | None = None,
    ) -> None:
        args = ["create", "--rm", "--label", MANAGED_LABEL, "--name", name]
        if stdin:
            args.append("-i")
        if tty:
            args.append("-t")
        if platform:
            args += ["--platform", platform]
        for mount in mounts or []:
            args += ["--mount", mount.to_arg()]
        result = self._run([*args, image, *command], capture_output=True, text=True)
        self._check(result, "Could not create the container.")

    def copy_tar(self, name: str, dest_dir: str, tar_bytes: bytes) -> None:
        result = self._run(["cp", "-", f"{name}:{dest_dir}"], input=tar_bytes, capture_output=True)
        if result.returncode != 0:
            output = result.stderr.decode("utf-8", "replace").strip()
            raise DockerCommandError(f"Could not copy the option file into the container.\n{output}", output=output)

    def start(self, name: str, *, stdin: bool, tty: bool = False, stdout: IO[bytes] | None = None) -> tuple[int, bool]:
        """Start the container and wait for it to exit. Returns (exit code, whether Ctrl+C was received).

        - With a TTY: Ctrl+C reaches the client through the terminal's raw mode, so if it also reaches
          Python, keep waiting on the first one and stop the docker CLI on the second.
        - Without a TTY (dump, piped input): the container's PID 1 may ignore SIGINT, so stop the
          docker CLI on the first one. The container itself is stopped by the caller's cleanup (rm -f).
        - On any other exception (such as SystemExit from SIGTERM), stop the docker CLI before re-raising.
        """
        args = [self.executable, "start", "-ai" if stdin else "-a", name]
        try:
            process = subprocess.Popen(args, stdout=stdout)
        except FileNotFoundError:
            raise DockerError("docker command not found.") from None
        interrupted = False
        try:
            while True:
                try:
                    # Wait with a timeout: on Windows, wait() without a timeout cannot be interrupted by Ctrl+C
                    code = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
                except KeyboardInterrupt:
                    if interrupted or not tty:
                        process.kill()
                    interrupted = True
        except BaseException:
            process.kill()
            process.wait()
            raise
        # If terminated by a signal (negative value), use 128 + signal number like a shell
        return (128 - code if code < 0 else code), interrupted

    def start_captured(self, name: str) -> tuple[int, str]:
        """Start the container and capture stdout as a string (used by doctor checks)."""
        result = self._run(["start", "-a", name], capture_output=True, text=True)
        return result.returncode, result.stdout

    def remove(self, name: str, *, force: bool = True) -> bool:
        """Remove the container along with its anonymous volumes. Returns True if removed (including when
        already gone). Warns on failure.

        With force=False, a running container is not removed (used by doctor --clean).
        docker rm is started in a separate process group: terminal Ctrl+C reaches the whole process group,
        and the docker CLI itself would abort even if Python ignores the signal.
        """
        args = ["rm", "-f", "-v", name] if force else ["rm", "-v", name]
        try:
            result = self._run(args, capture_output=True, text=True, timeout=REMOVE_TIMEOUT, **_detached())
        except subprocess.TimeoutExpired:
            out.warn(
                f"Removing container {name} did not finish within {REMOVE_TIMEOUT} seconds. "
                "Run `hirame doctor` to check."
            )
            return False
        except DockerError as e:
            out.warn(f"Could not remove container {name}: {e.message}")
            return False
        stderr = (result.stderr or "").strip()
        lowered = stderr.lower()
        # Treat as removed if it is already gone or removal by --rm is in progress
        if result.returncode == 0 or any(m in lowered for m in ("no such container", "is already in progress")):
            return True
        out.warn(
            f"Could not remove container {name} ({stderr.splitlines()[0] if stderr else 'unknown reason'})."
            + (" Run `hirame doctor --clean` to remove it." if force else "")
        )
        return False

    def list_managed(self) -> list[ManagedContainer]:
        result = self._run(
            ["ps", "-a", "--filter", f"label={MANAGED_LABEL}", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
        )
        self._check(result, "Could not list containers.")
        containers = []
        for line in result.stdout.splitlines():
            if line.strip():
                name, _, status = line.partition("\t")
                containers.append(ManagedContainer(name.strip(), status.strip()))
        return containers


def container_name(version: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in version)
    return f"{APP_NAME}-{safe}-{secrets.token_hex(4)}"


@dataclass
class ContainerSpec:
    image: str
    version: str
    command: list[str]
    option_file: OptionFile = field(repr=False)
    stdin: bool = True
    tty: bool = True
    platform: str | None = None
    mounts: list[Mount] = field(default_factory=list)


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    interrupted: bool


def _termination_signals() -> list[signal.Signals]:
    if sys.platform == "win32":
        # Ctrl+Break. Closing the console (CTRL_CLOSE_EVENT) cannot be handled, so rely on --rm and doctor --clean
        return [signal.SIGBREAK]  # type: ignore[attr-defined]
    return [signal.SIGTERM, signal.SIGHUP]


def _swap_handlers(signals: list[signal.Signals], handler: Any, *, skip_ignored: bool) -> dict[int, Any]:
    previous: dict[int, Any] = {}
    for sig in signals:
        try:
            current = signal.getsignal(sig)
            if current is None or (skip_ignored and current == signal.SIG_IGN):
                # None: handler set outside Python (cannot be restored). SIG_IGN: the user ignores it on purpose
                continue
            signal.signal(sig, handler)
            previous[sig] = current
        except ValueError:  # Signals cannot be handled outside the main thread
            pass
    return previous


def _restore_handlers(previous: dict[int, Any]) -> None:
    for sig, old in previous.items():
        signal.signal(sig, old)


@contextmanager
def _terminate_as_exit() -> Iterator[None]:
    """Convert SIGTERM / SIGHUP (Ctrl+Break on Windows) to SystemExit so finally (container removal) runs."""

    def handler(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    previous = _swap_handlers(_termination_signals(), handler, skip_ignored=True)
    try:
        yield
    finally:
        _restore_handlers(previous)


@contextmanager
def _shield_signals() -> Iterator[None]:
    """Ignore interrupts during cleanup (so rm is not cut short, leaving a container with credentials behind)."""
    previous = _swap_handlers([signal.SIGINT, *_termination_signals()], signal.SIG_IGN, skip_ignored=False)
    try:
        yield
    finally:
        _restore_handlers(previous)


class ContainerRunner:
    def __init__(self, docker: DockerCli) -> None:
        self.docker = docker

    def run(self, spec: ContainerSpec, *, stdout: IO[bytes] | None = None) -> RunResult:
        name = container_name(spec.version)
        # Fail on values that cannot be represented before creating the container
        tar = to_tar(spec.option_file.render())
        with _terminate_as_exit():
            try:
                self.docker.create(
                    name,
                    spec.image,
                    spec.command,
                    tty=spec.tty,
                    stdin=spec.stdin,
                    platform=spec.platform,
                    mounts=spec.mounts,
                )
                self.docker.copy_tar(name, CONTAINER_DIR, tar)
                # Drop references to the credentials after injection (Python strings cannot be wiped,
                # so dropping the references is all we can do)
                del tar
                spec.option_file.password = ""
                exit_code, interrupted = self.docker.start(name, stdin=spec.stdin, tty=spec.tty, stdout=stdout)
                return RunResult(exit_code, interrupted)
            finally:
                with _shield_signals():
                    self.docker.remove(name)
