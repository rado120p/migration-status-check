import logging

import pytest
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor
from migration_validator.runs.store import RunStore

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}


@pytest.fixture(autouse=True)
def restore_audit_logger():
    logger = logging.getLogger("migration_validator.gui.audit")
    handlers, propagate, level = list(logger.handlers), logger.propagate, logger.level
    yield
    logger.handlers[:] = handlers
    logger.propagate, logger.level = propagate, level


@pytest.fixture
def client(tmp_path):
    app = create_app(run_root=tmp_path, profiles_root=tmp_path / "profiles")
    app.state.actor_provider = lambda request: Actor(role="operator", username="eva")
    return TestClient(app), tmp_path


def test_create_run_records_creator_and_audits(client, caplog):
    c, root = client
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        resp = c.post("/api/runs", json={"name": "mig02", "kind": "migration", "devices": [OLD, NEW], "mappings": []})
    assert resp.status_code == 201
    assert resp.json()["created_by"] == "eva"
    assert RunStore(root, "mig02").load().created_by == "eva"
    assert "user=eva action=run-create target=mig02" in caplog.text


def test_archive_is_audited(client, caplog):
    c, root = client
    api.create_run("mig03", kind="migration", devices=[OLD, NEW], run_root=root)
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        assert c.post("/api/runs/mig03/archive").status_code == 200
    assert "user=eva action=run-archive target=mig03" in caplog.text


def test_group_create_records_creator(client):
    c, root = client
    resp = c.post("/api/groups", json={"group": "g1", "devices": [{"node": "MX1", "host": "10.0.0.1", "platform": "junos"}]})
    assert resp.status_code == 201
    assert RunStore(root, "g1-mx1").load().created_by == "eva"


def test_upgrade_dry_run_not_audited(client, caplog):
    c, root = client
    api.create_run("mig04", kind="migration", devices=[OLD, NEW], run_root=root)
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        c.post("/api/runs/mig04/upgrade?dry_run=true")
    assert "run-upgrade" not in caplog.text


def test_configure_audit_logging_writes_to_stream():
    import io
    from migration_validator.gui import audit

    stream = io.StringIO()
    audit.configure_audit_logging(stream)
    audit.configure_audit_logging(stream)  # idempotent: one line, not two
    audit.record("rado", "login", ip="1.2.3.4")
    lines = [l for l in stream.getvalue().splitlines() if "action=login" in l]
    assert len(lines) == 1 and "user=rado action=login ip=1.2.3.4" in lines[0]


def test_record_escapes_control_chars_and_whitespace(caplog):
    from migration_validator.gui import audit

    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        audit.record(
            "eve\nuser=admin action=run-archive target=prod", "login-failed", ip="1.2.3.4"
        )
    lines = [l for l in caplog.text.splitlines() if l]
    assert len(lines) == 1
    expected_user = "eve\\x0auser=admin\\x20action=run-archive\\x20target=prod"
    assert f"user={expected_user} action=login-failed ip=1.2.3.4" in lines[0]


def test_record_caps_value_length_at_128_chars(caplog):
    from migration_validator.gui import audit

    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        audit.record("rado", "login", target="x" * 200)
    line = next(l for l in caplog.text.splitlines() if "action=login" in l)
    target_value = line.split("target=", 1)[1]
    assert target_value == "x" * 128
