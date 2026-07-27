"""Zaklad collectoru.

Collector nikdy neinterpretuje - vraci syrova strukturovana data. Kdyz se
zmeni kriterium, meni se check, ne sber, a stare snapshoty zustanou pouzitelne.

Platformni rozdily MX vs EVO se resi tady. Navenek vraci obe platformy
stejne schema.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from lxml import etree

PLATFORMS = ("junos", "junos-evo")


class CollectorError(Exception):
    """Sber jedne oblasti selhal. Ostatni collectory pokracuji."""


class Collector(ABC):
    name: ClassVar[str]
    platforms: ClassVar[tuple[str, ...]] = PLATFORMS

    def supports(self, platform: str) -> bool:
        return platform in self.platforms

    @abstractmethod
    def rpc_name(self, platform: str) -> str:
        """Nazev RPC metody na PyEZ Device.rpc (podtrzitkova varianta)."""

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {}

    @abstractmethod
    def parse(self, xml: etree._Element, platform: str) -> Any:
        """Prevede RPC odpoved na strukturovana data. Zadne verdikty."""

    def collect(self, device: Any, platform: str) -> Any:
        if not self.supports(platform):
            raise CollectorError(
                f"collector '{self.name}' nepodporuje platformu '{platform}'"
            )

        rpc_name = self.rpc_name(platform)
        try:
            rpc = getattr(device.rpc, rpc_name)
        except AttributeError as error:
            raise CollectorError(
                f"collector '{self.name}': RPC '{rpc_name}' neni dostupne - {error}"
            ) from error

        try:
            xml = rpc(**self.rpc_kwargs(platform))
        except Exception as error:  # noqa: BLE001 - RpcError i sitove chyby
            raise CollectorError(
                f"collector '{self.name}': RPC '{rpc_name}' selhalo - "
                f"{type(error).__name__}: {error}"
            ) from error

        try:
            return self.parse(xml, platform)
        except Exception as error:  # noqa: BLE001
            raise CollectorError(
                f"collector '{self.name}': parsovani selhalo - "
                f"{type(error).__name__}: {error}"
            ) from error
