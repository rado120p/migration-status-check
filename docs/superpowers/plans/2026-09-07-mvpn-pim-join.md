# MVPN-PIM join Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Očekávané multicast (S,G) páry MVPN služby se čtou i z PIM join tabulky (receiver i sender site), subtype `mvpn-igmp` se sjednotí na `mvpn`, a forwarding / c-multicast checky pracují per role.

**Architecture:** Parser dostane per-rozhraní PIM záměr (RI-aware, jako IGMP) a `mvpn_site` roli instance z konfigurace. Nový collector `pim_join` (jedno RPC, EVO `instance all`, MX master + per-VRF přes sdílenou základnu s `multicast_route`). V `checks/multicast.py` helper `expected_pairs()` sjednotí IGMP páry (role receiver) a PIM páry (role z upstream/downstream); nový check `pim_join` a stávající forwarding / c-multicast checky nad tímto sjednocením.

**Tech Stack:** Python 3.13, lxml, PyEZ (`jnpr.junos`), pytest. Test runner: `pyats-venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-09-07-mvpn-pim-join-design.md`

## Global Constraints

- Branch `mvpn-pim-join` (odbočená z `oprava-2026-09-07`, na ní se staví). Commity s trailerem `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` a `Claude-Session: https://claude.ai/code/session_01WoX3LB4kcqGA1NznewHCcp`.
- Stav se nikdy nefabuluje: collector nic nesyntetizuje, absence klíče = absence ve výpisu.
- Oba parsery (MX i EVO) se mění v zámku, parser testy parametrizované přes `PARSERS`.
- `INVENTORY_SCHEMA_VERSION` 8 → **9**; snapshot `SCHEMA_VERSION` 12 → **13**.
- Subtype `mvpn-igmp` zaniká, nový je `mvpn`. Přesné texty hlášek (bez diakritiky, jako celý `checks/multicast.py`):
  - `bez PIM join, o streamy se hlasi IGMP`
  - `bez IGMP reportu, o streamy se hlasi PIM join`
  - `sender site bez vzdaleneho receiveru, neni co overit`
  - `Zadny PIM join`
  - `bez IGMP reportu ani PIM join`
- Fixtures z laborky 2026-09-07 už jsou v repu (commit 8026d2e): `tests/fixtures/rpc/junos/pim_join.xml` (receiver), `junos/pim_join.2.xml` (sender), `junos-evo/pim_join.xml` (instance all), `junos/multicast_route.6.xml`, `junos/mvpn_instance.2.xml`, `junos-evo/multicast_route.2.xml`.
- Test suite musí být zelená po každém tasku: `pyats-venv/bin/python -m pytest -q`.

---

## Task 1: PIM záměr per rozhraní, RI-aware

**Files:**
- Modify: `migration_validator/parsers/core.py:401` (init `igmp_interfaces`), `:426` (volání v `parse()`), `:959-976` (`_parse_global_protocol_interfaces`), `:978-997` (`_parse_igmp_interfaces`)
- Test: `tests/parsers/test_pim_intent.py`

**Interfaces:**
- Produces: `JunosServiceParserCore.pim_interfaces: set[str]` — jména logických rozhraní pod `protocols pim interface X` globálně i v RI; `"pim"` v `global_protocols_by_interface[name]` pro ta samá jména.

- [ ] **Step 1: Napiš failing test**

Do `tests/parsers/test_pim_intent.py` přidej na konec:

```python
RI_CONFIG = """
<configuration>
  <interfaces>
    <interface>
      <name>irb</name>
      <unit>
        <name>10</name>
        <family><inet><address><name>10.100.11.1/30</name></address></inet></family>
      </unit>
      <unit>
        <name>11</name>
        <family><inet><address><name>10.100.12.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>NGMVPN-PIM-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.10</name></interface>
      <interface><name>irb.11</name></interface>
      <route-distinguisher><rd-type>150.0.0.11:10</rd-type></route-distinguisher>
      <vrf-target><community>target:65000:10</community></vrf-target>
      <protocols>
        <mvpn><receiver-site/></mvpn>
        <pim>
          <interface><name>irb.10</name><mode>sparse</mode></interface>
        </pim>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ri_pim_interface_is_in_pim_interfaces_set(parser_class):
    """RI-scoped `protocols pim interface X` je zamer per rozhrani (spec
    2026-09-07). RoutingInstance.protocols dava "pim" vsem rozhranim instance,
    takze `protocol` seznam to nerozlisi - rozlisuje to jen pim_interfaces."""
    parser = parser_class(etree.fromstring(RI_CONFIG))
    parser.parse()
    assert parser.pim_interfaces == {"irb.10"}
    assert "pim" in parser.global_protocols_by_interface["irb.10"]
    assert "irb.11" not in parser.global_protocols_by_interface


@pytest.mark.parametrize("parser_class", PARSERS)
def test_global_pim_interface_is_in_pim_interfaces_set(parser_class):
    parser = parser_class(etree.fromstring(CONFIG))
    parser.parse()
    assert parser.pim_interfaces == {"ge-0/0/1.0"}
```

- [ ] **Step 2: Ověř, že test padá**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_pim_intent.py -q`
Expected: 4 FAIL s `AttributeError: ... has no attribute 'pim_interfaces'`

- [ ] **Step 3: Implementace**

V `parsers/core.py`:

V `__init__` (řádek ~401) za `self.igmp_interfaces: set[str] = set()` přidej:

```python
        self.pim_interfaces: set[str] = set()
```

V `parse()` nahraď `self._parse_global_protocol_interfaces("pim")` za `self._parse_pim_interfaces()`.

Za `_parse_igmp_interfaces` přidej:

```python
    def _parse_pim_interfaces(self) -> None:
        """Rozhraní pod `protocols pim interface X` — globálně i uvnitř
        routing-instance (spec 2026-09-07). Záměr říká: tady se čeká PIM
        soused (Core transit) nebo PIM join (MVPN site).

        RI-scoped varianta se čte přímo tady, ne přes `RoutingInstance.protocols`
        — ta dává "pim" každému rozhraní instance, ne jen tomu pod
        `pim interface`. Množina `pim_interfaces` je proto jediný zdroj
        per-rozhraní PIM záměru pro detekci subtype `mvpn`.
        """
        names = all_texts(
            self.config_xml,
            "./protocols/pim/interface/name/text()"
            " | ./routing-instances/instance/protocols/pim/interface/name/text()",
        )
        for name in names:
            self.pim_interfaces.add(name)
            self.global_protocols_by_interface.setdefault(name, set()).add("pim")
```

Uprav docstring `_parse_global_protocol_interfaces` — smaž odstavec o PIM (`PIM: protocols pim interface ...` až `_collect_protocols ho pripoji.`), metoda zůstává (nikdo jiný ji teď nevolá, ale je obecná; ponech ji).

Aktualizuj docstring modulu `tests/parsers/test_pim_intent.py`: odstavec „RI-PIM variantu ... test nema" nahraď větou `RI-PIM varianta je pokryta test_ri_pim_interface_is_in_pim_interfaces_set (spec 2026-09-07).`

- [ ] **Step 4: Ověř zelené testy**

Run: `pyats-venv/bin/python -m pytest tests/parsers -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migration_validator/parsers/core.py tests/parsers/test_pim_intent.py
git commit -m "feat(parser): PIM zamer per rozhrani i v routing-instance (pim_interfaces)"
```

---

## Task 2: `mvpn_site` na instanci, službě a v inventory (schema 9)

**Files:**
- Modify: `migration_validator/parsers/core.py:107-125` (`RoutingInstance`), `:168-198` (`InterfaceService`), `:505-550` (`_parse_routing_instances`), `:1170-1210` (konstrukce `InterfaceService` v `_classify_interface`), `:1995-2020` (`ordered_keys`)
- Modify: `migration_validator/models/inventory.py:49-130` (`ServiceEntry`), `:145-150` (`INVENTORY_SCHEMA_VERSION`), docstring `load_inventory`
- Modify: `migration_validator/models/scope.py:66-125` (`Selectors`)
- Modify: `migration_validator/scoping/builder.py:80-100`
- Test: `tests/parsers/test_mvpn_site.py` (nový), `tests/models/test_inventory.py` (existuje-li; jinak `tests/models/test_inventory_schema.py` nový), `tests/scoping/test_builder.py`

**Interfaces:**
- Produces: `RoutingInstance.mvpn_site: list[str]`, `InterfaceService.mvpn_site: list[str]`, `ServiceEntry.mvpn_site: list[str]`, `Selectors.mvpn_site: list[str]`; hodnoty seřazený podseznam `["receiver", "sender"]`.
- Produces: `JunosServiceParserCore._parse_mvpn_site(node) -> list[str]`.

- [ ] **Step 1: Failing parser test**

Vytvoř `tests/parsers/test_mvpn_site.py`:

```python
"""Role MVPN instance z konfigurace (spec 2026-09-07): sender <=> sender-site
nebo provider-tunnel; receiver <=> receiver-site nebo zadne site klicove slovo
(Junos default je obojí). Slouzi jen k interpretaci prazdne PIM join tabulky."""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

PARSERS = (
    pytest.param(JunosEvoAcxServiceParser, id="evo"),
    pytest.param(JunosServiceParser, id="mx"),
)

TUNNEL = """
<provider-tunnel><family><inet><rsvp-te>
  <label-switched-path-template><template-name>T</template-name></label-switched-path-template>
</rsvp-te></inet></family></provider-tunnel>
"""


