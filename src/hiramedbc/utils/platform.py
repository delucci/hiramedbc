"""Smooths over differences between operating systems and CPU architectures."""

import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path

from hiramedbc.utils.errors import HirameError


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def normalize_arch(machine: str) -> str:
    """Normalize a CPU architecture name to Docker's notation (arm64 / amd64)."""
    machine = machine.strip().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "amd64"
    return machine


def host_arch() -> str:
    """CPU architecture as seen by the Python process.

    Python running under Rosetta reports amd64, so prefer the Docker daemon's architecture when available.
    """
    return normalize_arch(platform.machine())


def split_command(value: str) -> list[str]:
    """Split a command taken from an environment variable.

    On Windows, values like `"C:\\Program Files\\...\\Code.exe" --wait` are expected: split while keeping
    backslashes, then strip the outer quotes from each token.
    If the whole value points to an existing file, do not split it (an unquoted path containing spaces).
    """
    if Path(value).is_file():
        return [value]
    if not is_windows():
        return shlex.split(value)
    tokens = shlex.split(value, posix=False)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]


def editor_command(path: Path) -> list[str] | None:
    """Command to open the config file, or None when os.startfile is used on Windows.

    `VISUAL` / `EDITOR` take precedence if set; otherwise the OS default editor is used.
    """
    for var in ("VISUAL", "EDITOR"):
        value = os.environ.get(var)
        if value and value.strip():
            return [*split_command(value.strip()), str(path)]
    if is_macos():
        return ["open", "-t", str(path)]
    if is_windows():
        return None
    return ["xdg-open", str(path)]


def open_in_editor(path: Path) -> None:
    command = editor_command(path)
    try:
        if command is None:
            try:
                os.startfile(str(path), "edit")  # type: ignore[attr-defined]
            except OSError:
                subprocess.run(["notepad", str(path)], check=False)
            return
        subprocess.run(command, check=False)
    except OSError as e:
        raise HirameError(
            f"Could not start the editor: {command[0] if command else 'notepad'} ({e.strerror or e})",
            hint=f"Check the VISUAL / EDITOR environment variables, or open {path} directly.",
        ) from None
