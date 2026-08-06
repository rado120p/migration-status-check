import json

import pytest

from migration_validator.cli import main
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import (
    SCHEMA_VERSION,
    CaptureMeta,
    DeviceMeta,
    Snapshot,
    load_snapshot,
    save_snapshot,
)
from migration_validator.runs.manifest import load_manifest

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
