from hiramedbc import __version__
from hiramedbc.utils.console import console


def version() -> None:
    console.print(f"HirameDBC {__version__}")
