"""Config file schema.

- An "empty" `user` / `password` (missing key, null, or empty string) is normalized to None.
- `password` may only be empty or `"keyring"`. Plaintext passwords are rejected.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KEYRING = "keyring"
DEFAULT_PORT = 3306

SslMode = Literal["DISABLED", "PREFERRED", "REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"]
SSL_MODES: tuple[str, ...] = ("DISABLED", "PREFERRED", "REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY")

# Strings that are valid Docker image tags (`8.4`, `8.0.36`, `lts`, etc.)
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def validate_version_tag(value: str | None) -> str | None:
    value = _blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, int | float):
        raise ValueError(f'Version must be a string (for example "{value}")')
    if not isinstance(value, str) or not _TAG_PATTERN.fullmatch(value):
        raise ValueError(f"Version {value!r} is not a valid MySQL image tag")
    return value


class SslConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: SslMode | None = None
    ca: str | None = None
    cert: str | None = None
    key: str | None = None

    @field_validator("mode", "ca", "cert", "key", mode="before")
    @classmethod
    def _blank(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("mode", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    def is_empty(self) -> bool:
        return all(v is None for v in (self.mode, self.ca, self.cert, self.key))

    def files(self) -> dict[str, str]:
        """Configured certificate files (kind → host path)."""
        return {k: v for k, v in (("ca", self.ca), ("cert", self.cert), ("key", self.key)) if v}


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    user: str | None = None
    password: Literal["keyring"] | None = None
    version: str | None = None
    ssl: SslConfig | None = None
    # SSH tunnels (F-11) are reserved for a future release. A non-empty value is rejected as unsupported.
    ssh: Any = None

    @field_validator("host", mode="before")
    @classmethod
    def _strip_host(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if any(ch.isspace() or not ch.isprintable() for ch in value):
            raise ValueError("Host name must not contain whitespace or control characters")
        return value

    @field_validator("port", mode="before")
    @classmethod
    def _default_port(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Port must be an integer")
        return DEFAULT_PORT if _blank_to_none(value) is None else value

    @field_validator("user", mode="before")
    @classmethod
    def _blank_user(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("password", mode="before")
    @classmethod
    def _password(cls, value: Any) -> Any:
        value = _blank_to_none(value)
        if value is None or value == KEYRING:
            return value
        raise ValueError(
            "Plaintext passwords cannot be saved. "
            'Leave it empty (ask every time) or set it to "keyring" (saved in the keychain)'
        )

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> Any:
        return validate_version_tag(value)

    @field_validator("ssl", mode="after")
    @classmethod
    def _empty_ssl(cls, value: SslConfig | None) -> SslConfig | None:
        return None if value is not None and value.is_empty() else value

    @field_validator("ssh", mode="before")
    @classmethod
    def _ssh(cls, value: Any) -> Any:
        if value in (None, "", {}, []):
            return None
        raise ValueError("SSH tunnels are not supported yet (planned for a future version)")

    @model_validator(mode="after")
    def _user_required_for_keyring(self) -> "ConnectionConfig":
        if self.user is None and self.password == KEYRING:
            raise ValueError("A saved password requires a saved user name (passwords are saved per connection)")
        return self

    @property
    def user_fixed(self) -> bool:
        return self.user is not None

    @property
    def password_fixed(self) -> bool:
        return self.password == KEYRING

    def to_json_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "user": self.user or "",
            "password": self.password or "",
        }
        if self.version:
            data["version"] = self.version
        if self.ssl is not None:
            data["ssl"] = self.ssl.model_dump(exclude_none=True)
        return data


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> Any:
        return validate_version_tag(value)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: dict[str, ConnectionConfig] = Field(default_factory=dict)
    defaults: Defaults = Field(default_factory=Defaults)

    @field_validator("connections", mode="before")
    @classmethod
    def _null_connections(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator("defaults", mode="before")
    @classmethod
    def _null_defaults(cls, value: Any) -> Any:
        return {} if value is None else value

    def used_versions(self) -> list[str]:
        """All versions used in the config (connection versions and defaults.version), in order, deduplicated."""
        versions = [c.version for c in self.connections.values()]
        versions.append(self.defaults.version)
        return list(dict.fromkeys(v for v in versions if v))

    def to_json_dict(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        if self.defaults.version:
            defaults["version"] = self.defaults.version
        return {
            "connections": {name: c.to_json_dict() for name, c in self.connections.items()},
            "defaults": defaults,
        }