def _config(mvpn: str | None, tunnel: bool) -> str:
    mvpn_xml = f"<mvpn>{mvpn or ''}</mvpn>" if mvpn is not None else ""
    return f"""
<configuration>
  <interfaces>
    <interface><name>irb</name><unit><name>10</name>
      <family><inet><address><name>10.100.11.1/30</name></address></inet></family>
    </unit></interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>RI</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.10</name></interface>
      {TUNNEL if tunnel else ''}
      <protocols>
        {mvpn_xml}
        <pim><interface><name>irb.10</name></interface></pim>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""


CASES = [
    pytest.param(None, False, [], id="no-mvpn"),
    pytest.param("<sender-site/>", False, ["sender"], id="sender-site"),
    pytest.param("<receiver-site/>", False, ["receiver"], id="receiver-site"),
    pytest.param("<sender-site/>", True, ["sender"], id="tunnel+sender-site"),
    pytest.param("<receiver-site/>", True, ["receiver", "sender"], id="tunnel+receiver-site"),
    pytest.param("", True, ["receiver", "sender"], id="tunnel-no-site-keyword"),
    pytest.param("", False, ["receiver"], id="mvpn-no-site-no-tunnel"),
]


@pytest.mark.parametrize("parser_class", PARSERS)
@pytest.mark.parametrize(("mvpn", "tunnel", "expected"), CASES)
def test_mvpn_site_derivation(parser_class, mvpn, tunnel, expected):
    parser = parser_class(etree.fromstring(_config(mvpn, tunnel)))
    services = {s.interface: s for s in parser.parse()}
    assert parser.routing_instances["RI"].mvpn_site == expected
    assert services["irb.10"].mvpn_site == expected
```

- [ ] **Step 2: Ověř, že padá**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_mvpn_site.py -q`
Expected: FAIL `AttributeError: 'RoutingInstance' object has no attribute 'mvpn_site'`

- [ ] **Step 3: Implementace parseru**

`parsers/core.py`, `RoutingInstance` — za `bfd: dict[...]` přidej:

```python
    # Role MVPN site z konfigurace (spec 2026-09-07): podmnozina
    # ["receiver", "sender"], prazdne bez `protocols mvpn`. Sender <=>
    # sender-site nebo provider-tunnel (sender musi byt root P2MP tunelu);
    # receiver <=> receiver-site nebo zadne site klicove slovo (Junos
    # default je obojí). Checky s tim interpretuji prazdnou PIM join tabulku,
    # role per (S,G) se ale bere z vypisu, ne odsud.
    mvpn_site: list[str] = field(default_factory=list)
```

`InterfaceService` — za `l2_interface` přidej:

```python
    # Role MVPN instance (viz RoutingInstance.mvpn_site), prazdne mimo MVPN.
    mvpn_site: list[str] = field(default_factory=list)
```

V `_parse_routing_instances` do konstruktoru `RoutingInstance(...)` přidej `mvpn_site=self._parse_mvpn_site(node),` a za metodu přidej:

```python
    @staticmethod
    def _parse_mvpn_site(node: etree._Element) -> list[str]:
        """Role MVPN site z `protocols mvpn` + `provider-tunnel` (spec 2026-09-07)."""
        if not node.xpath("./protocols/mvpn"):
            return []
        sender_site = bool(node.xpath("./protocols/mvpn/sender-site"))
        receiver_site = bool(node.xpath("./protocols/mvpn/receiver-site"))
        tunnel = bool(node.xpath("./provider-tunnel"))
        roles = []
        if receiver_site or not sender_site:
            roles.append("receiver")
        if sender_site or tunnel:
            roles.append("sender")
        return roles
```

V `_classify_interface` do `InterfaceService(...)` přidej `mvpn_site=list(instance.mvpn_site) if instance else [],`.

V `ordered_keys` (řádek ~2013) přidej `"mvpn_site",` hned za `"l2_interface",`.

- [ ] **Step 4: Ověř parser test**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_mvpn_site.py -q`
Expected: PASS (14 testů)

- [ ] **Step 5: Failing test inventory + builder**

Do `tests/scoping/test_builder.py` na konec:

```python
def test_mvpn_site_protece_do_selektoru():
    entry = ServiceEntry(
        interface="irb.10", service_type="IPVPN", service_subtype="mvpn",
        routing_instance="NGMVPN-PIM-SOURCE", mvpn_site=["sender"],
    )
    (scope,) = build_scopes(Inventory(device="x", entries=[entry]))
    assert scope.selectors.mvpn_site == ["sender"]
    assert Selectors.from_dict(scope.selectors.to_dict()).mvpn_site == ["sender"]
```

(Ověř, že `Selectors` je v testu importován; pokud ne, přidej `from migration_validator.models.scope import Selectors`.)

Najdi test, který kontroluje `INVENTORY_SCHEMA_VERSION` / round-trip `ServiceEntry.from_dict` (`grep -rn "schema_version\|from_dict" tests/models tests/parsers | head`). Do souboru s round-trip testem přidej:

```python
def test_service_entry_roundtrips_mvpn_site():
    entry = ServiceEntry(interface="irb.10", service_type="IPVPN", mvpn_site=["receiver", "sender"])
    assert ServiceEntry.from_dict(entry.to_dict()).mvpn_site == ["receiver", "sender"]
    assert ServiceEntry.from_dict({"interface": "x", "service_type": "IPVPN"}).mvpn_site == []
```

Run: `pyats-venv/bin/python -m pytest tests/scoping/test_builder.py tests/models -q`
Expected: FAIL (`TypeError: unexpected keyword argument 'mvpn_site'`)

- [ ] **Step 6: Implementace modelů**

`models/inventory.py`, `ServiceEntry`: za `l2_interface` přidej `mvpn_site: list[str] = field(default_factory=list)`; do `from_dict` `mvpn_site=_as_list(data.get("mvpn_site")),`; do `to_dict` `"mvpn_site": list(self.mvpn_site),`. `INVENTORY_SCHEMA_VERSION = 9` a komentář nad ním:

```python
# 9: subtype IPVPN "mvpn" nahrazuje "mvpn-igmp" (IGMP nebo PIM zamer) a pole
#    mvpn_site (role instance z konfigurace, spec 2026-09-07). Stara inventory
#    by nesla "mvpn-igmp" a parovaci pravidlo description+type+subtype by
#    baseline scope vyradilo z multicast checku.
```

Do docstringu `load_inventory` přidej odstavec:

```
    Verze 9 prejmenovala "mvpn-igmp" na "mvpn" a pridala mvpn_site.
    Tolerantni cteni stare (v8) inventory by nechalo subtype "mvpn-igmp",
    na ktery uz zadny check nenaskoci - MVPN sluzba by tise prosla bez
    multicast radku.
```

`models/scope.py`, `Selectors`: za `protocols` přidej:

```python
    # Role MVPN instance z konfigurace (spec 2026-09-07) - jen zamer pro
    # interpretaci prazdne PIM join tabulky, do vyberu faktu se nepromita.
    mvpn_site: list[str] = field(default_factory=list)
```

a do `to_dict` `"mvpn_site": list(self.mvpn_site),` (`from_dict` klíče bere z `to_dict`, nic dalšího).

`scoping/builder.py`: do `Selectors(...)` přidej `mvpn_site=list(entry.mvpn_site),`.

- [ ] **Step 7: Fixtures inventory na schema 9**

`tests/fixtures/172.20.20.4.yml` a `tests/fixtures/172.20.20.5.yml` (vstup `synthetic_snapshot` pro AR-29 test `tests/test_end_to_end.py::test_full_migration_run_has_no_unexplained_fail_or_warn`) mají `schema_version: 8` a žádný klíč `mvpn_site`. Uprav je skriptem (klíč `mvpn_site: []` hned za `l2_interface`, aby pořadí sedělo s `ordered_keys`):

```bash
for f in tests/fixtures/172.20.20.4.yml tests/fixtures/172.20.20.5.yml; do
  sed -i 's/^schema_version: 8$/schema_version: 9/' "$f"
  sed -i 's/^  l2_interface: \(.*\)$/  l2_interface: \1\n  mvpn_site: []/' "$f"
done
grep -c "mvpn_site: \[\]" tests/fixtures/172.20.20.4.yml tests/fixtures/172.20.20.5.yml   # = pocet entries (28 / 29)
```

Pozor: pokud má některý entry `l2_interface:` s víceřádkovým seznamem (`l2_interface:\n  - ge-...`), sed výše vloží `mvpn_site` mezi klíč a položky — zkontroluj `grep -n -A2 "l2_interface:$" tests/fixtures/*.yml` a takové případy oprav ručně (`mvpn_site: []` až za poslední položku seznamu).

`tests/models/test_inventory.py`: nahraď `schema_version: 8` → `schema_version: 9` (řádky ~55, 95, 153; ne řádek 177 se `schema_version: 2`, ten testuje odmítnutí staré verze).

Run: `pyats-venv/bin/python -m pytest -q`
Expected: PASS. Kořenové `172.20.20.4.yml` / `172.20.20.5.yml` jsou ukázkové výstupy — netýkají se testů, přegenerují se v Task 13.

- [ ] **Step 8: Commit**

```bash
git add migration_validator tests
git commit -m "feat(inventory): mvpn_site role instance z konfigurace, schema 9"
```

---

## Task 3: Subtype `mvpn` místo `mvpn-igmp`

**Files:**
- Modify: `migration_validator/parsers/core.py:1571-1589` (`_ipvpn_subtype`)
- Modify: `migration_validator/checks/multicast.py:23,28-30,188,441` (konstanty a `service_subtypes`)
- Modify: `tests/parsers/test_multicast_intent.py`, `tests/checks/test_multicast.py:495`, `tests/models/test_scope_multicast.py:35,58`, `tests/conftest.py:163,356,365,374`, `tests/scoping/test_builder.py:271`

**Interfaces:**
- Produces: subtype string `"mvpn"`; `checks.multicast.MULTICAST_SUBTYPES == frozenset({"multicast", "mvpn"})`, `MVPN_SUBTYPE = "mvpn"`.

- [ ] **Step 1: Failing parser testy**

V `tests/parsers/test_multicast_intent.py`:
- v `CONFIG` do instance `PLAIN-VRF` nic neměň; přidej třetí instanci před `</routing-instances>`:

```xml
    <instance>
      <name>NGMVPN-PIM-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.10</name></interface>
      <interface><name>irb.11</name></interface>
      <protocols>
        <mvpn><receiver-site/></mvpn>
        <pim><interface><name>irb.10</name><mode>sparse</mode></interface></pim>
      </protocols>
    </instance>
```

a do `<interfaces>` k `irb` units:

```xml
      <unit>
        <name>10</name>
        <family><inet><address><name>10.100.11.1/30</name></address></inet></family>
      </unit>
      <unit>
        <name>11</name>
        <family><inet><address><name>10.100.12.1/30</name></address></inet></family>
      </unit>
```

- přejmenuj `test_ipvpn_with_igmp_and_mvpn_gets_mvpn_igmp_subtype` na `test_ipvpn_with_igmp_and_mvpn_gets_mvpn_subtype` a assert na `== "mvpn"`;
- přidej:

```python
@pytest.mark.parametrize("parser_class", PARSERS)
def test_ipvpn_with_pim_only_and_mvpn_gets_mvpn_subtype(parser_class):
    service = _services(parser_class)["irb.10"]
    assert service.service_subtype == "mvpn"
    assert "pim" in service.protocol
    assert "igmp" not in service.protocol
    assert "protocols pim" in " ".join(service.detection_reason)


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ipvpn_interface_without_per_interface_pim_stays_plain(parser_class):
    """irb.11 je v MVPN instanci, ale neni pod `pim interface` ani `igmp
    interface` - instance-level "pim" v protocol seznamu subtype nedava."""
    service = _services(parser_class)["irb.11"]
    assert service.service_type == "IPVPN"
    assert service.service_subtype is None


@pytest.mark.parametrize("parser_class", PARSERS)
def test_detection_reason_names_both_intents(parser_class):
    reasons = " ".join(_services(parser_class)["irb.2"].detection_reason)
    assert "protocols igmp a pim" in reasons
```

Aktualizuj docstring modulu (`"mvpn-igmp"` → `"mvpn"`, „IGMP **nebo PIM** záměr").

- [ ] **Step 2: Ověř, že padá**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_multicast_intent.py -q`
Expected: FAIL

- [ ] **Step 3: Implementace parseru**

`parsers/core.py`, nahraď `_ipvpn_subtype`:

```python
    def _ipvpn_subtype(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance | None,
        reasons: list[str],
    ) -> str | None:
        """`mvpn` = `protocols mvpn` v instanci + IGMP nebo PIM záměr na rozhraní
        (spec 2026-09-07; do té doby `mvpn-igmp` jen s IGMP). Bez per-rozhraní
        záměru zůstává obyčejná IPVPN — instance-level "pim" nestačí."""
        if instance is None or "mvpn" not in instance.protocols:
            return None
        intents = [
            name
            for name, members in (("igmp", self.igmp_interfaces), ("pim", self.pim_interfaces))
            if interface.name in members
        ]
        if not intents:
            return None
        reasons.append(
            f"Rozhraní je pod protocols {' a '.join(intents)} a instance má protocols mvpn — MVPN site."
        )
        return "mvpn"
```

- [ ] **Step 4: Přejmenování v checkách a testech**

`checks/multicast.py`:
- `MULTICAST_SUBTYPES = frozenset({"multicast", "mvpn"})`, přidej `MVPN_SUBTYPE = "mvpn"` pod něj;
- komentář nad `MVPN_UPSTREAM_PREFIXES`: `IPVPN/mvpn-igmp` → `IPVPN/mvpn`;
- `_upstream_problem`: `subtype == "mvpn-igmp"` → `subtype == MVPN_SUBTYPE`;
- `MvpnCmulticastStatusCheck.service_subtypes = frozenset({MVPN_SUBTYPE})`.

Testy: nahraď `"mvpn-igmp"` za `"mvpn"` v `tests/checks/test_multicast.py` (řádek 495 `_mvpn_scope`), `tests/models/test_scope_multicast.py` (35, 58), `tests/conftest.py` (163 komentář, 356, 365, 374), `tests/scoping/test_builder.py` (271). Přejmenuj `test_mvpn_check_applies_only_to_mvpn_igmp` → `test_mvpn_check_applies_only_to_mvpn`.

Fixtures inventory: v `tests/fixtures/172.20.20.4.yml` (řádek ~707) a `tests/fixtures/172.20.20.5.yml` (~617) je jeden entry `irb.2` se `service_subtype: mvpn-igmp` — změň na `service_subtype: mvpn` a jeho `mvpn_site: []` na:

```yaml
  mvpn_site:
  - receiver
```

(instance `MULTICAST-STREAM-B-MUX1-RECEIVER` je v laborce `receiver-site`). Jeho `protocol` už obsahuje `pim`, takže od Task 7 na něm poběží `pim_join` check i v AR-29 testu.

- [ ] **Step 5: Celá sada**

Run: `pyats-venv/bin/python -m pytest -q`
Expected: PASS. `grep -rn "mvpn-igmp" migration_validator tests` musí vrátit 0 řádků (docs se řeší v Task 12).

- [ ] **Step 6: Commit**

```bash
git add migration_validator tests
git commit -m "feat(parser): subtype mvpn (IGMP nebo PIM zamer) nahrazuje mvpn-igmp"
```

---

## Task 4: Sdílená per-instance základna collectorů

**Files:**
- Modify: `migration_validator/collectors/multicast.py:110-165` (`MulticastRouteCollector.record_calls` / `collect`)
- Test: `tests/collectors/test_multicast.py` (stávající testy `test_collect_*`, `test_record_calls_*` — beze změny chování)

**Interfaces:**
- Produces: `class _PerInstanceCollector(Collector)` s `record_calls(device, platform)` a `collect(device, platform)`; podtřída implementuje `rpc_name`, `rpc_kwargs` a `parse(xml, platform) -> dict[str, dict[str, Any]]` (instance → tabulka). `collect` slévá tabulky per instance přes `dict.update`.

- [ ] **Step 1: Refaktor**

V `collectors/multicast.py` před `MulticastRouteCollector` přidej:

```python
class _PerInstanceCollector(Collector):
    """Zaklad pro RPC, ktere MX neumi zavolat pres vsechny instance najednou
    (`instance all` vraci <output>instance is not running</output>, zmereno
    2026-09-02): EVO jedno volani, MX master bez argumentu + jedno volani
    per VRF z get-instance-information. Podtrida dava rpc_name/rpc_kwargs
    a parse(xml) -> {instance: {klic: payload}}."""

    def record_calls(self, device: Any, platform: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Na MX se seznam instanci bere ze zarizeni (get-instance-information,
        instance-type vrf) - collector inventory nema a jmena RI hardcodovat nesmi."""
        calls = list(self.rpc_calls(platform))
        if platform == "junos-evo":
            return tuple(calls)
        rpc_name = self.rpc_name(platform)
        base = dict(self.rpc_kwargs(platform))
        for name in _vrf_instances(device):
            calls.append((rpc_name, {**base, "instance": name}))
        return tuple(calls)

    def collect(self, device: Any, platform: str) -> Any:
        if not self.supports(platform):
            raise CollectorError(f"collector '{self.name}' nepodporuje platformu '{platform}'")
        tables: dict[str, dict[str, dict[str, Any]]] = {}
        failures: list[str] = []
        try:
            # record_calls() na MX vola get-instance-information (_vrf_instances)
            # - selhani tohoto volani je stejna chyba jako selhani samotne RPC,
            # ne neosetrena vyjimka co spadne mimo capture.
            calls = self.record_calls(device, platform)
        except Exception as error:  # noqa: BLE001
            raise CollectorError(
                f"collector '{self.name}': zjisteni seznamu instanci selhalo - "
                f"{type(error).__name__}: {error}"
            ) from error
        for rpc_name, kwargs in calls:
            variant = f"{rpc_name}({kwargs})"
            try:
                xml = getattr(device.rpc, rpc_name)(**kwargs)
                parsed = self.parse(xml, platform)
            except Exception as error:  # noqa: BLE001
                failures.append(f"{variant}: {type(error).__name__}: {error}")
                continue
            for instance, entries in parsed.items():
                tables.setdefault(instance, {}).update(entries)
        if failures:
            # Castecna data nesmi vypadat jako zmerena (stejne jako routes.py).
            raise CollectorError(f"collector '{self.name}': RPC selhalo - " + "; ".join(failures))
        return tables
