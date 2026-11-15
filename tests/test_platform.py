from pathlib import Path

import pytest

import hiramedbc.utils.platform as platform_utils
from hiramedbc.utils.errors import HirameError


def test_opens_with_editor_env_var_when_set(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "code --wait")
    monkeypatch.setattr(platform_utils, "is_windows", lambda: False)
    path = Path("/c.json")
    assert platform_utils.editor_command(path) == ["code", "--wait", str(path)]


def test_visual_takes_precedence_over_editor(monkeypatch):
    monkeypatch.setenv("VISUAL", "vim")
    monkeypatch.setenv("EDITOR", "nano")
    assert platform_utils.editor_command(Path("/c.json"))[0] == "vim"


def test_splits_quoted_path_without_quotes_on_windows(monkeypatch):
    monkeypatch.setattr(platform_utils, "is_windows", lambda: True)
    value = r'"C:\Program Files\Microsoft VS Code\Code.exe" --wait'
    assert platform_utils.split_command(value) == [r"C:\Program Files\Microsoft VS Code\Code.exe", "--wait"]


def test_does_not_split_when_whole_value_is_existing_file(tmp_path):
    editor = tmp_path / "my editor"
    editor.write_text("")
    assert platform_utils.split_command(str(editor)) == [str(editor)]


def test_macos_default_is_open_t(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(platform_utils, "is_macos", lambda: True)
    path = Path("/c.json")
    assert platform_utils.editor_command(path) == ["open", "-t", str(path)]


def test_missing_editor_raises_error_with_hint(monkeypatch):
    monkeypatch.setenv("VISUAL", "no-such-editor-xyz")

    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(platform_utils.subprocess, "run", missing)
    with pytest.raises(HirameError, match="no-such-editor-xyz") as excinfo:
        platform_utils.open_in_editor(Path("/c.json"))
    assert "VISUAL" in excinfo.value.hint
