from typing import Annotated

import typer

from hiramedbc.commands.add import prompt_connection, save_connection
from hiramedbc.commands.common import get_connection
from hiramedbc.context import get_app_context
from hiramedbc.utils import console as out


def edit(
    ctx: typer.Context,
    alias: Annotated[str, typer.Argument(metavar="ALIAS", help="Connection name to edit")],
) -> None:
    """Edit a connection interactively, using the current values as defaults (Enter keeps them)."""
    app_ctx = get_app_context(ctx)
    config = app_ctx.manager.load()
    current = get_connection(config, alias)
    draft = prompt_connection(app_ctx, config, current_name=alias, current=current)
    save_connection(app_ctx, config, draft, old_name=alias)
    renamed = f" ({alias} → {draft.name})" if draft.name != alias else ""
    out.info(f"Connection [bold]{draft.name}[/bold] updated.{renamed}")
