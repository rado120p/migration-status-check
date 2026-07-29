"""Testy checku statickych rout.

Check na XML nesaha - fakta se skladaji rucne, protoze prave kombinace
'nakonfigurovano ale nenainstalovano' se z jedne nahravky vytahnout neda.
"""

from __future__ import annotations

import pytest

from migration_validator.checks.base import CheckContext
from migration_validator.checks.routes import StaticRouteStatusCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope

CONFIGURED = [
    {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]},
]


def _scope(static_routes=None) -> Scope:
    return Scope(
        id="svc:CPE13:Internet",
        kind="service",
        key=ScopeKey(description="CPE13", service_type="Internet"),
        selectors=Selectors(static_routes=list(static_routes or [])),
    )


def _ctx(subject_routes, baseline_routes=None, scope=None) -> CheckContext:
    return CheckContext(
        scope=scope or _scope(CONFIGURED),
        subject={"routes": subject_routes},
        baseline={"routes": baseline_routes} if baseline_routes is not None else None,
        config=default_config(),
    )


def _installed(next_hop="152.11.13.2"):
    return {
        "inet.0": {
            "198.62.1.0/29": {
                "next_hop": [next_hop],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        }
    }


def test_installed_route_passes():
    findings = StaticRouteStatusCheck().run(_ctx(_installed(), _installed()))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "152.11.13.2"


def test_configured_but_not_installed_is_broken():
    """Presne to, co laborka delala 2026-07-29: 5 statik v konfiguraci, 0 v tabulce."""
    findings = StaticRouteStatusCheck().run(_ctx({}, _installed()))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "neni v tabulce"
    assert findings[0].baseline_value == "152.11.13.2"


def test_changed_next_hop_is_degraded_and_carries_the_old_value():
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed("152.11.13.9"), _installed("152.11.13.2"))
    )

    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "152.11.13.9"
    assert findings[0].baseline_value == "152.11.13.2"


def test_route_that_vanished_from_config_and_table_is_broken():
    """Routa vyrazena z konfigurace se do selektoru subjektu nedostane.

    Bez tretiho zdroje sjednoceni (mereni baseline) by se scope na jeji
    chybeni nikdy nezeptal a zmizela by beze stopy.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx({}, _installed(), scope=_scope([]))
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "chybi"


def test_new_route_passes():
    findings = StaticRouteStatusCheck().run(_ctx(_installed(), {}))

    assert findings[0].outcome is Outcome.OK
    assert findings[0].baseline_value is None


def test_family_comes_from_the_prefix_not_the_rib_name():
    """Bez rodiny by radek spadl do bezhlavickove sekce nad IPv4 i IPv6."""
    ctx = _ctx(
        {
            "inet6.0": {
                "2001:aaaa::/64": {
                    "next_hop": ["2001:abcd:11:13::b"],
                    "via": ["et-0/0/8.13"],
                    "active": True,
                }
            }
        },
        scope=_scope(
            [{"rib": "inet6.0", "prefix": "2001:aaaa::/64", "next_hop": []}]
        ),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].family == 6


def test_family_survives_a_rib_name_that_does_not_say_the_family():
    """RIB jmeno nemusi rodinu vubec obsahovat (napr. VRF bez '.inet6.').

    Kdyby check cetl rodinu ze jmena RIB misto z prefixu, tenhle prefix by
    se pri hledani '6' ve jmene minul a spadl by do IPv4 nebo bezhlavickove
    sekce.
    """
    ctx = _ctx(
        {
            "L3VPN-CPE13-NNI": {
                "2001:aaaa::/64": {
                    "next_hop": ["2001:abcd:11:13::b"],
                    "via": ["et-0/0/8.13"],
                    "active": True,
                }
            }
        },
        scope=_scope(
            [{"rib": "L3VPN-CPE13-NNI", "prefix": "2001:aaaa::/64", "next_hop": []}]
        ),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].family == 6


def test_label_carries_rib_and_prefix():
    """Identita jde do popisku, next-hop je hodnota - jinak by ZMENA vypsala par dvakrat."""
    findings = StaticRouteStatusCheck().run(_ctx(_installed()))

    assert findings[0].label == "Staticka routa (inet.0 198.62.1.0/29)"


def test_service_without_static_routes_gets_no_row():
    """R-1: chybejici konfigurace se v bloku neprojevi vubec."""
    findings = StaticRouteStatusCheck().run(_ctx({}, {}, scope=_scope([])))

    assert findings == []


def test_device_scope_reports_state_without_intent():
    """Bez inventory neni zamer znam, takze se nehlasi 'nakonfigurovano a chybi'."""
    ctx = CheckContext(
        scope=device_scope(),
        subject={"routes": _installed()},
        baseline=None,
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
