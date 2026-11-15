"""Show the config file location (config path) and edit it (config edit)."""

import typer
from rich.markup import escape

from hiramedbc.context import get_app_context
from hiramedbc.utils import console as out
from hiramedbc.utils.errors import ConfigNotFoundError
from hiramedbc.utils.platform import open_in_editor

config_app = typer.Typer(
    name="config",
    help="Show or edit the config file",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@config_app.command("path", help="Show the path of the config file in use")
def path(ctx: typer.Context) -> None:
    manager = get_app_context(ctx).manager
    # The path goes to stdout (so `code "$(hirame config path)"` works) and the note goes to stderr
    out.console.print(str(manager.path), markup=False, highlight=False, soft_wrap=True)
    note = ". Not created yet (run `hirame init` to create it)" if not manager.exists() else ""
    out.info(f"[dim]Resolved from the {manager.location.source_label}{note}[/dim]")


@config_app.command("edit", help="Open the config file in the default OS editor")
def edit(ctx: typer.Context) -> None:
    manager = get_app_context(ctx).manager
    if not manager.exists():
        raise ConfigNotFoundError(
            f"Config file {manager.path} does not exist yet.", hint="Run `hirame init` to create it."
        )
    open_in_editor(manager.path)
    out.info(f"Opened {escape(str(manager.path))}. After saving, run `hirame doctor` to validate it.")
