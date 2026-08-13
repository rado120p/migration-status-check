"""Auth soubor - per-user credentials, heslo jen pres env nebo 0600."""

import pytest

from migration_validator.auth import AuthSettings, load_auth_file


def write(tmp_path, content, mode=0o600):
    path = tmp_path / "auth.yml"
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_chybejici_default_soubor_je_prazdne_nastaveni(tmp_path):
    settings = load_auth_file(tmp_path / "neni.yml", required=False)
    assert settings == AuthSettings()


def test_chybejici_explicitni_soubor_je_chyba(tmp_path):
    with pytest.raises(ValueError, match="auth soubor nenalezen"):
        load_auth_file(tmp_path / "neni.yml", required=True)


def test_plna_sada_poli(tmp_path):
    path = write(
        tmp_path,
        "username: rmohyla\nauth: key\nkey_file: ~/.ssh/lab\n"
        "ssh_port: 2222\ntimeout: 60\n",
    )
    settings = load_auth_file(path, required=True)
    assert settings.username == "rmohyla"
    assert settings.auth_type == "key"
    assert settings.key_file.endswith("/.ssh/lab")
    assert not settings.key_file.startswith("~")
    assert settings.ssh_port == 2222
    assert settings.timeout == 60


def test_password_env_se_resolvuje(tmp_path, monkeypatch):
    monkeypatch.setenv("MIG_TEST_HESLO", "tajne")
    path = write(tmp_path, "auth: password\npassword_env: MIG_TEST_HESLO\n")
    assert load_auth_file(path, required=True).password == "tajne"


def test_nenastavena_promenna_je_chyba_hned(tmp_path, monkeypatch):
    monkeypatch.delenv("MIG_TEST_HESLO", raising=False)
    path = write(tmp_path, "password_env: MIG_TEST_HESLO\n")
    with pytest.raises(ValueError, match="MIG_TEST_HESLO neni nastavena"):
        load_auth_file(path, required=True)


def test_plaintext_heslo_pri_0600_projde(tmp_path):
    path = write(tmp_path, "password: tajne\n", mode=0o600)
    assert load_auth_file(path, required=True).password == "tajne"


def test_plaintext_heslo_pri_sirsich_pravech_je_chyba(tmp_path):
    path = write(tmp_path, "password: tajne\n", mode=0o640)
    with pytest.raises(ValueError, match="chmod 600"):
        load_auth_file(path, required=True)


def test_heslo_a_env_zaroven_je_chyba(tmp_path, monkeypatch):
    monkeypatch.setenv("MIG_TEST_HESLO", "x")
    path = write(tmp_path, "password: a\npassword_env: MIG_TEST_HESLO\n")
    with pytest.raises(ValueError, match="password i password_env"):
        load_auth_file(path, required=True)


def test_neznamy_klic_je_chyba(tmp_path):
    path = write(tmp_path, "usrename: preklep\n")
    with pytest.raises(ValueError, match="neznamy klic"):
        load_auth_file(path, required=True)
