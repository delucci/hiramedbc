"""Command alias definitions and resolution, and the reserved names that cannot be used as connection names."""

import re

from hiramedbc.utils.errors import ConfigError

COMMANDS: tuple[str, ...] = (
    "init",
    "connect",
    "add",
    "list",
    "remove",
    "edit",
    "dump",
    "config",
    "version",
    "doctor",
)

COMMAND_ALIASES: dict[str, str] = {
    "c": "connect",
    "a": "add",
    "ls": "list",
    "rm": "remove",
    "e": "edit",
    "d": "dump",
    "v": "version",
    "dr": "doctor",
}

VERSION_PATTERN = re.compile(r"^\d+(\.\d+){0,2}$")

_ALIAS_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


def resolve_command_name(name: str) -> str | None:
    """Return the full command name for a full name or alias, or None if there is no match.

    Prefix matching is not done (so it never becomes ambiguous with connection names).
    """
    if name in COMMANDS:
        return name
    return COMMAND_ALIASES.get(name)


def alias_of(command: str) -> str | None:
    for short, full in COMMAND_ALIASES.items():
        if full == command:
            return short
    return None


def is_version(token: str) -> bool:
    return VERSION_PATTERN.fullmatch(token) is not None


def reserved_names() -> frozenset[str]:
    return frozenset(COMMANDS) | frozenset(COMMAND_ALIASES)


def validate_alias_name(name: str) -> None:
    """Check that the name can be used as a connection name. Raises ConfigError if not."""
    if not _ALIAS_NAME_PATTERN.fullmatch(name):
        raise ConfigError(
            f"Invalid connection name {name!r}.",
            hint="Use up to 64 characters: letters, digits, `_`, `.`, and `-`, starting with a letter, digit, or `_`.",
        )
    if is_version(name):
        raise ConfigError(
            f"Connection name {name!r} cannot be used because it looks like a version.",
            hint="Include a letter in the name, for example `prod-84`.",
        )
    if name.lower() in reserved_names():
        raise ConfigError(
            f"Connection name {name!r} cannot be used because it clashes with a command name or alias.",
            hint="Reserved names: " + ", ".join(sorted(reserved_names())),
        )