```

`MulticastRouteCollector`: změň bázi na `_PerInstanceCollector`, smaž jeho `record_calls` a `collect` (ponech `name`, `rpc_name`, `rpc_kwargs`, `parse`).

- [ ] **Step 2: Ověř stávající testy**

Run: `pyats-venv/bin/python -m pytest tests/collectors -q`
Expected: PASS (zejména `test_collect_merges_master_and_per_vrf_on_junos`, `test_record_calls_on_junos_includes_master_and_per_vrf`, `test_collect_raises_when_instance_lookup_fails_on_junos`, `test_record_calls_on_evo_is_just_the_static_call`).

Pozor: MX per-VRF kwargs pro `multicast_route` byly `{"extensive": True, "instance": name}` — nová základna dává `{**rpc_kwargs, "instance": name}` = totéž. Ověř v `test_record_calls_on_junos_includes_master_and_per_vrf` přidáním:

```python
    assert ("get_multicast_route_information", {"extensive": True, "instance": "MULTICAST-STREAM-B-MUX1-RECEIVER"}) in calls
```

- [ ] **Step 3: Commit**

```bash
git add migration_validator/collectors/multicast.py tests/collectors/test_multicast.py
git commit -m "refactor(collectors): sdilena per-instance zakladna pro multicast_route"
```

---

## Task 5: Collector `pim_join`, snapshot schema 13, scope select

**Files:**
- Modify: `migration_validator/collectors/multicast.py` (nový `PimJoinCollector`)
- Modify: `migration_validator/models/snapshot.py:19-28`, `migration_validator/models/scope.py:19-40,300-345`
- Test: `tests/collectors/test_multicast.py`, `tests/models/test_scope_multicast.py`

**Interfaces:**
- Produces: fact area `pim_join: dict[str, dict[str, dict]]`: instance → `route_key(source or "*", group)` → `{"source": str|None, "group": str, "upstream_interface": str|None, "upstream_neighbor": str|None, "downstream_interfaces": list[str], "uptime_seconds": int|None}`.
- Produces: `Scope.select()` vrací `pim_join` scoped per instance stejně jako `multicast_route`.

- [ ] **Step 1: Failing collector testy**

Do `tests/collectors/test_multicast.py` importů přidej `PimJoinCollector`; na konec souboru:

```python
# --- pim_join (spec 2026-09-07) --------------------------------------------

PIM_RI_RECEIVER = "NGMVPN-PIM-RECEIVER"
PIM_RI_SENDER = "NGMVPN-PIM-SOURCE"
PIM_SG = route_key("10.10.10.1", "232.10.10.1")


def test_pim_join_parses_receiver_on_junos(rpc_fixture):
    data = PimJoinCollector().parse(rpc_fixture("junos", "pim_join"), "junos")
    join = data[PIM_RI_RECEIVER][PIM_SG]
    assert join["source"] == "10.10.10.1"
    assert join["group"] == "232.10.10.1"
    assert join["upstream_interface"] == "Through BGP"
    assert join["upstream_neighbor"] == "Through MVPN"
    assert join["downstream_interfaces"] == ["irb.10"]
    assert isinstance(join["uptime_seconds"], int)


def test_pim_join_parses_sender_on_junos(rpc_fixture):
    data = PimJoinCollector().parse(rpc_fixture("junos", "pim_join.2"), "junos")
    join = data[PIM_RI_SENDER][PIM_SG]
    assert join["upstream_interface"] == "irb.10"
    assert join["upstream_neighbor"] == "10.10.13.1"
    assert join["downstream_interfaces"] == ["Pseudo-MVPN"]


