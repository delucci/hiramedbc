import pytest

from tests.cli_env import CliEnv
from tests.conftest import LockedKeyring, ScriptedPrompter


@pytest.fixture
def env(tmp_path, monkeypatch):
    env = CliEnv(tmp_path, monkeypatch)
    env.write_config({"connections": {}, "defaults": {"version": "8.4"}})
    return env


def _add_answers(name="prod", user_fixed=True, store="keychain", password="pw", version="", ssl=False):
    texts = [name, "db.example.com", "3306"]
    confirms = [user_fixed]
    secrets = []
    choices = []
    if user_fixed:
        texts.append("app")
        choices.append(store)
        if store != "ask":
            secrets = [password, password]
    texts.append(version)
    confirms.append(ssl)
    return ScriptedPrompter(texts=texts, secrets=secrets, confirms=confirms, choices=choices)


# --- add ---


def test_add_with_saved_user_and_password_stores_password_in_keychain(env):
    result = env.invoke(["add"], _add_answers())
    assert result.exit_code == 0, result.output
    assert env.read_config()["connections"]["prod"] == {
        "host": "db.example.com",
        "port": 3306,
        "user": "app",
        "password": "keyring",
    }
    assert env.keychain.get("prod") == "pw"


def test_add_does_not_write_password_value_to_config_file(env):
    env.invoke(["a"], _add_answers(password="TOPSECRET"))
    assert "TOPSECRET" not in env.config_path.read_text(encoding="utf-8")


def test_add_with_user_asked_every_time_also_asks_password_every_time(env):
    prompter = _add_answers(user_fixed=False)
    env.invoke(["add"], prompter)
    conn = env.read_config()["connections"]["prod"]
    assert (conn["user"], conn["password"]) == ("", "")
    assert not any("Where to save the password?" in q for q in prompter.asked)


def test_add_saves_entered_version_to_connection(env):
    env.invoke(["add"], _add_answers(version="5.7"))
    assert env.read_config()["connections"]["prod"]["version"] == "5.7"


def test_add_asks_version_with_suggestion_when_no_default(env):
    env.write_config({"connections": {}})
    env.invoke(["add"], _add_answers(version=""))
    assert env.read_config()["connections"]["prod"]["version"] == "8.4"


def test_add_asks_again_when_password_confirmation_does_not_match(env):
    prompter = ScriptedPrompter(
        texts=["prod", "h", "3306", "app", ""],
        secrets=["a", "b", "pw", "pw"],
        confirms=[True, False],
        choices=["keychain"],
    )
    env.invoke(["add"], prompter)
    assert env.keychain.get("prod") == "pw"


def test_add_falls_back_to_ask_every_time_when_keychain_save_fails(env):
    from hiramedbc.secrets.keychain import Keychain

    env.keychain = Keychain(LockedKeyring())
    result = env.invoke(["add"], _add_answers())
    assert result.exit_code == 0, result.output
    assert env.read_config()["connections"]["prod"]["password"] == ""


def test_add_rejects_reserved_connection_name_given_as_argument(env):
    result = env.invoke(["add", "list"])
    assert result.exit_code == 1
    assert "clashes with a command name" in result.output


def test_add_asks_again_when_invalid_connection_name_is_entered(env):
    prompter = _add_answers()
    prompter.texts.insert(0, "8.4")
    env.invoke(["add"], prompter)
    assert list(env.read_config()["connections"]) == ["prod"]


def test_add_rejects_connection_name_differing_only_in_case(env):
    env.write_config({"connections": {"prod": {"host": "h"}}, "defaults": {"version": "8.4"}})
    result = env.invoke(["add", "PROD"])
    assert result.exit_code == 1
    assert "only in case" in result.output


