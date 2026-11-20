"""Replacements for system boundaries (the OS keychain and interactive prompts)."""

import keyring.errors
import pytest
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, username)]


class SetFailingKeyring(MemoryKeyring):
    """Backend that can get and delete, but fails to save."""

    def set_password(self, service, username, password):
        raise keyring.errors.PasswordSetError("denied")


class LockedKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def get_password(self, service, username):
        raise keyring.errors.KeyringLocked("locked")

    def set_password(self, service, username, password):
        raise keyring.errors.KeyringLocked("locked")

    def delete_password(self, service, username):
        raise keyring.errors.KeyringLocked("locked")


class ScriptedPrompter:
    """Returns prepared answers in order and records what was asked."""

    def __init__(self, texts=(), secrets=(), confirms=(), choices=()) -> None:
        self.texts = list(texts)
        self.secrets = list(secrets)
        self.confirms = list(confirms)
        self.choices = list(choices)
        self.asked: list[str] = []

    def ask_text(self, message, *, default=None, allow_empty=False):
        self.asked.append(message)
        value = self.texts.pop(0)
        return default if value == "" and default is not None else value

    def ask_secret(self, message):
        self.asked.append(message)
        return self.secrets.pop(0)

    def confirm(self, message, *, default):
        self.asked.append(message)
        assert self.confirms, f"Unexpected confirmation: {message}"
        return self.confirms.pop(0)

    def remaining(self) -> dict[str, list]:
        """Unused answers (to detect questions asked in an unexpected order or number)."""
        left = {"texts": self.texts, "secrets": self.secrets, "confirms": self.confirms, "choices": self.choices}
        return {k: v for k, v in left.items() if v}

    def choose(self, message, choices, *, default, labels=None):
        self.asked.append(message)
        value = self.choices.pop(0) if self.choices else default
        assert value in choices
        return value


@pytest.fixture
def memory_keyring():
    return MemoryKeyring()
