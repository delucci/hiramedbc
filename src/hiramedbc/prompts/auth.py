"""Input Manager: interactively asks only for credentials that are not in the config file or keychain.

Values entered at the prompt are used for that connection only. The one exception is the keychain fallback,
which offers to save the entered password to the keychain.
"""

import sys
from dataclasses import dataclass, field
from typing import Protocol

import questionary
from prompt_toolkit.output import create_output
from rich.markup import escape
from rich.prompt import Confirm, Prompt

from hiramedbc.config.schema import ConnectionConfig
from hiramedbc.secrets.keychain import Keychain
from hiramedbc.utils import console as out
from hiramedbc.utils.errors import KeychainError


@dataclass
class Credentials:
    user: str
    password: str = field(repr=False)


class Prompter(Protocol):
    def ask_text(self, message: str, *, default: str | None = None, allow_empty: bool = False) -> str: ...

    def ask_secret(self, message: str) -> str: ...

    def confirm(self, message: str, *, default: bool) -> bool: ...

    def choose(
        self, message: str, choices: list[str], *, default: str, labels: dict[str, str] | None = None
    ) -> str: ...


class RichPrompter:
    """Interactive input with Rich. Prompts are written to stderr."""

    def ask_text(self, message: str, *, default: str | None = None, allow_empty: bool = False) -> str:
        while True:
            if default is None:
                value = Prompt.ask(message, console=out.err_console, default="", show_default=False)
            else:
                value = Prompt.ask(message, console=out.err_console, default=default)
            value = value.strip()
            if value or allow_empty:
                return value
            out.warn("Please enter a value.")

    def ask_secret(self, message: str) -> str:
        # No echo (getpass). Prompt.ask is not used because it strips input (leading/trailing spaces are part
        # of the password). An empty password is valid in MySQL, so it is allowed.
        return out.err_console.input(f"{message}: ", password=True)

    def confirm(self, message: str, *, default: bool) -> bool:
        return Confirm.ask(message, console=out.err_console, default=default)

    def choose(self, message: str, choices: list[str], *, default: str, labels: dict[str, str] | None = None) -> str:
        """Pick one choice. On a terminal, show a radio list (↑/↓ and Enter); otherwise ask for the value as text.

        `labels` maps a choice to the text shown in the radio list (the returned value is still the choice).
        """
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            return Prompt.ask(message, console=out.err_console, choices=choices, default=default)
        labels = labels or {}
        answer = questionary.select(
            message,
            choices=[questionary.Choice(title=labels.get(c, c), value=c) for c in choices],
            default=default,
            use_indicator=True,
            output=create_output(stdout=sys.stderr),
        ).ask()
        if answer is None:  # Ctrl-C
            raise KeyboardInterrupt
        return answer


def resolve_credentials(
    alias: str,
    conn: ConnectionConfig,
    *,
    keychain: Keychain,
    prompter: Prompter,
    ask_user: bool = False,
    ask_password: bool = False,
) -> Credentials:
    """Decide the credentials to connect with.

    - User name: use the configured one, or ask if empty.
    - Password: read from the keychain if `"keyring"`, use the config value if plaintext, or ask if empty.
    - `--ask-user` also asks for the password (the saved password belongs to the saved user).
    """
    if ask_user:
        ask_password = True

    if conn.user and not ask_user:
        user = conn.user
    else:
        user = prompter.ask_text(f"User name for {alias}")

    if conn.password_in_keychain and not ask_password:
        password = _password_from_keychain(alias, user, keychain=keychain, prompter=prompter)
    elif conn.plaintext_password is not None and not ask_password:
        password = conn.plaintext_password
    else:
        password = prompter.ask_secret(f"Password for {escape(user)}@{alias}")

    return Credentials(user=user, password=password)


def _password_from_keychain(alias: str, user: str, *, keychain: Keychain, prompter: Prompter) -> str:
    try:
        password = keychain.get(alias)
    except KeychainError as e:
        out.warn(f"{e.message} Enter it for this session only.")
        return prompter.ask_secret(f"Password for {escape(user)}@{alias}")

    if password is not None:
        return password

    out.warn(f"No password for {alias!r} is saved in the keychain.")
    password = prompter.ask_secret(f"Password for {escape(user)}@{alias}")
    if prompter.confirm("Save this password to the keychain?", default=True):
        try:
            keychain.set(alias, password)
            out.info("Saved to the keychain.")
        except KeychainError as e:
            out.warn(f"{e.message} It will be used for this connection only.")
    return password
