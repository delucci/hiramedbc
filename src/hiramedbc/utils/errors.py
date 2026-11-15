"""Error types shown to users.

The CLI layer catches HirameError and prints its message and hint instead of a traceback, then exits.
"""


class HirameError(Exception):
    """Base class for exceptions that carry a user-facing message."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(HirameError):
    """Invalid config file content or connection specification."""


class ConfigNotFoundError(ConfigError):
    """The config file does not exist."""


class DockerError(HirameError):
    """An operation on the Docker-compatible runtime failed."""


class KeychainError(HirameError):
    """The OS keychain cannot be accessed."""


class UnsupportedError(HirameError):
    """An unsupported feature was requested."""


class InterruptedByUser(HirameError):
    """The user interrupted with Ctrl+C."""

    exit_code = 130
