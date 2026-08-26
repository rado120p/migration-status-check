"""Selekce novych protokolovych areas. isis_overview je device-global a
patri jen loopback scopu - transit by s nim tvrdil mereni, ktere se ho netyka."""

from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope

FACTS = {
    "isis_adjacency": {
        "ge-0/0/0.0": {"state": "Up"},
        "ge-0/0/1.0": {"state": "Down"},
    },
    "isis_interface": {
        "ge-0/0/0.0": {"level": 2},
        "ge-0/0/1.0": {"level": 2},
    },
    "isis_overview": {"overload_enabled": False},
    "ldp_neighbor": {
        "ge-0/0/0.0": {"state": "Operational"},
        "ge-0/0/1.0": {"state": "Operational"},
    },
    "pim_neighbor": {
        "ge-0/0/0.0": {"mode": "sparse"},
        "ge-0/0/1.0": {"mode": "sparse"},
    },
    "mpls_interface": {
        "ge-0/0/0.0": {"admin_group": []},
        "ge-0/0/1.0": {"admin_group": []},
    },
    "bfd": {
        "10.0.0.1": {"state": "Up", "interface": "ge-0/0/0.0"},
        "10.0.0.9": {"state": "Up", "interface": "ge-0/0/9.0"},
    },
}


def _core_transit_scope() -> Scope:
    return Scope(
        id="svc:core-transit:Core",
        kind="service",
        key=ScopeKey("core-transit", "Core", "transit"),
        selectors=Selectors(
            interfaces=["ge-0/0/0.0"],
            physical_interfaces=["ge-0/0/0"],
        ),
    )


def _core_loopback_scope() -> Scope:
    return Scope(
        id="svc:core-loopback:Core",
        kind="service",
        key=ScopeKey("core-loopback", "Core", "loopback"),
        selectors=Selectors(interfaces=["lo0.0"]),
    )


def _internet_scope_same_interface() -> Scope:
    """Zakaznicky scope se stejnym rozhranim, jake ma transit Core session."""
    return Scope(
        id="svc:internet-cust:Internet",
        kind="service",
        key=ScopeKey("internet-cust", "Internet", None),
        selectors=Selectors(
            interfaces=["ge-0/0/0.0"],
            physical_interfaces=["ge-0/0/0"],
        ),
    )


def test_transit_scope_selects_only_own_interface_for_per_interface_areas():
    scope = _core_transit_scope()
    selected = scope.select(FACTS)

    assert selected["isis_adjacency"] == {"ge-0/0/0.0": {"state": "Up"}}
    assert selected["isis_interface"] == {"ge-0/0/0.0": {"level": 2}}
    assert selected["ldp_neighbor"] == {"ge-0/0/0.0": {"state": "Operational"}}
    assert selected["pim_neighbor"] == {"ge-0/0/0.0": {"mode": "sparse"}}
    assert selected["mpls_interface"] == {"ge-0/0/0.0": {"admin_group": []}}


def test_isis_overview_goes_only_to_loopback_scope():
    """Mutant kill (2026-08-26, overeno spustenim): smazani podminky
    `if self.service_subtype == "loopback"` u isis_overview v Scope.select
    necha tenhle test padnout (transit by dostal overview taky)."""
    transit = _core_transit_scope()
    loopback = _core_loopback_scope()

    assert transit.select(FACTS)["isis_overview"] == {}
    assert loopback.select(FACTS)["isis_overview"] == {"overload_enabled": False}


def test_isis_overview_reaches_device_scope():
    assert device_scope().select(FACTS)["isis_overview"] == {"overload_enabled": False}


def test_bfd_session_reaches_core_transit_scope_by_interface():
    scope = _core_transit_scope()
    selected = scope.select(FACTS)

    assert "10.0.0.1" in selected["bfd"]
    assert selected["bfd"]["10.0.0.1"]["interface"] == "ge-0/0/0.0"
    # Session na jinem rozhrani do tohoto scopu nepatri.
    assert "10.0.0.9" not in selected["bfd"]


def test_bfd_by_interface_path_does_not_apply_to_customer_scope():
    """Cesta pres rozhrani plati jen pro Core transit - zakaznicky scope se
    stejnym rozhranim v selektorech session nesmi dostat."""
    scope = _internet_scope_same_interface()
    selected = scope.select(FACTS)

    assert selected["bfd"] == {}


def test_selectors_protocols_roundtrip():
    selectors = Selectors(protocols=["isis", "ldp", "pim"])
    restored = Selectors.from_dict(selectors.to_dict())

    assert restored.protocols == ["isis", "ldp", "pim"]
