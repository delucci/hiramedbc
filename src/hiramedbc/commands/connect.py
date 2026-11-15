from typing import Annotated

import typer

from hiramedbc.commands.common import is_interactive_terminal, parse_connect_args, prepare_run
from hiramedbc.context import get_app_context
from hiramedbc.docker.option_file import DEFAULTS_ARG
from hiramedbc.docker.runner import ContainerRunner, ContainerSpec

ASK_USER_HELP = "Ask for the user name instead of using the saved one (also asks for the password)"
ASK_PASSWORD_HELP = "Ask for the password instead of using the saved one"


def connect(
    ctx: typer.Context,
    args: Annotated[list[str], typer.Argument(metavar="[VERSION] ALIAS [DATABASE]", show_default=False)],
    ask_user: Annotated[bool, typer.Option("--ask-user", help=ASK_USER_HELP)] = False,
    ask_password: Annotated[bool, typer.Option("--ask-password", help=ASK_PASSWORD_HELP)] = False,
) -> None:
    app_ctx = get_app_context(ctx)
    parsed = parse_connect_args(args)
    prepared = prepare_run(
        app_ctx, parsed.alias, parsed.version, ask_user=ask_user, ask_password=ask_password, purpose="Connecting to"
    )
    command = ["mysql", DEFAULTS_ARG]
    if parsed.database:
        command.append(parsed.database)
    spec = ContainerSpec(
        image=prepared.image,
        version=prepared.version,
        command=command,
        option_file=prepared.option_file,
        stdin=True,
        # Do not allocate a TTY when stdin is not a terminal, as in `hirame c prod < script.sql`
        tty=is_interactive_terminal(),
        platform=prepared.platform,
        mounts=prepared.mounts,
    )
    result = ContainerRunner(app_ctx.docker).run(spec)
    # Without a TTY, exit with 130 on Ctrl+C like dump does (with a TTY, respect the client's exit code)
    raise typer.Exit(130 if result.interrupted and not spec.tty and result.exit_code != 0 else result.exit_code)
