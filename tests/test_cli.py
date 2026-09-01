import json

import pytest
import yaml

from migration_validator.cli import (
    EXIT_FAILED_CHECKS,
    EXIT_OK,
    EXIT_TOOL_ERROR,
    build_parser,
    main,
)
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import (
    SCHEMA_VERSION,
    CaptureMeta,
    DeviceMeta,
    Snapshot,
    load_snapshot,
    save_snapshot,
)
from migration_validator.runs.manifest import (
    CaptureRecord,
    InterfaceMapping,
    MappingEndpoint,
    RunDevice,
    RunManifest,
    load_manifest,
)
from migration_validator.runs.store import RunStore

NOW = "2026-07-24T11:40:02Z"


def _write(tmp_path, name, address, interface, *, oper="up", pps=400):
    scope = Scope(
        id="svc:L3VPN:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN", "IPVPN", None),
        selectors=Selectors(interfaces=[interface]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(
            started_at=NOW, finished_at=NOW, phase=name,
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts={
            "interfaces": {
                interface: {
                    "admin_status": "up", "oper_status": oper,
                    "input_pps": pps, "output_pps": pps,
                    "input_errors": 0, "output_errors": 0,
                }
            }
        },
        probes={"ping": []},
        scopes=[scope],
        inventory=[],
    )
    path = tmp_path / f"{name}.json"
    save_snapshot(snapshot, path)
    return path


def _write_run_snapshot(store, phase, node, port, address, interface, *, oper="up", pps=400):
    """Ulozi snapshot na misto, kde ho ceka RunStore, a vrati jeho cestu."""
    scope = Scope(
        id="svc:L3VPN:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN", "IPVPN", None),
        selectors=Selectors(interfaces=[interface]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(
            started_at=NOW, finished_at=NOW, phase=phase,
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts={
            "interfaces": {
                interface: {
                    "admin_status": "up", "oper_status": oper,
                    "input_pps": pps, "output_pps": pps,
                    "input_errors": 0, "output_errors": 0,
                }
            }
        },
        probes={"ping": []},
        scopes=[scope],
        inventory=[],
    )
    path = store.snapshot_path(phase, node, port)
    save_snapshot(snapshot, path)
    return path


def test_evaluate_text_output(tmp_path, capsys):
    old = _write(tmp_path, "pre", "172.20.20.4", "ge-0/0/2.113")
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113")

    code = main(["evaluate", "--snapshot", str(new), "--baseline", str(old)])

    assert code == 0
    output = capsys.readouterr().out
    assert "172.20.20.4" in output
    assert "NESPAROVANO" in output


def test_evaluate_json_output_to_file(tmp_path, capsys):
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113")
    target = tmp_path / "result.json"

    code = main(["evaluate", "--snapshot", str(new), "--format", "json",
                 "--output", str(target)])

    assert code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["baseline"] is None


def test_exit_code_1_when_fail_present(tmp_path):
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113", oper="down")
    assert main(["evaluate", "--snapshot", str(new)]) == 1


def test_warn_does_not_change_exit_code(tmp_path):
    old = _write(tmp_path, "pre", "172.20.20.4", "ge-0/0/2.113", pps=400)
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113", pps=50)
    assert main(["evaluate", "--snapshot", str(new), "--baseline", str(old)]) == 0


def test_warn_as_error_flag(tmp_path):
    old = _write(tmp_path, "pre", "172.20.20.4", "ge-0/0/2.113", pps=400)
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113", pps=50)
    code = main(
        ["evaluate", "--snapshot", str(new), "--baseline", str(old), "--warn-as-error"]
    )
    assert code == 1


def test_missing_snapshot_file_is_tool_error(tmp_path, capsys):
    code = main(["evaluate", "--snapshot", str(tmp_path / "nope.json")])
    assert code == 2
    assert "nope.json" in capsys.readouterr().err


def test_wrong_schema_version_is_tool_error(tmp_path, capsys):
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")

    code = main(["evaluate", "--snapshot", str(path)])

    assert code == 2
    assert "99" in capsys.readouterr().err


def test_incomplete_snapshot_is_tool_error(tmp_path, capsys):
    # schema_version musi souhlasit, jinak spadne na kontrole verze drive,
    # nez se stihne poznat, ze chybi device/capture.
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION}), encoding="utf-8")

    code = main(["evaluate", "--snapshot", str(path)])

    assert code == 2
    err = capsys.readouterr().err
    assert "chyba" in err
    assert str(path) in err


def test_checks_subcommand_lists_registry(capsys):
    assert main(["checks"]) == 0
    output = capsys.readouterr().out
    assert "interface_traffic" in output
    assert "evpn_esi_status" in output


def test_checks_subcommand_json(capsys):
    assert main(["checks", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == "ping_reachability" for item in payload)


def test_match_subcommand_reports_pairs_and_unmatched(tmp_path, capsys):
    old = _write(tmp_path, "pre", "172.20.20.4", "ge-0/0/2.113")
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113")

    code = main(["match", "--baseline", str(old), "--subject", str(new)])

    assert code == 0
    output = capsys.readouterr().out
    assert "description+service_type" in output
    assert "svc:L3VPN:IPVPN" in output


def test_status_filter(tmp_path, capsys):
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113", oper="down")

    main(["evaluate", "--snapshot", str(new), "--status", "fail"])

    output = capsys.readouterr().out
    assert "L3VPN" in output


# --- capture --run rezim (faze 4, task 4) ---------------------------------


def _snapshot(*, host, platform="junos", phase=None):
    scope = Scope(
        id="svc:L3VPN:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN", "IPVPN", None),
        selectors=Selectors(interfaces=[]),
    )
    return Snapshot(
        device=DeviceMeta(address=host, platform=platform),
        capture=CaptureMeta(
            started_at=NOW, finished_at=NOW, phase=phase, collectors={}
        ),
        facts={},
        probes={"ping": []},
        scopes=[scope],
        inventory=[],
    )


