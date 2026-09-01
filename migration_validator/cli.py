"""CLI - tenky obal nad api.py.

Cokoliv umi CLI, umi i GUI, protoze jdou stejnou cestou.
capture podprikaz doplni Plan 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from migration_validator import api
from migration_validator.auth import ConnectionSettings, load_settings
from migration_validator.collectors.registry import collectors_for
from migration_validator.config import Profile, default_profile, load_profile
from migration_validator.connection.junos import (
    ConnectionOptions,
    JunosConnectionError,
    connect,
    detect_platform,
)
from migration_validator.models.result import Status
from migration_validator.models.snapshot import (
    Snapshot,
    SnapshotVersionError,
    load_snapshot,
)
from migration_validator.reporting.json_report import to_json, write_json
from migration_validator.reporting.text_report import filter_result, render, use_color
from migration_validator.runs.manifest import MappingEndpoint, RunManifest
from migration_validator.runs.orchestrate import capture_into_run
from migration_validator.runs.pairing import plan_evaluations
from migration_validator.runs.store import RunStore
from migration_validator.scoping.mapping import empty_mapping, load_mapping
from migration_validator.scoping.matcher import match_scopes

EXIT_OK = 0
EXIT_FAILED_CHECKS = 1
EXIT_TOOL_ERROR = 2


class ToolError(Exception):
    """Nastroj selhal - jina vec nez selhany test."""


def _load_snapshot(path: str) -> Snapshot:
    try:
        return load_snapshot(path)
    except FileNotFoundError as error:
        raise ToolError(f"snapshot nenalezen: {path}") from error
    except SnapshotVersionError as error:
        raise ToolError(str(error)) from error
    except json.JSONDecodeError as error:
        raise ToolError(f"{path}: nevalidni JSON ({error})") from error
    except (KeyError, TypeError) as error:
        raise ToolError(f"{path}: poskozeny snapshot ({error})") from error


def _parse_statuses(value: str | None) -> set[Status] | None:
    if not value:
        return None
    return {Status(item.strip().upper()) for item in value.split(",") if item.strip()}


def _cmd_evaluate(args: argparse.Namespace) -> int:
    if args.run and args.snapshot:
        raise ToolError("--run a --snapshot se vzajemne vylucuji")
    if args.run:
        return _evaluate_run(args)
    if not args.snapshot:
        raise ToolError("--snapshot je povinny, pokud nepouzivas --run")

    subject = _load_snapshot(args.snapshot)
    baseline = _load_snapshot(args.baseline) if args.baseline else None
    mapping = load_mapping(args.mapping) if args.mapping else empty_mapping()
    profile = load_profile(args.profile) if args.profile else default_profile()
    service_types = _parse_service_types(args, profile)

    result = api.evaluate(
        subject,
        baseline=baseline,
        mapping=mapping,
        config=profile.checks,
        service_types=service_types,
        profile_name=profile.name or None,
    )

    shown = filter_result(result, text=args.filter, statuses=_parse_statuses(args.status))

    if args.format == "json":
        if args.output:
            write_json(shown, args.output)
        else:
            print(to_json(shown))
    else:
        color = use_color(force_on=args.color, force_off=args.no_color)
        print(render(shown, detail=args.detail, color=color), end="")
        if args.output:
            write_json(result, args.output)

    if result.summary["fail"]:
        return EXIT_FAILED_CHECKS
    if args.warn_as_error and result.summary["warn"]:
        return EXIT_FAILED_CHECKS
    return EXIT_OK


def _require_run_manifest(store: RunStore, run: str) -> RunManifest:
    """Nacte manifest existujiciho runu, nebo shodi ToolError.

    RunStore.load() vraci prazdny manifest pro chybejici run.yml - to je
    spravne pro capture (bootstrapuje run), ale pro evaluate/status by to
    tise predstiralo, ze run bez zaznamu existuje.
    """
    if not store.manifest_path.exists():
        raise ToolError(
            f"run '{run}' neexistuje ({store.manifest_path} nenalezen)"
        )
    return store.load()


def _evaluate_run(args: argparse.Namespace) -> int:
    if args.output:
        raise ToolError("--run a --output se vzajemne vylucuji")
    if args.baseline:
        raise ToolError("--run a --baseline se vzajemne vylucuji")

    store = RunStore(args.run_root, args.run)
    manifest = _require_run_manifest(store, args.run)

    missing = store.missing_snapshots(manifest)
    if missing:
        raise ToolError(
            "chybejici soubory snimku: " + ", ".join(sorted(missing))
        )

    ports = [p.strip() for p in args.ports.split(",") if p.strip()] if args.ports else None
    evaluations = plan_evaluations(manifest, ports)

    if not evaluations:
        print("zadne snimky k vyhodnoceni", file=sys.stderr)
        return EXIT_OK

    mapping = load_mapping(args.mapping) if args.mapping else empty_mapping()
    profile = load_profile(args.profile) if args.profile else default_profile()
    service_types = _parse_service_types(args, profile)
    statuses = _parse_statuses(args.status)
    color = use_color(force_on=args.color, force_off=args.no_color)

    exit_code = EXIT_OK
    for evaluation in evaluations:
        subject = _load_snapshot(str(store.dir / evaluation.subject.snapshot))
        baseline = None
        baseline_label = "bez baseline"
        if evaluation.baseline is not None:
            baseline = _load_snapshot(str(store.dir / evaluation.baseline.snapshot))
            baseline_label = evaluation.baseline.snapshot
        elif evaluation.reason:
            print(f"varovani: {evaluation.reason}", file=sys.stderr)

        step_payload = None
        step_label = ""
        if evaluation.step is not None:
            step_payload = {
                "old": {
                    "node": evaluation.step.old.node,
                    "port": evaluation.step.old.port,
                },
                "new": {
                    "node": evaluation.step.new.node,
                    "port": evaluation.step.new.port,
                },
            }
            step_label = (
                f" [krok {evaluation.step.old.node}:{evaluation.step.old.port}"
                f" -> {evaluation.step.new.node}:{evaluation.step.new.port}]"
            )
        elif evaluation.same_device:
            step_label = " [stejne zarizeni]"

        print(f"=== {evaluation.subject.snapshot} vs {baseline_label}{step_label} ===")

        result = api.evaluate(
            subject,
            baseline=baseline,
            mapping=mapping,
            config=profile.checks,
            service_types=service_types,
            profile_name=profile.name or None,
            step=step_payload,
        )
        shown = filter_result(result, text=args.filter, statuses=statuses)

        if args.format == "json":
            print(to_json(shown))
        else:
            print(render(shown, detail=args.detail, color=color), end="")

        if result.summary["fail"]:
            exit_code = EXIT_FAILED_CHECKS
        elif args.warn_as_error and result.summary["warn"] and exit_code == EXIT_OK:
            exit_code = EXIT_FAILED_CHECKS

    return exit_code


def _status_rows(
    manifest: RunManifest,
) -> list[tuple[str, str, bool, bool, bool]]:
    """Radky pro status --run: (old label, new label, pre, post, rollback)."""
    rows: list[tuple[str, str, bool, bool, bool]] = []

    for mapping in manifest.interface_mapping:
        old, new = mapping.old, mapping.new
        rows.append(
            (
                f"{old.node}:{old.port}",
                f"{new.node}:{new.port}",
                manifest.find_capture("pre", old.node, old.port) is not None,
                manifest.find_capture("post", new.node, new.port) is not None,
                manifest.find_capture("rollback", old.node, old.port) is not None,
            )
        )

    seen_devices: set[str] = set()
    for capture in manifest.captures:
        if capture.port is not None or capture.device in seen_devices:
            continue
        seen_devices.add(capture.device)

        device = manifest.devices.get(capture.device)
        label = f"{capture.device}:all"
        pre_ok = manifest.find_capture("pre", capture.device, None) is not None
        post_ok = manifest.find_capture("post", capture.device, None) is not None
        rollback_ok = manifest.find_capture("rollback", capture.device, None) is not None

        if device is not None and device.role == "new":
            rows.append(("-", label, pre_ok, post_ok, rollback_ok))
        else:
            rows.append((label, "-", pre_ok, post_ok, rollback_ok))

    return rows


def _cmd_status(args: argparse.Namespace) -> int:
    store = RunStore(args.run_root, args.run)
    manifest = _require_run_manifest(store, args.run)

    def mark(ok: bool) -> str:
        return "ano" if ok else "-"

    print(f"{'OLD':<28} {'NEW':<28} {'PRE':<5} {'POST':<5} {'ROLLBACK':<8}")
    for old_label, new_label, pre_ok, post_ok, rollback_ok in _status_rows(manifest):
        print(
            f"{old_label:<28} {new_label:<28} "
            f"{mark(pre_ok):<5} {mark(post_ok):<5} {mark(rollback_ok):<8}"
        )

    return EXIT_OK


def _cmd_match(args: argparse.Namespace) -> int:
    baseline = _load_snapshot(args.baseline)
    subject = _load_snapshot(args.subject)
    mapping = load_mapping(args.mapping) if args.mapping else empty_mapping()

    matches = match_scopes(baseline.scopes, subject.scopes, mapping)

    print(f"SPAROVANO ({len(matches.pairs)})")
    for pair in matches.pairs:
        print(f"  {pair.confidence:<8} {pair.method:<45} {pair.baseline.id}")
        print(f"           -> {pair.subject.id}")

    print(f"\nNESPAROVANO baseline ({len(matches.unmatched_baseline)})")
    for item in matches.unmatched_baseline:
        print(f"  {item.scope.id:<50} {item.reason}")

    print(f"\nNESPAROVANO subject ({len(matches.unmatched_subject)})")
    for item in matches.unmatched_subject:
        print(f"  {item.scope.id:<50} {item.reason}")

    return EXIT_OK


def _cmd_checks(args: argparse.Namespace) -> int:
    described = api.list_checks()
    if args.format == "json":
        print(json.dumps(described, indent=2, ensure_ascii=False))
        return EXIT_OK

    print(f"{'ID':<24} {'MODE':<8} {'SEVERITY':<9} TYPY SLUZEB")
    for item in described:
        types = ", ".join(item["service_types"]) if item["service_types"] else "vsechny"
        print(
            f"{item['id']:<24} {item['mode']:<8} "
            f"{item['default_severity']:<9} {types}"
        )
    return EXIT_OK


def _add_auth_arguments(
    parser: argparse.ArgumentParser, *, port_flag: str = "--port", port_dest: str = "port"
) -> None:
    # default=None vsude: merge flag > settings > default se deje az
    # v _connection_options, argparse default by settings tise prebil.
    parser.add_argument("--username", default=None)
    parser.add_argument("--password")
    parser.add_argument(port_flag, dest=port_dest, type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument(
        "--settings",
        help="cesta k settings YAML (default config/settings.yml)",
    )


def _pick(*values):
    """Prvni hodnota, ktera neni None - precedence flag > soubor > default."""
    for value in values:
        if value is not None:
            return value
    return None


def _connection_settings(args: argparse.Namespace) -> ConnectionSettings:
    if args.settings:
        path = Path(args.settings)
        if not path.exists():
            raise ToolError(f"settings soubor nenalezen: {path}")
        return load_settings(path)
    return load_settings()


def _connection_options(
    args: argparse.Namespace, settings: ConnectionSettings
) -> ConnectionOptions:
    # capture ma --ssh-port (dest "ssh_port"), protoze --port u nej znamena
    # cislo/jmeno sitoveho portu v run rezimu; record pouziva puvodni --port
    # jako SSH port. Rozlisuje se pritomnosti atributu, ne hodnotou None.
    if hasattr(args, "ssh_port"):
        flag_port = args.ssh_port
    else:
        flag_port = getattr(args, "port", None)
    return ConnectionOptions(
        host=args.device,
        username=_pick(args.username, settings.username),
        ssh_key_paths=settings.ssh_key_paths,
        password=_pick(args.password, settings.password),
        port=_pick(flag_port, settings.netconf_port),
        timeout=_pick(args.timeout, settings.timeout),
    )


def _parse_service_types(
    args: argparse.Namespace, profile: Profile
) -> list[str] | None:
    raw = getattr(args, "service_types", None)
    if raw is not None:
        parsed = [s.strip() for s in raw.split(",") if s.strip()]
        if not parsed:
            # Flag byl zadany (raw neni None), ale po parsovani nezbyl
            # zadny typ - "," i "" spadaji sem. Ticha shoda na profil by
            # u "," odfiltrovala kazdou sluzbu bez ohlaseni proc.
            raise ToolError("zadny platny typ v --service-types")
        return parsed
    return profile.service_types


def _cmd_capture(args: argparse.Namespace) -> int:
    if args.run:
        if args.output:
            raise ToolError("--run a --output se vzajemne vylucuji")
        return _capture_into_run(args)

    if args.parse_services:
        raise ToolError("--parse-services vyzaduje --run")

    if args.port is not None:
        raise ToolError(
            "--port je jen pro --run rezim, SSH port zadej pres --ssh-port"
        )
    if args.maps_to:
        raise ToolError("--maps-to je jen pro --run rezim")
    if not args.output:
        raise ToolError("--output je povinny mimo --run rezim")

    from migration_validator.models.snapshot import save_snapshot

    profile = load_profile(args.profile) if args.profile else default_profile()
    collectors = (
        args.collectors.split(",") if args.collectors else profile.collectors
    )
    ping_count = _pick(args.ping_count, profile.ping_count, 5)
    service_types = _parse_service_types(args, profile)

    try:
        snapshot = api.capture(
            args.device,
            inventory=args.inventory,
            options=_connection_options(args, _connection_settings(args)),
            collectors=collectors,
            phase=args.phase,
            ping_count=ping_count,
            record_raw=args.record_raw,
            service_types=service_types,
        )
    except JunosConnectionError as error:
        raise ToolError(str(error)) from error

    save_snapshot(snapshot, args.output)
    print(f"snapshot ulozen: {args.output}")

    failed = snapshot.capture.failed_collectors()
    for name, message in failed.items():
        print(f"  varovani: collector '{name}' selhal - {message}", file=sys.stderr)

    return EXIT_OK


def _capture_into_run(args: argparse.Namespace) -> int:
    if args.maps_to and not args.port:
        raise ToolError("--maps-to vyzaduje --port (parovani je vzdy per-port)")

    store = RunStore(args.run_root, args.run)
    profile = load_profile(args.profile) if args.profile else default_profile()
    collectors = (
        args.collectors.split(",") if args.collectors else profile.collectors
    )
    ping_count = _pick(args.ping_count, profile.ping_count, 5)
    service_types = _parse_service_types(args, profile)

    try:
        outcome = capture_into_run(
            store,
            host=args.device,
            phase=args.phase,
            port=args.port,
            options=_connection_options(args, _connection_settings(args)),
            profile=profile,
            parse_services=args.parse_services,
            inventory=args.inventory,
            overwrite=args.overwrite,
            collectors=collectors,
            ping_count=ping_count,
            service_types=service_types,
            record_raw=args.record_raw,
        )
    except ValueError as error:
        raise ToolError(str(error)) from error
    except JunosConnectionError as error:
        raise ToolError(str(error)) from error

    for warning in outcome.warnings:
        print(warning)

    print(f"snapshot ulozen: {outcome.snapshot_path}")
    for name, message in outcome.failed_collectors.items():
        print(f"  varovani: collector '{name}' selhal - {message}", file=sys.stderr)

    if args.maps_to:
        manifest = store.load()
        node = manifest.node_for_host(args.device) or args.device
        try:
            other_node, other_port = args.maps_to.rsplit(":", 1)
        except ValueError as error:
            raise ToolError(
                f"nevalidni --maps-to '{args.maps_to}', ocekavano NODE:PORT"
            ) from error
        here = MappingEndpoint(node=node, port=args.port)
        there = MappingEndpoint(node=other_node, port=other_port)
        if args.phase == "post":
            manifest.add_mapping(old=there, new=here)
        else:
            manifest.add_mapping(old=here, new=there)
        store.save(manifest)

    return EXIT_OK


def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as error:
        raise ToolError(
            "GUI vyzaduje 'pip install migration-validator[gui]'"
        ) from error
    from migration_validator.gui.app import create_app

    app = create_app(run_root=args.run_root, profile_path=args.profile)
    uvicorn.run(app, host=args.host, port=args.gui_port)
    return EXIT_OK


def _cmd_record(args: argparse.Namespace) -> int:
    from lxml import etree

    import migration_validator.collectors.all  # noqa: F401  (registrace)

    target_root = Path(args.output_dir)
    try:
        with connect(_connection_options(args, _connection_settings(args))) as device:
            platform = detect_platform(device)
            target = target_root / platform
            target.mkdir(parents=True, exist_ok=True)

            for collector in collectors_for(platform):
                # Collector muze mit vic RPC (EVPN MAC tabulka na MX) a
                # jednotliva volani se muzou lisit jen v kwargs (interfaces:
                # extensive/terse, routes: protocol=static/aggregate).
                # rpc_calls() je podle base.py autorita presne pro tenhle
                # pripad - rpc_names()+jedno rpc_kwargs() by druhe a dalsi
                # volani zopakovalo se stejnymi kwargs jako prvni a nahravka
                # by tise obsahovala dvakrat totez misto druhe varianty.
                # Prvni se uklada pod jmenem oblasti, dalsi s poradovym
                # cislem - jinak by fixture obsahovala jen pulku dat.
                for index, (rpc_name, rpc_kwargs) in enumerate(
                    collector.rpc_calls(platform)
                ):
                    try:
                        xml = getattr(device.rpc, rpc_name)(**rpc_kwargs)
                    except Exception as error:  # noqa: BLE001
                        print(
                            f"  {collector.name} ({rpc_name}): SELHALO - {error}",
                            file=sys.stderr,
                        )
                        continue

                    suffix = "" if index == 0 else f".{index + 1}"
                    path = target / f"{collector.name}{suffix}.xml"
                    path.write_bytes(etree.tostring(xml, pretty_print=True))
                    print(f"  {collector.name} ({rpc_name}): {path}")
    except JunosConnectionError as error:
        raise ToolError(str(error)) from error

    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mig-validate",
        description="Validace stavu sitovych sluzeb pri migraci Junos -> Junos EVO",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate = sub.add_parser("evaluate", help="vyhodnoti snapshot, volitelne proti baseline")
    evaluate.add_argument("--snapshot", help="vzajemne vylucne s --run")
    evaluate.add_argument("--baseline")
    evaluate.add_argument("--run", help="nazev run adresare, vyhodnoti sparovane snimky")
    evaluate.add_argument(
        "--run-root", type=Path, default=Path("runs"), help="koren run adresaru"
    )
    evaluate.add_argument(
        "--ports", help="carkou oddeleny seznam portu, filtr pro --run rezim"
    )
    evaluate.add_argument("--mapping")
    evaluate.add_argument(
        "--profile", "--config", dest="profile", help="profil YAML (--config je alias)"
    )
    evaluate.add_argument("--service-types", help="carkou oddeleny seznam typu sluzeb")
    evaluate.add_argument("--format", choices=("text", "json"), default="text")
    evaluate.add_argument("--output")
    evaluate.add_argument("--filter", help="podretezec v description nebo scope id")
    evaluate.add_argument("--status", help="carkou oddeleny seznam: pass,warn,fail,skip,info")
    evaluate.add_argument(
        "--detail",
        action="store_true",
        help="rozbali plny blok i u sluzeb se stavem PASS (WARN/FAIL se rozbaluji vzdy)",
    )
    color = evaluate.add_mutually_exclusive_group()
    color.add_argument(
        "--color",
        action="store_true",
        help="vynuti barvy i mimo terminal (napr. do 'less -R')",
    )
    color.add_argument(
        "--no-color",
        action="store_true",
        help="vypne barvy (autodetekce: barvi se jen na TTY bez NO_COLOR)",
    )
    evaluate.add_argument("--warn-as-error", action="store_true")
    evaluate.set_defaults(func=_cmd_evaluate)

    status = sub.add_parser("status", help="prehled parovani a stavu snimku v run adresari")
    status.add_argument("--run", required=True)
    status.add_argument(
        "--run-root", type=Path, default=Path("runs"), help="koren run adresaru"
    )
    status.set_defaults(func=_cmd_status)

    match = sub.add_parser("match", help="jen parovani sluzeb, pro ladeni mapping.yml")
    match.add_argument("--baseline", required=True)
    match.add_argument("--subject", required=True)
    match.add_argument("--mapping")
    match.set_defaults(func=_cmd_match)

    checks = sub.add_parser("checks", help="vypise registrovane checky")
    checks.add_argument("--format", choices=("text", "json"), default="text")
    checks.set_defaults(func=_cmd_checks)

    capture = sub.add_parser("capture", help="sebere stav zarizeni do snapshotu")
    capture.add_argument("--device", required=True)
    capture.add_argument("--inventory")
    capture.add_argument("--phase")
    capture.add_argument("--output")
    capture.add_argument("--collectors", help="carkou oddeleny seznam")
    capture.add_argument("--ping-count", type=int, default=None)
    capture.add_argument("--record-raw")
    capture.add_argument(
        "--profile", "--config", dest="profile", help="profil YAML (--config je alias)"
    )
    capture.add_argument("--service-types", help="carkou oddeleny seznam typu sluzeb")
    capture.add_argument("--run", help="nazev run adresare (runs/<nazev>/)")
    capture.add_argument(
        "--run-root", type=Path, default=Path("runs"), help="koren run adresaru"
    )
    capture.add_argument(
        "--port", help="cislo/jmeno sitoveho portu pro --run rezim, napr. ge-0/0/0"
    )
    capture.add_argument(
        "--maps-to", help="parovani portu ve tvaru NODE:PORT (jen s --run a --port)"
    )
    capture.add_argument(
        "--parse-services",
        action="store_true",
        help="inventory pro --run vzdy pregeneruj z konfigurace (samostatne spojeni)",
    )
    capture.add_argument(
        "--overwrite",
        action="store_true",
        help="povol prepsani existujiciho pre snimku v run adresari",
    )
    _add_auth_arguments(capture, port_flag="--ssh-port", port_dest="ssh_port")
    capture.set_defaults(func=_cmd_capture)

    record = sub.add_parser(
        "record", help="ulozi syrove RPC XML jako fixtures pro testy"
    )
    record.add_argument("--device", required=True)
    record.add_argument("--output-dir", required=True)
    _add_auth_arguments(record)
    record.set_defaults(func=_cmd_record)

    gui = sub.add_parser("gui", help="spusti webove GUI")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", dest="gui_port", type=int, default=8321)
    gui.add_argument(
        "--run-root", type=Path, default=Path("runs"), help="koren run adresaru"
    )
    gui.add_argument(
        "--profile", "--config", dest="profile", help="profil YAML (--config je alias)"
    )
    gui.set_defaults(func=_cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ToolError as error:
        print(f"chyba: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    except (OSError, ValueError) as error:
        print(f"chyba: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
