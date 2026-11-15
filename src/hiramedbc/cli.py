"""CLI entry point.

- Command aliases (`c` -> `connect`, etc.) are resolved in one place: HirameGroup.get_command.
- If the first argument matches no command name or alias, `connect` is inserted (`hirame production`).
- The global `--config` option is accepted before or after the subcommand.
"""

from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from hiramedbc.context import RAW_ARGS_KEY, AppContext
from hiramedbc.utils import console as out
from hiramedbc.utils.aliases import alias_of, resolve_command_name
from hiramedbc.utils.errors import HirameError

CONFIG_OPTION = "--config"


def normalize_args(args: list[str]) -> list[str]:
    """Move `--config` to the front, and insert `connect` if the command name is omitted.

    Arguments after `--` are passed to the external command untouched.
    """
    if "--" in args:
        split = args.index("--")
        head, tail = args[:split], args[split:]
    else:
        head, tail = list(args), []

    hoisted: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(head):
        token = head[i]
        if token == CONFIG_OPTION and i + 1 < len(head):
            hoisted += [token, head[i + 1]]
            i += 2
            continue
        if token.startswith(CONFIG_OPTION + "="):
            hoisted.append(token)
        else:
            rest.append(token)
        i += 1

    for token in rest:
        if token.startswith("-"):
            if token in ("--help", "-h"):
                break
            continue
        if resolve_command_name(token) is None:
            # Insert at the front so connect options placed first, as in `hirame --ask-user prod`, still parse
            rest.insert(0, "connect")
        break

    return hoisted + rest + tail


class HirameGroup(TyperGroup):
    def get_command(self, ctx: Any, cmd_name: str) -> Any:
        return super().get_command(ctx, resolve_command_name(cmd_name) or cmd_name)

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        normalized = normalize_args(args)
        # click strips `--`, so keep the original order for commands (dump) that need to tell arguments after `--` apart
        ctx.meta[RAW_ARGS_KEY] = list(normalized)
        return super().parse_args(ctx, normalized)

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except HirameError as e:
            out.error(e.message, e.hint)
            raise typer.Exit(e.exit_code) from None


app = typer.Typer(
    cls=HirameGroup,
    name="hirame",
    help="HirameDBC: switch between MySQL client versions using the official MySQL Docker image",
    invoke_without_command=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode="rich",
    # Do not show local variables (which may contain passwords) in tracebacks. Typer defaults to False, but be explicit
    pretty_exceptions_show_locals=False,
)


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        str | None,
        typer.Option(
            CONFIG_OPTION,
            metavar="PATH",
            help="Path to the config file (takes precedence over the HIRAMEDBC_CONFIG environment variable)",
        ),
    ] = None,
) -> None:
    if isinstance(ctx.obj, AppContext):  # An AppContext with replaced dependencies was passed in (e.g. by tests)
        ctx.obj.config_option = config
    else:
        ctx.obj = AppContext(config_option=config)
    if ctx.invoked_subcommand is not None:
        return
    help_text = ctx.get_help()
    if help_text:
        typer.echo(help_text)
    if not ctx.obj.manager.exists():
        out.info("\nNo config file yet. Run `hirame init` to start the initial setup.")
    raise typer.Exit(0)


def command_help(name: str, text: str) -> str:
    short = alias_of(name)
    return f"{text} (alias: {short})" if short else text


def _register() -> None:
    from hiramedbc.commands import add, connect, doctor, dump, edit, init, remove, version
    from hiramedbc.commands import config as config_command
    from hiramedbc.commands import list as list_command

    commands = [
        ("init", init.init, "Initial setup (environment check → create config file → add connections → pull images)"),
        ("connect", connect.connect, "Connect to a database"),
        ("add", add.add, "Add a connection"),
        ("list", list_command.list_connections, "List saved connections"),
        ("remove", remove.remove, "Remove a connection (also deletes its password from the keychain)"),
        ("edit", edit.edit, "Edit a connection"),
        ("dump", dump.dump, "Create a dump with mysqldump"),
        ("version", version.version, "Show the app version"),
        (
            "doctor",
            doctor.doctor,
            "Check the environment (--pull to pre-pull images, --clean to remove leftover containers)",
        ),
    ]
    for name, func, text in commands:
        # Show the docstring below the help as a detailed description
        epilog = (func.__doc__ or "").strip() or None
        app.command(name, help=command_help(name, text), epilog=epilog)(func)
    app.add_typer(config_command.config_app, name="config")


_register()
