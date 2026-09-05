"""Model run.yml - jediny zdroj pravdy o migraci."""

import pytest

from migration_validator.runs.manifest import (
    CaptureRecord,
    DEFAULT_KIND,
    InterfaceMapping,
    MappingEndpoint,
    RUN_KINDS,
    RunDevice,
    RunManifest,
    VALID_ROLES,
    check_kind_devices,
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


def test_mapped_olds_returns_all_old_endpoints_of_new_port():
    manifest = RunManifest()
    manifest.interface_mapping = [
        InterfaceMapping(
            old=MappingEndpoint(node="MX1", port="ge-0/0/4"),
            new=MappingEndpoint(node="PTX1", port="ae0"),
        ),
        InterfaceMapping(
            old=MappingEndpoint(node="MX1", port="ge-0/0/5"),
            new=MappingEndpoint(node="PTX1", port="ae0"),
        ),
        InterfaceMapping(
            old=MappingEndpoint(node="MX1", port="ge-0/0/6"),
            new=MappingEndpoint(node="PTX1", port="ae1"),
        ),
    ]

    olds = manifest.mapped_olds("PTX1", "ae0")

    assert [o.port for o in olds] == ["ge-0/0/4", "ge-0/0/5"]
    assert manifest.mapped_olds("PTX1", "et-9/9/9") == []


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


# -- kind / profile / group (GUI vlna 2026-09, spec 2) ----------------------


def _single_manifest():
    return RunManifest(
        devices={
            "PTX1-POP1": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="single"
            )
        },
        kind="single",
    )


def test_run_kinds_and_roles_constants():
    assert RUN_KINDS == {"single", "migration"}
    assert DEFAULT_KIND == "migration"
    assert "single" in VALID_ROLES


def test_manifest_defaults_to_migration_without_profile_or_group():
    manifest = _manifest()
    assert manifest.kind == "migration"
    assert manifest.profile is None
    assert manifest.group is None


def test_load_without_kind_is_migration(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(), path)
    text = path.read_text(encoding="utf-8").replace("kind: migration\n", "")
    assert "kind" not in text
    path.write_text(text, encoding="utf-8")
    loaded = load_manifest(path)
    assert loaded.kind == "migration"
    assert loaded == _manifest()


def test_roundtrip_single_with_profile_and_group(tmp_path):
    manifest = _single_manifest()
    manifest.profile = "core-only"
    manifest.group = "batch-2026-09"
    manifest.captures.append(CaptureRecord("pre", "PTX1-POP1", None, "a.json", "T1"))
    path = tmp_path / "run.yml"
    save_manifest(manifest, path)
    text = path.read_text(encoding="utf-8")
    assert "kind: single" in text
    assert "profile: core-only" in text
    assert "group: batch-2026-09" in text
    loaded = load_manifest(path)
    assert loaded == manifest
    assert loaded.kind == "single"
    assert loaded.devices["PTX1-POP1"].role == "single"


def test_save_omits_profile_and_group_when_none(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(), path)
    text = path.read_text(encoding="utf-8")
    assert "profile" not in text
    assert "group" not in text
    assert text.splitlines()[0] == "schema_version: 1"
    assert text.splitlines()[1] == "kind: migration"


def test_load_rejects_unknown_kind(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "kind: bulk\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: single}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="neznamy kind 'bulk', ocekavano single nebo migration") as excinfo:
        load_manifest(path)
    assert str(path) in str(excinfo.value)


def test_load_rejects_single_with_two_devices(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "kind: single\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: single}\n"
        "  Y: {host: 1.2.3.5, platform: junos, role: single}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run typu single") as excinfo:
        load_manifest(path)
    assert str(path) in str(excinfo.value)


def test_load_rejects_single_with_old_role(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "kind: single\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: old}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run typu single"):
        load_manifest(path)


def test_load_rejects_migration_with_single_role(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: single}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run typu migration"):
        load_manifest(path)


def test_load_rejects_migration_with_two_old(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: old}\n"
        "  Y: {host: 1.2.3.5, platform: junos, role: old}\n"
        "  Z: {host: 1.2.3.6, platform: junos-evo, role: new}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="jeden box role 'old', nalezeno 2"):
        load_manifest(path)


def test_load_accepts_one_sided_migration(tmp_path):
    # CLI flow: run.yml sepsany rucne zatim jen se starym boxem musi jit nacist
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  MX1-POP1: {host: 172.20.20.4, platform: junos, role: old}\n",
        encoding="utf-8",
    )
    assert load_manifest(path).kind == "migration"


def test_check_kind_devices_direct():
    check_kind_devices("migration", _manifest().devices)
    check_kind_devices("single", _single_manifest().devices)
    with pytest.raises(ValueError, match="neznamy kind"):
        check_kind_devices("bulk", {})
    with pytest.raises(ValueError, match="run typu single"):
        check_kind_devices("single", {})


def test_save_manifest_rejects_invalid_kind_devices(tmp_path):
    path = tmp_path / "run.yml"
    manifest = RunManifest(kind="single", devices={})
    with pytest.raises(ValueError, match="run typu single"):
        save_manifest(manifest, path)
    assert not path.exists()


def test_load_rejects_empty_kind(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "kind: ''\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: single}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="neznamy kind ''"):
        load_manifest(path)
