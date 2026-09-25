import pytest

pytest.importorskip("uvicorn")

from migration_validator import users as users_mod
from migration_validator.cli import EXIT_OK, EXIT_TOOL_ERROR, main
from migration_validator.users import User, UserStore, hash_password


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text(f"auth:\n  users_file: {tmp_path / 'users.yml'}\n")
    return path


@pytest.fixture
def captured(monkeypatch):
    calls = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    # Real configure would set propagate=False on the audit logger for the
    # rest of the session and hide it from caplog in other tests.
    monkeypatch.setattr("migration_validator.gui.audit.configure_audit_logging", lambda stream=None: None)
    return calls


def _add_admin(tmp_path):
    UserStore(tmp_path / "users.yml").save(
        {"rado": User("rado", "admin", hash_password("correct horse battery", iterations=1000))}
    )


def test_gui_refuses_without_users(settings, captured, capsys, tmp_path):
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path)]) == EXIT_TOOL_ERROR
    assert "mig-validate user add" in capsys.readouterr().err
    assert not captured


def test_gui_starts_with_auth(settings, captured, tmp_path):
    _add_admin(tmp_path)
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path)]) == EXIT_OK
    assert captured["app"].state.auth_enabled is True
    assert "ssl_certfile" not in captured and "forwarded_allow_ips" not in captured


def test_gui_passes_tls_and_proxy_flags(settings, captured, tmp_path):
    _add_admin(tmp_path)
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path),
                 "--ssl-certfile", "c.pem", "--ssl-keyfile", "k.pem",
                 "--forwarded-allow-ips", "10.0.0.5"]) == EXIT_OK
    assert captured["ssl_certfile"] == "c.pem"
    assert captured["ssl_keyfile"] == "k.pem"
    assert captured["forwarded_allow_ips"] == "10.0.0.5"


def test_gui_needs_both_tls_files(settings, captured, tmp_path, capsys):
    _add_admin(tmp_path)
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path),
                 "--ssl-certfile", "c.pem"]) == EXIT_TOOL_ERROR
    assert not captured


def test_gui_refuses_missing_settings_path(captured, capsys, tmp_path):
    # finding #1: explicit --settings, ktera neexistuje, se dnes tise
    # ignoruje a spadne na default config/settings.yml misto ToolError.
    missing = tmp_path / "missing.yml"
    assert main(["gui", "--settings", str(missing), "--run-root", str(tmp_path)]) == EXIT_TOOL_ERROR
    assert "nenalezen" in capsys.readouterr().err
    assert not captured


def test_gui_refusal_hint_includes_settings_path(settings, captured, capsys, tmp_path):
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path)]) == EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert f"--settings {settings}" in err
