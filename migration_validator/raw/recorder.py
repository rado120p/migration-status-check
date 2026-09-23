"""Nahravani NETCONF session na hranici device.rpc (spec 2026-09-23).

Obal zapise kazde volani v poradi: jmeno RPC, kanonicke argumenty, a bud
odpoved (serializovanou hned pri volani - volajici ji smi upravit), nebo
chybu (jmeno tridy + text). Zaznam zije v pameti; na disk ho zapisuje
raw.bundle az po ulozeni snapshotu/inventory.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.raw.calls import RecordedCall, canonical_kwargs, config_filter

# Jen facts, ktere nastroj cte (detect_platform, device_meta). Cteni dalsich
# by na PyEZ spoustelo dalsi fact RPC.
FACT_KEYS = ("hostname", "model", "version")


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class SessionRecording:
    """Volani jedne session v poradi + facts a hostname zarizeni."""

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self.facts: dict[str, Any] = {}
        self.hostname: str | None = None


class RecordingDevice:
    """Obal kolem PyEZ Device: `rpc` nahrava, `facts`/`hostname` prochazi."""

    def __init__(self, device: Any, recording: SessionRecording) -> None:
        self._device = device
        self.recording = recording
        facts = getattr(device, "facts", {}) or {}
        recording.facts = {key: jsonable(facts.get(key)) for key in FACT_KEYS}
        recording.hostname = jsonable(getattr(device, "hostname", None))
        self.rpc = _RecordingRpc(device.rpc, recording)

    @property
    def facts(self) -> Any:
        return getattr(self._device, "facts", {})

    @property
    def hostname(self) -> Any:
        return getattr(self._device, "hostname", None)


class _RecordingRpc:
    def __init__(self, rpc: Any, recording: SessionRecording) -> None:
        self._rpc = rpc
        self._recording = recording

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        target = getattr(self._rpc, name)

        def call(**kwargs: Any) -> Any:
            entry = RecordedCall(
                seq=len(self._recording.calls) + 1,
                rpc=name,
                kwargs=canonical_kwargs(name, kwargs),
                filter=config_filter(name, kwargs),
            )
            try:
                reply = target(**kwargs)
            except Exception as error:  # noqa: BLE001 - nahraje se a propadne dal
                entry.error = {"type": type(error).__name__, "message": str(error)}
                self._recording.calls.append(entry)
                raise
            if isinstance(reply, etree._Element):
                entry.reply_xml = etree.tostring(reply)
            else:
                entry.reply_value = jsonable(reply)
            self._recording.calls.append(entry)
            return reply

        return call
