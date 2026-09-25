"""Testy pro config/settings.yml - nahrada auth.yml."""

from pathlib import Path

import pytest

from migration_validator.auth import (
    DEFAULT_NETCONF_PORT,
    DEFAULT_SETTINGS_PATH,
    ConnectionSettings,
    load_settings,
    DEFAULT_USERS_FILE,
    AuthSettings,
    load_auth_settings,
)


def test_chybejici_soubor_vraci_defaulty(tmp_path):
    settings = load_settings(tmp_path / "settings.yml")
    assert settings.username == "ansible"
    assert settings.netconf_port == 830
    assert settings.timeout == 30
    assert settings.password is None
    assert len(settings.ssh_key_paths) == 2
    assert settings.ssh_key_paths[0].endswith("/.ssh/id_ed25519")
    assert settings.ssh_key_paths[1].endswith("/.ssh/id_rsa")
    # expanduser probehl uz pri load - zadna ~ v cestach
    assert not any(p.startswith("~") for p in settings.ssh_key_paths)


def test_default_cesta_je_v_repu():
    assert str(DEFAULT_SETTINGS_PATH) == "config/settings.yml"


def test_plny_soubor(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text(
        "connection:\n"
        "  netconf_port: 22\n"
        "  timeout: 60\n"
        "  username: rmohyla\n"
        "  ssh_key_paths:\n"
        "    - ~/.ssh/moje_klic\n",
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.username == "rmohyla"
    assert settings.netconf_port == 22
    assert settings.timeout == 60
    assert len(settings.ssh_key_paths) == 1
    assert settings.ssh_key_paths[0].endswith("/.ssh/moje_klic")


def test_password_env_se_cte_z_prostredi(tmp_path, monkeypatch):
    monkeypatch.setenv("MIG_TEST_PW", "tajne")
    path = tmp_path / "settings.yml"
    path.write_text(
        "connection:\n  password_env: MIG_TEST_PW\n", encoding="utf-8"
    )
    assert load_settings(path).password == "tajne"


def test_password_env_nenastavena_je_chyba(tmp_path, monkeypatch):
    monkeypatch.delenv("MIG_TEST_PW", raising=False)
    path = tmp_path / "settings.yml"
    path.write_text(
        "connection:\n  password_env: MIG_TEST_PW\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="MIG_TEST_PW"):
        load_settings(path)


def test_plaintext_password_vyzaduje_0600(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  password: tajne\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="chmod 600"):
        load_settings(path)
    path.chmod(0o600)
    assert load_settings(path).password == "tajne"


def test_password_a_password_env_zaroven_je_chyba(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text(
        "connection:\n  password: a\n  password_env: B\n", encoding="utf-8"
    )
    path.chmod(0o600)
    with pytest.raises(ValueError, match="vyber jedno"):
        load_settings(path)


def test_neznamy_klic_je_chyba(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  passwd: preklep\n", encoding="utf-8")
    with pytest.raises(ValueError, match="passwd"):
        load_settings(path)


def test_soubor_bez_connection_bloku_vraci_defaulty(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("{}\n", encoding="utf-8")
    settings = load_settings(path)
    assert settings.username == "ansible"


def test_nemapovy_yaml_je_chyba(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("- polozka\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_settings(path)


def test_capture_pool_default_je_10(tmp_path):
    settings = load_settings(tmp_path / "settings.yml")
    assert settings.capture_pool == 10


def test_capture_pool_se_cte_ze_souboru(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  capture_pool: 3\n", encoding="utf-8")
    assert load_settings(path).capture_pool == 3


def test_capture_pool_pod_1_je_chyba(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  capture_pool: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="capture_pool musi byt >= 1"):
        load_settings(path)


def test_auth_settings_default_when_file_missing(tmp_path):
    assert load_auth_settings(tmp_path / "missing.yml") == AuthSettings()
    assert AuthSettings().users_file == DEFAULT_USERS_FILE


def test_auth_settings_default_when_block_missing(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  timeout: 10\n")
    assert load_auth_settings(path).users_file == DEFAULT_USERS_FILE


def test_auth_settings_users_file(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("auth:\n  users_file: /srv/mig/users.yml\n")
    assert load_auth_settings(path).users_file == Path("/srv/mig/users.yml")


def test_auth_settings_expands_user(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("auth:\n  users_file: ~/users.yml\n")
    assert load_auth_settings(path).users_file == Path("~/users.yml").expanduser()


def test_auth_settings_unknown_key_rejected(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("auth:\n  userfile: x.yml\n")
    with pytest.raises(ValueError, match="userfile"):
        load_auth_settings(path)


def test_auth_settings_block_must_be_mapping(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("auth: config/users.yml\n")
    with pytest.raises(ValueError, match="'auth' musi byt mapping"):
        load_auth_settings(path)


def test_auth_settings_does_not_need_password_env(tmp_path, monkeypatch):
    # `mig-validate user add` must work without MIG_LAB_PASSWORD in the env.
    monkeypatch.delenv("NOPE_UNSET", raising=False)
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  password_env: NOPE_UNSET\nauth:\n  users_file: u.yml\n")
    assert load_auth_settings(path).users_file == Path("u.yml")


def test_inventory_settings_default_disabled(tmp_path):
    from migration_validator.auth import InventorySettings, load_inventory_settings
    assert load_inventory_settings(tmp_path / "missing.yml") == InventorySettings(path=None)
    path = tmp_path / "settings.yml"
    path.write_text("auth:\n  users_file: u.yml\n")
    assert load_inventory_settings(path).path is None


def test_inventory_settings_path(tmp_path):
    from migration_validator.auth import InventorySettings, load_inventory_settings
    path = tmp_path / "settings.yml"
    path.write_text("inventory:\n  path: ~/hosts\n")
    assert load_inventory_settings(path).path == Path("~/hosts").expanduser()


def test_inventory_settings_rejects_unknown_key_and_scalar(tmp_path):
    from migration_validator.auth import load_inventory_settings
    path = tmp_path / "settings.yml"
    path.write_text("inventory:\n  file: hosts\n")
    with pytest.raises(ValueError, match="file"):
        load_inventory_settings(path)
    path.write_text("inventory: /etc/ansible/hosts\n")
    with pytest.raises(ValueError, match="'inventory' musi byt mapping"):
        load_inventory_settings(path)
