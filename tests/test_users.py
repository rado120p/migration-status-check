import os
import stat

import pytest
import yaml

from migration_validator import users as users_mod
from migration_validator.users import (
    AuthenticationError,
    User,
    UserStore,
    hash_password,
    needs_rehash,
    verify_password,
)

FAST = 1_000  # iterations for tests; production value is exercised once below


def test_hash_format_and_round_trip():
    stored = hash_password("correct horse battery", iterations=FAST)
    scheme, iterations, salt, digest = stored.split("$")
    assert scheme == "pbkdf2_sha256"
    assert iterations == "1000"
    assert salt and digest
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterx", stored)


def test_default_iterations_are_600k():
    stored = hash_password("correct horse battery")
    assert stored.split("$")[1] == "600000"
    assert verify_password("correct horse battery", stored)
    assert not needs_rehash(stored)


def test_salt_is_random():
    assert hash_password("same password!!", iterations=FAST) != hash_password("same password!!", iterations=FAST)


def test_iterations_read_from_stored_hash():
    stored = hash_password("correct horse battery", iterations=FAST)
    assert verify_password("correct horse battery", stored)  # verified with 1000, not 600000
    assert needs_rehash(stored)


@pytest.mark.parametrize("stored", [
    "", "garbage", "md5$1$a$b", "pbkdf2_sha256$notanumber$YQ==$YQ==",
    "pbkdf2_sha256$1000$!!!$YQ==", "pbkdf2_sha256$1000$YQ==",
    "pbkdf2_sha256$0$YQ==$YQ==",
])
def test_malformed_hash_verifies_false(stored):
    assert verify_password("whatever password", stored) is False


def test_password_length_limits():
    with pytest.raises(AuthenticationError, match="12"):
        hash_password("short", iterations=FAST)
    with pytest.raises(AuthenticationError, match="1024"):
        hash_password("x" * 1025, iterations=FAST)


@pytest.mark.parametrize("name", ["ab", "a" * 65, "rado mohyla", "rado/..", "ráda"])
def test_bad_usernames(name):
    with pytest.raises(AuthenticationError):
        users_mod.validate_username(name)


def test_validate_username_rejects_trailing_newline():
    # re.match + $ povoli "jmeno\n" (re.match nekotvi konec). fullmatch to
    # odmita - finding #5 final-fix-findings.md.
    with pytest.raises(AuthenticationError):
        users_mod.validate_username("rado\n")


def test_bad_role():
    with pytest.raises(AuthenticationError, match="guest"):
        users_mod.validate_role("guest")


def _user(name="rado", role="admin"):
    return User(name, role, hash_password("correct horse battery", iterations=FAST))


def test_store_missing_file_is_empty(tmp_path):
    store = UserStore(tmp_path / "users.yml")
    assert store.load() == {}
    assert store.get("rado") is None


def test_store_save_load_round_trip_and_mode(tmp_path):
    path = tmp_path / "sub" / "users.yml"
    store = UserStore(path)
    store.save({"rado": _user()})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = store.load()
    assert loaded["rado"].role == "admin"
    assert verify_password("correct horse battery", loaded["rado"].password_hash)
    raw = yaml.safe_load(path.read_text())
    assert set(raw["users"]["rado"]) == {"role", "password_hash"}
    assert [p.name for p in path.parent.iterdir()] == ["users.yml"]  # no temp leftovers


def test_store_get_sees_external_edit(tmp_path):
    path = tmp_path / "users.yml"
    store = UserStore(path)
    store.save({"rado": _user(role="viewer")})
    assert store.get("rado").role == "viewer"
    other = UserStore(path)  # e.g. the CLI process
    other.save({"rado": _user(role="admin")})
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert store.get("rado").role == "admin"
    other.save({})
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
    assert store.get("rado") is None


def test_store_get_sees_equal_size_rewrite_same_mtime(tmp_path):
    # finding #7: cache key was (mtime_ns, size) only - an os.replace()
    # rewrite that lands on the same size, with mtime forced identical
    # (coarse filesystem clock, or two writes in the same tick), looked
    # unchanged and served the stale cached entry. st_ino changes on every
    # os.replace() (new inode via tempfile+rename), so it belongs in the key.
    path = tmp_path / "users.yml"
    store = UserStore(path)
    store.save({"rado": _user(role="viewer")})
    first_hash = store.get("rado").password_hash
    st_before = path.stat()

    other = UserStore(path)
    other.save({"rado": _user(role="viewer")})  # same length -> same size, new inode
    os.utime(path, ns=(st_before.st_atime_ns, st_before.st_mtime_ns))
    st_after = path.stat()
    assert st_after.st_size == st_before.st_size
    assert st_after.st_ino != st_before.st_ino

    assert store.get("rado").password_hash != first_hash


@pytest.mark.parametrize("content, match", [
    ("- rado\n", "mapping"),
    ("users: [rado]\n", "users"),
    ("users:\n  rado: admin\n", "rado"),
    ("users:\n  rado: {role: guest, password_hash: 'pbkdf2_sha256$1000$YQ==$YQ=='}\n", "guest"),
    ("users:\n  rado: {role: admin, password_hash: 'plain'}\n", "password_hash"),
    ("users:\n  'x y': {role: admin, password_hash: 'pbkdf2_sha256$1000$YQ==$YQ=='}\n", "x y"),
])
def test_store_malformed_file_raises(tmp_path, content, match):
    path = tmp_path / "users.yml"
    path.write_text(content)
    with pytest.raises(ValueError, match=match):
        UserStore(path).load()


def test_store_invalid_yaml_raises_value_error(tmp_path):
    # finding #4 (cross-wave): yaml.YAMLError used to escape load()
    # unwrapped; wrap it like hostname_filter.FilterStore.load() does.
    path = tmp_path / "users.yml"
    path.write_text("users: [unterminated\n")
    with pytest.raises(ValueError, match="users.yml"):
        UserStore(path).load()


def test_store_empty_file_and_empty_users(tmp_path):
    path = tmp_path / "users.yml"
    path.write_text("")
    assert UserStore(path).load() == {}
    path.write_text("users:\n")
    assert UserStore(path).load() == {}
