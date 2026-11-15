"""Shared state passed to subcommands, and accessors for external dependencies (keychain, prompts, docker).

Tests build an AppContext and inject it with `CliRunner.invoke(app, ..., obj=ctx)`.
"""

from dataclasses import dataclass, field
from functools import cached_property

import typer

from hiramedbc.config.manager import ConfigLocation, ConfigManager, resolve_config_location
from hiramedbc.docker.runner import DockerCli
from hiramedbc.prompts.auth import Prompter, RichPrompter
from hiramedbc.secrets.keychain import Keychain

RAW_ARGS_KEY = "hiramedbc.raw_args"
"""ctx.meta key holding the original normalized argument list (to locate the `--` that click removes)."""


@dataclass
class AppContext:
    config_option: str | None = None
    keychain: Keychain = field(default_factory=Keychain)
    prompter: Prompter = field(default_factory=RichPrompter)
    docker: DockerCli = field(default_factory=DockerCli)
    arch: str | None = None
    """Host CPU architecture. If None, it is detected from the runtime environment."""

    @cached_property
    def location(self) -> ConfigLocation:
        # Treat `--config ""` as not specified
        return resolve_config_location(self.config_option or None)

    @cached_property
    def manager(self) -> ConfigManager:
        return ConfigManager(self.location)


def get_app_context(ctx: typer.Context) -> AppContext:
    obj = ctx.find_root().obj
    if not isinstance(obj, AppContext):  # pragma: no cover - always set by the main callback
        raise RuntimeError("AppContext is not initialized")
    return obj
