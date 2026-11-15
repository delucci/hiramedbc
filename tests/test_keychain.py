import pytest

from hiramedbc.secrets.keychain import Keychain
from hiramedbc.utils.errors import KeychainError
from tests.conftest import LockedKeyring


def test_gets_saved_password(memory_keyring):
    keychain = Keychain(memory_keyring)
    keychain.set("prod", "pw")
    assert keychain.get("prod") == "pw"


def test_saves_under_service_hiramedbc_and_alias(memory_keyring):
    Keychain(memory_keyring).set("prod", "pw")
    assert memory_keyring.store == {("hiramedbc", "prod"): "pw"}


def test_returns_none_when_not_saved(memory_keyring):
    assert Keychain(memory_keyring).get("prod") is None


def test_delete_removes_entry(memory_keyring):
    keychain = Keychain(memory_keyring)
    keychain.set("prod", "pw")
    assert keychain.delete("prod") is True
    assert keychain.get("prod") is None


def test_deleting_missing_entry_returns_false(memory_keyring):
    assert Keychain(memory_keyring).delete("prod") is False


def test_rename_moves_entry_to_new_name(memory_keyring):
    keychain = Keychain(memory_keyring)
    keychain.set("old", "pw")
    assert keychain.rename("old", "new") is True
    assert (keychain.get("old"), keychain.get("new")) == (None, "pw")


def test_rename_returns_false_when_no_entry_to_move(memory_keyring):
    assert Keychain(memory_keyring).rename("old", "new") is False


@pytest.mark.parametrize("operation", ["get", "delete"])
def test_locked_keychain_raises_keychain_error(operation):
    with pytest.raises(KeychainError, match="prod"):
        getattr(Keychain(LockedKeyring()), operation)("prod")


def test_save_failure_error_excludes_password():
    with pytest.raises(KeychainError) as excinfo:
        Keychain(LockedKeyring()).set("prod", "TOPSECRET")
    assert "TOPSECRET" not in excinfo.value.message


def test_fail_backend_is_unavailable():
    import keyring.backends.fail

    assert Keychain(keyring.backends.fail.Keyring()).status().available is False


def test_regular_backend_is_available(memory_keyring):
    status = Keychain(memory_keyring).status()
    assert status.available is True
    assert "MemoryKeyring" in status.name
