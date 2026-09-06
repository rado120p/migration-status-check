"""Testy pro config/settings.yml - nahrada auth.yml."""

import pytest

from migration_validator.auth import (
    DEFAULT_NETCONF_PORT,
    DEFAULT_SETTINGS_PATH,
    ConnectionSettings,
    load_settings,
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
