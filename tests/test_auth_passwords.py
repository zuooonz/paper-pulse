import sys
from io import StringIO

import pytest

from api.passwords import (
    MIN_PASSWORD_HASH_ITERATIONS,
    hash_password,
    verify_password,
)


def test_pbkdf2_password_hash_verifies_and_uses_minimum_iterations():
    encoded = hash_password("correct horse")

    scheme, iterations, _, _ = encoded.split("$")
    assert scheme == "pbkdf2_sha256"
    assert int(iterations) >= MIN_PASSWORD_HASH_ITERATIONS
    assert verify_password("correct horse", encoded)
    assert not verify_password("wrong horse", encoded)
    assert verify_password("correct horse", encoded, allow_plaintext=False)


def test_plaintext_password_migration_switch():
    assert verify_password("old-password", "old-password", allow_plaintext=True)
    assert not verify_password("old-password", "old-password", allow_plaintext=False)
    assert verify_password("旧密码", "旧密码", allow_plaintext=True)


def test_invalid_or_weak_hash_is_not_treated_as_plaintext():
    assert not verify_password("password", "pbkdf2_sha256$invalid")
    weak_hash = "pbkdf2_sha256$599999$YWJjZGVmZ2hpamtsbW5vcA==$" + ("A" * 44)
    assert not verify_password(weak_hash, weak_hash, allow_plaintext=True)


def test_hash_password_cli_reads_stdin(monkeypatch, capsys):
    from api import hash_password as cli_module

    monkeypatch.setattr(sys, "argv", ["api.hash_password", "--password-stdin"])
    monkeypatch.setattr(sys, "stdin", StringIO("from stdin\n"))
    assert cli_module.main() == 0
    assert verify_password("from stdin", capsys.readouterr().out.strip(), allow_plaintext=False)


def test_hash_password_rejects_iteration_count_below_minimum():
    with pytest.raises(ValueError):
        hash_password("password", iterations=MIN_PASSWORD_HASH_ITERATIONS - 1)