def test_pim_join_parses_all_instances_on_evo(rpc_fixture):
    data = PimJoinCollector().parse(rpc_fixture("junos-evo", "pim_join"), "junos-evo")
    assert set(data) == {"master", "NGMVPN-IGMP-RECEIVER", PIM_RI_RECEIVER}
    # Internet/multicast join: downstream Pseudo-GMP + skutecne jmeno v
    # pim-pseudo-downstream-interface-name - obe se sbiraji.
    master = data["master"][route_key("10.11.11.1", "232.1.1.1")]
    assert master["upstream_interface"] == "et-0/0/0.0"
    assert master["downstream_interfaces"] == ["Pseudo-GMP", "et-0/0/8.11"]
    assert data["NGMVPN-IGMP-RECEIVER"][route_key("10.12.12.1", "239.1.1.1")]["downstream_interfaces"] == ["irb.2"]


def test_pim_join_inet6_family_and_empty_instance_have_no_key(rpc_fixture):
    data = PimJoinCollector().parse(rpc_fixture("junos", "pim_join.2"), "junos")
    assert set(data) == {PIM_RI_SENDER}


_PIM_ASM_XML = """
<pim-join-information>
  <join-family>
    <pim-instance>PIM.master</pim-instance>
    <address-family>INET</address-family>
    <join-group>
      <multicast-group-address>239.5.5.5</multicast-group-address>
      <upstream-interface-name>et-0/0/0.0</upstream-interface-name>
      <upstream-neighbor>10.1.1.2</upstream-neighbor>
      <uptime seconds="7">00:00:07</uptime>
      <downstream-interfaces>
        <downstream-interface>
          <pim-interface-name>irb.7</pim-interface-name>
        </downstream-interface>
        <downstream-interface>
          <pim-interface-name>irb.7</pim-interface-name>
        </downstream-interface>
      </downstream-interfaces>
    </join-group>
  </join-family>
</pim-join-information>
"""


def test_pim_join_asm_has_none_source_and_star_key_and_unique_downstream():
    data = PimJoinCollector().parse(etree.fromstring(_PIM_ASM_XML), "junos")
    join = data["master"]["*,239.5.5.5"]
    assert join["source"] is None
    assert join["group"] == "239.5.5.5"
    assert join["downstream_interfaces"] == ["irb.7"]
    assert join["uptime_seconds"] == 7


def test_pim_join_rpc_names_and_kwargs():
    collector = PimJoinCollector()
    assert collector.rpc_name("junos") == "get_pim_join_information"
    assert collector.rpc_kwargs("junos") == {"extensive": True}
    assert collector.rpc_kwargs("junos-evo") == {"extensive": True, "instance": "all"}


def test_pim_join_record_calls_on_junos_includes_master_and_per_vrf():
    calls = PimJoinCollector().record_calls(_FakeDevice({}), "junos")
    assert ("get_pim_join_information", {"extensive": True}) in calls
    assert ("get_pim_join_information", {"extensive": True, "instance": "MULTICAST-STREAM-B-MUX1-RECEIVER"}) in calls
```

- [ ] **Step 2: Ověř, že padá**

Run: `pyats-venv/bin/python -m pytest tests/collectors/test_multicast.py -q -k pim_join`
Expected: FAIL `ImportError: cannot import name 'PimJoinCollector'`

- [ ] **Step 3: Implementace collectoru**

Na konec `collectors/multicast.py`:

```python
PIM_INSTANCE_PREFIX = "PIM."
ASM_KEY_SOURCE = "*"


@register
class PimJoinCollector(_PerInstanceCollector):
    """PIM join tabulka (spec 2026-09-07). Rozhrani sluzby je bud upstream
    (sender site) nebo mezi downstream (receiver site) - obe pole se drzi
    verbatim ("Through BGP", "Pseudo-MVPN"), role rozhoduje az check."""

    name = "pim_join"

    def rpc_name(self, platform: str) -> str:
        return "get_pim_join_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        if platform == "junos-evo":
            return {"extensive": True, "instance": "all"}
        return {"extensive": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, dict[str, Any]]]:
        tables: dict[str, dict[str, dict[str, Any]]] = {}
        for family_node in xml.iter("{*}join-family"):
            if (_localname_text(family_node, "address-family") or "").upper() != "INET":
                continue
            instance = _localname_text(family_node, "pim-instance") or MASTER
            if instance.startswith(PIM_INSTANCE_PREFIX):
                instance = instance[len(PIM_INSTANCE_PREFIX):]
            joins: dict[str, dict[str, Any]] = {}
            for join_node in family_node.iter("{*}join-group"):
                group = _localname_text(join_node, "multicast-group-address")
                if not group:
                    continue
                source = _localname_text(join_node, "multicast-source-address") or None
                downstream: list[str] = []
                for iface_node in join_node.iter("{*}downstream-interface"):
                    for element in ("pim-interface-name", "pim-pseudo-downstream-interface-name"):
                        name = _localname_text(iface_node, element)
                        if name and name not in downstream:
                            downstream.append(name)
                uptime = next(join_node.iter("{*}uptime"), None)
                joins[route_key(source or ASM_KEY_SOURCE, group)] = {
                    "source": source,
                    "group": group,
                    "upstream_interface": _localname_text(join_node, "upstream-interface-name"),
                    "upstream_neighbor": _localname_text(join_node, "upstream-neighbor"),
                    "downstream_interfaces": downstream,
                    "uptime_seconds": _seconds_attr(uptime),
                }
            if joins:
                tables.setdefault(instance, {}).update(joins)
        return tables
```

Ověř, že `_localname_text` hledá první potomka daného jména v celém podstromu (je z `collectors/isis.py`) — u `downstream-interface` s vnořeným `downstream-neighbor` to vrátí správně `pim-interface-name` z úrovně rozhraní, protože `downstream-neighbor` žádný `pim-interface-name` nenese. Doplň docstring modulu (`Tri collectory` → `Ctyri collectory ... + PIM join (spec 2026-09-07)`).

- [ ] **Step 4: Ověř collector testy**

Run: `pyats-venv/bin/python -m pytest tests/collectors -q`
Expected: PASS

- [ ] **Step 5: Failing scope test**

`tests/models/test_scope_multicast.py`: do `FACTS` přidej:

```python
    "pim_join": {
        "master": {"10.11.11.1,232.1.1.1": {"upstream_interface": "et-0/0/0.0"}},
        "MVPN-RI": {"10.12.12.1,239.1.1.1": {"upstream_interface": "Through BGP"}},
    },
```

a testy:

```python
def test_pim_join_is_selected_per_instance_like_multicast_route():
    internet = _scope("Internet", "multicast", ["et-0/0/8.11"]).select(FACTS)
    mvpn = _scope("IPVPN", "mvpn", ["irb.2"], ["MVPN-RI"]).select(FACTS)
    transit = _scope("Core", "transit", ["et-0/0/0.0"]).select(FACTS)
    assert set(internet["pim_join"]) == {"master"}
    assert set(mvpn["pim_join"]) == {"MVPN-RI"}
    assert transit["pim_join"] == {}


def test_pim_join_missing_instance_yields_empty_dict():
    assert _scope("IPVPN", "mvpn", ["irb.5"], ["OTHER-RI"]).select(FACTS)["pim_join"] == {}
```

Do `test_device_scope_passes_everything` přidej assert `selected["pim_join"] == FACTS["pim_join"]`.

Run: `pyats-venv/bin/python -m pytest tests/models/test_scope_multicast.py -q`
Expected: FAIL (`KeyError: 'pim_join'`)

- [ ] **Step 6: Snapshot + scope**

`models/snapshot.py`: komentář `# 13: fact area pim_join (PIM join tabulka per instance, spec 2026-09-07).` a `SCHEMA_VERSION = 13`.

`models/scope.py`: do `FACT_AREAS` přidej `"pim_join",` za `"mvpn_instance",`. V `select()` nahraď blok `multicast_route: dict[str, Any] = {}` … `multicast_route = {instance: table}` za:

```python
        # Multicast tabulka i PIM join patri instanci, ne lince: scope bez RI
        # dostane master, scope s RI svou tabulku. Filtr per (S,G) / per
        # rozhrani (upstream i downstream) dela check. Jen role, ktere
        # multicast meri - tranzitni Core ani L2 sluzby tabulku nedostanou
        # (spec 2026-09-02, pim_join spec 2026-09-07).
        measures_multicast = self.service_type in ("Internet", "IPVPN") or (
            self.service_type == "Core" and self.service_subtype == "loopback"
        )
        multicast_route = self._instance_table(facts, "multicast_route", measures_multicast)
        pim_join = self._instance_table(facts, "pim_join", measures_multicast)
```

přidej `"pim_join": pim_join,` do návratového dictu a metodu:

```python
    def _instance_table(self, facts: dict[str, Any], area: str, enabled: bool) -> dict[str, Any]:
        if not enabled:
            return {}
        instance = (
            self.selectors.routing_instances[0]
            if self.selectors.routing_instances
            else "master"
        )
        table = (facts.get(area) or {}).get(instance)
        return {instance: table} if table else {}
```

Zkontroluj `_empty(area)` v `scope.py` — pokud mapuje area na prázdný typ, `pim_join` musí dávat `{}` (stejně jako `multicast_route`).

- [ ] **Step 7: Celá sada**

Run: `pyats-venv/bin/python -m pytest -q`
Expected: PASS. Pokud existuje test na `SCHEMA_VERSION == 12` nebo snapshot fixture se `schema_version: 12`, aktualizuj na 13 (`grep -rn "schema_version.*12\|SCHEMA_VERSION" tests | head`).

- [ ] **Step 8: Commit**

```bash
git add migration_validator tests
git commit -m "feat(collectors): pim_join collector, snapshot schema 13, scope per instance"
```

---

## Task 6: Helpery `pim_pairs` a `expected_pairs`

**Files:**
- Modify: `migration_validator/checks/multicast.py:61-72` (za `igmp_pairs`)
- Test: `tests/checks/test_multicast.py`

**Interfaces:**
- Produces: `RECEIVER = "receiver"`, `SENDER = "sender"`; `pim_pairs(facts, scope) -> list[tuple[str | None, str, frozenset[str]]]`; `expected_pairs(facts, scope) -> list[tuple[str | None, str, frozenset[str]]]` (seřazené `(source or "", group)`, bez duplicit, link-local vynecháno).

- [ ] **Step 1: Failing testy**