def _fake_capture(monkeypatch, platform="junos"):
    calls = {}

    def fake(host, **kwargs):
        calls["host"] = host
        calls.update(kwargs)
        return _snapshot(host=host, platform=platform, phase=kwargs.get("phase"))

    monkeypatch.setattr("migration_validator.cli.api.capture", fake)
    return calls


def _refuse_capture(monkeypatch):
    def fake(host, **kwargs):
        raise AssertionError("capture nemel byt volan pred validaci")

    monkeypatch.setattr("migration_validator.cli.api.capture", fake)


def test_capture_run_rejects_output(tmp_path, monkeypatch, capsys):
    _refuse_capture(monkeypatch)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.4",
            "--phase", "pre",
            "--output", "y.json",
        ]
    )

    assert code == 2
    assert "--run" in capsys.readouterr().err


def test_capture_run_requires_known_phase(tmp_path, monkeypatch, capsys):
    _refuse_capture(monkeypatch)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.4",
            "--phase", "fialova",
            "--inventory", "tests/fixtures/172.20.20.4.yml",
        ]
    )

    assert code == 2


def test_capture_run_creates_manifest_and_snapshot(tmp_path, monkeypatch):
    _fake_capture(monkeypatch)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.4",
            "--phase", "pre",
            "--inventory", "tests/fixtures/172.20.20.4.yml",
        ]
    )

    assert code == 0
    manifest_path = tmp_path / "mig01" / "run.yml"
    assert manifest_path.exists()
    manifest = load_manifest(manifest_path)
    assert manifest.devices["172.20.20.4"].role == "old"
    assert len(manifest.captures) == 1
    record = manifest.captures[0]
    assert record.phase == "pre"
    assert record.port is None
    assert record.snapshot == "snapshot_pre_172.20.20.4_all.json"

    snapshot_path = tmp_path / "mig01" / record.snapshot
    assert snapshot_path.exists()
    load_snapshot(snapshot_path)


def test_capture_run_port_mode_and_maps_to(tmp_path, monkeypatch):
    _fake_capture(monkeypatch)

    code_pre = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.4",
            "--phase", "pre",
            "--port", "ge-0/0/0",
            "--inventory", "tests/fixtures/172.20.20.4.yml",
        ]
    )
    assert code_pre == 0

    code_post = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.5",
            "--phase", "post",
            "--port", "et-0/0/0",
            "--maps-to", "172.20.20.4:ge-0/0/0",
            "--inventory", "tests/fixtures/172.20.20.5.yml",
        ]
    )
    assert code_post == 0

    manifest = load_manifest(tmp_path / "mig01" / "run.yml")
    assert len(manifest.interface_mapping) == 1
    mapping = manifest.interface_mapping[0]
    assert mapping.old.node == "172.20.20.4"
    assert mapping.old.port == "ge-0/0/0"
    assert mapping.new.node == "172.20.20.5"
    assert mapping.new.port == "et-0/0/0"

    post_record = manifest.find_capture("post", "172.20.20.5", "et-0/0/0")
    assert post_record is not None


def test_capture_run_post_with_pre_snapshot_passes_baselines(tmp_path, monkeypatch):
    calls = _fake_capture(monkeypatch)
    store = RunStore(tmp_path, "mig01")

    manifest = RunManifest(
        devices={
            "172.20.20.4": RunDevice(host="172.20.20.4", platform="junos", role="old"),
            "172.20.20.5": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="new"
            ),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="172.20.20.4", port="ge-0/0/0"),
                new=MappingEndpoint(node="172.20.20.5", port="et-0/0/0"),
            )
        ],
    )
    pre_path = _write_run_snapshot(
        store, "pre", "172.20.20.4", "ge-0/0/0", "172.20.20.4", "ge-0/0/2.113"
    )
    manifest.record_capture(
        CaptureRecord(
            phase="pre",
            device="172.20.20.4",
            port="ge-0/0/0",
            snapshot=pre_path.name,
            taken=NOW,
        )
    )
    store.save(manifest)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.5",
            "--phase", "post",
            "--port", "et-0/0/0",
            "--inventory", "tests/fixtures/172.20.20.5.yml",
        ]
    )

    assert code == 0
    assert calls["baselines"] is not None
    assert len(calls["baselines"]) == 1
    assert calls["baselines"][0].device.address == "172.20.20.4"


def test_capture_run_post_falls_back_to_whole_box_pre_snapshot(tmp_path, monkeypatch):
    """Bez port-parovani se pouzije celoboxovy pre snimek stareho boxu.

    Manifest tu zamerne nema zadny interface_mapping - paired_old() vrati
    None a musi se pouzit fallback pres device_with_role("old") +
    find_capture("pre", old_node, None).
    """
    calls = _fake_capture(monkeypatch)
    store = RunStore(tmp_path, "mig01")

    manifest = RunManifest(
        devices={
            "172.20.20.4": RunDevice(host="172.20.20.4", platform="junos", role="old"),
            "172.20.20.5": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="new"
            ),
        },
    )
    pre_path = _write_run_snapshot(
        store, "pre", "172.20.20.4", None, "172.20.20.4", "ge-0/0/2.113"
    )
    manifest.record_capture(
        CaptureRecord(
            phase="pre",
            device="172.20.20.4",
            port=None,
            snapshot=pre_path.name,
            taken=NOW,
        )
    )
    store.save(manifest)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.5",
            "--phase", "post",
            "--port", "et-0/0/0",
            "--inventory", "tests/fixtures/172.20.20.5.yml",
        ]
    )

    assert code == 0
    assert calls["baselines"] is not None
    assert len(calls["baselines"]) == 1
    assert calls["baselines"][0].device.address == "172.20.20.4"


