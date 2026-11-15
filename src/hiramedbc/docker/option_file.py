"""Building the MySQL option file and packing it into a tar stream for `docker cp -`.

The content is kept in memory only and never written to the host's disk.
"""

import io
import tarfile
import time
from dataclasses import dataclass, field

from hiramedbc.utils.errors import ConfigError

CONTAINER_DIR = "/tmp"
OPTION_FILENAME = "hiramedbc.cnf"
CONTAINER_PATH = f"{CONTAINER_DIR}/{OPTION_FILENAME}"
DEFAULTS_ARG = f"--defaults-extra-file={CONTAINER_PATH}"

# Use only documented escape sequences
_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b"}


def escape_value(value: str) -> str:
    """Make a value safe to write in an option file (wrapped in quotes)."""
    if "\x00" in value:
        # MySQL skips everything after NUL, which would turn it into a different value
        raise ConfigError("A value containing a NUL character cannot be written to a MySQL option file.")
    escaped = "".join(_ESCAPES.get(ch, ch) for ch in value)
    if '"' not in value:
        return f'"{escaped}"'
    if "'" not in value:
        return f"'{escaped}'"
    raise ConfigError(
        "A value containing both double quotes (\") and single quotes (') "
        "cannot be safely represented in a MySQL option file.",
        hint="Use a value that contains only one kind of quote.",
    )


@dataclass
class OptionFile:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    ssl_mode: str | None = None
    ssl_files: dict[str, str] = field(default_factory=dict)
    """SSL file kind (ca / cert / key) -> path inside the container."""

    def render(self) -> bytes:
        lines = [
            "[client]",
            f"host={escape_value(self.host)}",
            f"port={self.port}",
            f"user={escape_value(self.user)}",
            f"password={escape_value(self.password)}",
        ]
        if self.ssl_mode:
            lines.append(f"ssl-mode={self.ssl_mode}")
        for kind in ("ca", "cert", "key"):
            if kind in self.ssl_files:
                lines.append(f"ssl-{kind}={escape_value(self.ssl_files[kind])}")
        return ("\n".join(lines) + "\n").encode("utf-8")


def to_tar(content: bytes, name: str = OPTION_FILENAME) -> bytes:
    """Build a tar containing a single file, with mode 0600 and owned by root.

    The mode is 0600 because the MySQL client ignores option files that others can write to.
    """
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    info.mode = 0o600
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = int(time.time())
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()