Do `tests/checks/test_multicast.py` k importům přidej `RECEIVER, SENDER, expected_pairs, pim_pairs`. Za `_igmp` helper přidej:

```python
def _join(upstream, downstream, source="10.11.11.1", group="232.1.1.1"):
    return {f"{source or '*'},{group}": {
        "source": source, "group": group, "upstream_interface": upstream,
        "upstream_neighbor": None, "downstream_interfaces": list(downstream),
        "uptime_seconds": 10,
    }}


def _pim(instance="master", *joins):
    table = {}
    for join in joins:
        table.update(join)
    return {"pim_join": {instance: table}}
```

a v sekci helperů:

```python
def test_pim_pairs_role_from_upstream_or_downstream():
    facts = _pim("master",
                 _join("Through BGP", [POST]),                       # receiver
                 _join(POST, ["Pseudo-MVPN"], group="232.1.1.2"),    # sender
                 _join("et-0/0/0.0", ["irb.9"], group="232.1.1.3"))  # cizi rozhrani
    assert pim_pairs(facts, _scope()) == [
        ("10.11.11.1", "232.1.1.1", frozenset({RECEIVER})),
        ("10.11.11.1", "232.1.1.2", frozenset({SENDER})),
    ]
    assert pim_pairs(None, _scope()) == []
    assert pim_pairs({"pim_join": {}}, _scope()) == []


def test_pim_pairs_asm_and_link_local():
    facts = _pim("master",
                 _join("et-0/0/0.0", [POST], source=None, group="239.1.1.1"),
                 _join("et-0/0/0.0", [POST], source=None, group="224.0.0.13"))
    assert pim_pairs(facts, _scope()) == [(None, "239.1.1.1", frozenset({RECEIVER}))]


def test_expected_pairs_unions_igmp_and_pim_with_role_merge():
    # SG je z IGMP (receiver) i z PIM joinu, kde je servisni rozhrani upstream
    # i downstream zaroven (sender + receiver) -> unie roli.
    facts = {**_igmp(POST, SG, ("10.0.0.9", "232.9.9.9")),
             **_pim("master", _join(POST, [POST]))}
    assert expected_pairs(facts, _scope()) == [
        ("10.0.0.9", "232.9.9.9", frozenset({RECEIVER})),
        ("10.11.11.1", "232.1.1.1", frozenset({RECEIVER, SENDER})),
    ]


def test_expected_pairs_without_any_source_is_empty():
    assert expected_pairs({}, _scope()) == []
    assert expected_pairs(_igmp(POST), _scope()) == []
```

- [ ] **Step 2: Ověř, že padá**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k "pairs"`
Expected: FAIL `ImportError`

- [ ] **Step 3: Implementace**

Do `checks/multicast.py` za `igmp_pairs`:

```python
RECEIVER = "receiver"
SENDER = "sender"

Pair = tuple[str | None, str, frozenset[str]]


def _service_interface(scope: Scope) -> str | None:
    return scope.selectors.interfaces[0] if scope.selectors.interfaces else None


def pim_pairs(facts: dict[str, Any] | None, scope: Scope) -> list[Pair]:
    """(source, group, roles) z PIM join tabulky scopu (spec 2026-09-07).
    Role z vypisu, ne z konfigurace: servisni rozhrani mezi downstream =
    receiver, servisni rozhrani == upstream = sender. Join, ktery se
    rozhrani nedotyka, patri jine sluzbe v teze instanci."""
    iface = _service_interface(scope)
    if iface is None:
        return []
    found: dict[tuple[str | None, str], set[str]] = {}
    for table in ((facts or {}).get("pim_join") or {}).values():
        for join in table.values():
            group = str(join.get("group") or "")
            if not group or _link_local(group):
                continue
            roles = set()
            if iface in (join.get("downstream_interfaces") or []):
                roles.add(RECEIVER)
            if join.get("upstream_interface") == iface:
                roles.add(SENDER)
            if roles:
                found.setdefault((join.get("source"), group), set()).update(roles)
    return sorted(
        ((s, g, frozenset(r)) for (s, g), r in found.items()),
        key=lambda p: (p[0] or "", p[1]),
    )


def expected_pairs(facts: dict[str, Any] | None, scope: Scope) -> list[Pair]:
    """Sjednoceni IGMP paru (vzdy receiver) a PIM paru; stejne (S,G) dostane
    unii roli. Tohle je mnozina ocekavanych streamu pro forwarding
    a c-multicast checky (rozhodnuti 2026-09-07)."""
    merged: dict[tuple[str | None, str], set[str]] = {}
    for source, group in igmp_pairs(facts, scope):
        merged.setdefault((source, group), set()).add(RECEIVER)
    for source, group, roles in pim_pairs(facts, scope):
        merged.setdefault((source, group), set()).update(roles)
    return sorted(
        ((s, g, frozenset(r)) for (s, g), r in merged.items()),
        key=lambda p: (p[0] or "", p[1]),
    )


def roles_label(roles: frozenset[str]) -> str:
    return "/".join(sorted(roles))
```

- [ ] **Step 4: Ověř**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/multicast.py tests/checks/test_multicast.py
git commit -m "feat(checks): pim_pairs a expected_pairs - sjednoceni IGMP a PIM paru s rolemi"
```

---

## Task 7: Check `pim_join` + přečíslování order

**Files:**
- Modify: `migration_validator/checks/multicast.py` (nová třída za `IgmpMembershipReportCheck`; `order` u forwarding 11→12, core 12→13, c-multicast 13→14)
- Test: `tests/checks/test_multicast.py`

**Interfaces:**
- Produces: `PimJoinCheck` (`id="pim_join"`, `label="PIM join"`), konstanty `NO_JOIN = "Zadny PIM join"`, `NO_JOIN_IGMP_INFO = "bez PIM join, o streamy se hlasi IGMP"`, `SENDER_NO_RECEIVER = "sender site bez vzdaleneho receiveru, neni co overit"`.

- [ ] **Step 1: Failing testy**

Do importů testu přidej `NO_JOIN, NO_JOIN_IGMP_INFO, SENDER_NO_RECEIVER, PimJoinCheck`. Uprav `_scope` helper, aby přijímal `protocols` a `mvpn_site`:

```python
def _scope(interface=POST, service_type="Internet", subtype="multicast", instances=(),
           static_routes=(), protocols=("igmp", "pim"), mvpn_site=()):
    return Scope(
        id=f"svc:x:{service_type}", kind="service",
        key=ScopeKey("x", service_type, subtype),
        selectors=Selectors(
            interfaces=[interface], routing_instances=list(instances),
            static_routes=[dict(r) for r in static_routes],
            protocols=list(protocols), mvpn_site=list(mvpn_site),
        ),
    )
```

Nová sekce testů:

```python
# --- pim_join ---------------------------------------------------------------

def test_pim_join_pass_lists_pairs_with_role():
    facts = _pim("master", _join("Through BGP", [POST]), _join(POST, ["Pseudo-MVPN"], group="232.1.1.2"))
    (finding,) = PimJoinCheck().run(_ctx(facts))
    assert finding.outcome is Outcome.OK
    assert finding.label == "PIM join"
    assert finding.value == "(10.11.11.1, 232.1.1.1) [receiver], (10.11.11.1, 232.1.1.2) [sender]"
    assert finding.baseline_value is None


def test_pim_join_changed_set_is_warn_roles_ignored():
    now = _pim("master", _join("Through BGP", [POST]), _join(POST, ["Pseudo-MVPN"], group="232.1.1.2"))
    was = _pim("master", _join("Through BGP", [PRE]))
    (finding,) = PimJoinCheck().run(_ctx(now, baseline=was, baseline_scope=_scope(PRE)))
    assert finding.outcome is Outcome.DEGRADED
    assert finding.baseline_value == "(10.11.11.1, 232.1.1.1) [receiver]"


def test_pim_join_same_set_different_role_is_pass():
    now = _pim("master", _join(POST, ["Pseudo-MVPN"]))
    was = _pim("master", _join("Through BGP", [PRE]))
    (finding,) = PimJoinCheck().run(_ctx(now, baseline=was, baseline_scope=_scope(PRE)))
    assert finding.outcome is Outcome.OK


def test_pim_join_missing_with_igmp_is_info():
    facts = {**_igmp(POST, SG), "pim_join": {}}
    (finding,) = PimJoinCheck().run(_ctx(facts))
    assert (finding.outcome, finding.value) == (Outcome.INFO, NO_JOIN_IGMP_INFO)


def test_pim_join_missing_on_sender_only_site_is_warn():
    scope = _scope("irb.10", "IPVPN", "mvpn", ["RI"], mvpn_site=["sender"])
    (finding,) = PimJoinCheck().run(_ctx({"pim_join": {}}, scope=scope))
    assert (finding.outcome, finding.value) == (Outcome.DEGRADED, SENDER_NO_RECEIVER)


def test_pim_join_missing_on_receiver_or_both_site_is_fail():
    for site in ([], ["receiver"], ["receiver", "sender"]):
        scope = _scope("irb.10", "IPVPN", "mvpn", ["RI"], mvpn_site=site)
        (finding,) = PimJoinCheck().run(_ctx({"pim_join": {}}, scope=scope))
        assert (finding.outcome, finding.value) == (Outcome.BROKEN, NO_JOIN), site


def test_pim_join_silent_without_pim_intent():
    assert PimJoinCheck().run(_ctx(_pim("master", _join("Through BGP", [POST])), scope=_scope(protocols=["igmp"]))) == []


def test_pim_join_applies_to_multicast_and_mvpn_only():
    check = PimJoinCheck()
    assert check.applies_to(_scope())
    assert check.applies_to(_scope("irb.10", "IPVPN", "mvpn", ["RI"]))
    assert not check.applies_to(_scope("irb.3", "IPVPN", None, ["RI"]))
    assert not check.applies_to(_scope("lo0.0", "Core", "loopback"))


def test_multicast_check_order():
    assert [c.order for c in (IgmpMembershipReportCheck, PimJoinCheck, MulticastForwardingStatusCheck,
                              CoreMulticastForwardingCheck, MvpnCmulticastStatusCheck)] == [10, 11, 12, 13, 14]
```

- [ ] **Step 2: Ověř, že padá**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k "pim_join or order"`
Expected: FAIL `ImportError`

- [ ] **Step 3: Implementace**

Konstanty vedle `NO_REPORT`:

```python
NO_JOIN = "Zadny PIM join"
NO_JOIN_IGMP_INFO = "bez PIM join, o streamy se hlasi IGMP"
SENDER_NO_RECEIVER = "sender site bez vzdaleneho receiveru, neni co overit"
```

Helper vedle `pairs_text`:

```python
def role_pairs_text(pairs: list[Pair]) -> str:
    return ", ".join(f"{sg_label(s, g)} [{roles_label(r)}]" for s, g, r in pairs)