def test_capture_run_post_without_pre_snapshot_has_no_baseline(tmp_path, monkeypatch, capsys):
    calls = _fake_capture(monkeypatch)
    store = RunStore(tmp_path, "mig01")

    manifest = RunManifest(
        devices={
            "172.20.20.4": RunDevice(host="172.20.20.4", platform="junos", role="old"),
            "172.20.20.5": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="new"
            ),
        },
    )
    store.save(manifest)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.5",
            "--phase", "post",
            "--port", "et-0/0/0",
            "--inventory", "tests/fixtures/172.20.20.5.yml",
        ]
    )

    assert code == 0
    assert calls["baselines"] is None
    assert "pre snimek nenalezen" in capsys.readouterr().err


def test_capture_run_post_on_lag_passes_union_of_mapped_pre_snapshots(
    tmp_path, monkeypatch
):
    """run.yml se 2 mappingy ge-0/0/4,ge-0/0/5 -> ae0 a obema pre snimky:
    post capture ae0 dostane baselines=[pre4, pre5] (poradi mappingu)."""
    calls = _fake_capture(monkeypatch)
    store = RunStore(tmp_path, "mig01")

    manifest = RunManifest(
        devices={
            "172.20.20.4": RunDevice(host="172.20.20.4", platform="junos", role="old"),
            "172.20.20.5": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="new"
            ),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="172.20.20.4", port="ge-0/0/4"),
                new=MappingEndpoint(node="172.20.20.5", port="ae0"),
            ),
            InterfaceMapping(
                old=MappingEndpoint(node="172.20.20.4", port="ge-0/0/5"),
                new=MappingEndpoint(node="172.20.20.5", port="ae0"),
            ),
        ],
    )
    pre4_path = _write_run_snapshot(
        store, "pre", "172.20.20.4", "ge-0/0/4", "172.20.20.4", "ge-0/0/4.0"
    )
    manifest.record_capture(
        CaptureRecord(
            phase="pre",
            device="172.20.20.4",
            port="ge-0/0/4",
            snapshot=pre4_path.name,
            taken=NOW,
        )
    )
    pre5_path = _write_run_snapshot(
        store, "pre", "172.20.20.4", "ge-0/0/5", "172.20.20.4", "ge-0/0/5.0"
    )
    manifest.record_capture(
        CaptureRecord(
            phase="pre",
            device="172.20.20.4",
            port="ge-0/0/5",
            snapshot=pre5_path.name,
            taken=NOW,
        )
    )
    store.save(manifest)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.5",
            "--phase", "post",
            "--port", "ae0",
            "--inventory", "tests/fixtures/172.20.20.5.yml",
        ]
    )

    assert code == 0
    assert len(calls["baselines"]) == 2
    assert [b.device.address for b in calls["baselines"]] == [
        "172.20.20.4",
        "172.20.20.4",
    ]


def test_capture_run_missing_inventory_hint(tmp_path, monkeypatch, capsys):
    _refuse_capture(monkeypatch)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.4",
            "--phase", "pre",
        ]
    )

    assert code == 2
    assert "--parse-services" in capsys.readouterr().err


def test_capture_run_second_capture_replaces_record(tmp_path, monkeypatch):
    _fake_capture(monkeypatch)

    args = [
        "capture",
        "--run", "mig01",
        "--run-root", str(tmp_path),
        "--device", "172.20.20.4",
        "--phase", "post",
        "--inventory", "tests/fixtures/172.20.20.4.yml",
    ]

    assert main(args) == 0
    assert main(args) == 0

    manifest = load_manifest(tmp_path / "mig01" / "run.yml")
    assert len(manifest.captures) == 1


def test_pre_capture_refuses_overwrite_without_flag(tmp_path, monkeypatch, capsys):
    """Druhy pre capture stejneho node/port konci chybou s navodem;
    s --overwrite probehne. post se prepisuje bez flagu (dnesni chovani)."""
    _fake_capture(monkeypatch)
    args = [
        "capture", "--run", "mig01", "--run-root", str(tmp_path),
        "--device", "172.20.20.4", "--phase", "pre",
        "--inventory", "tests/fixtures/172.20.20.4.yml",
    ]
    assert main(args) == 0                       # vlna 1: pre vznikne
    assert main(args) == 2                       # opakovani: ToolError
    err = capsys.readouterr().err
    assert "pre snimek uz existuje" in err
    assert "snapshot_pre_172.20.20.4_all.json" in err
    assert main(args + ["--overwrite"]) == 0     # explicitni prepis projde


def test_post_capture_can_be_overwritten_without_flag(tmp_path, monkeypatch):
    """post se prepisuje bez flagu (dnesni chovani)."""
    _fake_capture(monkeypatch)
    args = [
        "capture", "--run", "mig01", "--run-root", str(tmp_path),
        "--device", "172.20.20.5", "--phase", "post",
        "--inventory", "tests/fixtures/172.20.20.5.yml",
    ]
    assert main(args) == 0     # prvni post
    assert main(args) == 0     # druhy post bez flagu - ok


