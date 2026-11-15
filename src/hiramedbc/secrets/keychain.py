"""Storing passwords in the OS keychain (macOS Keychain / Windows Credential Manager).

The service name is `hiramedbc` and the account name is the connection name.
keyring exceptions are wrapped in KeychainError, and password values are never included in messages.
"""

from dataclasses import dataclass
from typing import Any

import keyring
import keyring.backends.fail
import keyring.errors

from hiramedbc import APP_NAME
from hiramedbc.utils.errors import KeychainError

SERVICE_NAME = APP_NAME


@dataclass(frozen=True)
class BackendStatus:
    available: bool
    name: str


class Keychain:
    def __init__(self, backend: Any = None, service: str = SERVICE_NAME) -> None:
        self._backend = backend
        self.service = service

    @property
    def backend(self) -> Any:
        return self._backend if self._backend is not None else keyring.get_keyring()

    def status(self) -> BackendStatus:
        try:
            backend = self.backend
        except Exception:  # noqa: BLE001 - treat a backend initialization failure as "unavailable"
            return BackendStatus(False, "(initialization failed)")
        name = f"{type(backend).__module__}.{type(backend).__name__}"
        available = not isinstance(backend, keyring.backends.fail.Keyring)
        return BackendStatus(available, name)

    def get(self, alias: str) -> str | None:
        try:
            return self.backend.get_password(self.service, alias)
        except Exception as e:  # noqa: BLE001 - each backend raises different exception types
            raise KeychainError(
                f"Could not read the password for {alias!r} from the keychain ({type(e).__name__})."
            ) from None

    def set(self, alias: str, password: str) -> None:
        try:
            self.backend.set_password(self.service, alias, password)
        except Exception as e:  # noqa: BLE001
            raise KeychainError(
                f"Could not save the password for {alias!r} to the keychain ({type(e).__name__})."
            ) from None

    def delete(self, alias: str) -> bool:
        """Delete the entry. Returns False if it does not exist."""
        try:
            if self.backend.get_password(self.service, alias) is None:
                return False
            self.backend.delete_password(self.service, alias)
            return True
        except keyring.errors.PasswordDeleteError:
            return False
        except Exception as e:  # noqa: BLE001
            raise KeychainError(
                f"Could not delete the password for {alias!r} from the keychain ({type(e).__name__})."
            ) from None

    def rename(self, old: str, new: str) -> bool:
        """Move the entry to a new connection name. Returns False if there is nothing to move."""
        password = self.get(old)
        if password is None:
            return False
        self.set(new, password)
        self.delete(old)
        return True