def _sg_set(pairs: list[Pair]) -> set[tuple[str | None, str]]:
    return {(s, g) for s, g, _ in pairs}
```

Za `IgmpMembershipReportCheck`:

```python
# --- pim_join ---------------------------------------------------------------

@register
class PimJoinCheck(Check):
    id = "pim_join"
    order = 11
    title = "PIM join na servisnim rozhrani"
    label = "PIM join"
    mode = Mode.BOTH
    requires = ("pim_join",)
    requires_inventory = True
    service_types = MULTICAST_TYPES
    service_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        if "pim" not in ctx.scope.selectors.protocols:
            # Bez zameru ticho, ne SKIP - stejne jako pim_neighbor_state.
            return []
        now = pim_pairs(ctx.subject, ctx.scope)
        was = pim_pairs(ctx.baseline, ctx.baseline_scope or ctx.scope) if ctx.has_baseline else []
        was_value = role_pairs_text(was) if was else None
        if not now:
            if igmp_pairs(ctx.subject, ctx.scope):
                return [Finding(
                    Outcome.INFO, "bez PIM join, o streamy se hlasi IGMP",
                    label=self.label, value=NO_JOIN_IGMP_INFO, baseline_value=was_value,
                )]
            if list(ctx.scope.selectors.mvpn_site) == [SENDER]:
                # Sender-only site bez vzdaleneho receiveru nema zadny join
                # state - neni co overit, ale neni to rozbity receiver
                # (rozhodnuti 2026-09-07).
                return [Finding(
                    Outcome.DEGRADED, "sender site bez vzdaleneho receiveru",
                    label=self.label, value=SENDER_NO_RECEIVER, baseline_value=was_value,
                )]
            return [Finding(
                Outcome.BROKEN, "zadny PIM join na servisnim rozhrani",
                label=self.label, value=NO_JOIN, baseline_value=was_value,
            )]
        # Role se neporovnavaji - migraci se nemeni, pri rozdilu by slo
        # o jiny stream. Porovnava se jen mnozina (S,G).
        if was and _sg_set(was) != _sg_set(now):
            return [Finding(
                Outcome.DEGRADED, f"PIM join se zmenil proti baseline: {role_pairs_text(now)}",
                label=self.label, value=role_pairs_text(now), baseline_value=was_value,
            )]
        return [Finding(
            Outcome.OK, f"PIM join: {role_pairs_text(now)}",
            label=self.label, value=role_pairs_text(now), baseline_value=was_value,
        )]
```

Přečísluj `order`: `MulticastForwardingStatusCheck.order = 12`, `CoreMulticastForwardingCheck.order = 13`, `MvpnCmulticastStatusCheck.order = 14`. Zkontroluj, že žádný jiný check nemá order 11–14 kolizně relevantní (`grep -rn "order = 1[1-4]" migration_validator/checks`) — kolize v jiném modulu nevadí, řazení je stabilní, ale zapiš do commit message, pokud existuje.

- [ ] **Step 4: Ověř**

Run: `pyats-venv/bin/python -m pytest tests/checks -q`
Expected: PASS. Pokud existuje test řazení checků nebo audit-snapshot výstupu reportu (`grep -rn "order" tests/checks/test_registry*.py tests/reporting 2>/dev/null | head`), aktualizuj očekávané pořadí.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/multicast.py tests/checks/test_multicast.py
git commit -m "feat(checks): pim_join check s roli per (S,G), sender-only bez joinu DEGRADED"
```

---

## Task 8: INFO zrcadlo v `igmp_membership_report`

**Files:**
- Modify: `migration_validator/checks/multicast.py:158-178` (`IgmpMembershipReportCheck.run`)
- Test: `tests/checks/test_multicast.py`

**Interfaces:**
- Produces: `NO_REPORT_PIM_INFO = "bez IGMP reportu, o streamy se hlasi PIM join"`.

- [ ] **Step 1: Failing testy**

```python
def test_igmp_report_missing_with_pim_join_is_info():
    facts = {**_igmp(POST), **_pim("master", _join("Through BGP", [POST]))}
    (finding,) = IgmpMembershipReportCheck().run(_ctx(facts))
    assert (finding.outcome, finding.value) == (Outcome.INFO, NO_REPORT_PIM_INFO)


def test_igmp_report_missing_without_pim_area_stays_fail():
    """Snapshot bez pim_join area (schema 12 fixture nebo selhany collector)
    nesmi zmenit dosavadni chovani."""
    (finding,) = IgmpMembershipReportCheck().run(_ctx(_igmp(POST)))
    assert (finding.outcome, finding.value) == (Outcome.BROKEN, NO_REPORT)
```

(přidej `NO_REPORT_PIM_INFO` do importů)

- [ ] **Step 2: Ověř, že padá** — `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k igmp_report_missing` → FAIL ImportError

- [ ] **Step 3: Implementace**

Konstanta `NO_REPORT_PIM_INFO = "bez IGMP reportu, o streamy se hlasi PIM join"` vedle `NO_REPORT`. V `IgmpMembershipReportCheck.run` nahraď blok `if not now:`:

```python
        if not now:
            if pim_pairs(ctx.subject, ctx.scope):
                # Rozhrani s obema zamery: streamy nese PIM join, IGMP mlci
                # (rozhodnuti 2026-09-07). pim_join area se cte volitelne.
                return [Finding(
                    Outcome.INFO, "bez IGMP reportu, o streamy se hlasi PIM join",
                    label=self.label, value=NO_REPORT_PIM_INFO, baseline_value=was_value,
                )]
            return [Finding(
                Outcome.BROKEN, "receiver neposila zadny IGMP membership report",
                label=self.label, value=NO_REPORT, baseline_value=was_value,
            )]
```

- [ ] **Step 4: Ověř** — `pyats-venv/bin/python -m pytest tests/checks -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/multicast.py tests/checks/test_multicast.py
git commit -m "feat(checks): IGMP report bez paru je INFO, kdyz streamy hlasi PIM join"
```

---

## Task 9: Role-aware `multicast_forwarding_status`

**Files:**
- Modify: `migration_validator/checks/multicast.py:203-273` (`MulticastForwardingStatusCheck`)
- Test: `tests/checks/test_multicast.py`

**Interfaces:**
- Produces: `NO_PAIRS_SKIP = "bez IGMP reportu ani PIM join"`; `MulticastForwardingStatusCheck._stream(sg, iface, subtype, route, roles)`.

- [ ] **Step 1: Failing testy**

Helper `_scope` s parametry `protocols` / `mvpn_site` je z Task 7. Do importů `NO_PAIRS_SKIP`. Uprav `test_forwarding_without_igmp_is_single_skip` — očekávaná value je `NO_PAIRS_SKIP`. Přidej:

```python
def _sender_scope():
    return _scope("irb.10", "IPVPN", "mvpn", ["RI"], mvpn_site=["sender"])


def _sender_facts(upstream="irb.10", downstream=("ge-0/0/0.0",)):
    return {
        **_pim("RI", _join("irb.10", ["Pseudo-MVPN"], source="10.10.10.1", group="232.10.10.1")),
        "multicast_route": {"RI": {"10.10.10.1,232.10.10.1": _route(upstream, downstream)}},
    }


def test_forwarding_pairs_come_from_pim_join_too():
    facts = {**_pim("master", _join("et-0/0/0.0", [POST])),
             "multicast_route": {"master": {"10.11.11.1,232.1.1.1": _route()}}}
    findings = MulticastForwardingStatusCheck().run(_ctx(facts))
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "1 S,G"


def test_forwarding_sender_pass_rows():
    findings = MulticastForwardingStatusCheck().run(_ctx(_sender_facts(), scope=_sender_scope()))
    rows = _by_label(findings, sg_label("10.10.10.1", "232.10.10.1"))
    assert findings[0].outcome is Outcome.OK
    assert rows["Stream"].outcome is Outcome.OK
    assert rows["Stream"].value == "Stream odchazi na ge-0/0/0.0"
    assert rows["Upstream interface"].outcome is Outcome.OK
    assert rows["Upstream interface"].value == "irb.10"


def test_forwarding_sender_upstream_must_be_service_interface():
    findings = MulticastForwardingStatusCheck().run(_ctx(_sender_facts(upstream="lsi.5"), scope=_sender_scope()))
    rows = _by_label(findings, sg_label("10.10.10.1", "232.10.10.1"))
    assert rows["Upstream interface"].outcome is Outcome.BROKEN
    assert "neni servisni rozhrani irb.10" in rows["Upstream interface"].message
    assert rows["Stream"].outcome is Outcome.OK


def test_forwarding_sender_without_downstream_fails_stream_row():
    findings = MulticastForwardingStatusCheck().run(_ctx(_sender_facts(downstream=()), scope=_sender_scope()))
    rows = _by_label(findings, sg_label("10.10.10.1", "232.10.10.1"))
    assert rows["Stream"].outcome is Outcome.BROKEN
    assert rows["Stream"].value == "S,G je v tabulce ale nema zadny downstream"
    assert findings[0].outcome is Outcome.BROKEN


def test_forwarding_both_roles_passes_when_either_role_matches():
    """Stejne (S,G) z IGMP (receiver) i z PIM jako sender: Stream OK, kdyz
    plati kterakoli role; Upstream se hodnoti podle role, jejiz Stream prosel."""
    scope = _scope("irb.10", "IPVPN", "mvpn", ["RI"])
    facts = {
        **_igmp("irb.10", ("10.10.10.1", "232.10.10.1")),
        **_pim("RI", _join("irb.10", ["Pseudo-MVPN"], source="10.10.10.1", group="232.10.10.1")),
        "multicast_route": {"RI": {"10.10.10.1,232.10.10.1": _route("irb.10", ("ge-0/0/0.0",))}},
    }
    findings = MulticastForwardingStatusCheck().run(_ctx(facts, scope=scope))
    rows = _by_label(findings, sg_label("10.10.10.1", "232.10.10.1"))
    assert rows["Stream"].outcome is Outcome.OK
    assert rows["Upstream interface"].outcome is Outcome.OK


def test_forwarding_both_roles_neither_matching_fails_with_receiver_texts():
    scope = _scope("irb.10", "IPVPN", "mvpn", ["RI"])
    facts = {
        **_igmp("irb.10", ("10.10.10.1", "232.10.10.1")),
        **_pim("RI", _join("irb.10", ["Pseudo-MVPN"], source="10.10.10.1", group="232.10.10.1")),
        "multicast_route": {"RI": {"10.10.10.1,232.10.10.1": _route("lsi.5", ())}},
    }
    findings = MulticastForwardingStatusCheck().run(_ctx(facts, scope=scope))
    rows = _by_label(findings, sg_label("10.10.10.1", "232.10.10.1"))
    assert rows["Stream"].outcome is Outcome.BROKEN
    assert rows["Stream"].value == "S,G je v tabulce ale stream se na irb.10 neposila"
```

