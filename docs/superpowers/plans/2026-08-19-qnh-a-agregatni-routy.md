# QNH per-hop záznamy a agregátní routy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Statické routy nesou per-hop záznamy (qualified-next-hop s vlastním
`active` a `interface`), agregátní routy se parsují, sbírají druhým RPC
a kontrolují vlastním checkem; uzavírá staré body 20 a 17.

**Architecture:** Jeden sdílený model `StaticRoute` s `route_type`
a `next_hops`; `RoutesCollector` střílí `get_route_information` dvakrát
(static + aggregate) a tagne záznamy klíčem `protocol`; `static_route_status`
filtruje na static a anotuje deaktivované hopy, nový `aggregate_route_status`
porovnává přítomnost. Mapování: hop s interface má precedenci nad subnet
matchem; agregáty se mapují podle RIB (VRF → služby instance, globální →
Core lo0.0).

**Tech Stack:** Python 3, lxml, pytest, PyEZ (jen Task 1 na laborce).

**Spec:** `docs/superpowers/specs/2026-08-19-qnh-a-agregatni-routy-design.md`

## Global Constraints

- Stav se nikdy nefabuluje: tvar RPC odpovědi pro `protocol=aggregate` se
  před použitím ověří na laborce (Task 1); do té doby se nepíše kód, který
  na něm závisí.
- `INVENTORY_SCHEMA_VERSION` 5 → **6** (models/inventory.py:140),
  `SCHEMA_VERSION` 9 → **10** (models/snapshot.py:21). Starý baseline se
  čte tolerantně jen v checku (chybějící `protocol` = static, chybějící
  `next_hops` = bez anotace hopů); load funkcí se tolerance netýká — ty
  dál odmítají cizí verzi.
- Oba parsery (mx.py, evo.py) sdílejí core — testy parseru běží
  parametrizované přes oba (`PARSERS` v tests/parsers/test_static_routes.py).
- Deaktivovaný prvek konfigurace nikdy nemizí ze záměru; hlášení jde přes
  `deactivation_outcome` (checks/deactivation.py:23), nejednoznačnost se
  neeskaluje na FAIL (R-2).
- tests/conftest.py hardcoduje u rout `active: True` — test deaktivace
  musí fakta upravit ručně (viz Task 6).
- Komentáře česky, ve stylu okolí: zapisují omezení, ne popis změny.
- Po každém tasku zelená celá suita: `pytest -q` (aktuálně 1051 passed,
  1 skipped).

---

### Task 1: Ověření RPC odpovědi protocol=aggregate na laborce + fixtures

**Files:**
- Create: `tests/fixtures/rpc/junos-evo/routes.2.xml`
- Create (pokud MX1 agregáty má): `tests/fixtures/rpc/junos/routes.2.xml`

**Interfaces:**
- Produces: nahrávky odpovědí `get-route-information` s
  `<protocol>aggregate</protocol>` — Task 5 z nich čte přesná jména polí
  (`protocol-name`, `active-tag`, tvar `nh` u discard agregátu).

- [ ] **Step 1: Načti heslo laborky a ověř dostupnost**

`MIG_LAB_PASSWORD` je v `~/.bashrc` **pod** guardem pro neinteraktivní
shelly, takže:

```bash
eval "$(grep MIG_LAB_PASSWORD ~/.bashrc)"
ping -c1 -W2 172.20.20.5
```

- [ ] **Step 2: Stáhni odpověď z PTX1 (junos-evo) a MX1 (junos)**

```bash
python3 - <<'EOF'
import os
from jnpr.junos import Device
from lxml import etree

for host, name in (("172.20.20.5", "junos-evo"), ("172.20.20.4", "junos")):
    with Device(host=host, user="admin",
                password=os.environ["MIG_LAB_PASSWORD"],
                normalize=True) as dev:
        xml = dev.rpc.get_route_information(protocol="aggregate")
        out = f"tests/fixtures/rpc/{name}/routes.2.xml"
        etree.ElementTree(xml).write(out, pretty_print=True)
        print(out, "OK")
EOF
```

- [ ] **Step 3: Ověř tvar proti předpokladům specu**

Otevři obě nahrávky a zkontroluj: (a) `protocol-name` agregátu — očekávaná
hodnota `Aggregate` (case ověřit!), (b) `active-tag` nese `*` u aktivní
routy, (c) discard agregát nemá `<to>` ani `<via>` (nebo nese `nh-type
Discard` — zapiš skutečný tvar). Pokud MX1 žádný agregát nakonfigurovaný
nemá (prázdná odpověď), `tests/fixtures/rpc/junos/routes.2.xml` nevytvářej,
nahlas to uživateli v závěrečném reportu tasku a pokračuj — conformance
test čte jen existující nahrávky (`_fixture_paths`,
tests/collectors/test_conformance.py:75).

- [ ] **Step 4: Zapiš skutečnou hodnotu protocol-name do specu**

