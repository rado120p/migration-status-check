from pathlib import Path

import pytest

from migration_validator.connection.junos import (
    ConnectionOptions,
    JunosConnectionError,
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


def test_auth_attempts_zkousi_klice_v_poradi(tmp_path):
    key1 = tmp_path / "id_ed25519"
    key2 = tmp_path / "id_rsa"
    key1.write_text("k1")
    key2.write_text("k2")
    options = ConnectionOptions(
        host="172.20.20.4", ssh_key_paths=(str(key1), str(key2))
    )
    attempts = options.auth_attempts()
    assert [a["ssh_private_key_file"] for a in attempts] == [str(key1), str(key2)]
    assert all(a["host"] == "172.20.20.4" for a in attempts)


def test_auth_attempts_preskoci_neexistujici_klic(tmp_path):
    existing = tmp_path / "id_rsa"
    existing.write_text("k")
    options = ConnectionOptions(
        host="h", ssh_key_paths=(str(tmp_path / "neni"), str(existing))
    )
    attempts = options.auth_attempts()
    assert len(attempts) == 1
    assert attempts[0]["ssh_private_key_file"] == str(existing)


def test_auth_attempts_heslo_je_posledni(tmp_path):
    key = tmp_path / "id_rsa"
    key.write_text("k")
    options = ConnectionOptions(
        host="h", ssh_key_paths=(str(key),), password="tajne"
    )
    attempts = options.auth_attempts()
    assert attempts[-1]["passwd"] == "tajne"
    assert "ssh_private_key_file" not in attempts[-1]


def test_auth_attempts_bez_moznosti_je_chyba(tmp_path):
    options = ConnectionOptions(host="h", ssh_key_paths=(str(tmp_path / "neni"),))
    with pytest.raises(JunosConnectionError, match="zadna pouzitelna autentizace"):
        options.auth_attempts()


def test_default_port_je_netconf():
    assert ConnectionOptions(host="h").port == 830
