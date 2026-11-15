"""Terminal output.

A command's main output (lists, paths, etc.) goes to stdout; progress, hints, and warnings go to stderr.
This keeps hints out of the dump when `hirame dump ... -` streams it to stdout.

The text of warn / error is not interpreted as Rich markup (so that `[...]` in user names, paths, or docker
output does not garble the display or crash with MarkupError). When embedding external values in info,
the caller must apply rich.markup.escape.
"""

from rich.console import Console

console = Console()
err_console = Console(stderr=True)


def info(message: str) -> None:
    err_console.print(message)


def warn(message: str) -> None:
    err_console.print("[yellow]Warning:[/yellow] ", end="")
    err_console.print(message, markup=False, highlight=False)


def error(message: str, hint: str | None = None) -> None:
    err_console.print("[red]Error:[/red] ", end="")
    err_console.print(message, markup=False, highlight=False)
    if hint:
        err_console.print(f"→ {hint}", style="dim", markup=False, highlight=False)
