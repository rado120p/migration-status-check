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
from migration_validator.collectors.registry import collectors_for
from migration_validator.config import default_config, load_config
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
from migration_validator.reporting.text_report import filter_result, render
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
    subject = _load_snapshot(args.snapshot)
    baseline = _load_snapshot(args.baseline) if args.baseline else None
    mapping = load_mapping(args.mapping) if args.mapping else empty_mapping()
    config = load_config(args.config) if args.config else default_config()

    result = api.evaluate(subject, baseline=baseline, mapping=mapping, config=config)

    shown = filter_result(result, text=args.filter, statuses=_parse_statuses(args.status))

    if args.format == "json":
        if args.output:
            write_json(shown, args.output)
        else:
            print(to_json(shown))
    else:
        print(render(shown, detail=args.detail), end="")
        if args.output:
            write_json(result, args.output)

    if result.summary["fail"]:
        return EXIT_FAILED_CHECKS
    if args.warn_as_error and result.summary["warn"]:
        return EXIT_FAILED_CHECKS
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


def _add_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--username", default="ansible")
    parser.add_argument("--auth", choices=("key", "password"), default="key")
    parser.add_argument("--key-file", default=str(Path.home() / ".ssh" / "id_rsa"))
    parser.add_argument("--password")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--timeout", type=int, default=30)


def _connection_options(args: argparse.Namespace) -> ConnectionOptions:
    return ConnectionOptions(
        host=args.device,
        username=args.username,
        auth_type=args.auth,
        key_file=args.key_file,
        password=args.password,
        port=args.port,
        timeout=args.timeout,
    )


def _cmd_capture(args: argparse.Namespace) -> int:
    from migration_validator.models.snapshot import save_snapshot

    try:
        snapshot = api.capture(
            args.device,
            inventory=args.inventory,
            options=_connection_options(args),
            collectors=args.collectors.split(",") if args.collectors else None,
            phase=args.phase,
            ping_count=args.ping_count,
            record_raw=args.record_raw,
        )
    except JunosConnectionError as error:
        raise ToolError(str(error)) from error

    save_snapshot(snapshot, args.output)
    print(f"snapshot ulozen: {args.output}")

    failed = snapshot.capture.failed_collectors()
    for name, message in failed.items():
        print(f"  varovani: collector '{name}' selhal - {message}", file=sys.stderr)

    return EXIT_OK


def _cmd_record(args: argparse.Namespace) -> int:
    from lxml import etree

    import migration_validator.collectors.all  # noqa: F401  (registrace)

    target_root = Path(args.output_dir)
    try:
        with connect(_connection_options(args)) as device:
            platform = detect_platform(device)
            target = target_root / platform
            target.mkdir(parents=True, exist_ok=True)

            for collector in collectors_for(platform):
                # Collector muze mit vic RPC (EVPN MAC tabulka na MX).
                # Prvni se uklada pod jmenem oblasti, dalsi s poradovym
                # cislem - jinak by fixture obsahovala jen pulku dat.
                for index, rpc_name in enumerate(collector.rpc_names(platform)):
                    try:
                        xml = getattr(device.rpc, rpc_name)(
                            **collector.rpc_kwargs(platform)
                        )
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
    evaluate.add_argument("--snapshot", required=True)
    evaluate.add_argument("--baseline")
    evaluate.add_argument("--mapping")
    evaluate.add_argument("--config")
    evaluate.add_argument("--format", choices=("text", "json"), default="text")
    evaluate.add_argument("--output")
    evaluate.add_argument("--filter", help="podretezec v description nebo scope id")
    evaluate.add_argument("--status", help="carkou oddeleny seznam: pass,warn,fail,skip")
    evaluate.add_argument(
        "--detail",
        action="store_true",
        help="rozbali plny blok i u sluzeb se stavem PASS (WARN/FAIL se rozbaluji vzdy)",
    )
    evaluate.add_argument("--warn-as-error", action="store_true")
    evaluate.set_defaults(func=_cmd_evaluate)

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
    capture.add_argument("--output", required=True)
    capture.add_argument("--collectors", help="carkou oddeleny seznam")
    capture.add_argument("--ping-count", type=int, default=5)
    capture.add_argument("--record-raw")
    _add_auth_arguments(capture)
    capture.set_defaults(func=_cmd_capture)

    record = sub.add_parser(
        "record", help="ulozi syrove RPC XML jako fixtures pro testy"
    )
    record.add_argument("--device", required=True)
    record.add_argument("--output-dir", required=True)
    _add_auth_arguments(record)
    record.set_defaults(func=_cmd_record)

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