Zkontroluj `_route` helper (řádek ~157): signatura `_route(upstream="et-0/0/0.0", downstream=(POST,), pps=6, uptime=3266)` — sedí.

- [ ] **Step 2: Ověř, že padá** — `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k forwarding` → FAIL

- [ ] **Step 3: Implementace**

Konstanta `NO_PAIRS_SKIP = "bez IGMP reportu ani PIM join"` vedle `NO_REPORT_SKIP`. V `MulticastForwardingStatusCheck.run`:

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        pairs = expected_pairs(ctx.subject, ctx.scope)
        if not pairs:
            # Kaskada (rozhodnuti 2026-09-02/07): bez ocekavane mnoziny neni co hledat.
            return [Finding(
                Outcome.SKIP, "bez IGMP reportu ani PIM join neni co hledat v multicast tabulce",
                value=NO_PAIRS_SKIP,
            )]
        table = multicast_table(ctx.subject)
        iface = ctx.scope.selectors.interfaces[0]
        rows: list[Finding] = []
        failed = 0
        for source, group, roles in pairs:
            matches = routes_for(table, source, group)
            if not matches:
                sg = sg_label(source, group)
                rows.append(Finding(
                    Outcome.BROKEN, f"{sg}: S,G neni v multicast tabulce",
                    label="Stream", group=sg, value="S,G neni v multicast tabulce",
                ))
                failed += 1
                continue
            pair_failed = False
            for key, route in matches:
                sg = sg_label(key.split(",", 1)[0], group)
                stream = self._stream(sg, iface, ctx.scope.service_subtype, route, roles)
                if any(f.outcome is Outcome.BROKEN for f in stream):
                    pair_failed = True
                rows.extend(stream)
            if pair_failed:
                failed += 1
        return [_summary(self.label, len(pairs), failed), *rows]

    @staticmethod
    def _stream(
        sg: str, iface: str, subtype: str | None, route: dict[str, Any], roles: frozenset[str]
    ) -> list[Finding]:
        downstream = route.get("downstream_interfaces") or []
        upstream = route.get("upstream_interface")
        receiver_ok = RECEIVER in roles and iface in downstream
        sender_ok = SENDER in roles and bool(downstream)
        # Obe role: Stream OK, kdyz plati kterakoli; Upstream podle role,
        # jejiz Stream prosel (obe prosle -> receiver). Zadna prosla ->
        # receiver texty (rozhodnuti 2026-09-07).
        as_sender = (sender_ok and not receiver_ok) or roles == frozenset({SENDER})
        if as_sender:
            stream_row = Finding(
                Outcome.OK if sender_ok else Outcome.BROKEN,
                f"{sg}: stream " + (f"odchazi na {', '.join(downstream)}" if sender_ok
                                    else "nema zadny downstream"),
                label="Stream", group=sg,
                value=(f"Stream odchazi na {', '.join(downstream)}" if sender_ok
                       else "S,G je v tabulce ale nema zadny downstream"),
            )
            upstream_ok = upstream == iface
            upstream_row = Finding(
                Outcome.OK if upstream_ok else Outcome.BROKEN,
                f"{sg}: upstream {upstream or '-'}"
                + ("" if upstream_ok else f" neni servisni rozhrani {iface}"),
                label="Upstream interface", group=sg, value=upstream or "-",
            )
        else:
            problem = _upstream_problem(subtype, upstream)
            stream_row = Finding(
                Outcome.OK if receiver_ok else Outcome.BROKEN,
                f"{sg}: stream se na {iface} " + ("posila" if receiver_ok else "neposila"),
                label="Stream", group=sg,
                value=(
                    f"Stream se na {iface} posila" if receiver_ok
                    else f"S,G je v tabulce ale stream se na {iface} neposila"
                ),
            )
            upstream_row = Finding(
                Outcome.OK if problem is None else Outcome.BROKEN,
                f"{sg}: upstream {upstream or '-'}{problem or ''}",
                label="Upstream interface", group=sg, value=upstream or "-",
            )
        return [stream_row, upstream_row, *stream_rows(sg, route, rate_label="Forwarding-rate")]
```

Aktualizuj docstring modulu (`IGMP mnozina definuje ocekavane streamy` → `Sjednoceni IGMP a PIM join paru (expected_pairs) definuje ocekavane streamy`).

- [ ] **Step 4: Ověř** — `pyats-venv/bin/python -m pytest tests/checks -q` → PASS (včetně stávajících receiver testů: `test_forwarding_pass_block_shape`, `test_forwarding_downstream_without_service_interface_fails_stream_row`, `test_forwarding_mvpn_upstream_accepts_physical_and_irb`).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/multicast.py tests/checks/test_multicast.py
git commit -m "feat(checks): multicast forwarding nad expected_pairs, sender role (upstream == servisni rozhrani)"
```

---

## Task 10: `mvpn_cmulticast_status` nad `expected_pairs`

**Files:**
- Modify: `migration_validator/checks/multicast.py` (`MvpnCmulticastStatusCheck`)
- Test: `tests/checks/test_multicast.py`

- [ ] **Step 1: Failing testy**

Uprav `test_mvpn_without_igmp_is_skip` → přejmenuj na `test_mvpn_without_any_pairs_is_skip`, očekávaná value `NO_PAIRS_SKIP`. Přidej:

```python
def test_mvpn_sender_pairs_from_pim_join_pass_rows():
    scope = _scope("irb.10", "IPVPN", "mvpn", [RI], mvpn_site=["sender"])
    facts = {
        **_pim(RI, _join("irb.10", ["Pseudo-MVPN"], source=MSG[0], group=MSG[1])),
        "mvpn_instance": {RI: {"c_multicast": [_entry()]}},
    }
    findings = MvpnCmulticastStatusCheck().run(_ctx(facts, scope=scope))
    rows = _by_label(findings, sg_label(*MSG))
    assert rows["C-Multicast status"].outcome is Outcome.OK
    assert rows["Provider tunnel"].outcome is Outcome.OK


def test_mvpn_requires_only_mvpn_instance():
    assert MvpnCmulticastStatusCheck.requires == ("mvpn_instance",)
```

- [ ] **Step 2: Ověř, že padá** — `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k mvpn` → FAIL

- [ ] **Step 3: Implementace**

V `MvpnCmulticastStatusCheck`: `requires = ("mvpn_instance",)`; v `run` nahraď:

```python
        pairs = expected_pairs(ctx.subject, ctx.scope)
        if not pairs:
            # Kaskada (rozhodnuti 2026-09-02/07): bez ocekavane mnoziny neni co hledat v MVPN.
            return [Finding(
                Outcome.SKIP, "bez IGMP reportu ani PIM join neni co hledat v MVPN",
                label=self.label, value=NO_PAIRS_SKIP,
            )]
```

a smyčku `for source, group in pairs:` → `for source, group, _roles in pairs:`. Řádky c-multicast a tunelu jsou role-nezávislé, beze změny.

- [ ] **Step 4: Ověř** — `pyats-venv/bin/python -m pytest -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/multicast.py tests/checks/test_multicast.py
git commit -m "feat(checks): mvpn_cmulticast_status nad expected_pairs, requires jen mvpn_instance"
```

---

## Task 11: Conftest syntéza `pim_join` a `mvpn_site`

**Files:**
- Modify: `tests/conftest.py:160-170` (deklarace areas), `:353-380` (syntéza multicast), místo, kde se skládá `facts` dict (`grep -n "mvpn_instance" tests/conftest.py`)

**Interfaces:**
- Produces: syntetický snapshot má `pim_join` area; scopy subtype `mvpn` mají v `pim_join` join s downstream = servisní rozhraní (receiver), aby `pim_join` check dal OK a nikde nevznikl FAIL na zdravé migraci (AR-29).

- [ ] **Step 1: Failing test**

AR-29 test (`tests/test_end_to_end.py::test_full_migration_run_has_no_unexplained_fail_or_warn`) po Task 7 prochází, protože syntéza dává mvpn scopu IGMP páry a `pim_join` check tak vrací INFO. Syntetický snapshot ale nemá `pim_join` area vůbec — přidej do `tests/test_end_to_end.py`:

```python
def test_synthetic_mvpn_scope_has_pim_join(synthetic_snapshot):
    """Zdravy MVPN receiver ma join s IRB jako downstream (spec 2026-09-07);
    bez nej by pim_join check zil jen z INFO zrcadla a sender vetev
    forwarding checku by synteza nikdy neprosla."""
    snapshot = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")
    table = snapshot.facts["pim_join"]["MULTICAST-STREAM-B-MUX1-RECEIVER"]
    (join,) = table.values()
    assert join["downstream_interfaces"] == ["irb.2"]
    assert join["upstream_interface"] == "Through BGP"
```

Run: `pyats-venv/bin/python -m pytest tests/test_end_to_end.py -q -k pim_join`
Expected: FAIL `KeyError: 'pim_join'`

- [ ] **Step 2: Implementace**

V `tests/conftest.py` k `mvpn_instance = {}` přidej `pim_join = {}`. V bloku syntézy multicast za `multicast_route.setdefault(...)` přidej:

```python
            if scope.service_subtype == "mvpn":
                # PIM join (spec 2026-09-07): receiver site, join s IRB jako
                # downstream, upstream pres MVPN.
                pim_join.setdefault(instance, {})[f"{source},{group}"] = {
                    "source": source,
                    "group": group,
                    "upstream_interface": "Through BGP",
                    "upstream_neighbor": "Through MVPN",
                    "downstream_interfaces": [iface],
                    "uptime_seconds": 3266,
                }
```

Do výsledného `facts` dictu přidej `"pim_join": pim_join,`. Pokud syntéza inventory/scopů nastavuje `protocol` seznam pro mvpn scopy, přidej k němu `"pim"` (grep `"igmp"` v conftest).

- [ ] **Step 3: Ověř**

