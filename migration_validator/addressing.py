"""Predikaty nad adresami, spolecne pro capture i evaluate.

Zije mimo `checks/` i `probes/` schvalne: obe vrstvy sem sahaji dolu,
stejne jako obe sahaji do `models/`. Kdyby predikaty vlastnila jedna z
nich, druha by na ni musela zaviset - capture na evaluate, nebo naopak.
"""

from __future__ import annotations

import ipaddress

from migration_validator.models.scope import Scope


def is_link_local(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_link_local
    except ValueError:
        return False


def link_local_is_configured(scope: Scope) -> bool:
    """Ma sluzba link-local adresu primo pod rozhranim?

    Link-local sousede se objevi u kazdeho IPv6 rozhrani a o zakaznicke
    sluzbe nerikaji nic. Existuji ale nasazeni, kde sluzba pouziva link-local
    - staci, aby mela mezi nakonfigurovanymi adresami jednu link-local, klidne
    i vedle bezne routovatelne - pak je link-local soused legitimni cil.
    Rozhoduje konfigurace (pritomnost, ne vylucnost), ne heuristika.
    """
    for address in scope.selectors.local_ipv6:
        try:
            if ipaddress.ip_interface(address).ip.is_link_local:
                return True
        except ValueError:
            continue
    return False
