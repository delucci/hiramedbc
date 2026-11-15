from typing import Annotated

import typer
from rich.markup import escape

from hiramedbc.commands.common import get_connection
from hiramedbc.context import get_app_context
from hiramedbc.utils import console as out
from hiramedbc.utils.errors import KeychainError


def remove(
    ctx: typer.Context,
    alias: Annotated[str, typer.Argument(metavar="ALIAS", help="Connection name to remove")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Remove without confirmation")] = False,
) -> None:
    """Remove a saved connection and its password stored in the keychain."""
    app_ctx = get_app_context(ctx)
    config = app_ctx.manager.load()
    conn = get_connection(config, alias)
    if not yes and not app_ctx.prompter.confirm(
        f"Remove connection {alias} ({escape(conn.host)}:{conn.port})?", default=False
    ):
        raise typer.Exit(1)

    connections = {name: c for name, c in config.connections.items() if name != alias}
    app_ctx.manager.save(config.model_copy(update={"connections": connections}))

    # Delete any previously stored entry even if the config no longer says "keyring"
    try:
        removed_secret = app_ctx.keychain.delete(alias)
    except KeychainError as e:
        out.warn(f"{e.message} Remove it from the keychain manually (service name: {app_ctx.keychain.service}).")
        removed_secret = False
    suffix = " (keychain password also removed)" if removed_secret else ""
    out.info(f"Connection [bold]{alias}[/bold] removed.{suffix}")
