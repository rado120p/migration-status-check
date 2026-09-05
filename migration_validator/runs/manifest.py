"""Model run.yml - jediny zdroj pravdy o migraci (faze 4 roadmapy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RUN_SCHEMA_VERSION = 1
RUN_KINDS = frozenset({"single", "migration"})
DEFAULT_KIND = "migration"
VALID_ROLES = frozenset({"old", "new", "l2-switch", "single"})


def normalize_port(port: str) -> str:
    return port.replace("-", "_").replace("/", "_")


@dataclass
class RunDevice:
    host: str
    platform: str
    role: str

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(
                f"neplatna role '{self.role}', ocekavano jedno z {sorted(VALID_ROLES)}"
            )


@dataclass
class MappingEndpoint:
    node: str
    port: str
    l2_switch: dict[str, Any] | None = None


@dataclass
class InterfaceMapping:
    old: MappingEndpoint
    new: MappingEndpoint


@dataclass
class CaptureRecord:
    phase: str
    device: str
    port: str | None
    snapshot: str
    taken: str


@dataclass
class RunManifest:
    devices: dict[str, RunDevice] = field(default_factory=dict)
    interface_mapping: list[InterfaceMapping] = field(default_factory=list)
    captures: list[CaptureRecord] = field(default_factory=list)
    # Druh runu: "migration" (old -> new s mappingem) nebo "single" (jeden box,
    # pre/post kolem upgradu). Chybi-li v run.yml, je to migration.
    kind: str = DEFAULT_KIND
    # Jmeno profilu z profile store (spec 3); None = serverovy default.
    profile: str | None = None
    # Rezervovano pro bulk: N single runu se stejnou skupinou. Nikdo to zatim nepise.
    group: str | None = None

    def node_for_host(self, host: str) -> str | None:
        for node, device in self.devices.items():
            if device.host == host:
                return node
        return None

    def device_with_role(self, role: str) -> tuple[str, RunDevice] | None:
        candidates = [
            (node, device)
            for node, device in self.devices.items()
            if device.role == role
        ]
        if not candidates:
            return None
        if len(candidates) > 1:
            raise ValueError(
                f"faze 4 podporuje jeden box role '{role}', nalezeno "
                f"{len(candidates)}"
            )
        return candidates[0]

    def paired_old(self, new_node: str, new_port: str) -> MappingEndpoint | None:
        matches = [
            mapping.old
            for mapping in self.interface_mapping
            if mapping.new.node == new_node and mapping.new.port == new_port
        ]
        if len(matches) != 1:
            return None
        return matches[0]

    def mapped_olds(self, new_node: str, new_port: str) -> list[MappingEndpoint]:
        """Vsechny stare endpointy mapovane na dany novy port (N:1 u LAGu)."""
        return [
            mapping.old
            for mapping in self.interface_mapping
            if mapping.new.node == new_node and mapping.new.port == new_port
        ]

    def paired_new(self, old_node: str, old_port: str) -> MappingEndpoint | None:
        matches = [
            mapping.new
            for mapping in self.interface_mapping
            if mapping.old.node == old_node and mapping.old.port == old_port
        ]
        if len(matches) != 1:
            return None
        return matches[0]

    def add_mapping(self, old: MappingEndpoint, new: MappingEndpoint) -> None:
        for mapping in self.interface_mapping:
            if mapping.old.node == old.node and mapping.old.port == old.port:
                if mapping.new.node == new.node and mapping.new.port == new.port:
                    return  # idempotentni - stejna dvojice uz je zaznamenana
                raise ValueError(
                    f"old {old.node}/{old.port} uz je sparovan s jinym new "
                    f"portem ({mapping.new.node}/{mapping.new.port})"
                )
        self.interface_mapping.append(InterfaceMapping(old=old, new=new))

    def record_capture(self, record: CaptureRecord) -> None:
        for index, existing in enumerate(self.captures):
            if (
                existing.phase == record.phase
                and existing.device == record.device
                and existing.port == record.port
            ):
                self.captures[index] = record
                return
        self.captures.append(record)

    def find_capture(
        self, phase: str, device: str, port: str | None
    ) -> CaptureRecord | None:
        for record in self.captures:
            if (
                record.phase == phase
                and record.device == device
                and record.port == port
            ):
                return record
        return None


def check_kind_devices(kind: str, devices: dict[str, RunDevice]) -> None:
    """Strukturalni pravidla kind vs. role. Volane pri nacteni run.yml a pri
    zakladani runu; jednostranna migrace (zatim jen old) projde - CLI ji
    dopisuje postupne."""
    if kind not in RUN_KINDS:
        raise ValueError(f"neznamy kind '{kind}', ocekavano single nebo migration")
    roles = [device.role for device in devices.values()]
    if kind == "single":
        if len(devices) != 1 or roles != ["single"]:
            raise ValueError(
                "run typu single ma prave jedno zarizeni role 'single', "
                f"nalezeno {len(devices)} zarizeni s rolemi {sorted(roles)}"
            )
        return
    if "single" in roles:
        raise ValueError("run typu migration nesmi obsahovat zarizeni role 'single'")
    for role in ("old", "new"):
        count = roles.count(role)
        if count > 1:
            raise ValueError(
                f"run typu migration podporuje jeden box role '{role}', nalezeno {count}"
            )


def _load_device(data: dict[str, Any]) -> RunDevice:
    return RunDevice(host=data["host"], platform=data["platform"], role=data["role"])


def _load_endpoint(data: dict[str, Any]) -> MappingEndpoint:
    return MappingEndpoint(
        node=data["node"], port=data["port"], l2_switch=data.get("l2_switch")
    )


def _load_capture(data: dict[str, Any]) -> CaptureRecord:
    port = data.get("port")
    if port == "all":
        port = None
    return CaptureRecord(
        phase=data["phase"],
        device=data["device"],
        port=port,
        snapshot=data["snapshot"],
        taken=data["taken"],
    )


def load_manifest(path: Path) -> RunManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: run.yml musi byt mapping, nalezeno {type(raw)!r}")

    schema_version = raw.get("schema_version")
    if schema_version != RUN_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: neznama schema_version {schema_version!r}, "
            f"ocekavano {RUN_SCHEMA_VERSION}"
        )

    devices = {}
    for node, data in (raw.get("devices") or {}).items():
        try:
            devices[node] = _load_device(data)
        except ValueError as exc:
            raise ValueError(f"{path}: zarizeni '{node}': {exc}") from exc

    kind = raw.get("kind")
    kind = DEFAULT_KIND if kind is None else kind
    try:
        check_kind_devices(kind, devices)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc

    interface_mapping = [
        InterfaceMapping(
            old=_load_endpoint(entry["old"]), new=_load_endpoint(entry["new"])
        )
        for entry in raw.get("interface_mapping") or []
    ]
    captures = [_load_capture(entry) for entry in raw.get("captures") or []]

    return RunManifest(
        devices=devices,
        interface_mapping=interface_mapping,
        captures=captures,
        kind=kind,
        profile=raw.get("profile"),
        group=raw.get("group"),
    )


def _dump_endpoint(endpoint: MappingEndpoint) -> dict[str, Any]:
    data: dict[str, Any] = {"node": endpoint.node, "port": endpoint.port}
    if endpoint.l2_switch is not None:
        data["l2_switch"] = endpoint.l2_switch
    return data


def save_manifest(manifest: RunManifest, path: Path) -> None:
    check_kind_devices(manifest.kind, manifest.devices)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "kind": manifest.kind,
    }
    if manifest.profile is not None:
        data["profile"] = manifest.profile
    if manifest.group is not None:
        data["group"] = manifest.group
    data["devices"] = {
        node: {
            "host": device.host,
            "platform": device.platform,
            "role": device.role,
        }
        for node, device in manifest.devices.items()
    }
    data["interface_mapping"] = [
        {"old": _dump_endpoint(mapping.old), "new": _dump_endpoint(mapping.new)}
        for mapping in manifest.interface_mapping
    ]
    data["captures"] = [
        {
            "phase": record.phase,
            "device": record.device,
            "port": record.port if record.port is not None else "all",
            "snapshot": record.snapshot,
            "taken": record.taken,
        }
        for record in manifest.captures
    ]

    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