def test_capture_run_parse_services_generates_inventory(tmp_path, monkeypatch):
    from contextlib import contextmanager

    _fake_capture(monkeypatch)

    calls = {}

    @contextmanager
    def fake_connect(options):
        yield object()

    def fake_detect_platform(device):
        return "junos"

    def fake_generate_inventory(device, platform, output_path, port):
        calls["platform"] = platform
        calls["output_path"] = output_path
        calls["port"] = port
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("schema_version: 6\ndevice: x\ninterfaces: []\n")

    monkeypatch.setattr("migration_validator.runs.orchestrate.connect", fake_connect)
    monkeypatch.setattr(
        "migration_validator.runs.orchestrate.detect_platform", fake_detect_platform
    )
    monkeypatch.setattr(
        "migration_validator.runs.orchestrate.generate_inventory", fake_generate_inventory
    )

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.4",
            "--phase", "pre",
            "--port", "ge-0/0/0",
            "--parse-services",
        ]
    )

    assert code == 0
    assert calls["platform"] == "junos"
    assert calls["port"] == "ge-0/0/0"
    assert calls["output_path"] == tmp_path / "mig01" / "inventory_172.20.20.4_ge_0_0_0.yml"
    assert calls["output_path"].exists()


def test_record_command_pouziva_rpc_calls_ne_rpc_names_a_jedno_kwargs(
    tmp_path, monkeypatch
):
    """_cmd_record musi jit pres rpc_calls(), ne rpc_names()+jedno
    rpc_kwargs() - base.py:47 rika, ze rpc_calls() je autorita "pro record
    i pro vice-RPC collect". Kolektory jako routes (protocol=static pak
    protocol=aggregate) nebo interfaces (extensive pak terse) maji druhe
    RPC s JINYMI kwargs nez prvni; rpc_names()+jedno rpc_kwargs() by obe
    volani poslalo se stejnymi kwargs jako prvni, takze <name>.2.xml by
    tise byla kopie <name>.xml misto druhe varianty.
    """
    from contextlib import contextmanager

    import migration_validator.collectors.all  # noqa: F401  (registrace)
    from lxml import etree

    class _FakeRpc:
        def __getattr__(self, rpc_name):
            def call(**kwargs):
                marker = f"{rpc_name}:" + ",".join(
                    f"{k}={v}" for k, v in sorted(kwargs.items())
                )
                return etree.fromstring(f"<r><marker>{marker}</marker></r>".encode())

            return call

    class _FakeDevice:
        def __init__(self):
            self.rpc = _FakeRpc()

    @contextmanager
    def fake_connect(options):
        yield _FakeDevice()

    def fake_detect_platform(device):
        return "junos-evo"

    monkeypatch.setattr("migration_validator.cli.connect", fake_connect)
    monkeypatch.setattr("migration_validator.cli.detect_platform", fake_detect_platform)

    code = main(
        [
            "record",
            "--device", "172.20.20.4",
            "--output-dir", str(tmp_path),
        ]
    )

    assert code == 0

    target = tmp_path / "junos-evo"
    routes_xml = (target / "routes.xml").read_bytes()
    routes_2_xml = (target / "routes.2.xml").read_bytes()

    # Obe fixtures existuji, ale nesmi byt totozne - druhy pruchod je
    # protocol=aggregate, ne opakovani prvniho protocol=static.
    assert routes_xml != routes_2_xml
    assert b"protocol=static" in routes_xml
    assert b"protocol=aggregate" in routes_2_xml

    interfaces_xml = (target / "interfaces.xml").read_bytes()
    interfaces_2_xml = (target / "interfaces.2.xml").read_bytes()
    assert interfaces_xml != interfaces_2_xml
    assert b"extensive=True" in interfaces_xml
    assert b"terse=True" in interfaces_2_xml


def test_parse_services_requires_run(monkeypatch, capsys):
    _refuse_capture(monkeypatch)

    code = main(
        [
            "capture",
            "--device", "172.20.20.4",
            "--phase", "pre",
            "--output", "y.json",
            "--parse-services",
        ]
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "--parse-services" in err
    assert "--run" in err


def test_parse_services_regenerates_existing_inventory_and_prints_delta(
    tmp_path, monkeypatch, capsys
):
    """--parse-services prepise existujici per-port inventory a vypise deltu."""
    from contextlib import contextmanager

    _fake_capture(monkeypatch)

    @contextmanager
    def fake_connect(options):
        yield object()

    def fake_detect_platform(device):
        return "junos"

    monkeypatch.setattr("migration_validator.runs.orchestrate.connect", fake_connect)
    monkeypatch.setattr(
        "migration_validator.runs.orchestrate.detect_platform", fake_detect_platform
    )

    store = RunStore(tmp_path, "mig01")
    inventory_path = store.inventory_path("172.20.20.5", "ae0")
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(
        "schema_version: 6\ndevice: x\ninterfaces:\n"
        "  - interface: ae0.15\n"
    )

    def fake_generate_inventory(device, platform, output_path, port):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "schema_version: 6\ndevice: x\ninterfaces:\n"
            "  - interface: ae0.15\n"
            "  - interface: ae0.16\n"
        )

    monkeypatch.setattr(
        "migration_validator.runs.orchestrate.generate_inventory",
        fake_generate_inventory,
    )

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.5",
            "--phase", "post",
            "--port", "ae0",
            "--parse-services",
        ]
    )

    assert code == 0
    raw = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    assert len(raw["interfaces"]) == 2

    out = capsys.readouterr().out
    assert "inventory pregenerovana" in out
    assert "+1 nove, -0 odebrane" in out
    assert "generovani se preskakuje" not in out


