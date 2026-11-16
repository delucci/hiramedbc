"""Resolve the config file location, and read and write it.

Precedence: `--config` > `HIRAMEDBC_CONFIG` environment variable > `~/.hiramedbc/config.json`.
Project-local config files are not searched for automatically.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hiramedbc import APP_NAME
from hiramedbc.config.schema import AppConfig
from hiramedbc.config.validator import parse_config
from hiramedbc.utils.errors import ConfigError, ConfigNotFoundError
from hiramedbc.utils.platform import is_windows

ENV_VAR = "HIRAMEDBC_CONFIG"
CONFIG_FILENAME = "config.json"

ConfigSource = Literal["option", "env", "default"]

SOURCE_LABELS: dict[ConfigSource, str] = {
    "option": "--config option",
    "env": f"{ENV_VAR} environment variable",
    "default": "default config location",
}


def default_config_path() -> Path:
    # Same dotdir on every OS so the path is predictable and easy to document
    return Path.home() / f".{APP_NAME}" / CONFIG_FILENAME


@dataclass(frozen=True)
class ConfigLocation:
    path: Path
    source: ConfigSource

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS[self.source]


def resolve_config_location(
    cli_path: str | os.PathLike[str] | None,
    env: Mapping[str, str] | None = None,
) -> ConfigLocation:
    env = os.environ if env is None else env
    if cli_path:
        return ConfigLocation(Path(cli_path).expanduser(), "option")
    env_path = env.get(ENV_VAR)
    if env_path:
        return ConfigLocation(Path(env_path).expanduser(), "env")
    return ConfigLocation(default_config_path(), "default")


class ConfigManager:
    def __init__(self, location: ConfigLocation) -> None:
        self.location = location

    @property
    def path(self) -> Path:
        return self.location.path

    def exists(self) -> bool:
        return self.path.is_file()

    def load_raw(self) -> Any:
        if self.path.is_dir():
            raise ConfigError(
                f"Config file path {self.path} is a directory (from the {self.location.source_label}).",
                hint="Specify the path to a file, such as config.json.",
            )
        if not self.exists():
            if self.location.source == "default":
                raise ConfigNotFoundError(
                    "No config file yet.",
                    hint="Run `hirame init` to do the initial setup.",
                )
            raise ConfigNotFoundError(
                f"Config file {self.path} not found (from the {self.location.source_label}).",
                hint="Check the path, or run `hirame init` to create it.",
            )
        try:
            # utf-8-sig: accept the BOM added by Windows Notepad and similar editors
            text = self.path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raise ConfigError(
                f"Cannot read config file {self.path} as UTF-8.",
                hint="Save it again with UTF-8 encoding.",
            ) from None
        except OSError as e:
            raise ConfigError(f"Cannot read config file {self.path}: {e.strerror or e}") from None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ConfigError(
                f"Config file {self.path} is not valid JSON (line {e.lineno}, column {e.colno}: {e.msg}).",
                hint="Fix it with `hirame config edit`.",
            ) from None

    def load(self) -> AppConfig:
        return parse_config(self.load_raw(), source=str(self.path))

    def save(self, config: AppConfig) -> None:
        """Save the config. Write to a temp file and replace the target; on POSIX, set mode 0600.

        For a symlink, the link target is replaced (the link itself is not turned into a regular file).
        """
        target = self.path.resolve()
        text = json.dumps(config.to_json_dict(), ensure_ascii=False, indent=2) + "\n"
        try:
            # 0700: a newly created ~/.hiramedbc should not be traversable by other users (ignored on Windows)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._write_atomic(target, text)
        except OSError as e:
            raise ConfigError(f"Cannot save config file {self.path}: {e.strerror or e}") from None

    def _write_atomic(self, target: Path, text: str) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            if not is_windows():
                os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def is_readable_by_others(self) -> bool:
        """Whether other users can read the file (checked on POSIX only; always False on Windows)."""
        if is_windows() or not self.exists():
            return False
        return bool(self.path.stat().st_mode & 0o044)
