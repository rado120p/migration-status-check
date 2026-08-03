"""Predikaty nad adresami maji jeden vyskyt (AR-41).

Do vlny 5 existovaly dvakrat - v `checks/reachability.py` a v
`probes/ping.py`. Rozchazeni uz jednou zpusobilo chybu: Task 13b opravoval
docstring, ktery zasel nepravdive tvrzeni, zatimco druha kopie docstring
vubec nemela.
"""

import pytest

from migration_validator.addressing import is_link_local, link_local_is_configured
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _scope(local_ipv6):
    return Scope(
        id="svc:X:Internet",
        kind="service",
        key=ScopeKey("X", "Internet", None),
        selectors=Selectors(interfaces=["et-0/0/8.13"], local_ipv6=list(local_ipv6)),
    )


@pytest.mark.parametrize(
    "address,expected",
    [
        ("fe80::1", True),
        ("2001:db8::1", False),
        ("169.254.1.1", True),
        ("152.11.13.2", False),
        ("", False),
        ("neni adresa", False),
    ],
)
def test_is_link_local_recognises_both_families_and_survives_junk(address, expected):
    """Zabiji mutanta, ktery `except ValueError` zmeni na holy `return`.

    Prazdny retezec a nesmysl musi dat False, ne vyjimku - collector umi
    vratit oboji. Sourozenec `test_link_local_is_configured_*` tohle
    nehlida, ten se diva jen na selektory scopu.
    """
    assert is_link_local(address) is expected


def test_link_local_is_configured_finds_it_next_to_a_routable_address():
    """Zabiji mutanta, ktery podminku zmeni na vylucnost (`all` misto `any`).

    Rozhoduje pritomnost, ne vylucnost: sluzba muze mit link-local vedle
    bezne routovatelne adresy a link-local soused je pak legitimni cil.
    """
    assert link_local_is_configured(_scope(["2001:db8::1/64", "fe80::1/64"])) is True


def test_link_local_is_configured_is_false_without_one():
    assert link_local_is_configured(_scope(["2001:db8::1/64"])) is False


def test_link_local_is_configured_skips_unparsable_entries():
    """Zabiji mutanta, ktery `continue` v `except` zmeni na `return False`.

    Nesmyslny zaznam pred platnym link-localem nesmi hledani ukoncit.
    """
    assert link_local_is_configured(_scope(["nesmysl", "fe80::1/64"])) is True