def test_capture_run_resolves_existing_node_name(tmp_path, monkeypatch):
    # run.yml uz zna 172.20.20.4 pod uzlem "MX1-POP1" (jmeno z parseru
    # konfigurace, ne IP) - capture ho musi dohledat pres node_for_host
    # a pouzit "MX1-POP1" vsude (soubory, CaptureRecord.device), ne IP.
    store = RunStore(tmp_path, "mig01")
    manifest = RunManifest(
        devices={
            "MX1-POP1": RunDevice(host="172.20.20.4", platform="junos", role="old")
        }
    )
    store.save(manifest)

    _fake_capture(monkeypatch)

    code = main(
        [
            "capture",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--device", "172.20.20.4",
            "--phase", "pre",
            "--port", "ge-0/0/0",
            "--inventory", "tests/fixtures/172.20.20.4.yml",
        ]
    )

    assert code == 0

    manifest = load_manifest(tmp_path / "mig01" / "run.yml")

    # zadny novy zaznam keyed IP adresou - jen puvodni "MX1-POP1"
    assert list(manifest.devices.keys()) == ["MX1-POP1"]
    assert "172.20.20.4" not in manifest.devices

    record = manifest.find_capture("pre", "MX1-POP1", "ge-0/0/0")
    assert record is not None
    assert record.snapshot == "snapshot_pre_MX1-POP1_ge_0_0_0.json"

    snapshot_path = tmp_path / "mig01" / "snapshot_pre_MX1-POP1_ge_0_0_0.json"
    assert snapshot_path.exists()


def _base_run_manifest():
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
    )


def test_evaluate_run_pairs_and_exit_code(tmp_path, capsys):
    store = RunStore(tmp_path, "mig01")
    manifest = _base_run_manifest()

    pre_path = _write_run_snapshot(
        store, "pre", "MX1-POP1", "ge-0/0/0", "172.20.20.4", "ge-0/0/0.113"
    )
    post_path = _write_run_snapshot(
        store, "post", "PTX1-POP1", "et-0/0/0", "172.20.20.5", "et-0/0/0.113"
    )
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", pre_path.name, NOW)
    )
    manifest.record_capture(
        CaptureRecord("post", "PTX1-POP1", "et-0/0/0", post_path.name, NOW)
    )
    store.save(manifest)

    code = main(["evaluate", "--run", "mig01", "--run-root", str(tmp_path)])

    output = capsys.readouterr().out
    assert (
        f"=== {post_path.name} vs {pre_path.name}"
        " [krok MX1-POP1:ge-0/0/0 -> PTX1-POP1:et-0/0/0] ===" in output
    )
    # oba snimky maji sluzbu ve stejnem stavu (up) -> sluzba PASS -> exit 0
    assert code == EXIT_OK


def test_evaluate_run_exit_code_is_worst_across_evaluations(tmp_path, capsys):
    store = RunStore(tmp_path, "mig01")
    manifest = _base_run_manifest()
    manifest.add_mapping(
        MappingEndpoint(node="MX1-POP1", port="ge-0/0/1"),
        MappingEndpoint(node="PTX1-POP1", port="et-0/0/1"),
    )

    pre_ok = _write_run_snapshot(
        store, "pre", "MX1-POP1", "ge-0/0/0", "172.20.20.4", "ge-0/0/0.113"
    )
    post_ok = _write_run_snapshot(
        store, "post", "PTX1-POP1", "et-0/0/0", "172.20.20.5", "et-0/0/0.113"
    )
    pre_down = _write_run_snapshot(
        store, "pre", "MX1-POP1", "ge-0/0/1", "172.20.20.4", "ge-0/0/1.114", oper="up"
    )
    post_down = _write_run_snapshot(
        store, "post", "PTX1-POP1", "et-0/0/1", "172.20.20.5", "et-0/0/1.114",
        oper="down",
    )
    manifest.record_capture(CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", pre_ok.name, NOW))
    manifest.record_capture(CaptureRecord("post", "PTX1-POP1", "et-0/0/0", post_ok.name, NOW))
    manifest.record_capture(CaptureRecord("pre", "MX1-POP1", "ge-0/0/1", pre_down.name, NOW))
    manifest.record_capture(CaptureRecord("post", "PTX1-POP1", "et-0/0/1", post_down.name, NOW))
    store.save(manifest)

    code = main(["evaluate", "--run", "mig01", "--run-root", str(tmp_path)])

    output = capsys.readouterr().out
    # obe evaluace se skutecne provedly - hlavicky obou jsou ve vystupu
    assert (
        f"=== {post_ok.name} vs {pre_ok.name}"
        " [krok MX1-POP1:ge-0/0/0 -> PTX1-POP1:et-0/0/0] ===" in output
    )
    assert (
        f"=== {post_down.name} vs {pre_down.name}"
        " [krok MX1-POP1:ge-0/0/1 -> PTX1-POP1:et-0/0/1] ===" in output
    )
    # jedna sluzba spadla (down interface) -> nejhorsi kod vyhrava, druha
    # zdrava evaluace ho neprebiji zpatky na OK
    assert code == EXIT_FAILED_CHECKS


def test_evaluate_run_without_baseline_still_runs_and_warns(tmp_path, capsys):
    store = RunStore(tmp_path, "mig01")
    manifest = _base_run_manifest()

    post_path = _write_run_snapshot(
        store, "post", "PTX1-POP1", "et-0/0/0", "172.20.20.5", "et-0/0/0.113"
    )
    manifest.record_capture(
        CaptureRecord("post", "PTX1-POP1", "et-0/0/0", post_path.name, NOW)
    )
    store.save(manifest)

    code = main(["evaluate", "--run", "mig01", "--run-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert (
        f"=== {post_path.name} vs bez baseline"
        " [krok MX1-POP1:ge-0/0/0 -> PTX1-POP1:et-0/0/0] ===" in captured.out
    )
    assert "chybi pre snimek MX1-POP1:ge-0/0/0" in captured.err
    # subject se sam o sobe vyhodnoti (bez baseline porovnani neni fail)
    assert code == EXIT_OK
    assert "L3VPN" in captured.out


def test_evaluate_run_missing_file_lists_names(tmp_path, capsys):
    store = RunStore(tmp_path, "mig01")
    manifest = _base_run_manifest()
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "snapshot_pre_MX1-POP1_ge_0_0_0.json", NOW)
    )
    manifest.record_capture(
        CaptureRecord(
            "post", "PTX1-POP1", "et-0/0/0", "snapshot_post_PTX1-POP1_et_0_0_0.json", NOW
        )
    )
    store.save(manifest)
    # zadny snapshot soubor neni na disku

    code = main(["evaluate", "--run", "mig01", "--run-root", str(tmp_path)])

    assert code == EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "snapshot_pre_MX1-POP1_ge_0_0_0.json" in err
    assert "snapshot_post_PTX1-POP1_et_0_0_0.json" in err


