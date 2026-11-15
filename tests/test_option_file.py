import io
import tarfile

import pytest

from hiramedbc.docker.option_file import OptionFile, escape_value, to_tar
from hiramedbc.utils.errors import ConfigError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("simple", '"simple"'),
        ("has#hash", '"has#hash"'),
        ("back\\slash", '"back\\\\slash"'),
        ("new\nline", '"new\\nline"'),
        ("tab\tx", '"tab\\tx"'),
        (" spaced ", '" spaced "'),
        ('dq"inside', "'dq\"inside'"),
        ("sq'inside", '"sq\'inside"'),
        ("", '""'),
    ],
)
def test_escapes_value_and_wraps_it_in_quotes(value, expected):
    assert escape_value(value) == expected


def test_rejects_value_with_both_kinds_of_quotes():
    with pytest.raises(ConfigError, match="A value containing both double quotes"):
        escape_value("a'b\"c")


def test_writes_connection_info_to_client_section():
    text = OptionFile(host="db.example.com", port=3307, user="app", password="pw").render().decode()
    assert text == '[client]\nhost="db.example.com"\nport=3307\nuser="app"\npassword="pw"\n'


def test_writes_ssl_settings_to_option_file():
    option = OptionFile(
        host="h", port=3306, user="u", password="p", ssl_mode="VERIFY_CA", ssl_files={"ca": "/hiramedbc/ssl/ca.pem"}
    )
    text = option.render().decode()
    assert 'ssl-mode=VERIFY_CA\nssl-ca="/hiramedbc/ssl/ca.pem"\n' in text


def test_option_file_repr_excludes_password():
    assert "TOPSECRET" not in repr(OptionFile(host="h", port=1, user="u", password="TOPSECRET"))


def test_tar_contains_single_owner_only_file():
    data = to_tar(b"content")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = tar.getmembers()
        assert [(m.name, m.mode, m.uid) for m in members] == [("hiramedbc.cnf", 0o600, 0)]
        assert tar.extractfile(members[0]).read() == b"content"


def test_rejects_value_with_nul_character():
    with pytest.raises(ConfigError, match="NUL"):
        escape_value("pass\x00word")
