"""The `add` command, plus the interactive connection form and save logic shared with edit / init."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from hiramedbc.commands.common import find_case_conflict
from hiramedbc.config.schema import (
    DEFAULT_PORT,
    KEYRING,
    PASSWORD_STORE_ASK,
    PASSWORD_STORE_CONFIG,
    PASSWORD_STORE_KEYCHAIN,
    SSL_MODES,
    AppConfig,
    ConnectionConfig,
    SslConfig,
    validate_version_tag,
)
from hiramedbc.context import AppContext, get_app_context
from hiramedbc.prompts.auth import Prompter
from hiramedbc.utils import console as out
from hiramedbc.utils.aliases import validate_alias_name
from hiramedbc.utils.errors import ConfigError, KeychainError

CLEAR = "-"
"""Value to enter in edit to unset an optional field."""

SUGGESTED_VERSION = "8.4"

PASSWORD_STORE_LABELS = {
    PASSWORD_STORE_ASK: "Don't save (ask every time)",
    PASSWORD_STORE_CONFIG: "Save in the config file (plaintext)",
    PASSWORD_STORE_KEYCHAIN: "Save in the OS keychain",
}

NO_SSL_MODE = "DEFAULT"
"""Choice for not setting an SSL mode (leave it to the MySQL client default)."""


@dataclass
class ConnectionDraft:
    name: str
    conn: ConnectionConfig
    new_password: str | None = field(default=None, repr=False)
    """New password to store in the keychain. None keeps the saved one unchanged.

    A password saved in the config file is not held here; it is already in `conn.password`."""


def _validate_name(config: AppConfig, name: str, current_name: str | None) -> None:
    validate_alias_name(name)
    if current_name and name != current_name and name.lower() == current_name.lower():
        # Keychains that ignore case (such as Windows Credential Manager)
        # cannot safely move the entry
        raise ConfigError(
            f"Cannot change only the case of a connection name ({current_name} → {name}).",
            hint="Use a different name, or remove the connection and add it again.",
        )
    if name != current_name and name in config.connections:
        raise ConfigError(f"Connection {name!r} already exists.", hint="Use `hirame edit` to change it.")
    conflict = find_case_conflict(config, name, exclude=current_name)
    if conflict:
        raise ConfigError(
            f"Cannot add connection {name!r}: it differs from existing {conflict!r} only in case.",
            hint="The keychain (especially Windows Credential Manager) may treat them as the same name.",
        )


def _ask_name(prompter: Prompter, config: AppConfig, current_name: str | None) -> str:
    while True:
        name = prompter.ask_text("Connection name", default=current_name)
        try:
            _validate_name(config, name, current_name)
            return name
        except ConfigError as e:
            out.warn(e.message + (f"\n{e.hint}" if e.hint else ""))


def _ask_host(prompter: Prompter, current: ConnectionConfig | None) -> str:
    while True:
        host = prompter.ask_text("Host", default=current.host if current else None)
        try:
            return ConnectionConfig(host=host).host
        except ValidationError:
            out.warn("Host names cannot contain whitespace or control characters.")


def _ask_port(prompter: Prompter, current: ConnectionConfig | None) -> int:
    while True:
        value = prompter.ask_text("Port", default=str(current.port if current else DEFAULT_PORT))
        # isascii: "²" and full-width digits pass isdigit() but fail int() or give unintended values
        if value.isascii() and value.isdigit() and 1 <= int(value) <= 65535:
            return int(value)
        out.warn("Enter an integer from 1 to 65535.")


def _ask_new_password(prompter: Prompter, name: str, *, destination: str, plaintext: bool = False) -> str | None:
    """Ask for a password twice. With `plaintext`, values that cannot be stored in the config file are rejected."""
    for _ in range(3):
        first = prompter.ask_secret(f"Password for {name} (saved in {destination})")
        second = prompter.ask_secret("Confirm password")
        if first != second:
            out.warn("Passwords do not match.")
        elif plaintext and first.strip() == "":
            out.warn("An empty password cannot be saved in the config file.")
            return None
        elif plaintext and first == KEYRING:
            out.warn(f'The password "{KEYRING}" cannot be saved in the config file (reserved word).')
            return None
        else:
            return first
    return None


def _ask_version(prompter: Prompter, config: AppConfig, current: ConnectionConfig | None) -> str | None:
    editing = current is not None
    while True:
        if config.defaults.version:
            if editing and current.version:
                suffix = f" (Enter: keep current, {CLEAR}: use default {config.defaults.version})"
            else:
                suffix = f" (empty: use default {config.defaults.version})"
            value = prompter.ask_text(
                f"MySQL version{suffix}", default=current.version if current else None, allow_empty=True
            )
            if value in ("", CLEAR):
                return None
        else:
            value = prompter.ask_text(
                "MySQL version (e.g. 8.4, 8.0, 5.7)",
                default=(current.version if current and current.version else SUGGESTED_VERSION),
            )
        try:
            return validate_version_tag(value)
        except ValueError as e:
            out.warn(str(e))


def _ask_ssl_file(prompter: Prompter, label: str, current: str | None) -> str | None:
    while True:
        value = prompter.ask_text(
            f"{label} path" + (f" (Enter: keep current, {CLEAR}: remove)" if current else " (empty: none)"),
            default=current,
            allow_empty=True,
        )
        if value in ("", CLEAR):
            return None
        path = Path(value).expanduser()
        if path.is_file():
            return str(path.resolve())
        out.warn(f"File {value} not found.")


def _ask_ssl(prompter: Prompter, current: ConnectionConfig | None) -> SslConfig | None:
    current_ssl = current.ssl if current else None
    if not prompter.confirm("Configure SSL?", default=current_ssl is not None):
        return None
    if current_ssl is None:
        default_mode = "REQUIRED"
    else:
        default_mode = current_ssl.mode or NO_SSL_MODE
    mode = prompter.choose("SSL mode", [*SSL_MODES, NO_SSL_MODE], default=default_mode)
    ssl = SslConfig(
        mode=None if mode == NO_SSL_MODE else mode,
        ca=_ask_ssl_file(prompter, "CA certificate", current_ssl.ca if current_ssl else None),
        cert=_ask_ssl_file(prompter, "Client certificate", current_ssl.cert if current_ssl else None),
        key=_ask_ssl_file(prompter, "Client private key", current_ssl.key if current_ssl else None),
    )
    return None if ssl.is_empty() else ssl


def prompt_connection(
    app_ctx: AppContext,
    config: AppConfig,
    *,
    name: str | None = None,
    current_name: str | None = None,
    current: ConnectionConfig | None = None,
) -> ConnectionDraft:
    """Ask for the connection settings interactively. In edit, current values are the defaults."""
    prompter = app_ctx.prompter
    if name is not None:
        _validate_name(config, name, current_name)
    else:
        name = _ask_name(prompter, config, current_name)

    host = _ask_host(prompter, current)
    port = _ask_port(prompter, current)

    user_fixed = prompter.confirm(
        "Save the user name in the config file? (no: ask every time)",
        default=current.user_fixed if current else True,
    )
    user = prompter.ask_text("User name", default=current.user if current else None) if user_fixed else None

    password: str | None = None
    new_password: str | None = None
    # If the user name is asked every time, the password cannot be saved (config validation also rejects it)
    if user_fixed:
        store = prompter.choose(
            "Where to save the password?",
            [PASSWORD_STORE_ASK, PASSWORD_STORE_CONFIG, PASSWORD_STORE_KEYCHAIN],
            default=current.password_store if current else PASSWORD_STORE_ASK,
            labels=PASSWORD_STORE_LABELS,
        )
        # If the user name changed, the saved password belongs to the old user, so default to changing it
        user_changed = current is not None and current.user != user
        if store == PASSWORD_STORE_KEYCHAIN:
            has_stored = current is not None and current.password_in_keychain
            if has_stored and not prompter.confirm("Change the saved password?", default=user_changed):
                password = KEYRING
            else:
                new_password = _ask_new_password(prompter, name, destination="keychain")
                if new_password is not None:
                    password = KEYRING
                elif has_stored:
                    out.warn("Keeping the saved password.")
                    password = KEYRING
                elif current is not None and current.plaintext_password is not None:
                    out.warn("Keeping the saved password in the config file.")
                    password = current.plaintext_password
                else:
                    out.warn("Password not saved. It will be asked every time.")
        elif store == PASSWORD_STORE_CONFIG:
            saved_plaintext = current.plaintext_password if current else None
            if saved_plaintext is not None and not prompter.confirm("Change the saved password?", default=user_changed):
                password = saved_plaintext
            else:
                password = _ask_new_password(prompter, name, destination="config file", plaintext=True)
                if password is None and saved_plaintext is not None:
                    out.warn("Keeping the saved password.")
                    password = saved_plaintext
                elif password is None and current is not None and current.password_in_keychain:
                    out.warn("Keeping the saved password in the keychain.")
                    password = KEYRING
                elif password is None:
                    out.warn("Password not saved. It will be asked every time.")

    version = _ask_version(prompter, config, current)
    ssl = _ask_ssl(prompter, current)
    conn = ConnectionConfig(host=host, port=port, user=user, password=password, version=version, ssl=ssl)
    return ConnectionDraft(name=name, conn=conn, new_password=new_password)


def save_connection(
    app_ctx: AppContext,
    config: AppConfig,
    draft: ConnectionDraft,
    *,
    old_name: str | None = None,
) -> AppConfig:
    """Save the connection to the config file and the keychain.

    A new password goes into the keychain before the config is saved (on failure, keep using the
    saved password if there is one, otherwise switch to asking every time).
    Renaming and deleting entries no longer needed happen after the config is saved.
    """
    keychain = app_ctx.keychain
    conn = draft.conn
    old_conn = config.connections.get(old_name) if old_name else None

    stored_new = False
    previous_password: str | None = None
    had_stored = old_conn is not None and old_conn.password_in_keychain
    if draft.new_password is not None:
        try:
            if had_stored and old_name == draft.name:
                # With the same name the new password overwrites the old entry, so keep it to restore if saving fails
                previous_password = keychain.get(old_name)
            keychain.set(draft.name, draft.new_password)
            stored_new = True
        except KeychainError as e:
            if had_stored:
                # Keep using the saved password (on rename, the old entry is moved after saving)
                out.warn(f"{e.message} Keeping the saved password.")
            elif old_conn is not None and old_conn.plaintext_password is not None:
                out.warn(f"{e.message} Keeping the saved password in the config file.")
                conn = conn.model_copy(update={"password": old_conn.plaintext_password})
            else:
                out.warn(f"{e.message} The password will be asked every time.")
                conn = conn.model_copy(update={"password": None})

    connections: dict[str, ConnectionConfig] = {}
    for existing_name, existing in config.connections.items():
        if existing_name == old_name:
            connections[draft.name] = conn  # Keep the original position when editing
        else:
            connections[existing_name] = existing
    connections.setdefault(draft.name, conn)
    new_config = config.model_copy(update={"connections": connections})

    try:
        app_ctx.manager.save(new_config)
    except ConfigError:
        if stored_new:
            if previous_password is not None:
                _try_keychain(lambda: keychain.set(draft.name, previous_password))
            else:
                _try_keychain(lambda: keychain.delete(draft.name))
        raise

    if old_name and old_conn and old_conn.password_in_keychain:
        if not conn.password_in_keychain:
            _try_keychain(lambda: keychain.delete(old_name))
        elif old_name != draft.name:
            if stored_new:
                _try_keychain(lambda: keychain.delete(old_name))
            else:
                _try_keychain(lambda: keychain.rename(old_name, draft.name))
    return new_config


def _try_keychain(operation) -> None:  # noqa: ANN001
    try:
        operation()
    except KeychainError as e:
        out.warn(e.message)


def add(
    ctx: typer.Context,
    alias: Annotated[
        str | None, typer.Argument(metavar="[ALIAS]", help="Connection name to add (prompted if omitted)")
    ] = None,
) -> None:
    app_ctx = get_app_context(ctx)
    config = app_ctx.manager.load()
    draft = prompt_connection(app_ctx, config, name=alias)
    save_connection(app_ctx, config, draft)
    out.info(f"Connection [bold]{draft.name}[/bold] added. Connect with `hirame c {draft.name}`.")
