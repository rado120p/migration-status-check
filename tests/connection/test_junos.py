from pathlib import Path

import pytest

from migration_validator.connection.junos import (
    ConnectionOptions,
    detect_platform,
    device_meta,
)


class FakeDevice:
    def __init__(self, facts):
        self.facts = facts


@pytest.mark.parametrize(
    "facts,expected",
    [
        ({"version": "21.4R3-S4"}, "junos"),
        ({"version": "22.4R3-S2-EVO"}, "junos-evo"),
        ({"version": "23.2R1-EVO"}, "junos-evo"),
        ({"version": None, "model": "PTX10001-36MR"}, "junos-evo"),
        ({"version": None, "model": "MX204"}, "junos"),
        ({}, "junos"),
    ],
)
def test_detect_platform(facts, expected):
    assert detect_platform(FakeDevice(facts)) == expected


def test_device_meta_reads_facts():
    device = FakeDevice(
        {
            "hostname": "MX1-POP1",
            "model": "MX204",
            "version": "21.4R3-S4",
            "RE0": {"up_time": "95 days, 3 hours"},
        }
    )
    meta = device_meta(device, address="172.20.20.4")

    assert meta.address == "172.20.20.4"
    assert meta.hostname == "MX1-POP1"
    assert meta.model == "MX204"
    assert meta.platform == "junos"


def test_key_auth_kwargs():
    options = ConnectionOptions(host="172.20.20.4", key_file="/home/u/.ssh/id_rsa")
    kwargs = options.device_kwargs()

    assert kwargs["host"] == "172.20.20.4"
    assert kwargs["user"] == "ansible"
    assert kwargs["ssh_private_key_file"] == "/home/u/.ssh/id_rsa"
    assert "passwd" not in kwargs


def test_password_auth_kwargs():
    options = ConnectionOptions(
        host="172.20.20.4", auth_type="password", username="admin", password="secret"
    )
    kwargs = options.device_kwargs()

    assert kwargs["passwd"] == "secret"
    assert "ssh_private_key_file" not in kwargs


def test_password_auth_requires_password():
    with pytest.raises(ValueError, match="heslo"):
        ConnectionOptions(host="1.2.3.4", auth_type="password").device_kwargs()


def test_unknown_auth_type_is_rejected():
    with pytest.raises(ValueError, match="auth"):
        ConnectionOptions(host="1.2.3.4", auth_type="magic").device_kwargs()


def test_default_key_file_points_to_ssh_dir():
    options = ConnectionOptions(host="1.2.3.4")
    assert options.key_file.endswith(str(Path(".ssh") / "id_rsa"))
