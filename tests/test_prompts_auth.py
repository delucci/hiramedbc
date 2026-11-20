from hiramedbc.config.schema import ConnectionConfig
from hiramedbc.prompts.auth import Credentials, resolve_credentials
from hiramedbc.secrets.keychain import Keychain
from tests.conftest import LockedKeyring, ScriptedPrompter


def _resolve(conn, keychain, prompter, **kwargs):
    return resolve_credentials("prod", conn, keychain=keychain, prompter=prompter, **kwargs)


def test_asks_nothing_when_both_are_saved(memory_keyring):
    keychain = Keychain(memory_keyring)
    keychain.set("prod", "pw")
    prompter = ScriptedPrompter()
    creds = _resolve(ConnectionConfig(host="h", user="app", password="keyring"), keychain, prompter)
    assert (creds.user, creds.password, prompter.asked) == ("app", "pw", [])


def test_asks_only_password_when_user_saved_and_password_ask_every_time(memory_keyring):
    prompter = ScriptedPrompter(secrets=["typed"])
    creds = _resolve(ConnectionConfig(host="h", user="app"), Keychain(memory_keyring), prompter)
    assert (creds.user, creds.password, len(prompter.asked)) == ("app", "typed", 1)


def test_asks_both_when_both_are_ask_every_time(memory_keyring):
    prompter = ScriptedPrompter(texts=["me"], secrets=["typed"])
    creds = _resolve(ConnectionConfig(host="h"), Keychain(memory_keyring), prompter)
    assert (creds.user, creds.password) == ("me", "typed")


def test_does_not_save_typed_values_to_keychain(memory_keyring):
    prompter = ScriptedPrompter(texts=["me"], secrets=["typed"])
    _resolve(ConnectionConfig(host="h"), Keychain(memory_keyring), prompter)
    assert memory_keyring.store == {}


def test_ask_password_switches_saved_password_to_ask_every_time(memory_keyring):
    keychain = Keychain(memory_keyring)
    keychain.set("prod", "stored")
    prompter = ScriptedPrompter(secrets=["typed"])
    creds = _resolve(ConnectionConfig(host="h", user="app", password="keyring"), keychain, prompter, ask_password=True)
    assert creds.password == "typed"


def test_ask_user_also_asks_for_password(memory_keyring):
    keychain = Keychain(memory_keyring)
    keychain.set("prod", "stored")
    prompter = ScriptedPrompter(texts=["other"], secrets=["typed"])
    creds = _resolve(ConnectionConfig(host="h", user="app", password="keyring"), keychain, prompter, ask_user=True)
    assert (creds.user, creds.password) == ("other", "typed")


def test_keyring_without_saved_password_prompts_and_saves_on_consent(memory_keyring):
    keychain = Keychain(memory_keyring)
    prompter = ScriptedPrompter(secrets=["typed"], confirms=[True])
    creds = _resolve(ConnectionConfig(host="h", user="app", password="keyring"), keychain, prompter)
    assert (creds.password, keychain.get("prod")) == ("typed", "typed")


def test_keyring_without_saved_password_does_not_save_when_declined(memory_keyring):
    keychain = Keychain(memory_keyring)
    prompter = ScriptedPrompter(secrets=["typed"], confirms=[False])
    _resolve(ConnectionConfig(host="h", user="app", password="keyring"), keychain, prompter)
    assert keychain.get("prod") is None


def test_falls_back_to_prompt_when_keychain_is_inaccessible():
    prompter = ScriptedPrompter(secrets=["typed"])
    creds = _resolve(ConnectionConfig(host="h", user="app", password="keyring"), Keychain(LockedKeyring()), prompter)
    assert creds.password == "typed"


def test_credentials_repr_excludes_password():
    assert "TOPSECRET" not in repr(Credentials(user="app", password="TOPSECRET"))


def test_password_input_keeps_leading_and_trailing_whitespace(monkeypatch):
    from hiramedbc.prompts.auth import RichPrompter
    from hiramedbc.utils import console as out

    monkeypatch.setattr(out.err_console, "input", lambda prompt, password: "  pw with spaces  ")
    assert RichPrompter().ask_secret("pw") == "  pw with spaces  "


def test_prompts_even_with_square_brackets_in_user_name(memory_keyring):
    prompter = ScriptedPrompter(secrets=["pw"])
    creds = _resolve(ConnectionConfig(host="h", user="x[/y]"), Keychain(memory_keyring), prompter)
    assert creds.user == "x[/y]"
    assert "x\\[/y]" in prompter.asked[0]


def test_uses_plaintext_password_from_config_without_asking(memory_keyring):
    prompter = ScriptedPrompter()
    creds = _resolve(ConnectionConfig(host="h", user="app", password="in-config"), Keychain(memory_keyring), prompter)
    assert (creds.user, creds.password, prompter.asked) == ("app", "in-config", [])


def test_ask_password_overrides_plaintext_password_in_config(memory_keyring):
    prompter = ScriptedPrompter(secrets=["typed"])
    creds = _resolve(
        ConnectionConfig(host="h", user="app", password="in-config"),
        Keychain(memory_keyring),
        prompter,
        ask_password=True,
    )
    assert creds.password == "typed"


def test_plaintext_password_does_not_touch_keychain(memory_keyring):
    _resolve(ConnectionConfig(host="h", user="app", password="in-config"), Keychain(memory_keyring), ScriptedPrompter())
    assert memory_keyring.store == {}
