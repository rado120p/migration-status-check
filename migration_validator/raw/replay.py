"""ReplayDevice - vraci nahrane odpovedi misto zarizeni (spec 2026-09-23).

Co v nahravce neni, se nikdy nevydava za vysledek: vyhodi se NotRecorded
(collector -> status error, ping -> sent=0). Nahrana chyba se vyhodi znovu
se stejnym jmenem tridy a textem.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.parsers import parser_hierarchies
from migration_validator.parsers.core import find_configuration_root
from migration_validator.raw.bundle import Session
from migration_validator.raw.calls import (
    NOT_RECORDED,
    NotRecorded,
    RecordedCall,
    call_key,
    canonical_kwargs,
    config_filter,
)


class RecordedRpcError(Exception):
    """Zaklad pro nahrane chyby; podtrida nese jmeno puvodni tridy."""


def recorded_error(error: dict[str, str]) -> Exception:
    cls = type(str(error.get("type") or "RpcError"), (RecordedRpcError,), {})
    return cls(error.get("message", ""))


def _answer(entry: RecordedCall) -> Any:
    if entry.error is not None:
        raise recorded_error(entry.error)
    return entry.reply()


class ReplayDevice:
    def __init__(
        self, session: Session, *, required_config: frozenset[str] | None = None
    ) -> None:
        self.facts = dict(session.facts)
        self.hostname = session.hostname
        self.rpc = _ReplayRpc(
            session.calls,
            required_config if required_config is not None else parser_hierarchies(),
        )


class _ReplayRpc:
    def __init__(self, calls: list[RecordedCall], required: frozenset[str]) -> None:
        self._by_key: dict[str, list[RecordedCall]] = {}
        self._configs: list[RecordedCall] = []
        for call in calls:
            if call.rpc == "get_config":
                self._configs.append(call)
            else:
                self._by_key.setdefault(call_key(call.rpc, call.kwargs), []).append(call)
        self._served: dict[str, int] = {}
        self._required = required

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def call(**kwargs: Any) -> Any:
            if name == "get_config":
                return self._config(kwargs)
            key = call_key(name, canonical_kwargs(name, kwargs))
            entries = self._by_key.get(key)
            if not entries:
                raise NotRecorded(f"{NOT_RECORDED}: {name}")
            index = self._served.get(key, 0)
            self._served[key] = index + 1
            return _answer(entries[min(index, len(entries) - 1)])

        return call

    def _config(self, kwargs: dict[str, Any]) -> Any:
        requested = config_filter("get_config", kwargs)
        if requested is None or not self._configs:
            raise NotRecorded(f"{NOT_RECORDED}: get_config")
        entry = self._configs[0]
        recorded = set(entry.filter or ())
        # Rozhoduje nahrany filtr, ne odpoved - Junos prazdnou hierarchii
        # vynecha, takze chybejici v odpovedi muze byt jen prazdna.
        missing = [h for h in requested if h in self._required and h not in recorded]
        if missing:
            raise NotRecorded(f"konfigurace nema hierarchii {', '.join(missing)}")
        reply = _answer(entry)
        root = find_configuration_root(reply)
        keep = set(requested)
        for child in list(root):
            if isinstance(child.tag, str) and etree.QName(child).localname not in keep:
                root.remove(child)
        return reply
