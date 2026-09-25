import pytest

from migration_validator import users as users_mod
from migration_validator.cli import EXIT_OK, EXIT_TOOL_ERROR, main
from migration_validator.users import UserStore, verify_password


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(users_mod, "PASSWORD_ITERATIONS", 1_000)
    path = tmp_path / "settings.yml"
    path.write_text(f"auth:\n  users_file: {tmp_path / 'users.yml'}\n")
    return path


@pytest.fixture
def store(tmp_path):
    return UserStore(tmp_path / "users.yml")


def _prompts(monkeypatch, *answers):
    it = iter(answers)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(it))


def test_add_user(settings, store, monkeypatch, capsys):
    _prompts(monkeypatch, "correct horse battery", "correct horse battery")
    assert main(["user", "add", "rado", "--role", "admin", "--settings", str(settings)]) == EXIT_OK
    user = store.load()["rado"]
    assert user.role == "admin"
    assert verify_password("correct horse battery", user.password_hash)
    assert "rado" in capsys.readouterr().out


def test_add_refuses_mismatch(settings, store, monkeypatch, capsys):
    _prompts(monkeypatch, "correct horse battery", "correct horse batterx")
    assert main(["user", "add", "rado", "--role", "admin", "--settings", str(settings)]) == EXIT_TOOL_ERROR
    assert store.load() == {}
    assert "neshoduji" in capsys.readouterr().err


def test_add_refuses_short_password(settings, store, monkeypatch):
    _prompts(monkeypatch, "short", "short")
    assert main(["user", "add", "rado", "--role", "admin", "--settings", str(settings)]) == EXIT_TOOL_ERROR
    assert store.load() == {}


def test_add_refuses_existing(settings, store, monkeypatch, capsys):
    _prompts(monkeypatch, *["correct horse battery"] * 4)
    main(["user", "add", "rado", "--role", "admin", "--settings", str(settings)])
    assert main(["user", "add", "rado", "--role", "viewer", "--settings", str(settings)]) == EXIT_TOOL_ERROR
    assert store.load()["rado"].role == "admin"
    assert "existuje" in capsys.readouterr().err


def test_add_refuses_bad_name_before_prompting(settings, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": pytest.fail("prompted"))
    assert main(["user", "add", "a b", "--role", "admin", "--settings", str(settings)]) == EXIT_TOOL_ERROR


def test_role_must_be_known(settings):
    with pytest.raises(SystemExit):
        main(["user", "add", "rado", "--role", "guest", "--settings", str(settings)])


def test_passwd_role_delete_list(settings, store, monkeypatch, capsys):
    _prompts(monkeypatch, "correct horse battery", "correct horse battery",
             "another long password", "another long password")
    main(["user", "add", "rado", "--role", "viewer", "--settings", str(settings)])
    assert main(["user", "passwd", "rado", "--settings", str(settings)]) == EXIT_OK
    assert verify_password("another long password", store.load()["rado"].password_hash)
    assert main(["user", "role", "rado", "operator", "--settings", str(settings)]) == EXIT_OK
    assert store.load()["rado"].role == "operator"
    capsys.readouterr()
    assert main(["user", "list", "--settings", str(settings)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "rado" in out and "operator" in out and "pbkdf2" not in out
    assert main(["user", "delete", "rado", "--settings", str(settings)]) == EXIT_OK
    assert store.load() == {}


@pytest.mark.parametrize("argv", [["passwd", "nobody"], ["role", "nobody", "admin"], ["delete", "nobody"]])
def test_unknown_user(settings, argv, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": pytest.fail("prompted"))
    assert main(["user", *argv, "--settings", str(settings)]) == EXIT_TOOL_ERROR


def test_list_empty(settings, capsys):
    assert main(["user", "list", "--settings", str(settings)]) == EXIT_OK
    assert "zadni uzivatele" in capsys.readouterr().out
