import pytest
from typer.testing import CliRunner

from hiramedbc.cli import app, normalize_args


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["production"], ["connect", "production"]),
        (["8.4", "production"], ["connect", "8.4", "production"]),
        (["production", "my_db"], ["connect", "production", "my_db"]),
        (["c", "production"], ["c", "production"]),
        (["connect", "production"], ["connect", "production"]),
        (["list"], ["list"]),
        (["ls"], ["ls"]),
        ([], []),
        (["--help"], ["--help"]),
    ],
)
def test_inserts_connect_when_command_name_is_omitted(args, expected):
    assert normalize_args(args) == expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["c", "prod", "--config", "x.json"], ["--config", "x.json", "c", "prod"]),
        (["--config", "x.json", "prod"], ["--config", "x.json", "connect", "prod"]),
        (["prod", "--config=x.json"], ["--config=x.json", "connect", "prod"]),
    ],
)
def test_moves_config_option_to_front_regardless_of_position(args, expected):
    assert normalize_args(args) == expected


def test_leaves_arguments_after_double_dash_untouched():
    args = ["d", "prod", "db", "out.sql", "--", "--config", "x", "--no-data"]
    assert normalize_args(args) == args


def test_running_without_arguments_shows_help_and_init_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRAMEDBC_CONFIG", str(tmp_path / "none.json"))
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert "hirame init" in result.output


def test_runs_command_by_alias():
    result = CliRunner().invoke(app, ["v"])
    assert result.exit_code == 0
    assert "HirameDBC" in result.output


def test_inserts_connect_first_even_when_connect_option_comes_first():
    assert normalize_args(["--ask-user", "production"]) == ["connect", "--ask-user", "production"]


def test_does_not_show_init_hint_when_config_exists(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HIRAMEDBC_CONFIG", str(path))
    result = CliRunner().invoke(app, [])
    assert "hirame init" not in result.output


def test_h_option_shows_help():
    result = CliRunner().invoke(app, ["-h"])
    assert (result.exit_code, "Usage" in result.output) == (0, True)
