"""Replacement for subprocess (the docker CLI). Records the arguments and returns prepared results."""

import subprocess
from collections.abc import Callable


class FakeProcess:
    def __init__(self, returncode: int = 0, interrupts: int = 0) -> None:
        self.returncode = returncode
        self.interrupts = interrupts
        self.killed = False

    def wait(self, timeout=None):
        if self.killed:
            return self.returncode
        if self.interrupts:
            self.interrupts -= 1
            raise KeyboardInterrupt
        return self.returncode

    def kill(self):
        self.killed = True


class FakeDocker:
    """Used in place of subprocess.run / Popen.

    responders: docker subcommand name -> function that takes (args, kwargs) and returns a CompletedProcess
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.inputs: dict[str, bytes] = {}
        self.responders: dict[str, Callable[[list[str], dict], subprocess.CompletedProcess]] = {}
        self.process = FakeProcess()
        self.popen_kwargs: dict = {}
        self.output = b""
        """Content written as the stdout of docker start (for checking dump)."""

    def respond(self, subcommand: str, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.responders[subcommand] = lambda args, kw: subprocess.CompletedProcess(args, returncode, stdout, stderr)

    def run(self, args, check=False, **kwargs):
        assert isinstance(args, list), "Run with an argument list, not through a shell"
        assert "shell" not in kwargs
        self.calls.append(args)
        sub = args[1] if args[1] != "image" else "image " + args[2]
        if "input" in kwargs and kwargs["input"] is not None:
            self.inputs[sub] = kwargs["input"]
        responder = self.responders.get(sub)
        if responder:
            return responder(args, kwargs)
        stdout = b"" if not kwargs.get("text") else ""
        return subprocess.CompletedProcess(args, 0, stdout, stdout)

    def default_result(self, args, kwargs):
        empty = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(args, 0, empty, empty)

    def popen(self, args, **kwargs):
        self.calls.append(args)
        self.popen_kwargs = kwargs
        if kwargs.get("stdout") is not None and self.output:
            kwargs["stdout"].write(self.output)
            kwargs["stdout"].flush()
        return self.process

    def subcommands(self) -> list[str]:
        return [c[1] for c in self.calls]

    def call(self, subcommand: str) -> list[str]:
        return next(c for c in self.calls if c[1] == subcommand)

    def install(self, monkeypatch) -> "FakeDocker":
        import hiramedbc.docker.runner as runner

        monkeypatch.setattr(runner.subprocess, "run", self.run)
        monkeypatch.setattr(runner.subprocess, "Popen", self.popen)
        return self