Run: `pyats-venv/bin/python -m pytest -q`
Expected: PASS, žádný FAIL na syntetické migraci. Spusť také `grep -rn "schema_version" tests/fixtures | head` a ověř, že snapshot fixtures nesou 13 (pokud se nepřegenerovávají automaticky z `SCHEMA_VERSION`).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests
git commit -m "test(conftest): synteza pim_join pro mvpn scopy"
```

---

## Task 12: Dokumentace

**Files:**
- Modify: `docs/cs/reference.md:40-43`, `docs/en/reference.md:40-43`
- Modify: `docs/cs/files/checks.md:966-1130`, `docs/en/files/checks.md:980-1145`
- Modify: `docs/cs/files/parsers.md:285-300`, `docs/en/files/parsers.md:305-320`
- Modify: `docs/cs/files/collectors.md:410-460`, `docs/en/files/collectors.md` (odpovídající sekce)
- Modify: `docs/cs/files/models.md:138-165` (FACT_AREAS komentář, tabulka filtrů, Selectors), `docs/en/files/models.md`

- [ ] **Step 1: reference.md (cs + en)**

Nahraď `mvpn-igmp` → `mvpn` ve třech řádcích a přidej řádek za `igmp_membership_report`:

cs:
```
| `pim_join` | both | critical | Internet (multicast), IPVPN (mvpn) | jen tam, kde je rozhraní pod `protocols pim` (jinak žádný nález); (S,G) z PIM join tabulky s rolí `[receiver]` (rozhraní mezi downstream) / `[sender]` (rozhraní je upstream); množina proti baseline = WARN; bez joinu: INFO když streamy hlásí IGMP, WARN u sender-only site, jinak FAIL |
```
en:
```
| `pim_join` | both | critical | Internet (multicast), IPVPN (mvpn) | only where the interface is under `protocols pim` (otherwise no finding); (S,G) from the PIM join table with role `[receiver]` (interface among downstream) / `[sender]` (interface is upstream); set against baseline = WARN; no join: INFO when IGMP reports the streams, WARN on a sender-only site, otherwise FAIL |
```

U `igmp_membership_report` doplň „bez reportu, ale s PIM join = INFO"; u `multicast_forwarding_status` „páry = sjednocení IGMP + PIM join; sender role: upstream == servisní rozhraní, downstream neprázdný"; u `mvpn_cmulticast_status` „páry = sjednocení IGMP + PIM join".

- [ ] **Step 2: checks.md (cs + en)**

V sekci `multicast.py`: nadpis „Čtyři nové checky nad třemi collectory" → „Pět checků nad čtyřmi collectory (`pim_join` od 2026-09-07)"; role `IPVPN/mvpn-igmp` → `IPVPN/mvpn (IRB nebo tranzit v MVPN VRF s IGMP nebo PIM záměrem, receiver i sender site)`; pravidlo „Chybějící IGMP množina kaskáduje" → „Prázdné sjednocení IGMP + PIM join párů (`expected_pairs()`) kaskáduje do SKIP `bez IGMP reportu ani PIM join`". Přidej podsekci za `igmp_membership_report`:

```
### `pim_join` (Internet/multicast, IPVPN/mvpn, both, critical)

`requires=("pim_join",)`, gate `"pim" in scope.selectors.protocols` (jinak žádné řádky).
`pim_pairs()` bere z PIM join tabulky instance scopu jen joiny, kterých se servisní
rozhraní (`selectors.interfaces[0]`) dotýká: mezi `downstream_interfaces` (včetně
`pim-pseudo-downstream-interface-name`) = role `receiver`, rovno `upstream_interface` =
role `sender`. Link-local 224.0.0.0/24 vynecháno. Hodnota řádku `(S, G) [role], …`.
Outcomes: OK / DEGRADED (jiná množina (S,G) než baseline, role se neporovnávají) /
INFO `bez PIM join, o streamy se hlasi IGMP` / DEGRADED `sender site bez vzdaleneho
receiveru, neni co overit` (`selectors.mvpn_site == ["sender"]`) / BROKEN `Zadny PIM join`.
```

U `igmp_membership_report` doplň INFO zrcadlo `bez IGMP reportu, o streamy se hlasi PIM join`. U `multicast_forwarding_status` popiš sender větev `_stream()` (Stream `Stream odchazi na <downstream>` / `S,G je v tabulce ale nema zadny downstream`; Upstream `== servisní rozhraní` / `neni servisni rozhrani <iface>`) a pravidlo pro obě role. U `mvpn_cmulticast_status` `requires=("mvpn_instance",)`, páry z `expected_pairs()`.

- [ ] **Step 3: parsers.md (cs + en)**

Sekce Multicast: přidej odstavec **PIM záměr** (`_parse_pim_interfaces()`, `self.pim_interfaces`, RI-aware, instance-level "pim" nestačí), přepiš **Subtypy** (`mvpn` = `protocols mvpn` + IGMP nebo PIM záměr, reason `protocols igmp a pim`), přidej **`mvpn_site`** s tabulkou ze spec sekce 1, schema 9.

- [ ] **Step 4: collectors.md, models.md (cs + en)**

collectors.md: do tabulky řádek `| pim_join | get_pim_join_information(extensive=True[, instance=...]) | show pim join instance all extensive |`, podsekce `### PimJoinCollector (pim_join)` (payload ze spec sekce 2, `_PerInstanceCollector` sdílená s `multicast_route`, `record` ukládá `pim_join.xml` + `.2.xml` per RI na MX).

models.md: FACT_AREAS komentář + `pim_join` do tabulky filtrů (`podle instance jako multicast_route`), `Selectors.mvpn_site` k výčtu selektorů, snapshot schema 13.

- [ ] **Step 5: Ověř a commit**

`grep -rn "mvpn-igmp" docs/cs docs/en` — 0 řádků kromě `docs/superpowers/` (historické specy/audity se nemění).

```bash
git add docs
git commit -m "docs: pim_join check a collector, subtype mvpn, mvpn_site"
```

---

## Task 13: Ověření v laborce

**Files:**
- Modify: `172.20.20.4.yml`, `172.20.20.5.yml` (přegenerované ukázkové inventory, schema 9)
- Žádné testy — živý běh.

- [ ] **Step 1: Heslo a dostupnost**

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
docker ps --format '{{.Names}}' | grep -E "MX1-POP2|PTX1-POP1"
```

Zařízení: PTX1-POP1 `172.20.20.5` (junos-evo, receiver irb.2 + irb.10), MX1-POP2 `172.20.20.6` (junos, sender irb.10 v `NGMVPN-PIM-SOURCE`), user `admin`. Služby jsou od 2026-09-07 na PTX; MX1-POP1 (`172.20.20.4`) je bez MVPN instancí.

- [ ] **Step 2: Parse + capture + evaluate**

Inventory dělají kořenové parsery (`evo_parser.py` pro PTX, `mx_parser.py` pro MX), snapshot a report `mig-validate` (`~/.local/bin/mig-validate`, `pip install -e .`). Heslo pro oba je `$MIG_LAB_PASSWORD` (parsery s `--auth password` se na něj ptají nebo ho čtou z env — ověř `python3 evo_parser.py --help`; `mig-validate` bere `--password`).

```bash
mkdir -p runs/pimjoin
python3 evo_parser.py 172.20.20.5 --auth password -u admin -o runs/pimjoin/172.20.20.5.yml
python3 mx_parser.py  172.20.20.6 --auth password -u admin -o runs/pimjoin/172.20.20.6.yml
mig-validate capture --device 172.20.20.5 --inventory runs/pimjoin/172.20.20.5.yml \
    --phase post-migration --output runs/pimjoin/ptx.json \
    --username admin --password "$MIG_LAB_PASSWORD" --record-raw runs/pimjoin/raw-ptx
mig-validate capture --device 172.20.20.6 --inventory runs/pimjoin/172.20.20.6.yml \
    --phase post-migration --output runs/pimjoin/mx2.json \
    --username admin --password "$MIG_LAB_PASSWORD" --record-raw runs/pimjoin/raw-mx2
mig-validate evaluate --snapshot runs/pimjoin/ptx.json
mig-validate evaluate --snapshot runs/pimjoin/mx2.json
```

`runs/` je v `.gitignore` (ověř `git check-ignore runs/pimjoin`); nic z toho se necommituje.

- [ ] **Step 3: Kontrola inventory**

V PTX inventory: `irb.2` a `irb.10` mají `service_subtype: mvpn`, `mvpn_site: [receiver]`, `protocol` obsahuje `pim` (a `igmp` u irb.2). V MX1-POP2 inventory: `irb.10` v `NGMVPN-PIM-SOURCE` má `mvpn`, `mvpn_site: [sender]`.

- [ ] **Step 4: Kontrola reportu**

Očekávané řádky (streamy běží od 2026-09-07):
- PTX irb.10: `PIM join | (10.10.10.1, 232.10.10.1) [receiver]` OK; `IGMP membership report` INFO `bez IGMP reportu, o streamy se hlasi PIM join`; forwarding Stream OK (`irb.10`), Upstream `lsi.260`; c-multicast OK.
- PTX irb.2: `IGMP membership report` OK `(10.12.12.1, 239.1.1.1)`; `PIM join` OK `[receiver]`; forwarding OK.
- MX1-POP2 irb.10: `PIM join` OK `(10.10.10.1, 232.10.10.1) [sender]`; forwarding Stream `Stream odchazi na ge-0/0/0.0`, Upstream `irb.10` OK; c-multicast OK, tunnel `RSVP-TE P2MP:150.0.0.13, ...`.

Každý rozdíl proti očekávání je buď chyba implementace (oprav + test), nebo nová informace z laborky — zapiš ji do spec sekce „Uzavřená rozhodnutí" jako ověřený fakt s datem.

- [ ] **Step 5: Ukázkové inventory v kořeni**

Kořenové `172.20.20.4.yml` / `172.20.20.5.yml` jsou ukázky výstupu parseru. Přepiš `172.20.20.5.yml` čerstvým výstupem z Step 2 (`cp runs/pimjoin/172.20.20.5.yml 172.20.20.5.yml`). `172.20.20.4.yml` (MX1-POP1) přegeneruj stejně přes `mx_parser.py 172.20.20.4` — box je teď bez MVPN instancí, takže ukázka ponese jen schema 9 a `mvpn_site: []`.

```bash
git add 172.20.20.4.yml 172.20.20.5.yml
git commit -m "chore: ukazkove inventory schema 9 (mvpn, mvpn_site)"
```

Poté `superpowers:finishing-a-development-branch`.
