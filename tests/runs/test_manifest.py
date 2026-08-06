"""Model run.yml - jediny zdroj pravdy o migraci."""

import pytest

from migration_validator.runs.manifest import (
    CaptureRecord,
    InterfaceMapping,
    MappingEndpoint,
    RunDevice,
    RunManifest,
    load_manifest,
    normalize_port,
    save_manifest,
)


def _manifest():
    return RunManifest(
        devices={
            "MX1-POP1": RunDevice(host="172.20.20.4", platform="junos", role="old"),
            "PTX1-POP1": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="new"
            ),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/0"),
                new=MappingEndpoint(node="PTX1-POP1", port="et-0/0/0"),
            )
        ],
        captures=[],
    )


def test_normalize_port():
    assert normalize_port("ge-0/0/0") == "ge_0_0_0"
    assert normalize_port("ae0") == "ae0"


def test_roundtrip_preserves_l2_switch(tmp_path):
    manifest = _manifest()
    manifest.interface_mapping.append(
        InterfaceMapping(
            old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/1"),
            new=MappingEndpoint(
                node="PTX1-POP1",
                port="ae0",
                l2_switch={
                    "node": "EX1-POP1",
                    "ae_port": "ae0",
                    "access_port": "ge-0/0/0",
                },
            ),
        )
    )
    manifest.captures.append(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "a.json", "T1")
    )
    manifest.captures.append(CaptureRecord("pre", "PTX1-POP1", None, "b.json", "T2"))
    path = tmp_path / "run.yml"
    save_manifest(manifest, path)
    loaded = load_manifest(path)
    assert loaded == manifest
    assert loaded.interface_mapping[1].new.l2_switch["node"] == "EX1-POP1"
    assert "port: all" in path.read_text(encoding="utf-8")
    assert loaded.find_capture("pre", "PTX1-POP1", None).port is None


def test_load_rejects_foreign_schema(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text("schema_version: 99\ndevices: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_manifest(path)


def test_load_rejects_unknown_role(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: modern}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="role"):
        load_manifest(path)


def test_load_rejects_unknown_role_error_has_path_and_node(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: modern}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"X") as excinfo:
        load_manifest(path)
    assert str(path) in str(excinfo.value)


def test_load_missing_sections_default_to_empty(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  MX1-POP1: {host: 172.20.20.4, platform: junos, role: old}\n",
        encoding="utf-8",
    )
    manifest = load_manifest(path)
    assert manifest.interface_mapping == []
    assert manifest.captures == []


def test_save_manifest_puts_schema_version_first(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(), path)
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "schema_version: 1"


def test_save_manifest_omits_l2_switch_when_none(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(), path)
    assert "l2_switch" not in path.read_text(encoding="utf-8")


def test_save_manifest_creates_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "run.yml"
    save_manifest(_manifest(), path)
    assert path.exists()
    assert load_manifest(path) == _manifest()


def test_node_for_host_and_roles():
    manifest = _manifest()
    assert manifest.node_for_host("172.20.20.4") == "MX1-POP1"
    assert manifest.node_for_host("1.1.1.1") is None
    node, device = manifest.device_with_role("old")
    assert node == "MX1-POP1"
    assert device == RunDevice(host="172.20.20.4", platform="junos", role="old")
    assert manifest.device_with_role("l2-switch") is None


def test_device_with_role_rejects_duplicates():
    manifest = _manifest()
    manifest.devices["MX2-POP1"] = RunDevice(
        host="172.20.20.6", platform="junos", role="old"
    )
    with pytest.raises(ValueError, match="old"):
        manifest.device_with_role("old")


def test_pairing_both_directions():
    manifest = _manifest()
    assert manifest.paired_old("PTX1-POP1", "et-0/0/0").port == "ge-0/0/0"
    assert manifest.paired_new("MX1-POP1", "ge-0/0/0").port == "et-0/0/0"
    assert manifest.paired_old("PTX1-POP1", "et-0/0/9") is None


def test_paired_old_ambiguous_lag_returns_none():
    manifest = _manifest()
    for old_port in ("ge-0/0/1", "ge-0/0/2"):
        manifest.add_mapping(
            MappingEndpoint(node="MX1-POP1", port=old_port),
            MappingEndpoint(node="PTX1-POP1", port="ae0"),
        )
    assert manifest.paired_old("PTX1-POP1", "ae0") is None


def test_add_mapping_idempotent_and_conflicting():
    manifest = _manifest()
    manifest.add_mapping(
        MappingEndpoint(node="MX1-POP1", port="ge-0/0/0"),
        MappingEndpoint(node="PTX1-POP1", port="et-0/0/0"),
    )
    assert len(manifest.interface_mapping) == 1
    with pytest.raises(ValueError, match="ge-0/0/0"):
        manifest.add_mapping(
            MappingEndpoint(node="MX1-POP1", port="ge-0/0/0"),
            MappingEndpoint(node="PTX1-POP1", port="et-0/0/5"),
        )


def test_record_capture_replaces_same_key():
    manifest = _manifest()
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "a.json", "T1")
    )
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "b.json", "T2")
    )
    manifest.record_capture(CaptureRecord("pre", "MX1-POP1", None, "c.json", "T3"))
    assert len(manifest.captures) == 2
    assert manifest.find_capture("pre", "MX1-POP1", "ge-0/0/0").snapshot == "b.json"
    assert manifest.find_capture("pre", "MX1-POP1", None).snapshot == "c.json"
    assert manifest.find_capture("post", "MX1-POP1", None) is None
