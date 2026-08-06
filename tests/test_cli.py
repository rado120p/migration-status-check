import json

import pytest

from migration_validator.cli import EXIT_FAILED_CHECKS, EXIT_OK, EXIT_TOOL_ERROR, main
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
        "--phase", "pre",
        "--inventory", "tests/fixtures/172.20.20.4.yml",
    ]

    assert main(args) == 0
    assert main(args) == 0

    manifest = load_manifest(tmp_path / "mig01" / "run.yml")
    assert len(manifest.captures) == 1


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
        output_path.write_text("schema_version: 5\ndevice: x\ninterfaces: []\n")

    monkeypatch.setattr("migration_validator.cli.connect", fake_connect)
    monkeypatch.setattr("migration_validator.cli.detect_platform", fake_detect_platform)
    monkeypatch.setattr("migration_validator.cli.generate_inventory", fake_generate_inventory)

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


def test_capture_run_parse_services_skips_existing_inventory(tmp_path, monkeypatch, capsys):
    _fake_capture(monkeypatch)

    store = RunStore(tmp_path, "mig01")
    inventory_path = store.inventory_path("172.20.20.4", "ge-0/0/0")
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text("schema_version: 5\ndevice: x\ninterfaces: []\n")

    def fail_generate_inventory(*args, **kwargs):
        raise AssertionError("generate_inventory nemel byt volan, inventory uz existuje")

    monkeypatch.setattr(
        "migration_validator.cli.generate_inventory", fail_generate_inventory
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
    assert "jiz existuje" in capsys.readouterr().err


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
    assert f"=== {post_path.name} vs {pre_path.name} ===" in output
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
    assert f"=== {post_ok.name} vs {pre_ok.name} ===" in output
    assert f"=== {post_down.name} vs {pre_down.name} ===" in output
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
    assert f"=== {post_path.name} vs bez baseline ===" in captured.out
    assert "chybi pre snimek stareho boxu" in captured.err
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
            "--ports", "et-0/0/1",
        ]
    )

    output = capsys.readouterr().out
    assert post1.name in output
    assert post0.name not in output
    # oba pary maji shodny stav (up) na obou stranach -> PASS -> exit 0
    assert code == EXIT_OK


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