Pokud se liší od `Aggregate` (jiný case/tvar), oprav Task 5 tohoto plánu
a sekci 3 specu. Jinak beze změny.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/rpc/*/routes.2.xml
git commit -m "test: nahravka get-route-information protocol=aggregate z laborky"
```

---

### Task 2: Parser — StaticRoute s route_type a next_hops

**Files:**
- Modify: `migration_validator/parsers/core.py:147-159` (dataclass),
  `migration_validator/parsers/core.py:622-672` (`_static_routes_under`)
- Modify: `migration_validator/parsers/core.py:1186-1225`
  (`_assign_static_routes` — jen minimální adaptace, precedence až Task 3)
- Test: `tests/parsers/test_static_routes.py`

**Interfaces:**
- Produces: `StaticRoute(rib: str, prefix: str, route_type: str = "static",
  next_hops: list[dict], active: bool = True)`; hop dict =
  `{"to": str, "interface": str | None, "qualified": bool, "active": bool}`.
  Pole `next_hop` zaniká. Serializace přes `asdict` v `_assign_static_routes`
  → dicty ve `service.static_route` nesou nové klíče automaticky.

- [ ] **Step 1: Napiš failující testy**

Do `tests/parsers/test_static_routes.py` přidej (XML tvary doslovně
z laborky 2026-08-19, viz spec):

```python
QNH_MIX = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>13</name>
        <description>CPE13-NNI</description>
        <family>
          <inet6><address><name>2001:abcd:11:14::a/64</name></address></inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-options>
    <rib>
      <name>inet6.0</name>
      <static>
        <route>
          <name>2001:aaaa::/64</name>
          <next-hop>2001:abcd:11:14::4</next-hop>
          <qualified-next-hop inactive="inactive">
            <name>2001:db8::ffff</name>
          </qualified-next-hop>
        </route>
        <route>
          <name>2001:abcd:11:13::/64</name>
          <qualified-next-hop>
            <name>fe80::2</name>
            <interface>et-0/0/8.13</interface>
          </qualified-next-hop>
        </route>
      </static>
      <aggregate>
        <route>
          <name>2001:abcd::/32</name>
          <discard/>
        </route>
      </aggregate>
    </rib>
    <aggregate>
      <route>
        <name>198.62.0.0/16</name>
        <discard/>
      </route>
    </aggregate>
  </routing-options>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE13-NNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.13</name></interface>
      <routing-options>
        <aggregate>
          <route>
            <name>172.26.0.0/16</name>
            <discard/>
          </route>
        </aggregate>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_qualified_next_hop_ma_vlastni_zaznam_a_active(parser_cls):
    parser = parser_cls(etree.fromstring(QNH_MIX))
    routes = {r.prefix: r for r in parser.static_routes}

    mixed = routes["2001:aaaa::/64"]
    assert mixed.route_type == "static"
    assert mixed.next_hops == [
        {"to": "2001:abcd:11:14::4", "interface": None,
         "qualified": False, "active": True},
        {"to": "2001:db8::ffff", "interface": None,
         "qualified": True, "active": False},
    ]

    qnh_only = routes["2001:abcd:11:13::/64"]
    assert qnh_only.next_hops == [
        {"to": "fe80::2", "interface": "et-0/0/8.13",
         "qualified": True, "active": True},
    ]


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_aggregate_routy_se_parsuji_globalne_v_rib_i_v_instanci(parser_cls):
    parser = parser_cls(etree.fromstring(QNH_MIX))
    aggregates = {
        (r.rib, r.prefix): r
        for r in parser.static_routes
        if r.route_type == "aggregate"
    }
    assert set(aggregates) == {
        ("inet.0", "198.62.0.0/16"),
        ("inet6.0", "2001:abcd::/32"),
        ("L3VPN-CPE13-NNI.inet.0", "172.26.0.0/16"),
    }
    assert all(r.next_hops == [] for r in aggregates.values())
    assert all(r.active for r in aggregates.values())
```

Pozn.: fixture `L3VPN-CPE13-NNI.inet.0` — agregát přímo pod
`routing-options` instance dostává default RIB instance, stejné odvození
jako u statik (`_static_routes_under`, core.py:627).

- [ ] **Step 2: Ověř, že failují**

Run: `pytest tests/parsers/test_static_routes.py -k "qualified_next_hop_ma or aggregate_routy_se" -v`
Expected: FAIL — `StaticRoute` nemá `next_hops` / `route_type`.

- [ ] **Step 3: Implementuj model a parsování**

V `parsers/core.py` nahraď dataclass (komentář o deaktivaci u `active`
zůstává, jen se doplní věta o hopech):

```python
@dataclass
class StaticRoute:
    """Jedna routa z konfigurace — záměr, ne stav routovací tabulky."""

    rib: str
    prefix: str
    route_type: str = "static"  # "static" | "aggregate"
    # Per-hop záznamy: {"to", "interface", "qualified", "active"}.
    # qualified-next-hop jde deaktivovat individuálně, takže aktivita
    # patří hopu; route-level `active` níž nese deaktivaci routy nebo
    # kontejneru. Agregát hopy nemá — porovnává se přítomnost.
    next_hops: list[dict[str, Any]] = field(default_factory=list)
    active: bool = True
```

V `_static_routes_under` rozšiř containers o aggregate a route_type:

```python
containers: list[tuple[str, str, etree._Element]] = [
    (default_rib, kind, node)
    for kind in ("static", "aggregate")
    for node in options_node.xpath(f"./{kind}")
]

for rib_node in options_node.xpath("./rib"):
    rib_name = first_text(rib_node, "./name/text()")

    if not rib_name:
        continue

    containers.extend(
        (rib_name, kind, node)
        for kind in ("static", "aggregate")
        for node in rib_node.xpath(f"./{kind}")
    )

routes: list[StaticRoute] = []

for rib_name, route_type, container_node in containers:
    for route_node in container_node.xpath("./route"):
        prefix = first_text(route_node, "./name/text()")

        if not prefix:
            continue

        routes.append(
            StaticRoute(
                rib=rib_name,
                prefix=prefix,
                route_type=route_type,
                next_hops=(
                    self._parse_next_hops(route_node)
                    if route_type == "static"
                    else []
                ),
                active=not self._is_inactive(route_node),
            )
        )

return routes
```

Nová metoda vedle `_static_routes_under` (nahrazuje starý komentář
o qualified-next-hop — odstavec „vědomě odloženo" smaž, výčet tvarů bez
adresy se zkrátí na discard/reject/next-table):

```python
def _parse_next_hops(self, route_node: etree._Element) -> list[dict[str, Any]]:
    """Per-hop záznamy. discard, reject a next-table adresu nemají,
    takže hop nevydávají — u agregátu se <discard/> ignoruje ze
    stejného důvodu.

    Holý next-hop nejde deaktivovat individuálně — jeho deaktivace je
    deaktivace routy a nese ji route-level `active`. qualified-next-hop
    individuálně deaktivovat jde (změřeno 2026-08-19: `inactive` sedí
    přímo na jeho uzlu); `_is_inactive` chodí po předcích, takže hop
    pod deaktivovanou routou vyjde neaktivní taky.
    """
    hops = [
        {"to": text, "interface": None, "qualified": False, "active": True}
        for text in all_texts(route_node, "./next-hop/text()")
    ]

    for qnh_node in route_node.xpath("./qualified-next-hop"):
        to = first_text(qnh_node, "./name/text()")

        if not to:
            continue

        hops.append(
            {
                "to": to,
                "interface": first_text(qnh_node, "./interface/text()"),
                "qualified": True,
                "active": not self._is_inactive(qnh_node),
            }
        )

    return hops
```

V `_assign_static_routes` (core.py:1208-1216) minimální adaptace, aby
suita zůstala zelená (plná precedence až Task 3):

```python
matched = [
    route
    for route in self.static_routes
    if route.route_type == "static"
    and rib_instance(route.rib) == service.routing_instance
    and any(
        self._ip_in_interface_subnet(hop["to"], interface)
        for hop in route.next_hops
    )
]
```

- [ ] **Step 4: Spusť celou suitu, oprav zbylé čtenáře `next_hop`**

Run: `pytest -q`
Očekávané dopady: testy stavějící `StaticRoute(next_hop=[...])` nebo
čtoucí `route.next_hop` — přepiš na `next_hops` tvar
(`{"to": ..., "interface": None, "qualified": False, "active": True}`).
Checků a conftestu se to zatím netýká: selektorové dicty v testech checků
jsou ručně psané a check čte jen `rib`/`prefix`/`active`.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/parsers/core.py tests/parsers/test_static_routes.py
git commit -m "feat: StaticRoute nese per-hop zaznamy a parsuje aggregate kontejnery"
```

---

### Task 3: Mapování — precedence interface a agregáty na služby

**Files:**
- Modify: `migration_validator/parsers/core.py` (`_assign_static_routes`)
- Test: `tests/parsers/test_static_routes.py`

**Interfaces:**
- Consumes: `StaticRoute.next_hops`, `route_type` z Tasku 2;
  `rib_instance(rib)` (core.py:264), `_ip_in_interface_subnet` (core.py:1164).
- Produces: `service.static_route` obsahuje i agregáty; `detection_reason`
  dostává pro agregáty vlastní řádek.

- [ ] **Step 1: Napiš failující testy**

```python
@pytest.mark.parametrize("parser_cls", PARSERS)
def test_hop_s_interface_se_mapuje_jen_podle_rozhrani(parser_cls):
    # et-0/0/8.13 ma nakonfigurovany link-local subnet; kdyby bezel subnet
    # match, fe80::2 by matchnul i jina rozhrani s fe80::/64. Interface
    # je autoritativni (spec, bod 2).
    parser = parser_cls(etree.fromstring(QNH_LINK_LOCAL))
    by_iface = {s.interface: s for s in parser.services()}

    assert [r["prefix"] for r in by_iface["et-0/0/8.13"].static_route] == [
        "2001:abcd:11:13::/64"
    ]
    # Druhe rozhrani s tymz link-local subnetem routu nedostane.
    assert by_iface["et-0/0/8.14"].static_route == []


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_routa_s_deaktivovanym_jedinym_hopem_zustava_u_sluzby(parser_cls):
    parser = parser_cls(etree.fromstring(QNH_INACTIVE_ONLY))
    service = next(s for s in parser.services() if s.interface == "et-0/0/8.13")
    assert [r["prefix"] for r in service.static_route] == ["2001:aaaa::/64"]


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_vrf_aggregate_se_mapuje_na_sluzby_instance(parser_cls):
    parser = parser_cls(etree.fromstring(QNH_MIX))
    service = next(s for s in parser.services() if s.interface == "et-0/0/8.13")
    prefixes = [r["prefix"] for r in service.static_route
                if r["route_type"] == "aggregate"]
    assert prefixes == ["172.26.0.0/16"]


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_globalni_aggregate_se_mapuje_jen_na_core_lo0(parser_cls):
    parser = parser_cls(etree.fromstring(AGGREGATE_WITH_CORE))
    by_iface = {s.interface: s for s in parser.services()}

    core_prefixes = [r["prefix"] for r in by_iface["lo0.0"].static_route]
    assert core_prefixes == ["198.62.0.0/16"]
    # Tranzitni Core rozhrani globalni agregat nedostane.
    assert all(
        r["route_type"] != "aggregate"
        for r in by_iface["et-0/0/1.0"].static_route
    )
```

K tomu tři nové XML konstanty: `QNH_LINK_LOCAL` (dvě rozhraní se stejným
fe80::/64 subnetem + routa s QNH `fe80::2` interface `et-0/0/8.13`),
`QNH_INACTIVE_ONLY` (routa jen s `<qualified-next-hop inactive="inactive">`
s adresou v subnetu et-0/0/8.13) a `AGGREGATE_WITH_CORE` (lo0.0 s adresou
150.0.0.11/32 + core rozhraní et-0/0/1.0 s ISIS/MPLS, ať detekce dá
service_type Core oběma — vzor tvaru viz `tests/fixtures/172.20.20.4.yml`
záznam lo0.0 a jeho detection_reason; globální
`<aggregate><route><name>198.62.0.0/16</name><discard/></route></aggregate>`).

- [ ] **Step 2: Ověř, že failují**

Run: `pytest tests/parsers/test_static_routes.py -k "interface_se_mapuje or deaktivovanym_jedinym or vrf_aggregate or globalni_aggregate" -v`
Expected: FAIL (precedence neexistuje, agregáty se nemapují).

- [ ] **Step 3: Implementuj mapování**

Nahraď tělo smyčky v `_assign_static_routes`:

```python
for service in services:
    interface = interface_configs_by_name.get(service.interface)

    if interface is None:
        continue

    matched = [
        route
        for route in self.static_routes
        if self._route_matches_service(route, service, interface)
    ]

    if not matched:
        continue

    service.static_route = [asdict(route) for route in matched]

    statics = [r.prefix for r in matched if r.route_type == "static"]
    aggregates = [r.prefix for r in matched if r.route_type == "aggregate"]
    if statics:
        service.detection_reason.append(
            "Statická routa odpovídá subnetu rozhraní: " + ", ".join(statics)
        )
    if aggregates:
        service.detection_reason.append(
            "Agregátní routa patří této službě: " + ", ".join(aggregates)
        )
```

a přidej predikát (hned za `_assign_static_routes`):

```python
def _route_matches_service(
    self,
    route: StaticRoute,
    service: InterfaceService,
    interface: InterfaceConfig,
) -> bool:
    """Precedence, ne fallback: hop s interface se mapuje jen podle
    jména rozhraní. Link-local subnet bývá nakonfigurovaný na víc
    rozhraních (změřeno na et-0/0/8.13, 2026-08-19), takže subnet match
    na fe80 adresu by routu rozstřelil na služby, kterých se netýká.

    Matchují se i neaktivní hopy: routa, jejíž jediný hop operátor
    deaktivoval, musí zůstat u své služby — jinak by spadla do
    NEZARAZENO přesně ve chvíli, kdy ji report má hlásit.

    Agregát hopy nemá a mapuje se podle RIB: VRF na služby instance,
    globální jen na Core lo0.0 (rozhodnutí uživatele 2026-08-19 — ne na
    tranzitní rozhraní).
    """
    if rib_instance(route.rib) != service.routing_instance:
        return False

    if route.route_type == "aggregate":
        if rib_instance(route.rib) is not None:
            return True
        return service.service_type == "Core" and service.interface == "lo0.0"

    for hop in route.next_hops:
        target = hop["interface"]

        if target is None and self._parse_ip(hop["to"]) is None:
            # <name> vyjimecne nese jmeno rozhrani misto adresy
            # (mereni z vlny 10) - nic vic se nehada.
            target = hop["to"]

        if target is not None:
            if target == service.interface:
                return True
            continue

        if self._ip_in_interface_subnet(hop["to"], interface):
            return True

    return False

@staticmethod
def _parse_ip(value: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None
```

- [ ] **Step 4: Spusť testy**

Run: `pytest tests/parsers/ -q` a poté `pytest -q`
Expected: PASS. Pokud test `test_globalni_aggregate_se_mapuje_jen_na_core_lo0`
padá na detekci (lo0.0 nevyšlo jako Core), uprav XML fixture podle
skutečných pravidel v `_detect_service` — mapovací kód neohýbej.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/parsers/core.py tests/parsers/test_static_routes.py
git commit -m "feat: precedence interface hopu a mapovani agregatu na sluzby"
```

---

### Task 4: Schema bump inventory 6 + snapshot 10, fixtures

**Files:**
- Modify: `migration_validator/models/inventory.py:140`,
  `migration_validator/models/snapshot.py:18-21`
- Modify: `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml`
- Test: `tests/models/` (stávající verzní testy), celá suita

**Interfaces:**
- Consumes: nový tvar `static_route` dictů z Tasku 2.
- Produces: `INVENTORY_SCHEMA_VERSION = 6`, `SCHEMA_VERSION = 10` — Task 5
  a 6 na nich stavějí.

- [ ] **Step 1: Bumpni konstanty s komentářem**

`models/inventory.py`: `INVENTORY_SCHEMA_VERSION = 6` a do docstringu
`load_inventory` doplň řádek:

```
Verze 6 nahradila u static_route ploché next_hop per-hop záznamy
next_hops (+ route_type). Tolerantní čtení by vrátilo záměr bez hopů,
mapování i anotace deaktivace by tiše zmizely.
```

`models/snapshot.py`:

```python
# 10: route zaznamy nesou klic 'protocol' (static | aggregate) a selektory
#     static_routes maji per-hop next_hops + route_type misto plocheho
#     next_hop.
SCHEMA_VERSION = 10
```

- [ ] **Step 2: Přepiš fixtures na nový tvar**

V `tests/fixtures/172.20.20.4.yml` a `172.20.20.5.yml`: `schema_version: 6`
a každý z 6 výskytů `next_hop:` v `static_route` záznamech přepiš
mechanicky, např.:

```yaml
# z:
    next_hop:
    - 10.40.96.2
# na:
    route_type: static
    next_hops:
    - to: 10.40.96.2
      interface: null
      qualified: false
      active: true
```

Fixtures drží starý stav laborky **záměrně** (viz memory o lab driftu) —
měň jen tvar, nepřidávej agregáty.

- [ ] **Step 3: Spusť suitu a oprav čtenáře**

Run: `pytest -q`
Očekávané dopady: `tests/conftest.py:227-231` staví fakta z
`route.get("next_hop")` — přepiš na:

```python
for route in scope.selectors.static_routes:
    routes.setdefault(str(route["rib"]), {})[str(route["prefix"])] = {
        "next_hop": [
            hop["to"] for hop in route.get("next_hops") or [] if hop["active"]
        ],
        "via": list(scope.selectors.interfaces[:1]),
        "active": True,
        "protocol": str(route.get("route_type", "static")),
    }
```

Dále verzní testy s natvrdo zapsanou 5/9 a všechny testové inventory
dicty se `schema_version` — zvedni na 6/10. Selektorové dicty v testech
checků (`CONFIGURED` v tests/checks/test_routes.py) zatím nech — check
starý klíč nečte a Task 6 je přepíše spolu s novou logikou.

- [ ] **Step 4: Commit**

```bash
git add migration_validator/models/ tests/
git commit -m "feat: inventory schema 6 a snapshot schema 10 pro per-hop routy"
```

---

### Task 5: Collector — druhé RPC protocol=aggregate

**Files:**
- Modify: `migration_validator/collectors/routes.py`
- Test: `tests/collectors/test_routes.py`

**Interfaces:**
- Consumes: nahrávky z Tasku 1; vzor multi-RPC collectoru je
  `InterfacesCollector.collect` (collectors/interfaces.py:92-126)
  a `rpc_calls` (base.py:47).
- Produces: fakta `routes[table][prefix]` nesou nový klíč
  `"protocol": "static" | "aggregate"` — Task 6 a 7 podle něj filtrují.

- [ ] **Step 1: Napiš failující testy**

Do `tests/collectors/test_routes.py` (syntetické XML podle skutečného
tvaru z Tasku 1 — pole ověř proti nahrávce, toto je předloha):

```python
AGGREGATE_XML = b"""
<route-information>
  <route-table>
    <table-name>inet6.0</table-name>
    <rt>
      <rt-destination>2001:abcd::/32</rt-destination>
      <rt-entry>
        <active-tag>*</active-tag>
        <protocol-name>Aggregate</protocol-name>
        <nh-type>Discard</nh-type>
      </rt-entry>
    </rt>
  </route-table>
</route-information>
"""


def test_parse_tagne_aggregate_zaznam_protokolem():
    parsed = RoutesCollector().parse(etree.fromstring(AGGREGATE_XML), "junos-evo")
    entry = parsed["inet6.0"]["2001:abcd::/32"]
    assert entry["protocol"] == "aggregate"
    assert entry["next_hop"] == []
    assert entry["via"] == []
    assert entry["active"] is True


def test_parse_tagne_static_zaznam_protokolem():
    # stavajici STATIC_XML konstanta / fixture routes.xml
    parsed = RoutesCollector().parse(_static_xml(), "junos-evo")
    entry = next(iter(next(iter(parsed.values())).values()))
    assert entry["protocol"] == "static"


def test_collect_merguje_oba_pruchody_a_druhy_jen_pridava():
    device = _FakeDevice({
        "static": STATIC_XML,
        "aggregate": AGGREGATE_XML_SE_STEJNYM_PREFIXEM,
    })
    facts = RoutesCollector().collect(device, "junos-evo")
    # prefix z prvniho pruchodu nesmi byt prepsan druhym
    assert facts["inet6.0"]["2001:aaaa::/64"]["protocol"] == "static"


def test_rpc_calls_stril_static_a_aggregate():
    assert RoutesCollector().rpc_calls("junos-evo") == (
        ("get_route_information", {"protocol": "static"}),
        ("get_route_information", {"protocol": "aggregate"}),
    )
```

`_FakeDevice` vrací XML podle `protocol` kwargu — vzor fake device je
v stávajícím test_routes.py / test_interfaces.py, použij tamní idiom.

- [ ] **Step 2: Ověř, že failují**

Run: `pytest tests/collectors/test_routes.py -v`
Expected: nové testy FAIL (`protocol` klíč neexistuje, rpc_calls vrací
jedno volání).

- [ ] **Step 3: Implementuj**

V `collectors/routes.py`:

```python
PROTOCOLS = ("static", "aggregate")


@register
class RoutesCollector(Collector):
    name = "routes"

    def rpc_name(self, platform: str) -> str:
        return "get_route_information"

    def rpc_names(self, platform: str) -> tuple[str, ...]:
        # Dvakrat totez RPC (static + aggregate) - record tak ulozi obe
        # nahravky (routes.xml, routes.2.xml).
        return ("get_route_information", "get_route_information")

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        # Bez `all=True`: ta varianta pridava jen __juniper_private*
        # tabulky, coz je sum. Filtr na protokol drzi odpoved malou i na
        # zarizeni s plnou internetovou tabulkou.
        return {"protocol": STATIC}

    def rpc_calls(self, platform: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        return tuple(
            ("get_route_information", {"protocol": protocol})
            for protocol in PROTOCOLS
        )

    def collect(self, device: Any, platform: str) -> Any:
        # Selhani ktereholiv pruchodu je chyba celeho collectoru (stejny
        # duvod jako u interfaces): bez aggregate pruchodu by check cetl
        # chybejici agregat jako zmizely, castecna data nesmi vypadat
        # jako zmerena.
        if not self.supports(platform):
            raise CollectorError(
                f"collector '{self.name}' nepodporuje platformu '{platform}'"
            )

        tables: dict[str, dict[str, dict[str, Any]]] = {}
        failures: list[str] = []

        for rpc_name, rpc_kwargs in self.rpc_calls(platform):
            variant = f"{rpc_name}(protocol={rpc_kwargs['protocol']})"
            try:
                xml = getattr(device.rpc, rpc_name)(**rpc_kwargs)
            except Exception as error:  # noqa: BLE001 - RpcError i sitove chyby
                failures.append(f"{variant}: {type(error).__name__}: {error}")
                continue

            try:
                parsed = self.parse(xml, platform)
            except Exception as error:  # noqa: BLE001
                failures.append(f"{variant}: parsovani selhalo - {error}")
                continue

            for table, prefixes in parsed.items():
                target = tables.setdefault(table, {})
                for prefix, data in prefixes.items():
                    # Druhy pruchod jen pridava: prefix nemuze byt v jedne
                    # RIB zaroven static a aggregate, a last-write-wins by
                    # jeden z nich tise schoval.
                    target.setdefault(prefix, data)

        if failures:
            raise CollectorError(
                f"collector '{self.name}': RPC selhalo - " + "; ".join(failures)
            )

        return tables
```

a v `parse` zobecni pojistku (řádek 78) — místo porovnání se STATIC:

```python
protocol = (_text(entry, "protocol-name") or "").lower()
if protocol not in PROTOCOLS:
    continue

prefixes[prefix] = {
    "next_hop": _texts(entry, "to"),
    "via": _texts(entry, "via"),
    "active": _text(entry, "active-tag") == ACTIVE_TAG,
    "protocol": protocol,
}
```

Hodnotu `protocol-name` u agregátu ověř proti nahrávce z Tasku 1 — pokud
Junos vrací jiný tvar než `Aggregate`, uprav normalizaci tak, aby ve
faktech skončilo vždy `"aggregate"`.

Docstring modulu doplň: sběr jede dvěma průchody (static, aggregate),
mergem „druhý jen přidává".

- [ ] **Step 4: Spusť testy včetně conformance**

Run: `pytest tests/collectors/ -q && pytest -q`
Expected: PASS — conformance test si nové nahrávky routes.2.xml načte sám
(`_fixture_paths` bere všechny číslované varianty).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/collectors/routes.py tests/collectors/test_routes.py
git commit -m "feat: routes collector sbira i protocol=aggregate a tagne zaznamy"
```

---

### Task 6: static_route_status — filtr na static a anotace hopů

**Files:**
- Modify: `migration_validator/checks/routes.py`
- Test: `tests/checks/test_routes.py`

**Interfaces:**
- Consumes: selektory s `next_hops`/`route_type` (Task 2), fakta
  s `protocol` (Task 5), `deactivation_outcome(subject_off, baseline_off)`
  (checks/deactivation.py:23).
- Produces: `_flatten(routes, protocol)` — sdílená s Taskem 7; jinak se
  veřejné rozhraní checku nemění.

- [ ] **Step 1: Napiš failující testy**

Do `tests/checks/test_routes.py` — nejdřív přepiš `CONFIGURED*` konstanty
na nový tvar selektorů:

```python
CONFIGURED = [
    {
        "rib": "inet.0",
        "prefix": "198.62.1.0/29",
        "route_type": "static",
        "next_hops": [
            {"to": "152.11.13.2", "interface": None,
             "qualified": False, "active": True},
        ],
    },
]
```

a přidej testy:

```python
def test_aggregate_zaznamy_static_check_ignoruje():
    subject = {
        "inet.0": {
            "198.62.0.0/16": {"next_hop": [], "via": [],
                              "active": True, "protocol": "aggregate"},
        }
    }
    findings = StaticRouteStatusCheck().run(_ctx(subject, scope=_scope([])))
    assert findings == []


def test_stary_baseline_bez_protocol_se_cte_jako_static():
    baseline = {
        "inet.0": {
            "198.62.1.0/29": {"next_hop": ["152.11.13.2"], "via": [],
                              "active": True},
        }
    }
    findings = StaticRouteStatusCheck().run(_ctx(_installed(), baseline))
    assert [f.outcome for f in findings] == [Outcome.OK]


def test_deaktivovany_hop_se_anotuje_na_ok_radku():
    configured = [_route_with_hops([
        {"to": "152.11.13.2", "interface": None,
         "qualified": False, "active": True},
        {"to": "152.11.13.3", "interface": None,
         "qualified": True, "active": False},
    ])]
    ctx = _ctx(_installed(), scope=_scope(configured))
    (finding,) = StaticRouteStatusCheck().run(ctx)
    # hop deaktivovany, baseline neni k porovnani -> DEGRADED (R-2 drzi:
    # deaktivovany prvek je nalez, baseline urcuje jen JAK NAHLAS)
    assert finding.outcome is Outcome.DEGRADED
    assert "deaktivovany next-hop: 152.11.13.3" in finding.message


def test_hop_deaktivovany_i_v_baseline_zamer_je_degraded_s_tichou_zpravou():
    configured = [_route_with_hops([
        {"to": "152.11.13.2", "interface": None,
         "qualified": False, "active": True},
        {"to": "152.11.13.3", "interface": None,
         "qualified": True, "active": False},
    ])]
    ctx = _ctx(
        _installed(),
        scope=_scope(configured),
        baseline_scope=_scope(configured),
        baseline_routes=_installed(),
    )
    (finding,) = StaticRouteStatusCheck().run(ctx)
    assert finding.outcome is Outcome.DEGRADED
    assert "stejne jako v baseline" in finding.message


def test_hop_nove_deaktivovany_vs_baseline_je_broken():
    active_hops = [
        {"to": "152.11.13.2", "interface": None,
         "qualified": False, "active": True},
        {"to": "152.11.13.3", "interface": None,
         "qualified": True, "active": True},
    ]
    now_hops = [dict(active_hops[0]), {**active_hops[1], "active": False}]
    ctx = _ctx(
        _installed(),
        scope=_scope([_route_with_hops(now_hops)]),
        baseline_scope=_scope([_route_with_hops(active_hops)]),
        baseline_routes=_installed(),
    )
    (finding,) = StaticRouteStatusCheck().run(ctx)
    assert finding.outcome is Outcome.BROKEN
    assert "migrace nedokoncena" in finding.message


def test_vsechny_hopy_deaktivovane_a_neni_v_tabulce_neni_broken():
    configured = [_route_with_hops([
        {"to": "152.11.13.2", "interface": None,
         "qualified": True, "active": False},
    ])]
    ctx = _ctx({}, scope=_scope(configured))
    (finding,) = StaticRouteStatusCheck().run(ctx)
    # zadny aktivni hop = zamer neforwardovat; absence v tabulce je
    # informacni stav pres deactivation_outcome, ne BROKEN
    assert finding.outcome is Outcome.DEGRADED
    assert finding.value == "deaktivovana"
```

Helper `_route_with_hops(hops)` vrátí CONFIGURED-tvar dictu s danými hopy;
`_ctx` rozšiř o volitelný `baseline_scope` (CheckContext ho už nese — viz
checks/base.py, parametr `baseline_scope`).

- [ ] **Step 2: Ověř, že failují**

Run: `pytest tests/checks/test_routes.py -v`
Expected: nové testy FAIL, staré PASS.

- [ ] **Step 3: Implementuj**

V `checks/routes.py`:

1. `_flatten` dostane protokolový filtr:

```python
def _flatten(
    routes: dict[str, Any] | None, protocol: str
) -> dict[tuple[str, str], dict[str, Any]]:
    # Chybejici klic 'protocol' je zaznam ze snapshotu pred schematem 10 -
    # tehdy se sbiraly jen statiky, takze default je 'static', ne chyba.
    return {
        (table, prefix): data
        for table, prefixes in (routes or {}).items()
        for prefix, data in prefixes.items()
        if str(data.get("protocol", "static")) == protocol
    }
```

2. V `run` filtruj záměr na static (`route.get("route_type", "static") ==
"static"`) ve všech třech místech, kde se iterují selektory
(`configured`, `deactivated`, `baseline_*`), a `_flatten` volej
s `"static"`. Navíc posbírej per-route hopy:

```python
hops_by_identity = {
    (str(route.get("rib")), str(route.get("prefix"))): route.get("next_hops") or []
    for route in ctx.scope.selectors.static_routes
    if route.get("route_type", "static") == "static"
}
baseline_hops_by_identity = {
    (str(route.get("rib")), str(route.get("prefix"))): route.get("next_hops") or []
    for route in baseline_routes
    if route.get("route_type", "static") == "static"
}
```

3. `_finding` dostane dva nové parametry `hops` a `baseline_hops`
(defaulty `()` kvůli stávajícím jednotkovým testům se nezavádějí — všechna
volání je předávají explicitně). Logika:

```python
inactive_hops = [h for h in hops if not h.get("active", True)]
active_hops = [h for h in hops if h.get("active", True)]
```

- **Routa bez aktivního hopu a mimo tabulku** (`hops and not active_hops
  and subject is None and not deactivated`): chová se jako větev
  deaktivované routy (řádek 163) — `deactivation_outcome(True,
  baseline_all_hops_off)`, `value="deaktivovana"`, zpráva
  `"...: vsechny next-hopy jsou deaktivovane"` (+ `"- migrace
  nedokoncena"` u BROKEN). `baseline_all_hops_off` je `True/False` podle
  baseline hopů téže identity, `None` bez baseline záměru.
- **Smíšené hopy na živém řádku** (`inactive_hops` a jinak OK/diff
  větev): outcome řádku se zvedne přes `deactivation_outcome(True,
  hop_baseline_off)` per deaktivovaný hop (nejhorší vyhrává, BROKEN >
  DEGRADED > původní), do zprávy se doplní
  `"; deaktivovany next-hop: " + ", ".join(_hop_text(h) for h in inactive_hops)`
  a u nově deaktivovaného `" - migrace nedokoncena"`, u shody s baseline
  `" (stejne jako v baseline)"`. `hop_baseline_off` se čte
  z `baseline_hops` porovnáním `(to, interface)` dvojice; hop, který
  v baseline záměru není, dává `None`.
- `_hop_text(h)` vrací `h["to"]` nebo `f"{h['to']} via {h['interface']}"`.

Porovnání hodnot řádku (`was`/`now`) zůstává beze změny z měření —
záměrové hopy do `value` nevstupují.

4. Docstring modulu doplň: check čte jen `protocol == "static"`, agregáty
má `aggregate_route_status`; klíčování `(rib, prefix)` se s per-hop
záznamy nemění — částečná deaktivace žije uvnitř záznamu (uzavřený bod 17).
Zastaralý komentář na řádcích 94-100 (jediný záznam, změřeno 2026-08-04)
přepiš: parser od 2026-08-19 QNH čte, jeden záznam na `(rib, prefix)` dál
drží konstrukcí `_static_routes_under`.

- [ ] **Step 4: Spusť testy**

Run: `pytest tests/checks/test_routes.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/routes.py tests/checks/test_routes.py
git commit -m "feat: static_route_status filtruje protokol a anotuje deaktivovane hopy"
```

---

### Task 7: Nový check aggregate_route_status

**Files:**
- Modify: `migration_validator/checks/routes.py`
- Test: `tests/checks/test_routes.py`

**Interfaces:**
- Consumes: `_flatten(routes, "aggregate")` z Tasku 6, selektory
  s `route_type == "aggregate"`, `deactivation_outcome`.
- Produces: check id `aggregate_route_status`, group `"Agregatni routy"`,
  label `"Agregatni routa"` — objeví se v reportu automaticky přes registry
  (checks/registry.py; checks/all.py modul routes už importuje).

- [ ] **Step 1: Napiš failující testy**

```python
def _aggregate(rib="inet6.0", prefix="2001:abcd::/32", active=True):
    return {"rib": rib, "prefix": prefix, "route_type": "aggregate",
            "next_hops": [], "active": active}


def _aggregate_installed(active=True):
    return {"inet6.0": {"2001:abcd::/32": {
        "next_hop": [], "via": [], "active": active, "protocol": "aggregate"}}}


def test_aggregate_v_tabulce_je_ok():
    ctx = _ctx(_aggregate_installed(), scope=_scope([_aggregate()]))
    (finding,) = AggregateRouteStatusCheck().run(ctx)
    assert finding.outcome is Outcome.OK
    assert finding.group == "Agregatni routy"


def test_aggregate_nakonfigurovany_mimo_tabulku_je_broken():
    ctx = _ctx({}, scope=_scope([_aggregate()]))
    (finding,) = AggregateRouteStatusCheck().run(ctx)
    assert finding.outcome is Outcome.BROKEN
    assert "neni v routovaci tabulce" in finding.message


def test_aggregate_deaktivovany_v_konfiguraci():
    ctx = _ctx({}, scope=_scope([_aggregate(active=False)]))
    (finding,) = AggregateRouteStatusCheck().run(ctx)
    assert finding.outcome is Outcome.DEGRADED
    assert finding.value == "deaktivovana"


def test_aggregate_zmizely_proti_baseline():
    ctx = _ctx({}, baseline_routes=_aggregate_installed(), scope=_scope([]))
    (finding,) = AggregateRouteStatusCheck().run(ctx)
    assert finding.outcome is Outcome.BROKEN
    assert "v baseline byla, v subjektu neni" in finding.message


def test_static_zaznamy_aggregate_check_ignoruje():
    ctx = _ctx(_installed(), scope=_scope(CONFIGURED))
    assert AggregateRouteStatusCheck().run(ctx) == []
```

- [ ] **Step 2: Ověř, že failují**

Run: `pytest tests/checks/test_routes.py -k aggregate -v`
Expected: FAIL — `AggregateRouteStatusCheck` neexistuje.

- [ ] **Step 3: Implementuj**

Do `checks/routes.py` (pod StaticRouteStatusCheck):

```python
@register
class AggregateRouteStatusCheck(Check):
    """Agregat nema next-hop - porovnava se pritomnost a aktivita.

    Sdili se StaticRouteStatusCheck sjednoceni tri zdroju i vetve
    chybi/deaktivovana; nesdili porovnani next-hopu, protoze zadny neni.
    Zmizely zakaznicky agregat po migraci je signal vypadku - proto
    CRITICAL jako u statik.
    """

    id = "aggregate_route_status"
    title = "Stav agregatnich rout"
    label = "Agregatni routa"
    mode = Mode.BOTH
    requires = ("routes",)
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        selected = [
            route
            for route in ctx.scope.selectors.static_routes
            if route.get("route_type", "static") == "aggregate"
        ]
        configured = {
            (str(route.get("rib")), str(route.get("prefix"))) for route in selected
        }
        deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in selected
            if route.get("active", True) is False
        }
        baseline_routes = (
            ctx.baseline_scope.selectors.static_routes
            if ctx.baseline_scope is not None
            else []
        )
        baseline_selected = [
            route
            for route in baseline_routes
            if route.get("route_type", "static") == "aggregate"
        ]
        baseline_deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_selected
            if route.get("active", True) is False
        }
        baseline_configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_selected
        }
        subject = _flatten(ctx.subject.get("routes"), "aggregate")
        baseline = _flatten((ctx.baseline or {}).get("routes"), "aggregate")

        return [
            _presence_finding(
                identity,
                configured=identity in configured,
                subject=subject.get(identity),
                baseline=baseline.get(identity),
                is_device=ctx.scope.is_device,
                deactivated=identity in deactivated,
                baseline_deactivated=(
                    identity in baseline_deactivated
                    if identity in baseline_configured
                    else None
                ),
                label_prefix=self.label,
                group="Agregatni routy",
                value_ok="v tabulce",
            )
            for identity in sorted(configured | set(subject) | set(baseline))
        ]
```

`_presence_finding` je nová modulová funkce: vytáhni do ní ze
`StaticRouteStatusCheck._finding` větve, které jsou pro oba checky
doslova stejné — deaktivovaná routa mimo tabulku (řádek 163), `subject is
None` (řádek 190), `"active" not in subject` (řádek 231), `not
subject["active"]` (řádek 243) — parametrizované přes `group` a
`value_ok`. Pro OK větev vrací `Finding(Outcome.OK, f"{rib} {prefix}:
{value_ok}", value=value_ok, ...)`. Větev ZMENA (`was != now`, řádek 297)
do ní **nepatří** — zůstává jen ve statickém checku, agregát next-hop
nemá. `StaticRouteStatusCheck._finding` pak `_presence_finding` volá a
na výsledné OK/diff řádky aplikuje svou next-hop hodnotu a hop-anotace
z Tasku 6; co stejné není, zůstává v checku.

- [ ] **Step 4: Spusť testy + end-to-end**

Run: `pytest tests/checks/ tests/reporting/ -q && pytest -q`
Expected: PASS. `pytest tests/test_end_to_end.py -q` zvlášť potvrď —
report s prázdnou skupinou agregátů nesmí vyrábět prázdné bloky.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/routes.py tests/checks/test_routes.py
git commit -m "feat: check aggregate_route_status porovnava pritomnost agregatu"
```

---

### Task 8: NEZARAZENO — agregáty v unassigned výpisu

**Files:**
- Modify: `migration_validator/engine.py:360-389`
  (`_unassigned_static_routes`), `migration_validator/reporting/text_report.py`
  (render unassigned rout, pokud vypisuje next_hop)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: fakta s `protocol` (Task 5).
- Produces: unassigned route dict nese `"protocol"`; jinak beze změny.

- [ ] **Step 1: Napiš failující test**

Do `tests/test_engine.py` po vzoru stávajících testů
`_unassigned_static_routes` (najdi je greppem `unassigned_static_routes`):

```python
def test_nezarazeny_aggregate_nese_protokol():
    # agregat v tabulce, ktery si zadny scope nenarokuje (globalni rib,
    # zadna Core lo0.0 sluzba v inventory)
    subject = _snapshot_with_routes({
        "inet6.0": {"2001:abcd::/32": {
            "next_hop": [], "via": [], "active": True,
            "protocol": "aggregate"}},
    })
    rows = engine._unassigned_static_routes(subject, [_service_scope()])
    assert rows == [{
        "rib": "inet6.0", "prefix": "2001:abcd::/32",
        "next_hop": [], "via": [], "protocol": "aggregate",
        "snapshot": "subject",
    }]
```

- [ ] **Step 2: Ověř, že failuje**

Run: `pytest tests/test_engine.py -k nezarazeny_aggregate -v`
Expected: FAIL — dict `protocol` nenese.

- [ ] **Step 3: Implementuj**

V `_unassigned_static_routes` doplň do vraceného dictu
`"protocol": str(data.get("protocol", "static"))` a do docstringu větu:
sem spadne i agregát bez odpovídající služby (VRF bez služeb, box bez
Core lo0.0) — pojistka ze specu, bod 2. V text_report.py zkontroluj
renderer unassigned rout (grep `static_routes` v reporting/) — pokud
řádek skládá jen rib+prefix+next_hop, doplň ` (aggregate)` sufix pro
`protocol == "aggregate"`, ať operátor pozná, co mu vypadlo.

- [ ] **Step 4: Spusť testy**

Run: `pytest tests/test_engine.py tests/reporting/ -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/engine.py migration_validator/reporting/ tests/
git commit -m "feat: NEZARAZENO oznacuje agregatni routy protokolem"
```

---

### Task 9: Dokumentace cs+en

**Files:**
- Modify: `docs/cs/files/parsers.md`, `docs/en/files/parsers.md`
- Modify: `docs/cs/files/collectors.md` (sekce `routes.py`, řádky 224-258),
  `docs/en/files/collectors.md`
- Modify: `docs/cs/files/checks.md`, `docs/en/files/checks.md`
- Modify: `docs/superpowers/specs/2026-08-19-qnh-a-agregatni-routy-design.md`

**Interfaces:** žádné — jen texty; en verze je doslovný překlad cs změn
(en drift už jednou bolel — commit 5e5dd47).

- [ ] **Step 1: Aktualizuj parsers.md (cs, pak en)**

Sekce statických rout: per-hop záznamy (`next_hops` s to/interface/
qualified/active), qualified-next-hop se čte včetně individuální
deaktivace, agregátní kontejnery, mapovací pravidla (precedence interface,
match přes všechny hopy, agregáty VRF → instance / globální → Core lo0.0),
inventory schema 6.

- [ ] **Step 2: Aktualizuj collectors.md (cs, pak en)**

Sekce `routes.py`: dvě RPC volání (`{"protocol": "static"}` +
`{"protocol": "aggregate"}`), merge „druhý průchod jen přidává", nový klíč
`protocol` v ukázce faktů, nahrávky routes.xml + routes.2.xml, snapshot
schema 10.

- [ ] **Step 3: Aktualizuj checks.md (cs, pak en)**

`static_route_status`: filtr na static, anotace deaktivovaných hopů,
sémantika „žádný aktivní hop = záměr neforwardovat". Nová sekce
`aggregate_route_status` se skupinou a větvemi.

- [ ] **Step 4: Oprav spec**

V sekci 5 specu je `probes.md` omylem — routes RPC dokumentuje
`collectors.md` (probes.md je o ping sondách). Přepiš na collectors.md.

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: per-hop staticke routy, agregaty a dvoji RPC v routes collectoru"
```

---

### Task 10: Mutanty, finální suita a stav roadmapy

**Files:**
- Modify: `docs/superpowers/roadmap-2026-08-04-vlna10-hotovo.md` (bod 20 —
  odkaz na uzavření), memory se aktualizuje mimo repo.

**Interfaces:** žádné.

- [ ] **Step 1: Najdi docstringy tvrdící kill mutantu v dotčených souborech**

```bash
grep -rn "mutant\|Mutant" migration_validator/checks/routes.py \
  migration_validator/collectors/routes.py migration_validator/parsers/core.py \
  tests/checks/test_routes.py tests/collectors/test_routes.py \
  tests/parsers/test_static_routes.py
```

Každé takové tvrzení ověř přeběhnutím mutantu (ručně aplikuj popsanou
mutaci, spusť pojmenovaný test, vrať zpět). Zastaralé tvrzení oprav —
pravidlo z vlny 9: tvrzení o mutantovi zastarává.

- [ ] **Step 2: Finální ověření**

```bash
pytest -q
```

Expected: 0 failed; počet testů vzrostl proti 1051/1 skip. Do výstupu
tasku zapiš skutečné číslo (změřené, ne odhadnuté).

- [ ] **Step 3: Uzavři bod 20 v roadmapě**

Do `docs/superpowers/roadmap-2026-08-04-vlna10-hotovo.md` k bodu 20 doplň
jednu větu: uzavřeno 2026-08-19 specem
`2026-08-19-qnh-a-agregatni-routy-design.md` (per-hop záznamy; bod 17
vyřešen bez překlíčování). Bod 21 (ESI DF role) zůstává otevřený.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/roadmap-2026-08-04-vlna10-hotovo.md
git commit -m "docs: bod 20 uzavren - QNH a agregatni routy"
```