def test_add_saves_ssl_settings(env, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    prompter = ScriptedPrompter(
        texts=["prod", "h", "3306", "", str(ca), "", ""],
        confirms=[False, True],
        choices=["VERIFY_IDENTITY"],
    )
    env.invoke(["add"], prompter)
    assert env.read_config()["connections"]["prod"]["ssl"] == {"mode": "VERIFY_IDENTITY", "ca": str(ca.resolve())}


# --- edit ---


@pytest.fixture
def registered(env):
    env.write_config(
        {
            "connections": {
                "first": {"host": "f"},
                "prod": {"host": "db", "port": 3306, "user": "app", "password": "keyring", "version": "8.0"},
                "last": {"host": "l"},
            },
            "defaults": {"version": "8.4"},
        }
    )
    env.keychain.set("prod", "stored")
    return env


def _edit_answers(name="prod", keep_password=True, version="", user_fixed=True, save_password=True, store=None):
    store = store or ("keychain" if save_password else "ask")
    texts = [name, "", ""]  # empty = keep the current value
    confirms = [user_fixed]
    secrets = []
    choices = []
    if user_fixed:
        texts.append("")
        choices.append(store)
        if store == "keychain":  # the connection under edit has a saved keychain password
            confirms.append(not keep_password)
            if not keep_password:
                secrets = ["new", "new"]
        elif store == "config":
            secrets = ["new", "new"]
    texts.append(version)
    confirms.append(False)
    return ScriptedPrompter(texts=texts, secrets=secrets, confirms=confirms, choices=choices)


def test_edit_with_only_enter_keeps_everything_unchanged(registered):
    before = registered.read_config()["connections"]["prod"]
    result = registered.invoke(["edit", "prod"], _edit_answers())
    assert result.exit_code == 0, result.output
    assert registered.read_config()["connections"]["prod"] == before
    assert registered.keychain.get("prod") == "stored"


def test_edit_rename_moves_keychain_entry_and_keeps_order(registered):
    registered.invoke(["e", "prod"], _edit_answers(name="production"))
    assert list(registered.read_config()["connections"]) == ["first", "production", "last"]
    assert (registered.keychain.get("prod"), registered.keychain.get("production")) == (None, "stored")


def test_edit_rename_with_password_change_deletes_old_entry(registered):
    registered.invoke(["e", "prod"], _edit_answers(name="production", keep_password=False))
    assert (registered.keychain.get("prod"), registered.keychain.get("production")) == (None, "new")


def test_edit_switching_password_to_ask_every_time_deletes_it_from_keychain(registered):
    registered.invoke(["e", "prod"], _edit_answers(save_password=False))
    assert registered.read_config()["connections"]["prod"]["password"] == ""
    assert registered.keychain.get("prod") is None


def test_edit_switching_user_to_ask_every_time_also_switches_password(registered):
    registered.invoke(["e", "prod"], _edit_answers(user_fixed=False))
    conn = registered.read_config()["connections"]["prod"]
    assert (conn["user"], conn["password"]) == ("", "")
    assert registered.keychain.get("prod") is None


def test_edit_hyphen_unsets_version(registered):
    registered.invoke(["e", "prod"], _edit_answers(version="-"))
    assert "version" not in registered.read_config()["connections"]["prod"]


def test_edit_cannot_rename_to_another_existing_connection_name(registered):
    prompter = _edit_answers(name="first")
    prompter.texts.insert(1, "prod2")  # the first name is rejected; continue with the second one
    registered.invoke(["e", "prod"], prompter)
    assert "prod2" in registered.read_config()["connections"]


def test_edit_rejects_unknown_connection(registered):
    result = registered.invoke(["e", "none"])
    assert result.exit_code == 1
    assert "Connection 'none' not found." in result.output


# --- remove ---


def test_remove_deletes_connection_and_keychain_entry_after_confirmation(registered):
    result = registered.invoke(["rm", "prod"], ScriptedPrompter(confirms=[True]))
    assert result.exit_code == 0, result.output
    assert "prod" not in registered.read_config()["connections"]
    assert registered.keychain.get("prod") is None


def test_remove_changes_nothing_when_declined(registered):
    result = registered.invoke(["rm", "prod"], ScriptedPrompter(confirms=[False]))
    assert result.exit_code == 1
    assert "prod" in registered.read_config()["connections"]
    assert registered.keychain.get("prod") == "stored"


def test_remove_with_yes_deletes_without_confirmation(registered):
    prompter = ScriptedPrompter()
    registered.invoke(["remove", "prod", "--yes"], prompter)
    assert ("prod" not in registered.read_config()["connections"], prompter.asked) == (True, [])


def test_remove_deletes_from_config_and_suggests_manual_removal_when_keychain_delete_fails(registered):
    from hiramedbc.secrets.keychain import Keychain

    registered.keychain = Keychain(LockedKeyring())
    result = registered.invoke(["rm", "prod", "-y"])
    assert result.exit_code == 0
    assert "Remove it from the keychain manually" in result.output


# --- list ---


def test_list_shows_whether_password_is_saved_without_its_value(registered):
    result = registered.invoke(["ls"])
    assert result.exit_code == 0, result.output
    assert "stored" not in result.output
    assert "keychain (saved)" in result.output


def test_list_shows_password_not_saved_in_keychain(registered):
    registered.keychain.delete("prod")
    assert "keychain (not saved)" in registered.invoke(["list"]).output


def test_list_shows_ask_every_time_and_default_version(registered):
    output = registered.invoke(["list"]).output
    assert "(ask every time)" in output
    assert "8.4 (default)" in output


def test_list_suggests_add_when_no_connections(env):
    result = env.invoke(["list"])
    assert result.exit_code == 0
    assert "hirame add" in result.output


def test_list_exits_with_code_1_and_shows_line_number_when_config_is_broken(env):
    env.config_path.write_text("{", encoding="utf-8")
    result = env.invoke(["list"])
    assert result.exit_code == 1
    assert "line 1" in result.output


# --- M3 review fixes ---


def test_edit_restores_original_password_when_save_fails_after_password_change(registered, monkeypatch):
    from hiramedbc.config.manager import ConfigManager
    from hiramedbc.utils.errors import ConfigError

    def fail(self, config):
        raise ConfigError("Cannot save")

    monkeypatch.setattr(ConfigManager, "save", fail)
    result = registered.invoke(["e", "prod"], _edit_answers(keep_password=False))
    assert result.exit_code == 1
    assert registered.keychain.get("prod") == "stored"


def test_add_deletes_keychain_entry_when_save_fails(env, monkeypatch):
    from hiramedbc.config.manager import ConfigManager
    from hiramedbc.utils.errors import ConfigError

    def fail(self, config):
        raise ConfigError("Cannot save")

    monkeypatch.setattr(ConfigManager, "save", fail)
    env.invoke(["add"], _add_answers())
    assert env.keychain.get("prod") is None


def test_edit_keeps_saved_password_when_confirmation_mismatches_three_times(registered):
    prompter = ScriptedPrompter(
        texts=["prod", "", "", "", ""],
        secrets=["a", "b", "c", "d", "e", "f"],
        confirms=[True, True, False],  # save user / change saved password / no SSL
        choices=["keychain"],
    )
    registered.invoke(["e", "prod"], prompter)
    assert registered.read_config()["connections"]["prod"]["password"] == "keyring"
    assert registered.keychain.get("prod") == "stored"


def test_edit_does_not_add_ssl_mode_when_left_unset(registered, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    config = registered.read_config()
    config["connections"]["prod"]["ssl"] = {"ca": str(ca)}
    registered.write_config(config)
    prompter = ScriptedPrompter(
        texts=["prod", "", "", "", "", "", "", ""],
        confirms=[True, False, True],  # save user / keep saved password / use SSL
        choices=["keychain", "DEFAULT"],
    )
    registered.invoke(["e", "prod"], prompter)
    assert registered.read_config()["connections"]["prod"]["ssl"] == {"ca": str(ca)}


def test_add_asks_again_for_port_with_full_width_or_superscript_digits(env):
    prompter = _add_answers()
    prompter.texts[2:3] = ["²", "３３０６", "3306"]
    result = env.invoke(["add"], prompter)
    assert result.exit_code == 0, result.output
    assert env.read_config()["connections"]["prod"]["port"] == 3306


def test_remove_ask_every_time_connection_also_deletes_leftover_keychain_entry(registered):
    registered.keychain.set("first", "leftover")
    registered.invoke(["rm", "first", "-y"])
    assert registered.keychain.get("first") is None


def test_list_shows_unknown_when_keychain_cannot_be_checked(registered):
    from hiramedbc.secrets.keychain import Keychain

    registered.keychain = Keychain(LockedKeyring())
    assert "keychain (unknown)" in registered.invoke(["list"]).output


def test_list_shows_square_brackets_in_user_name_as_is(env):
    env.write_config({"connections": {"p": {"host": "h", "user": "[red]x[/x]"}}})
    result = env.invoke(["list"])
    assert result.exit_code == 0, result.output
    assert "[red]x[/x]" in result.output


# --- final review fixes ---


def test_edit_keeps_saved_password_when_only_saving_new_password_fails(registered):
    from hiramedbc.secrets.keychain import Keychain
    from tests.conftest import SetFailingKeyring

    backend = SetFailingKeyring()
    backend.store[("hiramedbc", "prod")] = "stored"
    registered.keychain = Keychain(backend)
    registered.invoke(["e", "prod"], _edit_answers(keep_password=False))
    assert registered.read_config()["connections"]["prod"]["password"] == "keyring"
    assert backend.store[("hiramedbc", "prod")] == "stored"


def test_edit_rejects_case_only_rename_and_asks_again(registered):
    prompter = _edit_answers(name="Prod")
    prompter.texts.insert(1, "prod")  # after the rejection, keep the original name
    result = registered.invoke(["e", "prod"], prompter)
    assert "Cannot change only the case of a connection name" in result.output
    assert list(registered.read_config()["connections"]) == ["first", "prod", "last"]
    assert registered.keychain.get("prod") == "stored"


# --- password saved in the config file ---


def test_add_with_config_store_saves_plaintext_password_in_config_file_only(env):
    result = env.invoke(["add"], _add_answers(store="config", password="pw"))
    assert result.exit_code == 0, result.output
    assert env.read_config()["connections"]["prod"]["password"] == "pw"
    assert env.backend.store == {}


def test_add_with_config_store_asks_again_when_confirmation_does_not_match(env):
    prompter = _add_answers(store="config", password="pw")
    prompter.secrets[:0] = ["a", "b"]
    env.invoke(["add"], prompter)
    assert env.read_config()["connections"]["prod"]["password"] == "pw"


def test_add_with_config_store_does_not_save_reserved_word_keyring_as_password(env):
    result = env.invoke(["add"], _add_answers(store="config", password="keyring"))
    assert result.exit_code == 0, result.output
    assert env.read_config()["connections"]["prod"]["password"] == ""
    assert "reserved word" in result.output


def test_add_with_config_store_does_not_save_empty_password(env):
    result = env.invoke(["add"], _add_answers(store="config", password=""))
    assert result.exit_code == 0, result.output
    assert env.read_config()["connections"]["prod"]["password"] == ""
    assert "empty password cannot be saved" in result.output


def test_add_with_user_asked_every_time_does_not_offer_config_store(env):
    prompter = _add_answers(user_fixed=False)
    env.invoke(["add"], prompter)
    assert env.read_config()["connections"]["prod"]["password"] == ""


def _registered_with_plaintext(registered):
    config = registered.read_config()
    config["connections"]["prod"]["password"] = "old"
    registered.write_config(config)
    registered.keychain.delete("prod")


def test_edit_keeps_plaintext_password_when_not_changed(registered):
    _registered_with_plaintext(registered)
    prompter = ScriptedPrompter(
        texts=["prod", "", "", "", ""],
        confirms=[True, False, False],  # save user / keep saved password / no SSL
        choices=["config"],
    )
    registered.invoke(["e", "prod"], prompter)
    assert registered.read_config()["connections"]["prod"]["password"] == "old"


def test_edit_changes_plaintext_password(registered):
    _registered_with_plaintext(registered)
    prompter = ScriptedPrompter(
        texts=["prod", "", "", "", ""],
        secrets=["new", "new"],
        confirms=[True, True, False],  # save user / change saved password / no SSL
        choices=["config"],
    )
    registered.invoke(["e", "prod"], prompter)
    assert registered.read_config()["connections"]["prod"]["password"] == "new"


def test_edit_switching_from_keychain_to_config_moves_password_and_deletes_keychain_entry(registered):
    registered.invoke(["e", "prod"], _edit_answers(store="config"))
    assert registered.read_config()["connections"]["prod"]["password"] == "new"
    assert registered.keychain.get("prod") is None


def test_edit_switching_from_config_to_keychain_saves_to_keychain_and_removes_plaintext(registered):
    _registered_with_plaintext(registered)
    prompter = ScriptedPrompter(
        texts=["prod", "", "", "", ""],
        secrets=["new", "new"],
        confirms=[True, False],  # save user / no SSL
        choices=["keychain"],
    )
    registered.invoke(["e", "prod"], prompter)
    assert registered.read_config()["connections"]["prod"]["password"] == "keyring"
    assert registered.keychain.get("prod") == "new"


def test_edit_switching_from_config_to_ask_every_time_removes_plaintext_password(registered):
    _registered_with_plaintext(registered)
    registered.invoke(["e", "prod"], _edit_answers(store="ask"))
    assert registered.read_config()["connections"]["prod"]["password"] == ""


def test_list_shows_config_file_store_without_password_value(registered):
    config = registered.read_config()
    config["connections"]["prod"]["password"] = "PLAINSECRET"
    registered.write_config(config)
    registered.keychain.delete("prod")
    result = registered.invoke(["ls"])
    assert result.exit_code == 0, result.output
    assert "config file" in result.output
    assert "PLAINSECRET" not in result.output


@pytest.mark.parametrize(
    "secrets",
    [["", ""], ["keyring", "keyring"], ["a", "b", "c", "d", "e", "f"]],
    ids=["empty", "reserved-word", "mismatch"],
)
def test_edit_keychain_to_config_keeps_keychain_password_when_new_password_is_unusable(registered, secrets):
    prompter = ScriptedPrompter(
        texts=["prod", "", "", "", ""],
        secrets=secrets,
        confirms=[True, False],  # save user / no SSL
        choices=["config"],
    )
    registered.invoke(["e", "prod"], prompter)
    assert registered.read_config()["connections"]["prod"]["password"] == "keyring"
    assert registered.keychain.get("prod") == "stored"


def test_edit_config_to_keychain_keeps_plaintext_password_when_keychain_save_fails(registered):
    from hiramedbc.secrets.keychain import Keychain

    _registered_with_plaintext(registered)
    registered.keychain = Keychain(LockedKeyring())
    prompter = ScriptedPrompter(
        texts=["prod", "", "", "", ""],
        secrets=["new", "new"],
        confirms=[True, False],  # save user / no SSL
        choices=["keychain"],
    )
    registered.invoke(["e", "prod"], prompter)
    assert registered.read_config()["connections"]["prod"]["password"] == "old"


def test_edit_config_to_keychain_with_rename_moves_password_to_new_keychain_entry(registered):
    _registered_with_plaintext(registered)
    prompter = ScriptedPrompter(
        texts=["production", "", "", "", ""],
        secrets=["new", "new"],
        confirms=[True, False],  # save user / no SSL
        choices=["keychain"],
    )
    registered.invoke(["e", "prod"], prompter)
    assert registered.read_config()["connections"]["production"]["password"] == "keyring"
    assert registered.keychain.get("production") == "new"
