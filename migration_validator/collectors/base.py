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

    def rpc_names(self, platform: str) -> tuple[str, ...]:
        """Vsechna RPC, ktera collector na dane platforme opravdu vola.

        Vetsina collectoru ma jedno. Kdyz jich ma vic, musi tuhle metodu
        prepsat, jinak `record` a `--record-raw` ulozi jen prvni z nich a
        nahrane fixtures budou tise nekompletni.
        """
        return (self.rpc_name(platform),)

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {}

    def record_calls(self, device: Any, platform: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Volani pro `record` a pro collect, kdyz zavisi na zarizeni (napr.
        seznam instanci). Default = staticke rpc_calls."""
        return self.rpc_calls(platform)

    def rpc_calls(self, platform: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Vsechna RPC volani vcetne kwargs, v poradi volani.

        Autorita pro `record` i pro vice-RPC collect. Default odvozuje
        z rpc_names + rpc_kwargs (stejne kwargs pro kazde RPC); collector,
        jehoz volani se lisi jen v kwargs (interfaces: extensive vs terse),
        musi prepsat tuhle metodu, jinak by record nahral dvakrat tutez
        variantu.
        """
        kwargs = self.rpc_kwargs(platform)
        return tuple((name, kwargs) for name in self.rpc_names(platform))

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
