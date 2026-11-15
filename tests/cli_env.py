"""Test environment that goes through the CLI. Only the keychain, prompts, and docker are replaced."""

import json
from pathlib import Path

from typer.testing import CliRunner

from hiramedbc.cli import app
from hiramedbc.context import AppContext
from hiramedbc.docker.runner import DockerCli
from hiramedbc.secrets.keychain import Keychain
from tests.conftest import MemoryKeyring, ScriptedPrompter
from tests.fake_docker import FakeDocker

OUTPUT_WIDTH = 10_000


class CliEnv:
    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.tmp_path = tmp_path
        self.config_path = tmp_path / "config.json"
        monkeypatch.setenv("HIRAMEDBC_CONFIG", str(self.config_path))
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)
        self.backend = MemoryKeyring()
        self.keychain = Keychain(self.backend)
        self.docker = FakeDocker().install(monkeypatch)
        self.docker.respond("image inspect", stdout="arm64")
        self.prompter = ScriptedPrompter()
        # Widen the output so tables and messages never wrap. Messages can contain long temp paths
        # (especially on Windows), and a wrap in the middle of a phrase breaks substring assertions.
        from hiramedbc.utils import console as out

        monkeypatch.setattr(out.console, "_width", OUTPUT_WIDTH)
        monkeypatch.setattr(out.err_console, "_width", OUTPUT_WIDTH)
        # CliRunner's stdin is not a terminal, so treat runs as "from a terminal" by default
        monkeypatch.setattr("hiramedbc.commands.common.stdin_is_terminal", lambda: True)

    def write_config(self, data: dict) -> None:
        self.config_path.write_text(json.dumps(data), encoding="utf-8")

    def read_config(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def invoke(self, args: list[str], prompter: ScriptedPrompter | None = None):
        if prompter is not None:
            self.prompter = prompter
        ctx = AppContext(keychain=self.keychain, prompter=self.prompter, docker=DockerCli(), arch="arm64")
        result = CliRunner().invoke(app, args, obj=ctx)
        if result.exit_code == 0:
            assert not self.prompter.remaining(), f"Unused answers:{self.prompter.remaining()}"
        return result

    def option_file(self) -> str:
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(self.docker.inputs["cp"])) as tar:
            return tar.extractfile("hiramedbc.cnf").read().decode()
