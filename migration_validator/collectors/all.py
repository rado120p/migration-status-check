"""Import vsech collectoru kvuli registraci."""

from __future__ import annotations

from migration_validator.collectors import (  # noqa: F401
    arp,
    bfd,
    bgp,
    evpn,
    interfaces,
    nd,
    optics,
    routes,
)


def load_all() -> None:
    return None
