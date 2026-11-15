import os
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from hiramedbc.commands.common import get_connection, parse_dump_args, prepare_run
from hiramedbc.commands.connect import ASK_PASSWORD_HELP, ASK_USER_HELP
from hiramedbc.context import RAW_ARGS_KEY, AppContext, get_app_context
from hiramedbc.docker.option_file import DEFAULTS_ARG
from hiramedbc.docker.runner import ContainerRunner, ContainerSpec
from hiramedbc.utils import console as out
from hiramedbc.utils.errors import ConfigError, HirameError, InterruptedByUser

STDOUT = "-"


def dump(
    ctx: typer.Context,
    args: Annotated[
        list[str],
        typer.Argument(metavar="[VERSION] ALIAS DATABASE OUTPUT [-- MYSQLDUMP_ARGS...]", show_default=False),
    ],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite the output file without confirmation")
    ] = False,
    ask_user: Annotated[bool, typer.Option("--ask-user", help=ASK_USER_HELP)] = False,
    ask_password: Annotated[bool, typer.Option("--ask-password", help=ASK_PASSWORD_HELP)] = False,
) -> None:
    """Use `-` as OUTPUT to write to stdout. Pass extra mysqldump options after `--`."""
    app_ctx = get_app_context(ctx)
    extra_count = _count_after_double_dash(ctx)
    parsed = parse_dump_args(args[: len(args) - extra_count], args[len(args) - extra_count :])
    output = None if parsed.output == STDOUT else Path(parsed.output).expanduser()

    # Check that the connection exists before asking to overwrite
    get_connection(app_ctx.manager.load(), parsed.alias)
    if output is not None:
        _check_output(app_ctx, output, force=force)

    prepared = prepare_run(
        app_ctx, parsed.alias, parsed.version, ask_user=ask_user, ask_password=ask_password, purpose="Dumping from"
    )
    spec = ContainerSpec(
        image=prepared.image,
        version=prepared.version,
        # Put extra arguments after DATABASE (so `users` in `-- --no-data users` is treated as a table name.
        # MySQL clients also accept options after positional arguments)
        command=["mysqldump", DEFAULTS_ARG, parsed.database, *parsed.extra],
        option_file=prepared.option_file,
        stdin=False,
        tty=False,
        platform=prepared.platform,
        mounts=prepared.mounts,
    )
    runner = ContainerRunner(app_ctx.docker)

    if output is None:
        sys.stdout.flush()
        result = runner.run(spec, stdout=sys.stdout.buffer)
        _check(result.exit_code, result.interrupted)
        return

    # Write to an unpredictable temp file (mkstemp: O_EXCL, 0600) and replace the output only on success.
    # A fixed name could reuse a pre-existing symlink or loosely permissioned file with the same name.
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".hiramedbc-tmp", dir=output.parent)
    except OSError as e:
        raise ConfigError(f"Cannot create a temp file in {output.parent}: {e.strerror or e}") from None
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            result = runner.run(spec, stdout=f)
        _check(result.exit_code, result.interrupted)
        try:
            os.replace(tmp, output)
        except OSError as e:
            raise ConfigError(f"Cannot save the dump to {output}: {e.strerror or e}") from None
    finally:
        tmp.unlink(missing_ok=True)
    out.info(f"Dump saved to {escape(str(output))}.")


def _count_after_double_dash(ctx: typer.Context) -> int:
    """Number of arguments after `--` (click strips `--`, so count from the original list kept by the CLI layer)."""
    raw = ctx.meta.get(RAW_ARGS_KEY) or []
    return len(raw) - raw.index("--") - 1 if "--" in raw else 0


def _check_output(app_ctx: AppContext, output: Path, *, force: bool) -> None:
    """Check that the output is writable before connecting or pulling the image."""
    if output.is_dir():
        raise ConfigError(f"Output {output} is a directory.", hint="Specify a file name.")
    if not output.parent.is_dir():
        raise ConfigError(f"Output directory {output.parent} does not exist.")
    if not os.access(output.parent, os.W_OK):
        raise ConfigError(f"Output directory {output.parent} is not writable.")
    if output.exists() and not force:
        if not app_ctx.prompter.confirm(f"{escape(str(output))} already exists. Overwrite?", default=False):
            raise typer.Exit(1)


def _check(exit_code: int, interrupted: bool) -> None:
    if interrupted:
        raise InterruptedByUser("Dump interrupted.")
    if exit_code != 0:
        raise HirameError(f"mysqldump failed (exit code {exit_code}).")