def test_evaluate_run_ports_filter(tmp_path, capsys):
    store = RunStore(tmp_path, "mig01")
    manifest = _base_run_manifest()
    manifest.add_mapping(
        MappingEndpoint(node="MX1-POP1", port="ge-0/0/1"),
        MappingEndpoint(node="PTX1-POP1", port="et-0/0/1"),
    )

    pre0 = _write_run_snapshot(
        store, "pre", "MX1-POP1", "ge-0/0/0", "172.20.20.4", "ge-0/0/0.113"
    )
    post0 = _write_run_snapshot(
        store, "post", "PTX1-POP1", "et-0/0/0", "172.20.20.5", "et-0/0/0.113"
    )
    pre1 = _write_run_snapshot(
        store, "pre", "MX1-POP1", "ge-0/0/1", "172.20.20.4", "ge-0/0/1.113"
    )
    post1 = _write_run_snapshot(
        store, "post", "PTX1-POP1", "et-0/0/1", "172.20.20.5", "et-0/0/1.113"
    )
    manifest.record_capture(CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", pre0.name, NOW))
    manifest.record_capture(CaptureRecord("post", "PTX1-POP1", "et-0/0/0", post0.name, NOW))
    manifest.record_capture(CaptureRecord("pre", "MX1-POP1", "ge-0/0/1", pre1.name, NOW))
    manifest.record_capture(CaptureRecord("post", "PTX1-POP1", "et-0/0/1", post1.name, NOW))
    store.save(manifest)

    code = main(
        [
            "evaluate", "--run", "mig01", "--run-root", str(tmp_path),
            "--ports", "ge-0/0/1",
        ]
    )

    output = capsys.readouterr().out
    assert post1.name in output
    assert post0.name not in output
    # oba pary maji shodny stav (up) na obou stranach -> PASS -> exit 0
    assert code == EXIT_OK


def _lag_run_manifest():
    """MX1-POP1 (old) + PTX1-POP1 (new); dva stare porty mapovane na ae0."""
    manifest = RunManifest(
        devices={
            "MX1-POP1": RunDevice(host="172.20.20.4", platform="junos", role="old"),
            "PTX1-POP1": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="new"
            ),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/4"),
                new=MappingEndpoint(node="PTX1-POP1", port="ae0"),
            ),
            InterfaceMapping(
                old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/5"),
                new=MappingEndpoint(node="PTX1-POP1", port="ae0"),
            ),
        ],
    )
    return manifest


def _write_lag_run(tmp_path):
    """Ulozi run se 2 mappingy na ae0 + pre4/pre5/post snimky na disku."""
    store = RunStore(tmp_path, "mig01")
    manifest = _lag_run_manifest()

    pre4 = _write_run_snapshot(
        store, "pre", "MX1-POP1", "ge-0/0/4", "172.20.20.4", "ge-0/0/4.113"
    )
    pre5 = _write_run_snapshot(
        store, "pre", "MX1-POP1", "ge-0/0/5", "172.20.20.4", "ge-0/0/5.113"
    )
    post = _write_run_snapshot(
        store, "post", "PTX1-POP1", "ae0", "172.20.20.5", "ae0.113"
    )
    manifest.record_capture(CaptureRecord("pre", "MX1-POP1", "ge-0/0/4", pre4.name, NOW))
    manifest.record_capture(CaptureRecord("pre", "MX1-POP1", "ge-0/0/5", pre5.name, NOW))
    manifest.record_capture(CaptureRecord("post", "PTX1-POP1", "ae0", post.name, NOW))
    store.save(manifest)
    return store


