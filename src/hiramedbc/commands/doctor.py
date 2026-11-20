"""Environment check (doctor) and image pre-pull (--pull)."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Annotated

import typer
from rich.markup import escape

from hiramedbc.config.schema import AppConfig
from hiramedbc.context import AppContext, get_app_context
from hiramedbc.docker.image import AMD64, ImageManager, image_ref, resolve_arch
from hiramedbc.docker.runner import container_name
from hiramedbc.utils import console as out
from hiramedbc.utils.errors import ConfigError, DockerError, HirameError, KeychainError
from hiramedbc.utils.platform import host_arch, is_windows


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


_MARKS = {
    Status.OK: "[green]✓[/green]",
    Status.WARN: "[yellow]![/yellow]",
    Status.FAIL: "[red]✗[/red]",
    Status.SKIP: "[dim]-[/dim]",
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


@dataclass
class Report:
    results: list[CheckResult]
    config: AppConfig | None
    runtime_ok: bool

    @property
    def failed(self) -> bool:
        return any(r.status is Status.FAIL for r in self.results)


def run_checks(app_ctx: AppContext, *, clean: bool = False) -> Report:
    """Run all doctor checks. Also used by init."""
    results: list[CheckResult] = []
    add = results.append

    docker = app_ctx.docker
    runtime_ok = False
    if not docker.is_installed():
        add(CheckResult("Docker CLI", Status.FAIL, "docker command not found on PATH"))
        add(CheckResult("Runtime", Status.SKIP, "Not checked because the Docker CLI is missing"))
    else:
        add(CheckResult("Docker CLI", Status.OK, "docker command found"))
        runtime = docker.runtime_status()
        if runtime.available:
            runtime_ok = True
            add(CheckResult("Runtime", Status.OK, f"Available (server {runtime.server_version})"))
        else:
            add(
                CheckResult(
                    "Runtime",
                    Status.FAIL,
                    f"Not available. Start a Docker-compatible runtime ({runtime.error})",
                )
            )

    config, config_results = _check_config(app_ctx)
    arch = resolve_arch(app_ctx.docker, app_ctx.arch) if runtime_ok else (app_ctx.arch or host_arch())
    add(_check_arch(app_ctx, arch, config, runtime_ok))
    results.extend(config_results)
    results.extend(_check_keychain(app_ctx, config))
    if runtime_ok:
        add(_check_leftovers(app_ctx, clean=clean))
    return Report(results, config, runtime_ok)


def _check_arch(app_ctx: AppContext, arch: str, config: AppConfig | None, runtime_ok: bool) -> CheckResult:
    if arch != "arm64":
        return CheckResult("Architecture", Status.OK, f"{arch} (no emulation needed)")
    if not runtime_ok:
        return CheckResult(
            "Architecture", Status.SKIP, "arm64. amd64 emulation not checked because the runtime is not available"
        )
    versions = config.used_versions() if config else []
    image = next((image_ref(v) for v in versions if app_ctx.docker.image_arch(image_ref(v)) == "amd64"), None)
    if image is None:
        return CheckResult(
            "Architecture",
            Status.OK,
            "arm64. Emulation not checked because no amd64 image has been pulled (checked when you use 5.7, etc.)",
        )
    name = container_name("doctor")
    try:
        app_ctx.docker.create(name, image, ["uname", "-m"], tty=False, stdin=False, platform=AMD64)
        code, stdout = app_ctx.docker.start_captured(name)
    except DockerError as e:
        code, stdout = 1, e.message
    finally:
        app_ctx.docker.remove(name)
    if code == 0 and stdout.strip() == "x86_64":
        return CheckResult("Architecture", Status.OK, f"arm64. amd64 emulation works (checked with {image})")
    return CheckResult(
        "Architecture",
        Status.WARN,
        "arm64. amd64 emulation does not seem to work. Check the Rosetta / QEMU settings of your runtime",
    )


def _check_config(app_ctx: AppContext) -> tuple[AppConfig | None, list[CheckResult]]:
    manager = app_ctx.manager
    location = f"{manager.path} (from the {manager.location.source_label})"
    if not manager.exists() and not manager.path.is_dir():
        return None, [CheckResult("Config file", Status.WARN, f"{location} not found. Run `hirame init` to create it")]

    results: list[CheckResult] = []
    try:
        manager.load_raw()
    except ConfigError as e:
        return None, [CheckResult("Config file", Status.FAIL, e.message)]

    config: AppConfig | None = None
    try:
        config = manager.load()
        results.append(CheckResult("Config file", Status.OK, f"{location} ({len(config.connections)} connection(s))"))
    except ConfigError as e:
        results.append(CheckResult("Config file", Status.FAIL, e.message))

    if not is_windows():
        if manager.is_readable_by_others():
            results.append(
                CheckResult(
                    "Permissions",
                    Status.WARN,
                    f"Readable by other users. Running `chmod 600 {manager.path}` is recommended",
                )
            )
        else:
            results.append(CheckResult("Permissions", Status.OK, "Only the owner can read and write"))
    return config, results


def _check_keychain(app_ctx: AppContext, config: AppConfig | None) -> list[CheckResult]:
    status = app_ctx.keychain.status()
    if not status.available:
        return [
            CheckResult(
                "Keychain",
                Status.WARN,
                f"Not available ({status.name}). You will be asked for the password every time you connect",
            )
        ]
    results = [CheckResult("Keychain", Status.OK, f"Available ({status.name})")]
    if config is None:
        return results
    for alias, conn in config.connections.items():
        if not conn.password_in_keychain:
            continue
        try:
            stored = app_ctx.keychain.get(alias) is not None
        except KeychainError as e:
            results.append(CheckResult(f"  {alias}", Status.WARN, e.message))
            continue
        if not stored:
            results.append(
                CheckResult(
                    f"  {alias}",
                    Status.WARN,
                    'Set to "keyring", but no password is saved (you can enter and save it when connecting)',
                )
            )
    return results


def _check_leftovers(app_ctx: AppContext, *, clean: bool) -> CheckResult:
    try:
        containers = app_ctx.docker.list_managed()
    except DockerError as e:
        return CheckResult("Leftover containers", Status.WARN, e.message)
    # Only stopped containers (Exited / Dead) are removed. Created ones are skipped because a connection in
    # another terminal may be between create and start. Running ones may be in use from another terminal, so
    # we only show their names and how to remove them manually. Removal uses `rm`, not `rm -f`, so running
    # containers are never removed
    stopped = [c for c in containers if c.status.lower().startswith(("exited", "dead"))]
    others = [c for c in containers if c not in stopped]
    running_note = (
        f" ({len(others)} running: {', '.join(c.name for c in others)}. "
        "If they are not in use, remove them with `docker rm -f <name>`)"
        if others
        else ""
    )
    if not stopped:
        return CheckResult("Leftover containers", Status.OK, f"No stopped containers{running_note}")
    names = ", ".join(c.name for c in stopped)
    if clean:
        removed = [c.name for c in stopped if app_ctx.docker.remove(c.name, force=False)]
        status = Status.OK if len(removed) == len(stopped) else Status.WARN
        detail = f"Removed {len(removed)} container(s)" + (f": {', '.join(removed)}" if removed else "")
        failed = len(stopped) - len(removed)
        if failed:
            detail += f" ({failed} could not be removed)"
        return CheckResult("Leftover containers", status, detail + running_note)
    return CheckResult(
        "Leftover containers",
        Status.WARN,
        f"{len(stopped)} container(s) left behind by abnormal exits: {names}. "
        f"Remove them with `hirame doctor --clean`{running_note}",
    )


def print_report(report: Report) -> None:
    for r in report.results:
        out.info(f"{_MARKS[r.status]} [bold]{escape(r.name)}[/bold]: {escape(r.detail)}")


def pull_images(
    app_ctx: AppContext, versions: list[str], *, on_error: Callable[[HirameError], None] | None = None
) -> bool:
    """Pull the images for the given versions. Return True if all succeed."""
    manager = ImageManager(app_ctx.docker, app_ctx.arch)
    ok = True
    for version in versions:
        try:
            manager.pull(version)
        except HirameError as e:
            ok = False
            if on_error is None:
                raise
            on_error(e)
    return ok


def doctor(
    ctx: typer.Context,
    versions: Annotated[
        list[str] | None,
        typer.Argument(metavar="[VERSION...]", help="Versions to pull with --pull", show_default=False),
    ] = None,
    pull: Annotated[
        bool, typer.Option("--pull", help="Pre-pull images (all versions in the config if VERSION is omitted)")
    ] = False,
    clean: Annotated[bool, typer.Option("--clean", help="Remove containers left behind by abnormal exits")] = False,
) -> None:
    """Check the Docker-compatible runtime, keychain, and config file."""
    app_ctx = get_app_context(ctx)
    if versions and not pull:
        raise ConfigError(
            "Versions can only be given together with --pull.", hint="Example: hirame doctor --pull 8.4 5.7"
        )

    report = run_checks(app_ctx, clean=clean)
    print_report(report)

    if pull:
        targets = list(dict.fromkeys(versions or (report.config.used_versions() if report.config else [])))
        if not targets:
            raise ConfigError(
                "No versions to pull.",
                hint="Specify one, as in `hirame doctor --pull 8.4`, or set version in the config file.",
            )
        if not report.runtime_ok:
            raise ConfigError("Cannot pull images because no Docker-compatible runtime is available.")
        out.info("")
        pull_images(app_ctx, targets)
        out.info(f"Pulled images: {', '.join(image_ref(v) for v in targets)}")

    if report.failed:
        raise typer.Exit(1)
