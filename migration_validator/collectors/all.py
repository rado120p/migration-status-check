"""Import vsech collectoru kvuli registraci."""

from __future__ import annotations

from migration_validator.collectors import arp, bgp, evpn, interfaces, nd  # noqa: F401


def load_all() -> None:
    return None
