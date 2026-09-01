"""Sdilene fixtures pro GUI testy."""

import pytest
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"}


@pytest.fixture
def client(tmp_path):
    api.create_run(
        "mig01", old_device=OLD, new_device=NEW,
        mappings=[("ge-0/0/1", "et-0/0/1")], run_root=tmp_path,
    )
    app = create_app(run_root=tmp_path)
    return TestClient(app)
