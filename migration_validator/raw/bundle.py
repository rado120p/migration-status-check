"""Raw bundle na disku: adresar session se session.json a gzip odpovedmi.

Raw format je jedina vec, ktera se nikdy nesmi sama potrebovat upgradovat:
kazda budouci verze nastroje musi precist kazdy starsi raw_format; zmena
formatu = ctecka umi obe verze, stare bundly se neprevadeji. Bundle proto
nese jen data ze zarizeni (odpovedi, chyby, facts) a vstupy capture, nic
odvozeneho (spec 2026-09-23, sekce 1).
"""

from __future__ import annotations

import functools
import gzip
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from migration_validator import __version__
from migration_validator.raw.calls import RecordedCall

RAW_FORMAT = 1
SESSION_FILE = "session.json"
INVENTORY_DIR = "inventory"
INVENTORY_YAML = "inventory.yml"


class RawFormatError(ValueError):
    """session.json chybi, je poskozeny nebo ma neznamy raw_format."""


@dataclass
class Session:
    kind: str  # capture | inventory
    address: str | None
    hostname: str | None
    facts: dict[str, Any]
    calls: list[RecordedCall]
    started_at: str | None = None
    finished_at: str | None = None
    params: dict[str, Any] | None = None
    inventory: dict[str, Any] | None = None
    baselines: list[str] | None = None
    port_filter: str | None = None
    tool: dict[str, Any] | None = None
    raw_format: int = RAW_FORMAT


@functools.lru_cache(maxsize=1)
def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def tool_info() -> dict[str, Any]:
    """Verze nastroje pro session.json (__version__ je staticke 0.1.0)."""
    return {"version": __version__, "commit": _git_commit()}


def reply_file_name(call: RecordedCall) -> str:
    return f"{call.seq:04d}-{call.rpc}.xml.gz"


def has_session(path: Path) -> bool:
    return (Path(path) / SESSION_FILE).is_file()


def remove_session(target: Path) -> None:
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)


def _call_to_json(call: RecordedCall, directory: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"seq": call.seq, "rpc": call.rpc, "kwargs": call.kwargs}
    if call.filter is not None:
        entry["filter"] = call.filter
    if call.error is not None:
        entry["error"] = call.error
    elif call.reply_xml is not None:
        name = reply_file_name(call)
        with gzip.open(directory / name, "wb") as handle:
            handle.write(call.reply_xml)
        entry["reply"] = name
    else:
        entry["value"] = call.reply_value
    return entry


def write_session(
    session: Session,
    target: Path,
    *,
    inventory_raw: Path | None = None,
    inventory_yaml: Path | None = None,
) -> None:
    """Zapise session do `target`.

    Stary target se smaze jako prvni: selhani zapisu nesmi nechat raw
    predchoziho capture vedle noveho souboru. Zapis jde do sourozence
    `.tmp-<jmeno>` a na misto se prejmenuje. `inventory_raw` se zkopiruje
    jako `inventory/`, jinak `inventory_yaml` jako `inventory/inventory.yml`.
    """
    target = Path(target)
    remove_session(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".tmp-{target.name}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        data = {
            "raw_format": session.raw_format,
            "kind": session.kind,
            "tool": session.tool,
            "address": session.address,
            "hostname": session.hostname,
            "facts": session.facts,
            "started_at": session.started_at,
            "finished_at": session.finished_at,
            "params": session.params,
            "inventory": session.inventory,
            "baselines": session.baselines,
            "port_filter": session.port_filter,
            "calls": [_call_to_json(call, tmp) for call in session.calls],
        }
        (tmp / SESSION_FILE).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if inventory_raw is not None:
            shutil.copytree(inventory_raw, tmp / INVENTORY_DIR)
        elif inventory_yaml is not None:
            (tmp / INVENTORY_DIR).mkdir()
            shutil.copy2(inventory_yaml, tmp / INVENTORY_DIR / INVENTORY_YAML)
        os.replace(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _call_from_json(entry: dict[str, Any], path: Path) -> RecordedCall:
    reply_xml = None
    if "reply" in entry:
        with gzip.open(path / entry["reply"], "rb") as handle:
            reply_xml = handle.read()
    return RecordedCall(
        seq=entry["seq"],
        rpc=entry["rpc"],
        kwargs=entry.get("kwargs") or {},
        filter=entry.get("filter"),
        reply_xml=reply_xml,
        reply_value=entry.get("value"),
        error=entry.get("error"),
    )


def read_session(path: Path) -> Session:
    path = Path(path)
    try:
        data = json.loads((path / SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RawFormatError(f"{path}: session.json nejde precist ({error})") from error
    raw_format = data.get("raw_format") if isinstance(data, dict) else None
    if raw_format != RAW_FORMAT:
        raise RawFormatError(f"{path}: neznamy raw_format {raw_format!r}")
    try:
        return Session(
            kind=data["kind"],
            address=data.get("address"),
            hostname=data.get("hostname"),
            facts=data.get("facts") or {},
            calls=[_call_from_json(entry, path) for entry in data["calls"]],
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            params=data.get("params"),
            inventory=data.get("inventory"),
            baselines=data.get("baselines"),
            port_filter=data.get("port_filter"),
            tool=data.get("tool"),
            raw_format=raw_format,
        )
    except (KeyError, TypeError, OSError) as error:
        raise RawFormatError(
            f"{path}: poskozeny raw zaznam ({type(error).__name__}: {error})"
        ) from error
