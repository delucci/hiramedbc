"""Initial setup: environment check → create config file → add connections → pull images."""

import typer
from rich.markup import escape

from hiramedbc.commands.add import SUGGESTED_VERSION, prompt_connection, save_connection
from hiramedbc.commands.doctor import print_report, pull_images, run_checks
from hiramedbc.config.schema import AppConfig, Defaults, validate_version_tag
from hiramedbc.context import AppContext, get_app_context
from hiramedbc.docker.image import image_ref
from hiramedbc.utils import console as out


def _ask_default_version(app_ctx: AppContext) -> str:
    while True:
        value = app_ctx.prompter.ask_text("Default MySQL version", default=SUGGESTED_VERSION)
        try:
            version = validate_version_tag(value)
        except ValueError as e:
            out.warn(str(e))
            continue
        if version:
            return version


def _register_connections(app_ctx: AppContext, config: AppConfig) -> tuple[AppConfig, list[str]]:
    registered: list[str] = []
    prompt = "Add a connection?" if not config.connections else "Add more connections?"
    if not app_ctx.prompter.confirm(prompt, default=True):
        return config, registered
    while True:
        draft = prompt_connection(app_ctx, config)
        config = save_connection(app_ctx, config, draft)
        registered.append(draft.name)
        out.info(f"Connection [bold]{draft.name}[/bold] added.")
        if not app_ctx.prompter.confirm("Add another connection?", default=False):
            return config, registered


def init(ctx: typer.Context) -> None:
    """Run the environment check, create the config file, add connections, and pull images in one go."""
    app_ctx = get_app_context(ctx)
    manager = app_ctx.manager

    out.info("[bold]1. Environment check[/bold]")
    report = run_checks(app_ctx)
    print_report(report)
    if not report.runtime_ok:
        out.warn("No Docker-compatible runtime is available. Setup will continue, but start one before connecting.")

    out.info("\n[bold]2. Config file[/bold]")
    if manager.exists():
        out.info(f"A config file already exists: {escape(str(manager.path))}")
        if not app_ctx.prompter.confirm("Keep it and continue from adding connections?", default=True):
            raise typer.Exit(0)
        config = manager.load()
    else:
        config = AppConfig(defaults=Defaults(version=_ask_default_version(app_ctx)))
        manager.save(config)
        out.info(f"Created config file: {escape(str(manager.path))}")

    out.info("\n[bold]3. Add connections[/bold]")
    config, registered = _register_connections(app_ctx, config)

    out.info("\n[bold]4. Pull images[/bold]")
    # Versions used by the connections added in this run (the connection version or defaults.version)
    versions = list(
        dict.fromkeys(v for name in registered if (v := config.connections[name].version or config.defaults.version))
    )
    if not registered:
        out.info("Skipped because no connections were added.")
    elif not report.runtime_ok:
        out.info("Skipped because the runtime is not available. You can pull them later with `hirame doctor --pull`.")
    elif versions and app_ctx.prompter.confirm(
        f"Pull the required images ({', '.join(image_ref(v) for v in versions)}) now?", default=True
    ):
        if pull_images(app_ctx, versions, on_error=lambda e: out.warn(e.message)):
            out.info("Images pulled.")
        else:
            out.warn("Some images could not be pulled. You can retry later with `hirame doctor --pull`.")

    out.info("\nSetup complete.")
    if registered:
        out.info(f"Connect with `hirame c {registered[0]}`.")
    else:
        out.info("Add a connection with `hirame add`.")