def test_evaluate_run_emits_one_report_per_step_with_header(tmp_path, capsys):
    """run.yml se 2 mappingy na ae0 + pre4/pre5/post snimky na disku:
    evaluate --run vypise 2 reporty, kazdy s [krok ...] sve dvojice."""
    _write_lag_run(tmp_path)

    code = main(["evaluate", "--run", "mig01", "--run-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "[krok MX1-POP1:ge-0/0/4 -> PTX1-POP1:ae0]" in out
    assert "[krok MX1-POP1:ge-0/0/5 -> PTX1-POP1:ae0]" in out
    assert out.count("=== ") == 2


def test_evaluate_run_json_output_carries_step_and_excluded_services(tmp_path, capsys):
    _write_lag_run(tmp_path)

    code = main(
        [
            "evaluate", "--run", "mig01", "--run-root", str(tmp_path),
            "--format", "json",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0

    blocks = [block for block in out.split("=== ") if block.strip()]
    # kazdy blok zacina "<hlavicka> ===\n{...json...}\n"
    payloads = [
        json.loads(block.split("===\n", 1)[1]) for block in blocks
    ]
    assert len(payloads) == 2
    for payload in payloads:
        assert "step" in payload
        assert "excluded_services" in payload


def test_status_run_lists_two_rows_for_lag_mappings_to_same_new_port(tmp_path, capsys):
    """Regresni zamek N:1 - status --run uz dnes iteruje mappingy, ne porty."""
    _write_lag_run(tmp_path)

    code = main(["status", "--run", "mig01", "--run-root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "MX1-POP1:ge-0/0/4" in out
    assert "MX1-POP1:ge-0/0/5" in out
    assert out.count("PTX1-POP1:ae0") == 2


def test_evaluate_run_rejects_snapshot_combo(tmp_path):
    code = main(
        [
            "evaluate",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--snapshot", "x.json",
        ]
    )
    assert code == EXIT_TOOL_ERROR


def test_status_run_overview(tmp_path, capsys):
    store = RunStore(tmp_path, "mig01")
    manifest = _base_run_manifest()
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "snapshot_pre_MX1-POP1_ge_0_0_0.json", NOW)
    )
    manifest.record_capture(
        CaptureRecord(
            "post", "PTX1-POP1", "et-0/0/0", "snapshot_post_PTX1-POP1_et_0_0_0.json", NOW
        )
    )
    # celoboxove capturey - MX1-POP1 (old) a PTX1-POP1 (new)
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", None, "snapshot_pre_MX1-POP1_all.json", NOW)
    )
    manifest.record_capture(
        CaptureRecord("post", "PTX1-POP1", None, "snapshot_post_PTX1-POP1_all.json", NOW)
    )
    store.save(manifest)

    code = main(["status", "--run", "mig01", "--run-root", str(tmp_path)])

    assert code == EXIT_OK
    lines = capsys.readouterr().out.splitlines()

    def find_line(fragment):
        matches = [line for line in lines if fragment in line]
        assert len(matches) == 1, f"ocekavan jeden radek s '{fragment}', nalezeno {matches}"
        return matches[0]

    mapping_row = find_line("MX1-POP1:ge-0/0/0")
    assert "PTX1-POP1:et-0/0/0" in mapping_row
    # sloupce v poradi PRE POST ROLLBACK: pre a post jsou zaznamenane, rollback ne
    columns = mapping_row.split()
    assert columns[-3:] == ["ano", "ano", "-"]

    old_whole_box_row = find_line("MX1-POP1:all")
    assert old_whole_box_row.split()[0] == "MX1-POP1:all"
    assert old_whole_box_row.split()[1] == "-"  # old box nema new sloupec

    new_whole_box_row = find_line("PTX1-POP1:all")
    assert new_whole_box_row.split()[0] == "-"  # new box nema old sloupec
    assert "PTX1-POP1:all" in new_whole_box_row


# --- final review fixes (faze 4, fix-wave) ---------------------------------


def test_evaluate_run_nonexistent_run_is_tool_error(tmp_path, capsys):
    code = main(["evaluate", "--run", "neexistuje", "--run-root", str(tmp_path)])

    assert code == EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "run 'neexistuje' neexistuje" in err
    assert "run.yml" in err


def test_status_run_nonexistent_run_is_tool_error(tmp_path, capsys):
    code = main(["status", "--run", "typo01", "--run-root", str(tmp_path)])

    assert code == EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "run 'typo01' neexistuje" in err
    assert "run.yml" in err


def test_evaluate_run_with_manifest_but_no_evaluations_prints_info(tmp_path, capsys):
    store = RunStore(tmp_path, "mig01")
    manifest = _base_run_manifest()
    # jen pre capture - plan_evaluations nevyrobi zadnou evaluaci
    pre_path = _write_run_snapshot(
        store, "pre", "MX1-POP1", "ge-0/0/0", "172.20.20.4", "ge-0/0/0.113"
    )
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", pre_path.name, NOW)
    )
    store.save(manifest)

    code = main(["evaluate", "--run", "mig01", "--run-root", str(tmp_path)])

    assert code == EXIT_OK
    err = capsys.readouterr().err
    assert "zadne snimky k vyhodnoceni" in err


def test_evaluate_run_rejects_baseline_combo(tmp_path):
    store = RunStore(tmp_path, "mig01")
    store.save(_base_run_manifest())

    code = main(
        [
            "evaluate",
            "--run", "mig01",
            "--run-root", str(tmp_path),
            "--baseline", "x.json",
        ]
    )
    assert code == EXIT_TOOL_ERROR


def test_capture_maps_to_outside_run_is_tool_error(monkeypatch, capsys):
    _refuse_capture(monkeypatch)

    code = main(
        [
            "capture",
            "--device", "172.20.20.4",
            "--phase", "pre",
            "--output", "y.json",
            "--maps-to", "172.20.20.5:et-0/0/0",
        ]
    )

    assert code == EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "--maps-to" in err
    assert "--run" in err


# --- color flag (faze 5, task 3) -----

def test_evaluate_color_flag_forces_ansi(tmp_path, capsys):
    path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    code = main(["evaluate", "--snapshot", str(path), "--color"])
    assert code == EXIT_OK
    assert "\x1b[32mPASS\x1b[0m" in capsys.readouterr().out


def test_evaluate_defaults_to_plain_when_not_a_tty(tmp_path, capsys):
    # capsys nahrazuje stdout ne-TTY objektem - autodetekce musi
    # barvy vypnout bez jakehokoli prepinace.
    path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    code = main(["evaluate", "--snapshot", str(path)])
    assert code == EXIT_OK
    assert "\x1b[" not in capsys.readouterr().out


def test_evaluate_color_and_no_color_are_exclusive(tmp_path, capsys):
    path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    with pytest.raises(SystemExit):
        main(["evaluate", "--snapshot", str(path), "--color", "--no-color"])


# --- profil, auth soubor, service-types (task 3) -----

def test_evaluate_prijima_profile_i_config_alias():
    parser = build_parser()
    args = parser.parse_args(["evaluate", "--snapshot", "s.json", "--profile", "p.yml"])
    assert args.profile == "p.yml"
    args = parser.parse_args(["evaluate", "--snapshot", "s.json", "--config", "p.yml"])
    assert args.profile == "p.yml"


def test_service_types_carka_bez_typu_je_chyba(tmp_path, capsys):
    """--service-types "," parsuje na [] - ticha shoda by odfiltrovala
    kazdou sluzbu bez jedineho `typy=` kriteria v reportu."""
    path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    code = main(
        ["evaluate", "--snapshot", str(path), "--service-types", ","]
    )
    assert code == EXIT_TOOL_ERROR
    assert "zadny platny typ v --service-types" in capsys.readouterr().err


def test_service_types_prazdny_retezec_je_chyba(tmp_path, capsys):
    """--service-types "" se drive tise vracelo na profil - ted je to
    chyba, protoze prazdny flag neni totez jako flag nezadany."""
    path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    code = main(["evaluate", "--snapshot", str(path), "--service-types", ""])
    assert code == EXIT_TOOL_ERROR
    assert "zadny platny typ v --service-types" in capsys.readouterr().err


def test_evaluate_s_profilem_bez_service_types_propise_jmeno_do_hlavicky(
    tmp_path, capsys
):
    """End-to-end: profil bez sekce service_types (tedy zadny --service-types
    filtr) porad musi rict v hlavicce reportu, pod jakym profilem beh
    vznikl - basename souboru, ne cela cesta."""
    snapshot_path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    profile_path = tmp_path / "core-only.yml"
    profile_path.write_text("profile:\n  ping_count: 3\n", encoding="utf-8")

    code = main(
        ["evaluate", "--snapshot", str(snapshot_path), "--profile", str(profile_path)]
    )

    assert code == EXIT_OK
    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line.endswith("[profil core-only.yml]")


def test_evaluate_bez_profilu_neprida_zavorku_do_hlavicky(tmp_path, capsys):
    snapshot_path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")

    code = main(["evaluate", "--snapshot", str(snapshot_path)])

    assert code == EXIT_OK
    first_line = capsys.readouterr().out.splitlines()[0]
    assert "[profil" not in first_line


def test_flag_prebiji_settings():
    from migration_validator.auth import ConnectionSettings
    from migration_validator.cli import _connection_options, build_parser

    settings = ConnectionSettings(username="rmohyla", netconf_port=2222)
    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--username", "jiny", "--output", "o.json"]
    )
    options = _connection_options(args, settings)
    assert options.username == "jiny"          # flag vyhrava
    assert options.port == 2222                # settings, flag nezadany


def test_settings_prebiji_default():
    from migration_validator.auth import ConnectionSettings
    from migration_validator.cli import _connection_options, build_parser

    args = build_parser().parse_args(["capture", "--device", "r1"])
    options = _connection_options(args, ConnectionSettings(username="rmohyla"))
    assert options.username == "rmohyla"


def test_default_kdyz_neni_flag_ani_settings():
    from migration_validator.auth import ConnectionSettings
    from migration_validator.cli import _connection_options, build_parser

    args = build_parser().parse_args(["capture", "--device", "r1"])
    options = _connection_options(args, ConnectionSettings())
    assert options.username == "ansible"
    assert options.port == 830
    assert options.timeout == 30


def test_settings_flag_urcuje_cestu(tmp_path):
    from migration_validator.cli import _connection_settings, build_parser

    path = tmp_path / "s.yml"
    path.write_text("connection:\n  username: laborant\n", encoding="utf-8")
    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--settings", str(path), "--output", "o.json"]
    )
    assert _connection_settings(args).username == "laborant"


def test_chybejici_default_settings_neni_chyba(tmp_path, monkeypatch):
    from migration_validator.cli import _connection_settings, build_parser

    monkeypatch.chdir(tmp_path)  # zadny config/settings.yml v cwd
    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--output", "o.json"]
    )
    assert _connection_settings(args).username == "ansible"


def test_capture_sitovy_port_se_nepropise_do_ssh_portu():
    # capture --port je sitovy port (ge-0/0/0); SSH/NETCONF port jde
    # ze settings/defaultu, nikdy z args.port.
    from migration_validator.auth import ConnectionSettings
    from migration_validator.cli import _connection_options, build_parser

    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--run", "mig", "--port", "ge-0/0/0"]
    )
    options = _connection_options(args, ConnectionSettings(netconf_port=830))
    assert options.port == 830


def test_gui_subcommand_parsuje():
    from migration_validator.cli import build_parser
    args = build_parser().parse_args(["gui", "--port", "9999"])
    assert args.gui_port == 9999
    assert args.host == "127.0.0.1"
