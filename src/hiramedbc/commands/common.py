"""Logic shared by the connect / dump commands and the connection management commands."""

import difflib
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.markup import escape

from hiramedbc.config.schema import AppConfig, ConnectionConfig, SslConfig
from hiramedbc.context import AppContext
from hiramedbc.docker.image import ImageManager, image_ref
from hiramedbc.docker.option_file import OptionFile
from hiramedbc.docker.runner import Mount
from hiramedbc.prompts.auth import resolve_credentials
from hiramedbc.utils import console as out
from hiramedbc.utils.aliases import is_version
from hiramedbc.utils.errors import ConfigError

SSL_CONTAINER_DIR = "/hiramedbc/ssl"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

VERSION_SOURCE_LABELS = {
    "argument": "argument",
    "connection": "connection",
    "default": "default",
}


def get_connection(config: AppConfig, alias: str) -> ConnectionConfig:
    conn = config.connections.get(alias)
    if conn is not None:
        return conn
    candidates = difflib.get_close_matches(alias, list(config.connections), n=3)
    hint = f"Did you mean: {', '.join(candidates)}" if candidates else "Run `hirame list` to see saved connections."
    raise ConfigError(f"Connection {alias!r} not found.", hint=hint)


def find_case_conflict(config: AppConfig, name: str, *, exclude: str | None = None) -> str | None:
    """Existing connection name that differs only in case (rejected because it may collide in the keychain)."""
    for existing in config.connections:
        if existing != exclude and existing.lower() == name.lower():
            return existing
    return None


def resolve_version(argument: str | None, conn: ConnectionConfig, config: AppConfig) -> tuple[str, str]:
    """Return the version to use and its source (argument / connection / default)."""
    if argument:
        return argument, VERSION_SOURCE_LABELS["argument"]
    if conn.version:
        return conn.version, VERSION_SOURCE_LABELS["connection"]
    if config.defaults.version:
        return config.defaults.version, VERSION_SOURCE_LABELS["default"]
    raise ConfigError(
        "No MySQL version specified.",
        hint="Pass it as an argument, as in `hirame connect 8.4 <ALIAS>`, "
        "or set the connection's version or defaults.version in the config file "
        "(`hirame edit` / `hirame config edit`).",
    )


@dataclass(frozen=True)
class ConnectArgs:
    version: str | None
    alias: str
    database: str | None


@dataclass(frozen=True)
class DumpArgs:
    version: str | None
    alias: str
    database: str
    output: str
    extra: list[str] = field(default_factory=list)


def _split_version(args: list[str]) -> tuple[str | None, list[str]]:
    if args and is_version(args[0]):
        return args[0], args[1:]
    return None, list(args)


def parse_connect_args(args: list[str]) -> ConnectArgs:
    """Parse `[VERSION] <ALIAS> [DATABASE]`."""
    version, rest = _split_version(args)
    if not rest or len(rest) > 2:
        raise ConfigError(
            "Invalid arguments.",
            hint="hirame connect [VERSION] <ALIAS> [DATABASE] (e.g. hirame c 8.4 production my_db)",
        )
    return ConnectArgs(version, rest[0], rest[1] if len(rest) == 2 else None)


DUMP_USAGE = "hirame dump [VERSION] <ALIAS> <DATABASE> <OUTPUT> [-- extra mysqldump options]"


def parse_dump_args(args: list[str], extra: list[str]) -> DumpArgs:
    """Parse `[VERSION] <ALIAS> <DATABASE> <OUTPUT>`. extra holds the arguments after `--`.

    Extra arguments without `--` are rejected (so that `hirame d prod db1 out.sql db2` does not
    silently run as a dump of table db1 in database db2).
    """
    version, rest = _split_version(args)
    if len(rest) < 3:
        raise ConfigError("Missing arguments.", hint=f"{DUMP_USAGE} (e.g. hirame d production my_db backup.sql)")
    if len(rest) > 3:
        raise ConfigError(
            f"Unexpected arguments: {' '.join(rest[3:])}",
            hint=f"Put mysqldump options and table names after `--`. {DUMP_USAGE}",
        )
    return DumpArgs(version, rest[0], rest[1], rest[2], list(extra))


def build_ssl(ssl: SslConfig | None) -> tuple[list[Mount], dict[str, str]]:
    """Build the SSL certificate mounts and the in-container paths written to the option file."""
    if ssl is None:
        return [], {}
    mounts: list[Mount] = []
    container_paths: dict[str, str] = {}
    for kind, host_path in ssl.files().items():
        path = Path(host_path).expanduser()
        if not path.is_file():
            raise ConfigError(
                f"SSL {kind} file {host_path} not found.",
                hint="Fix the path with `hirame edit` or `hirame config edit`.",
            )
        target = f"{SSL_CONTAINER_DIR}/{kind}.pem"
        mounts.append(Mount(str(path.resolve()), target))
        container_paths[kind] = target
    return mounts, container_paths


def warn_if_local_host(host: str) -> None:
    if host.lower() in LOCAL_HOSTS:
        out.warn(
            f"The connection host is {host}. Inside the container, {host} points to the container itself, "
            "so it cannot reach MySQL on the host machine. To connect to MySQL on the host machine, "
            "use `host.docker.internal`."
        )


@dataclass
class PreparedRun:
    alias: str
    conn: ConnectionConfig
    version: str
    image: str
    platform: str | None
    option_file: OptionFile = field(repr=False)
    mounts: list[Mount]


def prepare_run(
    app_ctx: AppContext,
    alias: str,
    version_argument: str | None,
    *,
    ask_user: bool,
    ask_password: bool,
    purpose: str,
) -> PreparedRun:
    """Load the config, resolve the version, prepare the image, and get the credentials."""
    config = app_ctx.manager.load()
    conn = get_connection(config, alias)
    version, source = resolve_version(version_argument, conn, config)
    if (ask_user or not conn.user) and not stdin_is_terminal():
        # Avoid reading a script on stdin as the user name (getpass reads the password from the terminal)
        raise ConfigError(
            "Cannot prompt for the user name because stdin is redirected.",
            hint="Save the user name with `hirame edit`, or run without redirecting stdin.",
        )
    mounts, ssl_files = build_ssl(conn.ssl)
    warn_if_local_host(conn.host)

    out.info(f"{purpose} [bold]{alias}[/bold] ({escape(conn.host)}:{conn.port}) with MySQL {version} ({source}).")
    platform = ImageManager(app_ctx.docker, app_ctx.arch).ensure(version)

    creds = resolve_credentials(
        alias,
        conn,
        keychain=app_ctx.keychain,
        prompter=app_ctx.prompter,
        ask_user=ask_user,
        ask_password=ask_password,
    )
    option_file = OptionFile(
        host=conn.host,
        port=conn.port,
        user=creds.user,
        password=creds.password,
        ssl_mode=conn.ssl.mode if conn.ssl else None,
        ssl_files=ssl_files,
    )
    return PreparedRun(alias, conn, version, image_ref(version), platform, option_file, mounts)


def stdin_is_terminal() -> bool:
    try:
        return os.isatty(0)
    except OSError:
        return False


def is_interactive_terminal() -> bool:
    """Whether both stdin and stdout are terminals (so a TTY can be allocated to the container)."""
    try:
        return os.isatty(0) and os.isatty(1)
    except OSError:
        return False
