"""HirameDBC: a CLI for switching between MySQL client versions running on Docker."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hiramedbc")
except PackageNotFoundError:  # pragma: no cover - when run directly from the source tree
    __version__ = "0.0.0+unknown"

APP_NAME = "hiramedbc"
"""Name used for the config directory, the keychain service name, and the container name prefix."""
