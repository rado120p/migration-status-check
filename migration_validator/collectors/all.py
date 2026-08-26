"""Import vsech collectoru kvuli registraci."""

from __future__ import annotations

from migration_validator.collectors import (  # noqa: F401
    arp,
    bfd,
    bgp,
    evpn,
    interfaces,
    isis,
    ldp,
    mpls,
    nd,
    optics,
    pim,
    routes,
)


def load_all() -> None:
    return None
