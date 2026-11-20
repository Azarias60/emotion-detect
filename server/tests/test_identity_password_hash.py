"""Password hashing contract for the development-only Argon2 migration."""

from pwdlib import PasswordHash

from server.support.identity.adapters.security import hash_password, verify_password


def test_password_hash_uses_argon2_and_verifies() -> None:
    password = "correct-horse-battery-staple"
    encoded = hash_password(password)

    assert encoded.startswith("$argon2id$")
    assert password not in encoded
    assert verify_password(password, encoded)
    assert not verify_password("wrong-password", encoded)


def test_password_hash_is_salted() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_invalid_hash_is_rejected() -> None:
    assert not verify_password("password", "not-a-valid-hash")


def test_old_pbkdf2_format_is_not_supported() -> None:
    assert not verify_password("password", "pbkdf2_sha256$310000$abc$def")


def test_hash_interoperates_with_pwdlib() -> None:
    password = "interoperability-test"
    encoded = hash_password(password)
    assert PasswordHash.recommended().verify(password, encoded)
