import typer
from rich.markup import escape
from rich.table import Table

from hiramedbc.config.schema import PASSWORD_STORE_ASK, PASSWORD_STORE_CONFIG, ConnectionConfig
from hiramedbc.context import get_app_context
from hiramedbc.secrets.keychain import Keychain
from hiramedbc.utils import console as out
from hiramedbc.utils.errors import KeychainError

ASK = "[dim](ask every time)[/dim]"


def _password_label(alias: str, conn: ConnectionConfig, keychain: Keychain) -> str:
    """Show only where the password is stored, never its value."""
    if conn.password_store == PASSWORD_STORE_ASK:
        return ASK
    if conn.password_store == PASSWORD_STORE_CONFIG:
        return "config file"
    try:
        stored = keychain.get(alias) is not None
    except KeychainError:
        return "[yellow]keychain (unknown)[/yellow]"
    return "keychain (saved)" if stored else "[yellow]keychain (not saved)[/yellow]"


def list_connections(ctx: typer.Context) -> None:
    app_ctx = get_app_context(ctx)
    config = app_ctx.manager.load()
    if not config.connections:
        out.info("No saved connections yet. Add one with `hirame add`.")
        return

    default_version = config.defaults.version
    table = Table(title=None, header_style="bold")
    for column in ("Connection", "Host", "User", "Password", "Version", "SSL"):
        table.add_column(column)
    for alias, conn in config.connections.items():
        if conn.version:
            version = conn.version
        elif default_version:
            version = f"[dim]{default_version} (default)[/dim]"
        else:
            version = "[yellow]not set[/yellow]"
        table.add_row(
            alias,
            f"{escape(conn.host)}:{conn.port}",
            escape(conn.user) if conn.user else ASK,
            _password_label(alias, conn, app_ctx.keychain),
            version,
            (conn.ssl.mode or "configured") if conn.ssl else "-",
        )
    out.console.print(table)
    out.info(f"[dim]Config file: {escape(str(app_ctx.manager.path))}[/dim]")
