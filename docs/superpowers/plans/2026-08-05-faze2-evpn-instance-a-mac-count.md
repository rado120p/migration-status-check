# Fáze 2 — EVPN instance checky + MAC count přes `count` RPC: implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nový collector + check per-instance EVPN stavu (local/IRB
interfaces, neighbors, ESI) a náhrada počítání MAC adres z plné tabulky
za `count` RPC s per-VLAN a per-interface pre/post porovnáním.

**Architecture:** Dva nezávislé bloky. (1) `EvpnMacCollector` přechází na
`count` variantu stávajících RPC — nové schéma `{instance: {vlans,
interfaces}}`, check porovnává per-VLAN (klíč VLAN id, union se baseline)
i per-interface (klíč přejmenovává engine přes pár rozhraní ze scope
párování). (2) Nový collector `evpn_instance` parsuje týž extensive výpis
jako `evpn_esi`, ale po ose instancí; nový check `evpn_instance_status`
vyhodnocuje pravidla ze zadání. Dělba collector=normalizace /
check=hodnocení beze změny. Snapshot schéma 6 → 7.

**Tech Stack:** Python 3, lxml, pytest. Testy: `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-08-05-evpn-a-run-management-roadmapa-design.md`, sekce „Fáze 2".

**Ověřeno proti laborce 2026-08-05** (přes `display_xml_rpc` + živé
spuštění, viz Task 1 — XML je nahrané, nic v tomto plánu není odhad):

| CLI | RPC (PyEZ) | kwargs | root odpovědi |
|---|---|---|---|
| MX `show bridge mac-table count` | `get_bridge_mac_table` | `count=True` | `l2ald-rtb-mac-count` |
| MX `show evpn mac-table count` | `get_evpn_mac_table` | `count=True` | `l2ald-rtb-mac-count` |
| EVO `show mac-vrf forwarding mac-table count` | `get_mac_vrf_mac_table` | `count=True` | `l2ng-l2ald-rtb-mac-count` |
| EVO `show mac-vrf routing instance extensive` | `get_mac_vrf_instance_information` | `extensive=True` | `evpn-instance-information` |

Poznámky z laborky:

- EVO bez L2 služeb vrací na mac-table count RpcError „the l2-learning
  subsystem is not running" — stávající CollectorError → SKIP cesta,
  žádná nová větev.
- MX s instancemi, ale bez naučených MAC, vrací prázdný root bez chyby.
- `interface-name` má tvar `ge-0/0/2.313:313` (za dvojtečkou VLAN);
  vedle plných entry bývají i prázdné `<...-if-mac-count-entry/>`.
- Vlan-based instance hlásí skutečné `learn-vlan` (413) na OBOU
  platformách — placeholder je jen v názvu domény (`__X__`/`VL-NONE`).
- Odpověď `get_mac_vrf_instance_information` má stejný tvar
  (`evpn-instance-information`) jako `get_evpn_instance_information`
  na MX — parser nepotřebuje platformní větev.

## Global Constraints

- Jazyk kódu, komentářů, hlášek a testů: čeština bez diakritiky (jako
  zbytek repa). Dokumentace v `docs/` s diakritikou.
- Komentáře vysvětlují PROČ (omezení, které kód sám neukáže), ne CO.
- `SCHEMA_VERSION` se zvedá z 6 na 7 **jednou**, v Tasku 2 (první změna
  fact schématu). Tasky 5–6 už bump nedělají.
- Každá změna chování = nejdřív failující test (TDD).
- Po každém tasku zelená celá suite: `.venv/bin/pytest -q`.
- Necommitovat `172.20.20.4.yml` / `172.20.20.5.yml` v kořeni repa
  (rozpracované inventory uživatele).
- Fixtures v Tasku 1 jsou skutečné nahrávky z laborky z 2026-08-05 —
  vkládají se doslova, neupravovat (ani „kosmeticky": prázdné
  `<...-entry/>` bloky tam patří, parser je musí přežít).

---

### Task 1: Fixtures — nahrané `count` výpisy + kopie pro `evpn_instance`

**Files:**
- Replace: `tests/fixtures/rpc/junos/evpn_mac.xml` (bridge count)
- Replace: `tests/fixtures/rpc/junos/evpn_mac.2.xml` (evpn count)
- Replace: `tests/fixtures/rpc/junos-evo/evpn_mac.xml` (mac-vrf count)
- Create: `tests/fixtures/rpc/junos/evpn_instance.xml` (kopie evpn_esi.xml)
- Create: `tests/fixtures/rpc/junos-evo/evpn_instance.xml` (kopie evpn_esi.xml)

**Interfaces:**
- Produces: fixtures, na kterých stojí testy Tasků 2, 5 a 6. Jména
  odpovídají konvenci `_record()` v capture.py (druhé RPC = `.2.xml`).

- [ ] **Step 1: Přepsat `tests/fixtures/rpc/junos/evpn_mac.xml`** tímto
  obsahem (nahrávka `get_bridge_mac_table(count=True)` z 172.20.20.4):

```xml
<l2ald-rtb-mac-count>
  <l2ald-rtb-mac-count-entry>
    <rtb-mac-count>2</rtb-mac-count>
    <rtb-name>EVPN-VLAN-AWARE-CPE13-NNI</rtb-name>
    <bd-name>BD-313</bd-name>
    <l2ald-rtb-if-mac-count>
      <l2ald-rtb-if-mac-count-entry>
</l2ald-rtb-if-mac-count-entry>
      <l2ald-rtb-if-mac-count-entry>
        <interface-name>ge-0/0/2.313:313</interface-name>
        <mac-count>1</mac-count>
      </l2ald-rtb-if-mac-count-entry>
    </l2ald-rtb-if-mac-count>
    <l2ald-rtb-learn-vlan-mac-count>
      <l2ald-rtb-learn-vlan-mac-count-entry>
        <learn-vlan>313</learn-vlan>
        <mac-count>2</mac-count>
        <static-mac-count>0</static-mac-count>
      </l2ald-rtb-learn-vlan-mac-count-entry>
    </l2ald-rtb-learn-vlan-mac-count>
  </l2ald-rtb-mac-count-entry>
  <l2ald-rtb-mac-count-entry>
    <rtb-mac-count>4</rtb-mac-count>
    <rtb-name>EVPN-VLAN-AWARE-POP1</rtb-name>
    <bd-name>BD-4094</bd-name>
    <l2ald-rtb-if-mac-count>
      <l2ald-rtb-if-mac-count-entry>
        <interface-name>ge-0/0/6.4094:4094</interface-name>
        <mac-count>4</mac-count>
      </l2ald-rtb-if-mac-count-entry>
    </l2ald-rtb-if-mac-count>
    <l2ald-rtb-learn-vlan-mac-count>
      <l2ald-rtb-learn-vlan-mac-count-entry>
        <learn-vlan>4094</learn-vlan>
        <mac-count>4</mac-count>
        <static-mac-count>0</static-mac-count>
      </l2ald-rtb-learn-vlan-mac-count-entry>
    </l2ald-rtb-learn-vlan-mac-count>
  </l2ald-rtb-mac-count-entry>
</l2ald-rtb-mac-count>
```

- [ ] **Step 2: Přepsat `tests/fixtures/rpc/junos/evpn_mac.2.xml`**
  (nahrávka `get_evpn_mac_table(count=True)` z 172.20.20.4):

```xml
<l2ald-rtb-mac-count>
  <l2ald-rtb-mac-count-entry>
    <rtb-mac-count>2</rtb-mac-count>
    <rtb-name>EVPN-VLAN-BASED-CPE13-NNI</rtb-name>
    <bd-name>__EVPN-VLAN-BASED-CPE13-NNI__</bd-name>
    <l2ald-rtb-if-mac-count>
      <l2ald-rtb-if-mac-count-entry>
</l2ald-rtb-if-mac-count-entry>
      <l2ald-rtb-if-mac-count-entry>
        <interface-name>ge-0/0/2.413:413</interface-name>
        <mac-count>1</mac-count>
      </l2ald-rtb-if-mac-count-entry>
    </l2ald-rtb-if-mac-count>
    <l2ald-rtb-learn-vlan-mac-count>
      <l2ald-rtb-learn-vlan-mac-count-entry>
        <learn-vlan>413</learn-vlan>
        <mac-count>2</mac-count>
        <static-mac-count>0</static-mac-count>
      </l2ald-rtb-learn-vlan-mac-count-entry>
    </l2ald-rtb-learn-vlan-mac-count>
  </l2ald-rtb-mac-count-entry>
</l2ald-rtb-mac-count>
```

- [ ] **Step 3: Přepsat `tests/fixtures/rpc/junos-evo/evpn_mac.xml`**
  (nahrávka `get_mac_vrf_mac_table(count=True)` z 172.20.20.5; entry
  `default-switch` tam patří — test v Tasku 2 ověřuje, že se přeskočí):

```xml
<l2ng-l2ald-rtb-mac-count>
  <l2ng-l2ald-rtb-mac-count-entry>
    <rtb-mac-count>2</rtb-mac-count>
    <rtb-name>EVPN-VLAN-AWARE-CPE13-NNI</rtb-name>
    <vlan-name>VL-313</vlan-name>
    <l2ald-rtb-if-mac-count>
      <l2ng-l2ald-rtb-if-mac-count-entry>
</l2ng-l2ald-rtb-if-mac-count-entry>
      <l2ng-l2ald-rtb-if-mac-count-entry>
        <interface-name>et-0/0/8.313:313</interface-name>
        <mac-count>1</mac-count>
      </l2ng-l2ald-rtb-if-mac-count-entry>
    </l2ald-rtb-if-mac-count>
    <l2ng-l2ald-rtb-learn-vlan-mac-count>
      <l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
        <learn-vlan>313</learn-vlan>
        <mac-count>2</mac-count>
        <static-mac-count>0</static-mac-count>
      </l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
    </l2ng-l2ald-rtb-learn-vlan-mac-count>
  </l2ng-l2ald-rtb-mac-count-entry>
  <l2ng-l2ald-rtb-mac-count-entry>
    <rtb-mac-count>2</rtb-mac-count>
    <rtb-name>EVPN-VLAN-AWARE-POP1</rtb-name>
    <vlan-name>VL-14</vlan-name>
    <l2ald-rtb-if-mac-count>
      <l2ng-l2ald-rtb-if-mac-count-entry>
        <interface-name>ae0.14:14</interface-name>
        <mac-count>2</mac-count>
      </l2ng-l2ald-rtb-if-mac-count-entry>
    </l2ald-rtb-if-mac-count>
    <l2ng-l2ald-rtb-learn-vlan-mac-count>
      <l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
        <learn-vlan>14</learn-vlan>
        <mac-count>2</mac-count>
        <static-mac-count>0</static-mac-count>
      </l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
    </l2ng-l2ald-rtb-learn-vlan-mac-count>
  </l2ng-l2ald-rtb-mac-count-entry>
  <l2ng-l2ald-rtb-mac-count-entry>
    <rtb-mac-count>1</rtb-mac-count>
    <rtb-name>EVPN-VLAN-AWARE-POP1</rtb-name>
    <vlan-name>VL-15</vlan-name>
    <l2ald-rtb-if-mac-count>
      <l2ng-l2ald-rtb-if-mac-count-entry>
        <interface-name>ae0.15:15</interface-name>
        <mac-count>1</mac-count>
      </l2ng-l2ald-rtb-if-mac-count-entry>
    </l2ald-rtb-if-mac-count>
    <l2ng-l2ald-rtb-learn-vlan-mac-count>
      <l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
        <learn-vlan>15</learn-vlan>
        <mac-count>1</mac-count>
        <static-mac-count>0</static-mac-count>
      </l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
    </l2ng-l2ald-rtb-learn-vlan-mac-count>
  </l2ng-l2ald-rtb-mac-count-entry>
  <l2ng-l2ald-rtb-mac-count-entry>
    <rtb-mac-count>5</rtb-mac-count>
    <rtb-name>EVPN-VLAN-AWARE-POP1</rtb-name>
    <vlan-name>VL-4094</vlan-name>
    <l2ald-rtb-if-mac-count>
      <l2ng-l2ald-rtb-if-mac-count-entry>
        <interface-name>ae0.4094:4094</interface-name>
        <mac-count>5</mac-count>
      </l2ng-l2ald-rtb-if-mac-count-entry>
    </l2ald-rtb-if-mac-count>
    <l2ng-l2ald-rtb-learn-vlan-mac-count>
      <l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
        <learn-vlan>4094</learn-vlan>
        <mac-count>5</mac-count>
        <static-mac-count>0</static-mac-count>
      </l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
    </l2ng-l2ald-rtb-learn-vlan-mac-count>
  </l2ng-l2ald-rtb-mac-count-entry>
  <l2ng-l2ald-rtb-mac-count-entry>
    <rtb-mac-count>2</rtb-mac-count>
    <rtb-name>EVPN-VLAN-BASED-CPE13-NNI</rtb-name>
    <vlan-name>VL-NONE</vlan-name>
    <l2ald-rtb-if-mac-count>
      <l2ng-l2ald-rtb-if-mac-count-entry>
</l2ng-l2ald-rtb-if-mac-count-entry>
      <l2ng-l2ald-rtb-if-mac-count-entry>
        <interface-name>et-0/0/8.413:413</interface-name>
        <mac-count>1</mac-count>
      </l2ng-l2ald-rtb-if-mac-count-entry>
    </l2ald-rtb-if-mac-count>
    <l2ng-l2ald-rtb-learn-vlan-mac-count>
      <l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
        <learn-vlan>413</learn-vlan>
        <mac-count>2</mac-count>
        <static-mac-count>0</static-mac-count>
      </l2ng-l2ald-rtb-learn-vlan-mac-count-entry>
    </l2ng-l2ald-rtb-learn-vlan-mac-count>
  </l2ng-l2ald-rtb-mac-count-entry>
  <l2ng-l2ald-rtb-mac-count-entry>
    <rtb-mac-count>0</rtb-mac-count>
    <rtb-name>default-switch</rtb-name>
    <vlan-name>default</vlan-name>
    <l2ald-rtb-if-mac-count>
</l2ald-rtb-if-mac-count>
    <l2ng-l2ald-rtb-learn-vlan-mac-count>
</l2ng-l2ald-rtb-learn-vlan-mac-count>
  </l2ng-l2ald-rtb-mac-count-entry>
</l2ng-l2ald-rtb-mac-count>
```

- [ ] **Step 4: Zkopírovat instance fixtures** (extensive výpis nese vše,
  co `evpn_instance` potřebuje — stejný výpis, jiná osa parsování):

```bash
cp tests/fixtures/rpc/junos/evpn_esi.xml tests/fixtures/rpc/junos/evpn_instance.xml
cp tests/fixtures/rpc/junos-evo/evpn_esi.xml tests/fixtures/rpc/junos-evo/evpn_instance.xml
```

- [ ] **Step 5: Spustit suite** — `.venv/bin/pytest -q`. Očekávané FAILy:
  testy `test_mac_*` v `tests/collectors/test_evpn.py` a MAC část
  `tests/collectors/test_conformance.py` (parser plné tabulky na count
  fixture nic nenajde). To je záměr — opraví je Task 2. Pokud spadne
  cokoli jiného, zastavit a vyšetřit.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/rpc
git commit -m "test: nahrat count fixtures z laborky, kopie pro evpn_instance"
```

---

### Task 2: `EvpnMacCollector` — parsování `count` RPC (+ schema bump 6→7)

**Files:**
- Modify: `migration_validator/collectors/evpn.py` (třída `EvpnMacCollector`,
  helpery `_normalise_domain`/`NO_DOMAIN` se ruší, `_is_no_domain` zůstává)
- Modify: `migration_validator/models/snapshot.py:17` (`SCHEMA_VERSION = 7`)
- Test: `tests/collectors/test_evpn.py` (přepsat `test_mac_*`)

**Interfaces:**
- Produces: `facts["evpn_mac"]` nový tvar (spoléhá na něj Task 3 check
  a Task 4 engine):

```json
{
  "EVPN-VLAN-AWARE-POP1": {
    "vlans": {"14": {"count": 2, "domain": "VL-14"}},
    "interfaces": {"ae0.14": {"count": 2, "name": "ae0.14:14", "domain": "VL-14"}}
  }
}
```

  Klíč `vlans` = `learn-vlan` (string), `domain` = `bd-name`/`vlan-name`,
  nebo `None` u placeholder názvů (vlan-based). Klíč `interfaces` =
  jméno bez `:vlan` přípony (páruje se se selektory scope), `name` =
  syrové jméno pro render.

- [ ] **Step 1: Přepsat MAC testy v `tests/collectors/test_evpn.py`.**
  Smazat `test_mac_schema`, `test_mac_counts_are_grouped_by_instance_and_vlan_on_mx`,
  `test_mac_counts_use_same_keys_on_evo`, `test_mac_merges_every_rpc_for_platform`,
  `test_mac_partial_rpc_failure_is_an_error`,
  `test_mac_count_vlan_based_uses_single_placeholder_domain` (pokud je
  v tomto souboru) a import `NO_DOMAIN`. Nahradit:

```python
@pytest.mark.parametrize("platform", PLATFORMS)
def test_mac_count_schema(rpc_fixture, platform):
    result = EvpnMacCollector().parse(rpc_fixture(platform, "evpn_mac"), platform)
    assert result, "fixture nema zadnou instanci"
    for data in result.values():
        assert set(data) == {"vlans", "interfaces"}
        for vlan, entry in data["vlans"].items():
            assert vlan.isdigit()
            assert set(entry) == {"count", "domain"}
        for key, entry in data["interfaces"].items():
            assert ":" not in key
            assert set(entry) == {"count", "name", "domain"}


def test_mac_count_vlan_key_is_learn_vlan_even_for_vlan_based(rpc_fixture):
    # Placeholder nazev domeny (__X__/VL-NONE) drive znamenal klic '-'.
    # Count vypis ale nese skutecne learn-vlan na obou platformach,
    # takze vlan-based instance se pre/post paruje pres VLAN id.
    result = EvpnMacCollector().parse(rpc_fixture("junos", "evpn_mac.2"), "junos")
    based = result["EVPN-VLAN-BASED-CPE13-NNI"]
    assert based["vlans"] == {"413": {"count": 2, "domain": None}}


def test_mac_count_interface_key_strips_vlan_suffix(rpc_fixture):
    result = EvpnMacCollector().parse(rpc_fixture("junos", "evpn_mac"), "junos")
    aware = result["EVPN-VLAN-AWARE-CPE13-NNI"]
    assert aware["interfaces"] == {
        "ge-0/0/2.313": {"count": 1, "name": "ge-0/0/2.313:313", "domain": "BD-313"}
    }


def test_mac_count_skips_system_instance_and_empty_entries(rpc_fixture):
    result = EvpnMacCollector().parse(rpc_fixture("junos-evo", "evpn_mac"), "junos-evo")
    assert "default-switch" not in result
    # prazdne <...-if-mac-count-entry/> bloky nesmi vyrobit zaznam
    aware = result["EVPN-VLAN-AWARE-CPE13-NNI"]
    assert set(aware["interfaces"]) == {"et-0/0/8.313"}


def test_mac_count_merges_domains_of_one_instance(rpc_fixture):
    result = EvpnMacCollector().parse(rpc_fixture("junos-evo", "evpn_mac"), "junos-evo")
    pop1 = result["EVPN-VLAN-AWARE-POP1"]
    assert set(pop1["vlans"]) == {"14", "15", "4094"}
    assert set(pop1["interfaces"]) == {"ae0.14", "ae0.15", "ae0.4094"}


def test_mac_count_uses_count_kwarg():
    # Bez count=True by RPC stahlo celou tabulku - a parser count tvaru
    # by z ni nic neprecetl.
    assert EvpnMacCollector().rpc_kwargs("junos") == {"count": True}
    assert EvpnMacCollector().rpc_kwargs("junos-evo") == {"count": True}
```

  Test `test_mac_merges_every_rpc_for_platform` a
  `test_mac_partial_rpc_failure_is_an_error` (metoda `collect`, mock
  device) přepsat na nové schéma, logika zůstává: obě junos RPC se
  slévají, selhání kteréhokoliv = CollectorError. Mock XML v testech
  použije count tvar (zkrácená verze fixtures výše).

- [ ] **Step 2: Spustit — musí FAILovat**

Run: `.venv/bin/pytest tests/collectors/test_evpn.py -q`
Expected: FAIL (parser vrací staré schéma / KeyError `vlans`)

- [ ] **Step 3: Implementace v `collectors/evpn.py`.** Smazat `NO_DOMAIN`,
  `SHAPES`, `_normalise_domain` a starý `parse`. `_is_no_domain` zůstává.
  Nová podoba třídy (docstring a RPCS komentář upravit — RPC jsou stejná,
  mění se kwargs a parsovaný tvar):

```python
@register
class EvpnMacCollector(Collector):
    """Pocty naucenych MAC adres z 'count' vypisu.

    Drive se stahovala cela MAC tabulka a pocitaly zaznamy - na boxu
    s tisici MAC to bylo drahe a per-interface pocty z toho nesly.
    'count' varianta tychz RPC vraci hotove pocty per learn-vlan a per
    interface.
    """

    name = "evpn_mac"

    # Poradi je zamerne: prvni je "hlavni" RPC pro `record`/`--record-raw`.
    # MX potrebuje dve RPC (bridge = vlan-aware, evpn = vlan-based),
    # EVO jedno. Uvadi se jen RPC, ktera na platforme opravdu plati.
    RPCS: dict[str, tuple[str, ...]] = {
        "junos": ("get_bridge_mac_table", "get_evpn_mac_table"),
        "junos-evo": ("get_mac_vrf_mac_table",),
    }

    # Systemove instance boxu - nejsou sluzba a v device scope by kazdy
    # beh svitily radkem bez vypovedi.
    SYSTEM_INSTANCES = frozenset({"default-switch"})

    def rpc_name(self, platform: str) -> str:
        return self.RPCS[platform][0]

    def rpc_names(self, platform: str) -> tuple[str, ...]:
        return self.RPCS[platform]

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"count": True}

    def collect(self, device: Any, platform: str) -> dict[str, dict[str, Any]]:
        # Stejny princip jako drive: selhani kterehokoliv RPC je chyba
        # celeho collectoru - castecna data by check porovnal proti plne
        # baseline a hlasil propad. Radsi SKIP nez tichy nesmysl.
        if not self.supports(platform):
            raise CollectorError(
                f"collector '{self.name}' nepodporuje platformu '{platform}'"
            )

        merged: dict[str, dict[str, Any]] = {}
        failures: list[str] = []

        for rpc_name in self.rpc_names(platform):
            try:
                xml = getattr(device.rpc, rpc_name)(**self.rpc_kwargs(platform))
            except Exception as error:  # noqa: BLE001
                failures.append(f"{rpc_name}: {type(error).__name__}: {error}")
                continue

            try:
                parsed = self.parse(xml, platform)
            except Exception as error:  # noqa: BLE001
                failures.append(f"{rpc_name}: parsovani selhalo - {error}")
                continue

            for instance, data in parsed.items():
                target = merged.setdefault(instance, {"vlans": {}, "interfaces": {}})
                for area in ("vlans", "interfaces"):
                    for key, entry in data[area].items():
                        slot = target[area].setdefault(key, dict(entry, count=0))
                        slot["count"] += entry["count"]

        if failures:
            raise CollectorError(
                f"collector '{self.name}': RPC selhalo - " + "; ".join(failures)
            )

        return merged

    # Tvary count vypisu. MX pouziva l2ald-* a domenu nazyva bd-name,
    # EVO l2ng-l2ald-* a vlan-name. Prochazi se oba, takze parse()
    # nepotrebuje vetev na platformu.
    #
    # (zaznam instance+domeny, nazev domeny, per-interface zaznam,
    #  per-vlan zaznam)
    COUNT_SHAPES: tuple[tuple[str, str, str, str], ...] = (
        (
            "l2ald-rtb-mac-count-entry",
            "bd-name",
            "l2ald-rtb-if-mac-count-entry",
            "l2ald-rtb-learn-vlan-mac-count-entry",
        ),
        (
            "l2ng-l2ald-rtb-mac-count-entry",
            "vlan-name",
            "l2ng-l2ald-rtb-if-mac-count-entry",
            "l2ng-l2ald-rtb-learn-vlan-mac-count-entry",
        ),
    )

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}

        for entry_tag, domain_tag, if_tag, vlan_tag in self.COUNT_SHAPES:
            for entry in xml.iter(entry_tag):
                instance = _text(entry, "rtb-name")
                if not instance or instance in self.SYSTEM_INSTANCES:
                    continue

                # Placeholder nazev (vlan-based: '__X__' na MX, 'VL-NONE'
                # na EVO) neni skutecna domena - klicem je vzdy learn-vlan,
                # nazev slouzi jen renderu ('BD-313 MAC count').
                raw_domain = _text(entry, domain_tag)
                domain = (
                    None
                    if raw_domain is None or _is_no_domain(raw_domain)
                    else raw_domain
                )

                target = instances.setdefault(
                    instance, {"vlans": {}, "interfaces": {}}
                )

                for vlan_entry in entry.iter(vlan_tag):
                    vlan = _text(vlan_entry, "learn-vlan")
                    count = _int(vlan_entry, "mac-count")
                    if vlan is None or count is None:
                        continue
                    slot = target["vlans"].setdefault(
                        vlan, {"count": 0, "domain": domain}
                    )
                    slot["count"] += count

                for if_entry in entry.iter(if_tag):
                    raw_name = _text(if_entry, "interface-name")
                    count = _int(if_entry, "mac-count")
                    # Prazdne <...-if-mac-count-entry/> bloky jsou ve
                    # vypisu bezne.
                    if not raw_name or count is None:
                        continue
                    # 'ge-0/0/2.313:313' -> klic 'ge-0/0/2.313': za
                    # dvojteckou je VLAN a selektory scope drzi jmeno bez ni.
                    key = raw_name.rsplit(":", 1)[0]
                    slot = target["interfaces"].setdefault(
                        key, {"count": 0, "name": raw_name, "domain": domain}
                    )
                    slot["count"] += count

        return instances
```

- [ ] **Step 4: `SCHEMA_VERSION = 7`** v `models/snapshot.py:17` (jediný
  bump celé fáze — mění se tvar `evpn_mac` a Task 6 přidá novou oblast).

- [ ] **Step 5: Spustit testy collectoru**

Run: `.venv/bin/pytest tests/collectors/test_evpn.py -q`
Expected: PASS

- [ ] **Step 6: Celá suite** — `.venv/bin/pytest -q`. Očekávané zbývající
  FAILy: `tests/checks/test_evpn.py` MAC testy a conformance (staré check
  schéma) — opraví Task 3. Nic jiného nesmí spadnout.
  Pozn.: pokud testy snapshotu nesou zadrátovanou verzi 6, aktualizovat
  na 7 v tomto tasku.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/collectors/evpn.py migration_validator/models/snapshot.py tests/collectors/test_evpn.py
git commit -m "feat: evpn_mac collector cte count RPC, per-VLAN a per-interface pocty (schema 7)"
```

---

### Task 3: `EvpnMacCountCheck` — per-VLAN union a per-interface řádky

**Files:**
- Modify: `migration_validator/checks/evpn.py` (třída `EvpnMacCountCheck`,
  helpery `_mac_state_finding`/`_mac_compare_finding` zůstávají, konstanta
  `NO_DOMAIN` v checks/evpn.py se ruší)
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes: `facts["evpn_mac"]` z Tasku 2.
- Produces: labely řádků `„BD-313 MAC count"` a
  `„BD-313 Interface ge-0/0/2.313:313 MAC count"` (bez domény jen
  `„MAC count"` / `„Interface … MAC count"`); při více instancích ve
  scope kvalifikace `qualified(label, instance)`.
- Per-interface baseline klíč je už přejmenovaný engine-em (Task 4);
  check jen hledá stejný klíč.

- [ ] **Step 1: Přepsat MAC testy v `tests/checks/test_evpn.py`.**
  Subject helper + testy (staré `test_mac_count_*` smazat):

```python
def _mac_subject(count=2, *, vlan="313", domain="BD-313",
                 iface="ge-0/0/2.313", iface_count=1):
    return {"evpn_mac": {"EVPN-AWARE-CPE13": {
        "vlans": {vlan: {"count": count, "domain": domain}},
        "interfaces": {iface: {"count": iface_count,
                               "name": f"{iface}:{vlan}", "domain": domain}},
    }}}


def test_mac_count_labels_carry_domain_and_interface():
    findings = EvpnMacCountCheck().run(_ctx(_mac_subject()))
    assert _by_label(findings, "BD-313 MAC count").outcome is Outcome.OK
    row = _by_label(findings, "BD-313 Interface ge-0/0/2.313:313 MAC count")
    assert row.outcome is Outcome.OK
    assert row.value == "1"


def test_mac_count_vlan_based_has_no_domain_prefix():
    subject = _mac_subject(domain=None)
    findings = EvpnMacCountCheck().run(_ctx(subject))
    assert _by_label(findings, "MAC count").outcome is Outcome.OK
    assert _by_label(findings, "Interface ge-0/0/2.313:313 MAC count")


def test_mac_count_zero_vlan_fails():
    findings = EvpnMacCountCheck().run(_ctx(_mac_subject(0)))
    assert _by_label(findings, "BD-313 MAC count").outcome is Outcome.BROKEN


def test_mac_count_compares_vlan_against_baseline_key():
    findings = EvpnMacCountCheck().run(
        _ctx(_mac_subject(1), baseline=_mac_subject(10))
    )
    row = _by_label(findings, "BD-313 MAC count")
    assert row.outcome is Outcome.BROKEN  # -90 % pod toleranci -60
    assert row.baseline_value == "10"


def test_mac_count_vlan_missing_in_subject_fails():
    # Count vypis mrtvou domenu vubec nevypise - kdyby check iteroval jen
    # subject, zmizela domena by z reportu tise vypadla.
    subject = {"evpn_mac": {"EVPN-AWARE-CPE13": {"vlans": {}, "interfaces": {}}}}
    findings = EvpnMacCountCheck().run(_ctx(subject, baseline=_mac_subject(5)))
    row = _by_label(findings, "BD-313 MAC count")
    assert row.outcome is Outcome.BROKEN
    assert row.baseline_value == "5"


def test_mac_count_interface_missing_in_subject_is_omitted():
    # EVO count vypis interface-name nekdy nevrati - per-interface radek
    # se pak vynechava a porovnava se jen per-VLAN (rozhodnuti ze specu).
    subject = _mac_subject()
    del subject["evpn_mac"]["EVPN-AWARE-CPE13"]["interfaces"]["ge-0/0/2.313"]
    findings = EvpnMacCountCheck().run(_ctx(subject, baseline=_mac_subject()))
    assert not [f for f in findings if "Interface" in (f.label or "")]


def test_mac_count_interface_compares_via_renamed_key():
    # Engine (Task 4) preklici baseline interfaces na jmena subjektu,
    # check tedy najde baseline pod svym klicem.
    baseline = _mac_subject(iface="et-0/0/8.313", iface_count=4)
    baseline["evpn_mac"]["EVPN-AWARE-CPE13"]["interfaces"] = {
        "ge-0/0/2.313": baseline["evpn_mac"]["EVPN-AWARE-CPE13"]["interfaces"].pop("et-0/0/8.313")
    }
    findings = EvpnMacCountCheck().run(_ctx(_mac_subject(), baseline=baseline))
    row = _by_label(findings, "BD-313 Interface ge-0/0/2.313:313 MAC count")
    assert row.baseline_value == "4"


def test_mac_count_missing_data_skips():
    findings = EvpnMacCountCheck().run(_ctx({"evpn_mac": {}}))
    assert findings[0].outcome is Outcome.SKIP
```

- [ ] **Step 2: Spustit — musí FAILovat**

Run: `.venv/bin/pytest tests/checks/test_evpn.py -q`
Expected: FAIL

- [ ] **Step 3: Implementace `run()`:**

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_mac", {})
        if not instances:
            return [
                Finding(
                    Outcome.SKIP, "pro tento scope nejsou data o MAC adresach", value="bez dat"
                )
            ]

        baseline_instances = (ctx.baseline or {}).get("evpn_mac", {})
        tolerance = float(ctx.options(self.id)["tolerance_percent"])
        many = len(instances) > 1

        findings = []
        for instance in sorted(instances):
            data = instances[instance]
            baseline = baseline_instances.get(instance, {})

            def label(text: str) -> str:
                return qualified(text, instance) if many else text

            subject_vlans = data.get("vlans", {})
            baseline_vlans = baseline.get("vlans", {})
            # Union se baseline: count vypis mrtvou domenu vubec neuvadi,
            # iterace jen pres subject by jeji zmizeni tise zahodila.
            for vlan in sorted(
                set(subject_vlans) | set(baseline_vlans),
                key=lambda v: int(v) if v.isdigit() else 0,
            ):
                subject_entry = subject_vlans.get(vlan)
                baseline_entry = baseline_vlans.get(vlan)
                domain = (subject_entry or baseline_entry).get("domain")
                row_label = label(f"{domain} MAC count" if domain else "MAC count")
                if subject_entry is None:
                    findings.append(
                        _mac_compare_finding(
                            row_label, int(baseline_entry["count"]), 0, tolerance
                        )
                    )
                elif baseline_entry is None:
                    findings.append(
                        _mac_state_finding(row_label, int(subject_entry["count"]))
                    )
                else:
                    findings.append(
                        _mac_compare_finding(
                            row_label,
                            int(baseline_entry["count"]),
                            int(subject_entry["count"]),
                            tolerance,
                        )
                    )

            baseline_interfaces = baseline.get("interfaces", {})
            # Per-interface se iteruje jen subject: kdyz box interface-name
            # nevrati (EVO count vypis), radek se vynechava - rozhodnuti
            # ze specu, per-VLAN uroven je vzdy pokryta.
            for key in sorted(data.get("interfaces", {})):
                entry = data["interfaces"][key]
                domain = entry.get("domain")
                prefix = f"{domain} " if domain else ""
                row_label = label(f"{prefix}Interface {entry['name']} MAC count")
                baseline_entry = baseline_interfaces.get(key)
                if baseline_entry is None:
                    findings.append(
                        _mac_state_finding(row_label, int(entry["count"]))
                    )
                else:
                    findings.append(
                        _mac_compare_finding(
                            row_label,
                            int(baseline_entry["count"]),
                            int(entry["count"]),
                            tolerance,
                        )
                    )
        return findings
```

  Smazat konstantu `NO_DOMAIN` v checks/evpn.py (nikdo jiný ji nepoužívá
  — ověřit grepem `NO_DOMAIN` přes repo).

- [ ] **Step 4: Spustit testy checků a conformance**

Run: `.venv/bin/pytest tests/checks/test_evpn.py tests/collectors/test_conformance.py -q`
Expected: PASS (conformance staví fakta skutečným collectorem z nových
fixtures a žene je skutečným checkem — pokud FAILuje na očekávaných
labelech/hodnotách MAC řádků, aktualizovat jeho očekávání na nové labely)

- [ ] **Step 5: Celá suite** — `.venv/bin/pytest -q`, musí být zelená.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/checks/evpn.py tests/checks/test_evpn.py tests/collectors/test_conformance.py
git commit -m "feat: evpn_mac_count porovnava per-VLAN (union) a per-interface pocty"
```

---

### Task 4: Engine — přejmenování interface klíčů v `evpn_mac` baseline

**Files:**
- Modify: `migration_validator/engine.py` (`_aligned_baseline_data`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `facts["evpn_mac"][instance]["interfaces"]` z Tasku 2;
  stávající pozicní `rename` mapu v `_aligned_baseline_data`.
- Produces: baseline `evpn_mac` s interface klíči přejmenovanými na jména
  subjektu — check z Tasku 3 pak najde pár pod svým klíčem.

- [ ] **Step 1: Failující test do `tests/test_engine.py`** (použít styl
  okolních testů `_aligned_baseline_data` / snapshot builderů v souboru;
  jádro testu):

```python
def test_aligned_baseline_renames_evpn_mac_interface_keys():
    # Jmena rozhrani se migraci meni (ge-0/0/2.313 -> et-0/0/8.313);
    # bez preklicovani by per-interface MAC pocty nikdy nenasly baseline.
    baseline_scope = _scope(interfaces=["ge-0/0/2.313"], physical=["ge-0/0/2"])
    subject_scope = _scope(interfaces=["et-0/0/8.313"], physical=["et-0/0/8"])
    baseline = _snapshot(facts={"evpn_mac": {"EVPN-X": {
        "vlans": {"313": {"count": 2, "domain": "BD-313"}},
        "interfaces": {"ge-0/0/2.313": {"count": 2, "name": "ge-0/0/2.313:313",
                                        "domain": "BD-313"}},
    }}})
    data = _aligned_baseline_data(baseline_scope, subject_scope, baseline)
    assert set(data["evpn_mac"]["EVPN-X"]["interfaces"]) == {"et-0/0/8.313"}
```

  (`_scope`/`_snapshot` helpery: pokud v souboru nejsou, postavit Scope a
  Snapshot přímo — vzor je v okolních testech téhož souboru.)

- [ ] **Step 2: Spustit — musí FAILovat**

Run: `.venv/bin/pytest tests/test_engine.py -q`
Expected: FAIL (klíč zůstal `ge-0/0/2.313`)

- [ ] **Step 3: Implementace** — do `_aligned_baseline_data` za stávající
  přejmenování `data["interfaces"]` přidat:

```python
    if rename and data.get("evpn_mac"):
        # Stejny pozicni princip jako u oblasti interfaces: per-interface
        # MAC pocty se paruji pres dvojici stary <-> novy port, ne pres
        # jmeno, ktere se migraci zmenilo.
        data["evpn_mac"] = {
            instance: {
                **instance_data,
                "interfaces": {
                    rename.get(key, key): entry
                    for key, entry in instance_data.get("interfaces", {}).items()
                },
            }
            for instance, instance_data in data["evpn_mac"].items()
        }
```

  A doplnit zmínku do docstringu funkce (třetí odstavec: proč se
  přejmenovává i `evpn_mac`).

- [ ] **Step 4: Spustit** — `.venv/bin/pytest tests/test_engine.py -q` → PASS,
  pak celá suite `.venv/bin/pytest -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/engine.py tests/test_engine.py
git commit -m "feat: baseline evpn_mac interface klice se preklicuji na jmena subjektu"
```

---

### Task 5: `EvpnInstanceCollector`

**Files:**
- Modify: `migration_validator/collectors/evpn.py` (nová třída za
  `EvpnEsiCollector`)
- Test: `tests/collectors/test_evpn.py`

**Interfaces:**
- Produces: `facts["evpn_instance"]` (spoléhá na něj Task 6 wiring a
  Task 7 check):

```json
{
  "EVPN-VLAN-AWARE-POP1": {
    "local_interfaces": {"total": 3, "up": 3,
      "entries": [{"name": "ae0.14", "status": "Up"}]},
    "irb_interfaces": {"total": 2, "up": 2,
      "entries": [{"name": "irb.14", "status": "Up", "l3_context": "master"}]},
    "neighbors": {"total": 1, "addresses": ["150.0.0.13"]},
    "esis": {"00:11:12:13:14:00:00:00:00:00": "Resolved by IFL ae0.15"}
  }
}
```

  `l3_context` je vstup fáze 3 (vazba L2+L3). `esis` bez `05:` prefixu,
  hodnota je text `evpn-esi-status` (může být prázdný string).

- [ ] **Step 1: Failující testy do `tests/collectors/test_evpn.py`:**

```python
from migration_validator.collectors.evpn import EvpnInstanceCollector


@pytest.mark.parametrize("platform", PLATFORMS)
def test_instance_schema(rpc_fixture, platform):
    result = EvpnInstanceCollector().parse(
        rpc_fixture(platform, "evpn_instance"), platform
    )
    assert result, "fixture nema zadnou instanci"
    for data in result.values():
        assert set(data) == {"local_interfaces", "irb_interfaces", "neighbors", "esis"}
        for area in ("local_interfaces", "irb_interfaces"):
            assert set(data[area]) == {"total", "up", "entries"}
        assert set(data["neighbors"]) == {"total", "addresses"}


def test_instance_skips_default_evpn(rpc_fixture):
    result = EvpnInstanceCollector().parse(
        rpc_fixture("junos-evo", "evpn_instance"), "junos-evo"
    )
    assert "__default_evpn__" not in result


def test_instance_irb_carries_l3_context(rpc_fixture):
    result = EvpnInstanceCollector().parse(
        rpc_fixture("junos-evo", "evpn_instance"), "junos-evo"
    )
    pop1 = result["EVPN-VLAN-AWARE-POP1"]
    assert pop1["irb_interfaces"]["total"] == 2
    assert {"name": "irb.14", "status": "Up", "l3_context": "master"} in (
        pop1["irb_interfaces"]["entries"]
    )
    assert {"name": "irb.15", "status": "Up", "l3_context": "L3VPN-CPE14-UNI"} in (
        pop1["irb_interfaces"]["entries"]
    )


def test_instance_esis_exclude_autogenerated(rpc_fixture):
    # 05: ESI si box generuje sam (per-IRB) a nenesou status - stejne
    # pravidlo jako u EvpnEsiCollector.
    result = EvpnInstanceCollector().parse(
        rpc_fixture("junos-evo", "evpn_instance"), "junos-evo"
    )
    esis = result["EVPN-VLAN-AWARE-POP1"]["esis"]
    assert esis == {"00:11:12:13:14:00:00:00:00:00": "Resolved by IFL ae0.15"}


def test_instance_neighbors(rpc_fixture):
    result = EvpnInstanceCollector().parse(
        rpc_fixture("junos-evo", "evpn_instance"), "junos-evo"
    )
    aware = result["EVPN-VLAN-AWARE-CPE13-NNI"]
    assert aware["neighbors"] == {"total": 1, "addresses": ["150.0.0.13"]}


def test_instance_platform_rpc_names():
    # Na EVO je kanonicky prikaz 'show mac-vrf routing instance extensive';
    # jmeno RPC overene v laborce pres '| display xml rpc' (odhadnuta
    # jmena v minulosti dvakrat nesedela).
    collector = EvpnInstanceCollector()
    assert collector.rpc_name("junos") == "get_evpn_instance_information"
    assert collector.rpc_name("junos-evo") == "get_mac_vrf_instance_information"
    assert collector.rpc_kwargs("junos") == {"extensive": True}
```

- [ ] **Step 2: Spustit — musí FAILovat** (ImportError)

Run: `.venv/bin/pytest tests/collectors/test_evpn.py -q`

- [ ] **Step 3: Implementace do `collectors/evpn.py`:**

```python
@register
class EvpnInstanceCollector(Collector):
    """Per-instance stav EVPN: local/IRB rozhrani, neighbors, ESI.

    Tentyz extensive vypis jako EvpnEsiCollector, ale jina osa: tady je
    jednotkou instance (RI), tam ethernet segment napric instancemi.
    Na EVO je kanonicky prikaz 'show mac-vrf routing instance extensive'
    (RPC get_mac_vrf_instance_information, overeno v laborce) - odpoved
    ma shodny tvar evpn-instance-information jako MX, takze parse()
    nepotrebuje platformni vetev.
    """

    name = "evpn_instance"

    RPC_NAMES = {
        "junos": "get_evpn_instance_information",
        "junos-evo": "get_mac_vrf_instance_information",
    }

    # Interni instance boxu - neni sluzba, nema local interfaces a check
    # by na ni v device scope trvale hlasil FAIL.
    SYSTEM_INSTANCES = frozenset({"__default_evpn__"})

    def rpc_name(self, platform: str) -> str:
        return self.RPC_NAMES[platform]

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"extensive": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}
        for node in xml.iter("evpn-instance"):
            name = _text(node, "evpn-instance-name")
            if not name or name in self.SYSTEM_INSTANCES:
                continue

            esis: dict[str, str] = {}
            for esi_node in node.iter("evpn-esi"):
                esi = _text(esi_node, "evpn-esi-value")
                # 05: ESI si box generuje sam - bez statusu, do reportu
                # nepatri (stejne pravidlo jako EvpnEsiCollector).
                if not esi or esi.startswith("05:"):
                    continue
                esis[esi] = _text(esi_node, "evpn-esi-status") or ""

            instances[name] = {
                "local_interfaces": {
                    "total": _int(node, "local-interfaces") or 0,
                    "up": _int(node, "local-interfaces-up") or 0,
                    "entries": [
                        {
                            "name": _text(iface, "evpn-interface-name"),
                            "status": _text(iface, "evpn-interface-status")
                            or "unknown",
                        }
                        for iface in node.iter("evpn-interface")
                    ],
                },
                "irb_interfaces": {
                    "total": _int(node, "irb-interfaces") or 0,
                    "up": _int(node, "irb-interfaces-up") or 0,
                    # Filtr na irb-interface-name: vypis obsahuje i hola
                    # <irb-interface>irb.14</irb-interface> pod bridge
                    # domenou, ktera zadne deti nemaji.
                    "entries": [
                        {
                            "name": _text(iface, "irb-interface-name"),
                            "status": _text(iface, "irb-interface-status")
                            or "unknown",
                            "l3_context": _text(iface, "irb-interface-l3-context"),
                        }
                        for iface in node.iter("irb-interface")
                        if iface.find("irb-interface-name") is not None
                    ],
                },
                "neighbors": {
                    "total": _int(node, "evpn-num-neighbors") or 0,
                    "addresses": [
                        element.text
                        for element in node.iter("evpn-neighbor-address")
                        if element.text
                    ],
                },
                "esis": esis,
            }
        return instances
```

- [ ] **Step 4: Spustit** — `.venv/bin/pytest tests/collectors/test_evpn.py -q`
  → PASS, pak celá suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/collectors/evpn.py tests/collectors/test_evpn.py
git commit -m "feat: collector evpn_instance parsuje per-instance stav z extensive vypisu"
```

---

### Task 6: Wiring — `FACT_AREAS`, `Scope.select`, conformance

**Files:**
- Modify: `migration_validator/models/scope.py` (`FACT_AREAS`, `Scope.select`)
- Modify: `tests/collectors/test_conformance.py` (přidat
  `EvpnInstanceCollector` do `COLLECTORS`)
- Test: `tests/models/test_scope.py`

**Interfaces:**
- Consumes: `facts["evpn_instance"]` z Tasku 5.
- Produces: `ctx.subject["evpn_instance"]` filtrované podle
  `selectors.routing_instances` — na tom stojí Task 7.

- [ ] **Step 1: Failující test do `tests/models/test_scope.py`** (styl
  okolních select testů):

```python
def test_select_evpn_instance_by_routing_instance():
    scope = Scope(
        id="svc:X:E-LAN", kind="service",
        key=ScopeKey("X", "E-LAN", "vlan-aware"),
        selectors=Selectors(routing_instances=["EVPN-A"]),
    )
    facts = {"evpn_instance": {"EVPN-A": {"neighbors": {}}, "EVPN-B": {}}}
    assert set(scope.select(facts)["evpn_instance"]) == {"EVPN-A"}


def test_device_scope_passes_evpn_instance():
    facts = {"evpn_instance": {"EVPN-A": {}}}
    assert device_scope().select(facts)["evpn_instance"] == {"EVPN-A": {}}
```

- [ ] **Step 2: Spustit — musí FAILovat** (KeyError `evpn_instance`)

Run: `.venv/bin/pytest tests/models/test_scope.py -q`

- [ ] **Step 3: Implementace v `models/scope.py`:**
  - do `FACT_AREAS` přidat `"evpn_instance"` (za `"evpn_esi"`),
  - do `Scope.select` service větve přidat (vedle `evpn_mac`):

```python
        evpn_instance = {
            name: data
            for name, data in (facts.get("evpn_instance") or {}).items()
            if name in self.selectors.routing_instances
        }
```

  a `"evpn_instance": evpn_instance,` do návratového dictu (za
  `"evpn_esi"`). `_empty()` už vrací `{}` pro vše mimo arp/nd — beze změny.

- [ ] **Step 4: Conformance** — v `tests/collectors/test_conformance.py`
  přidat import a `EvpnInstanceCollector()` do `COLLECTORS`. Fixture
  `evpn_instance.xml` existuje z Tasku 1.

- [ ] **Step 5: Spustit** — `.venv/bin/pytest tests/models/test_scope.py
  tests/collectors/test_conformance.py -q` → PASS, pak celá suite → PASS.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/models/scope.py tests/models/test_scope.py tests/collectors/test_conformance.py
git commit -m "feat: oblast evpn_instance ve scope filtru a conformance testech"
```

---

### Task 7: `EvpnInstanceStatusCheck`

**Files:**
- Modify: `migration_validator/checks/evpn.py` (nová třída za
  `EvpnEsiStatusCheck`)
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes: `ctx.subject["evpn_instance"]` (tvar z Tasku 5), baseline
  týž tvar (instance se párují jménem RI — jméno migraci přežije).
- Produces: řádky s labely `EVPN local interfaces`,
  `EVPN local interfaces up`, `EVPN IRB interfaces`,
  `EVPN IRB interfaces up`, `EVPN neighbors`, per-ESI řádek s labelem
  `ESI <hodnota>`, INFO řádky `EVPN interface` / `IRB interface` /
  `EVPN neighbor`.

**Pravidla (spec 2.2 + compare semantika 2.4):**

| Řádek | Stav | S baseline navíc |
|---|---|---|
| Local interfaces | `total > 0` OK, jinak BROKEN | `total != baseline` → BROKEN |
| Local interfaces up | `up == total` OK, jinak BROKEN | `up/total != baseline` → BROKEN |
| IRB interfaces | vždy INFO (instance IRB mít nemusí) | jen baseline_value |
| IRB interfaces up | jen když `total > 0`: `up == total` OK, jinak BROKEN | `up/total != baseline` → BROKEN |
| EVPN neighbors | `total > 0` OK, jinak BROKEN | `total != baseline` → BROKEN |
| ESI (per hodnota) | `"resolved" in status.lower()` OK, jinak BROKEN; žádné ESI → jeden SKIP řádek | ESI v baseline, ne v subjektu → BROKEN „chybi"; status texty se NEporovnávají na rovnost (nesou jméno IFL, které se migrací mění) |
| INFO výčty | jmenovité řádky interfaců, IRB (+ l3-context) a neighborů | bez baseline_value (jména se migrací mění — výjimka z 2.4) |

- [ ] **Step 1: Failující testy do `tests/checks/test_evpn.py`:**

```python
from migration_validator.checks.evpn import EvpnInstanceStatusCheck


def _instance_subject(*, total=2, up=2, irb_total=1, irb_up=1,
                      neighbors=1, esis=None):
    return {"evpn_instance": {"EVPN-AWARE-CPE13": {
        "local_interfaces": {"total": total, "up": up, "entries": [
            {"name": "ge-0/0/2.313", "status": "Up"}]},
        "irb_interfaces": {"total": irb_total, "up": irb_up, "entries": [
            {"name": "irb.14", "status": "Up", "l3_context": "master"}]},
        "neighbors": {"total": neighbors, "addresses": ["150.0.0.13"]},
        "esis": {"00:11:12:13:14:00:00:00:00:00": "Resolved by IFL ae0.14"}
        if esis is None else esis,
    }}}


def _instance_findings(subject, baseline=None):
    return EvpnInstanceStatusCheck().run(_ctx(subject, baseline))


def test_instance_healthy_rows_pass():
    findings = _instance_findings(_instance_subject())
    for label in ("EVPN local interfaces", "EVPN local interfaces up",
                  "EVPN IRB interfaces up", "EVPN neighbors"):
        assert _by_label(findings, label).outcome in (Outcome.OK, Outcome.INFO), label
    assert _by_label(findings, "EVPN IRB interfaces").outcome is Outcome.INFO


def test_instance_zero_local_interfaces_fails():
    findings = _instance_findings(_instance_subject(total=0, up=0))
    assert _by_label(findings, "EVPN local interfaces").outcome is Outcome.BROKEN


def test_instance_interface_down_fails_up_row():
    findings = _instance_findings(_instance_subject(total=2, up=1))
    row = _by_label(findings, "EVPN local interfaces up")
    assert row.outcome is Outcome.BROKEN
    assert row.value == "1/2"


def test_instance_without_irb_skips_irb_up_row():
    findings = _instance_findings(_instance_subject(irb_total=0, irb_up=0))
    assert not [f for f in findings if f.label == "EVPN IRB interfaces up"]


def test_instance_irb_down_fails():
    findings = _instance_findings(_instance_subject(irb_total=2, irb_up=1))
    assert _by_label(findings, "EVPN IRB interfaces up").outcome is Outcome.BROKEN


def test_instance_zero_neighbors_fails():
    findings = _instance_findings(_instance_subject(neighbors=0))
    assert _by_label(findings, "EVPN neighbors").outcome is Outcome.BROKEN


def test_instance_esi_resolved_passes_unresolved_fails():
    ok = _instance_findings(_instance_subject())
    assert _by_label(ok, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.OK
    bad = _instance_findings(
        _instance_subject(esis={"00:11:12:13:14:00:00:00:00:00": ""})
    )
    assert _by_label(bad, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.BROKEN


def test_instance_no_esi_gives_skip_row():
    findings = _instance_findings(_instance_subject(esis={}))
    row = _by_label(findings, "ESI status")
    assert row.outcome is Outcome.SKIP
    assert row.value == "bez dat"


def test_instance_count_differs_from_baseline_fails():
    # 2.4: hodnoty se pre/post musi rovnat - i kdyz je stavove pravidlo
    # samo o sobe splnene.
    findings = _instance_findings(
        _instance_subject(total=2, up=2),
        baseline=_instance_subject(total=3, up=3),
    )
    row = _by_label(findings, "EVPN local interfaces")
    assert row.outcome is Outcome.BROKEN
    assert row.baseline_value == "3"


def test_instance_esi_missing_against_baseline_fails():
    findings = _instance_findings(
        _instance_subject(esis={}),
        baseline=_instance_subject(),
    )
    row = _by_label(findings, "ESI 00:11:12:13:14:00:00:00:00:00")
    assert row.outcome is Outcome.BROKEN


def test_instance_esi_status_text_not_compared_to_baseline():
    # Text statusu nese jmeno IFL ('Resolved by IFL ae0.14'), ktere se
    # migraci meni - rovnost textu by FAILovala kazdou migraci.
    findings = _instance_findings(
        _instance_subject(),
        baseline=_instance_subject(
            esis={"00:11:12:13:14:00:00:00:00:00": "Resolved by IFL ge-0/0/2.313"}
        ),
    )
    assert _by_label(findings, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.OK


def test_instance_info_rows_list_names():
    findings = _instance_findings(_instance_subject())
    values = [f.value for f in findings if f.label == "EVPN interface"]
    assert "ge-0/0/2.313 Up" in values
    irb_values = [f.value for f in findings if f.label == "IRB interface"]
    assert "irb.14 Up (master)" in irb_values
    neighbor_values = [f.value for f in findings if f.label == "EVPN neighbor"]
    assert "150.0.0.13" in neighbor_values


def test_instance_missing_data_skips():
    findings = _instance_findings({"evpn_instance": {}})
    assert findings[0].outcome is Outcome.SKIP


def test_instance_not_run_on_eline_scope():
    result = run_check(
        EvpnInstanceStatusCheck(),
        _ctx(_instance_subject(), service_type="E-Line", subtype="vpws"),
    )
    assert result == []
```

  Pozn.: `_by_label` vyžaduje právě jeden řádek daného labelu — INFO
  výčty proto testují přes list comprehension, ne přes `_by_label`.

- [ ] **Step 2: Spustit — musí FAILovat** (ImportError)

Run: `.venv/bin/pytest tests/checks/test_evpn.py -q`

- [ ] **Step 3: Implementace do `checks/evpn.py`:**

```python
@register
class EvpnInstanceStatusCheck(Check):
    """Per-instance zdravi EVPN podle brief pravidel ze zadani.

    Compare semantika (spec 2.4): ciselne hodnoty se pre/post musi
    rovnat, jinak FAIL - s vyjimkou jmen interfacu a IRB, ktera se
    migraci meni (INFO vycty proto baseline_value nenesou). Text ESI
    statusu se na rovnost neporovnava, nese jmeno IFL.
    """

    id = "evpn_instance_status"
    title = "Stav EVPN instance"
    label = "EVPN instance"
    mode = Mode.BOTH
    requires = ("evpn_instance",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_instance", {})
        if not instances:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou data evpn-instance",
                    value="bez dat",
                )
            ]

        baseline_instances = (ctx.baseline or {}).get("evpn_instance", {})
        many = len(instances) > 1

        findings: list[Finding] = []
        for name in sorted(instances):
            findings.extend(
                self._instance_findings(
                    name, instances[name], baseline_instances.get(name), many
                )
            )
        return findings

    def _instance_findings(
        self,
        instance: str,
        data: dict[str, Any],
        baseline: dict[str, Any] | None,
        qualify: bool,
    ) -> list[Finding]:
        def label(text: str) -> str:
            return qualified(text, instance) if qualify else text

        baseline = baseline or {}
        findings: list[Finding] = []

        local = data.get("local_interfaces", {})
        baseline_local = baseline.get("local_interfaces") or None
        findings.append(
            _count_finding(
                label("EVPN local interfaces"),
                f"{instance}: local interfaces",
                str(local.get("total") or 0),
                str(baseline_local["total"]) if baseline_local else None,
                ok=(local.get("total") or 0) > 0,
                expectation="> 0",
            )
        )
        findings.append(
            _count_finding(
                label("EVPN local interfaces up"),
                f"{instance}: local interfaces up",
                f"{local.get('up') or 0}/{local.get('total') or 0}",
                (
                    f"{baseline_local.get('up') or 0}/{baseline_local.get('total') or 0}"
                    if baseline_local
                    else None
                ),
                ok=(local.get("up") or 0) == (local.get("total") or 0),
                expectation="vsechna up",
            )
        )

        irb = data.get("irb_interfaces", {})
        baseline_irb = baseline.get("irb_interfaces") or None
        # Instance IRB mit nemusi (ciste L2 sluzba) - pocet je informace,
        # ne pravidlo.
        findings.append(
            Finding(
                Outcome.INFO,
                f"{instance}: IRB interfaces {irb.get('total') or 0}",
                label=label("EVPN IRB interfaces"),
                value=str(irb.get("total") or 0),
                baseline_value=(
                    str(baseline_irb["total"]) if baseline_irb else None
                ),
            )
        )
        if (irb.get("total") or 0) > 0:
            findings.append(
                _count_finding(
                    label("EVPN IRB interfaces up"),
                    f"{instance}: IRB interfaces up",
                    f"{irb.get('up') or 0}/{irb.get('total') or 0}",
                    (
                        f"{baseline_irb.get('up') or 0}/{baseline_irb.get('total') or 0}"
                        if baseline_irb
                        else None
                    ),
                    ok=(irb.get("up") or 0) == (irb.get("total") or 0),
                    expectation="vsechna up",
                )
            )

        neighbors = data.get("neighbors", {})
        baseline_neighbors = baseline.get("neighbors") or None
        findings.append(
            _count_finding(
                label("EVPN neighbors"),
                f"{instance}: EVPN neighbors",
                str(neighbors.get("total") or 0),
                (
                    str(baseline_neighbors["total"])
                    if baseline_neighbors
                    else None
                ),
                ok=(neighbors.get("total") or 0) > 0,
                expectation="> 0",
            )
        )

        findings.extend(self._esi_findings(instance, data, baseline, label))

        for entry in local.get("entries", []):
            findings.append(
                Finding(
                    Outcome.INFO,
                    f"{instance}: interface {entry['name']} {entry['status']}",
                    label=label("EVPN interface"),
                    value=f"{entry['name']} {entry['status']}",
                )
            )
        for entry in irb.get("entries", []):
            context = entry.get("l3_context")
            value = f"{entry['name']} {entry['status']}"
            if context:
                value += f" ({context})"
            findings.append(
                Finding(
                    Outcome.INFO,
                    f"{instance}: IRB {value}",
                    label=label("IRB interface"),
                    value=value,
                )
            )
        for address in neighbors.get("addresses", []):
            findings.append(
                Finding(
                    Outcome.INFO,
                    f"{instance}: neighbor {address}",
                    label=label("EVPN neighbor"),
                    value=address,
                )
            )
        return findings

    def _esi_findings(
        self,
        instance: str,
        data: dict[str, Any],
        baseline: dict[str, Any],
        label,
    ) -> list[Finding]:
        esis: dict[str, str] = data.get("esis", {})
        baseline_esis: dict[str, str] = baseline.get("esis", {}) if baseline else {}

        if not esis and not baseline_esis:
            # Single-homed instance zadne konfigurovane ESI nema - bez dat
            # je vysledek SKIP, ne chyba (spec 2.2).
            return [
                Finding(
                    Outcome.SKIP,
                    f"{instance}: zadne ESI ve vypisu",
                    label=label("ESI status"),
                    value="bez dat",
                )
            ]

        findings = []
        for esi in sorted(set(esis) | set(baseline_esis)):
            status = esis.get(esi)
            baseline_status = baseline_esis.get(esi)
            if status is None:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{instance}: ESI {esi} v baseline bylo, ted chybi",
                        label=label(f"ESI {esi}"),
                        value="chybi",
                        baseline_value=baseline_status,
                    )
                )
                continue
            resolved = "resolved" in status.lower()
            findings.append(
                Finding(
                    Outcome.OK if resolved else Outcome.BROKEN,
                    f"{instance}: ESI {esi} {status or 'bez statusu'}",
                    label=label(f"ESI {esi}"),
                    value=status or "bez statusu",
                    # Text nese jmeno IFL, ktere se migraci meni - baseline
                    # se ukazuje, ale na rovnost se neporovnava.
                    baseline_value=baseline_status,
                )
            )
        return findings
```

  A module-level helper (vedle `_esi_value`):

```python
def _count_finding(
    label: str,
    message: str,
    value: str,
    baseline_value: str | None,
    *,
    ok: bool,
    expectation: str,
) -> Finding:
    """Ciselny radek: stavove pravidlo + rovnost s baseline (spec 2.4).

    Rovnost se vynucuje i kdyz je stavove pravidlo splnene: kdyz post
    boxu ubylo rozhrani, "up == total" plati, ale sluzba prisla o port.
    """
    outcome = Outcome.OK if ok else Outcome.BROKEN
    text = f"{message}: {value}" if ok else f"{message}: {value}, ocekavano {expectation}"
    if ok and baseline_value is not None and value != baseline_value:
        outcome = Outcome.BROKEN
        text = f"{message}: {value}, baseline {baseline_value}"
    return Finding(
        outcome, text, label=label, value=value, baseline_value=baseline_value
    )
```

- [ ] **Step 4: Spustit** — `.venv/bin/pytest tests/checks/test_evpn.py -q`
  → PASS. Pokud conformance test FAILuje (nové řádky u E-LAN scope
  změnily očekávané počty), aktualizovat jeho očekávání.

- [ ] **Step 5: Celá suite** — `.venv/bin/pytest -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/checks/evpn.py tests/checks/test_evpn.py tests/collectors/test_conformance.py
git commit -m "feat: check evpn_instance_status - brief pravidla + compare per instance"
```

---

### Task 8: Dokumentace + ověření proti laborce

**Files:**
- Modify: `docs/cs/reference.md` (tabulka checků: upravit popis
  `evpn_mac_count`, přidat řádek `evpn_instance_status`; zkontrolovat
  zmínky o schema verzi)
- Modify: `docs/en/reference.md` (totéž anglicky)
- Create: `docs/superpowers/roadmap-2026-08-05-faze2-hotovo.md`

- [ ] **Step 1: `docs/cs/reference.md`** — do tabulky checků přidat:

```markdown
| `evpn_instance_status` | both | critical | E-LAN | local interfaces > 0 a všechna up; IRB up (pokud IRB existují); EVPN neighbors > 0; ESI „resolved“; s baseline navíc rovnost počtů |
```

  a upravit řádek `evpn_mac_count` na:

```markdown
| `evpn_mac_count` | both | advisory | E-LAN | počty MAC z `count` výpisu per VLAN a per interface; > 0 a s baseline pokles proti toleranci |
```

  Projít `grep -n "schema" docs/cs/reference.md docs/cs/architecture.md`
  a zmínky o verzi snapshotu zvednout na 7. Totéž zrcadlově v `docs/en/`.

- [ ] **Step 2: Ověření proti laborce** (vyžaduje služby na MX — stav
  z 2026-08-05; koordinovat s uživatelem, pokud se laborka mezitím
  změnila):

```bash
eval "$(grep 'export MIG_LAB_PASSWORD' ~/.bashrc)"
.venv/bin/mig-validate capture --device 172.20.20.4 --username admin \
    --auth password --password "$MIG_LAB_PASSWORD" \
    --inventory 172.20.20.4.yml --phase pre --output /tmp/faze2-pre.json
.venv/bin/mig-validate evaluate --snapshot /tmp/faze2-pre.json --detail
```

  (Přesná jména flagů ověřit přes `.venv/bin/mig-validate capture --help` —
  tento plán je nepředepisuje, CLI se ve fázi 2 nemění.)
  Očekávání: capture projde, `evpn_instance` i `evpn_mac` bez chyby
  collectoru, E-LAN bloky nesou nové řádky (Local interfaces, EVPN
  neighbors, `BD-313 MAC count`, `Interface ge-0/0/2.313:313 MAC count`).
  EVO (172.20.20.5) capture také spustit: očekává se `evpn_mac` collector
  error „l2-learning subsystem is not running" → SKIP řádky (služby jsou
  na MX) — to je správné chování, ne vada.

- [ ] **Step 3: Poznámka o rozbitých starých snapshotech** — snapshoty
  se `schema_version: 6` (`runs/mig01/*.json`) nástroj s verzí 7 odmítne.
  Do `docs/superpowers/roadmap-2026-08-05-faze2-hotovo.md` napsat shrnutí
  fáze (vzor: `roadmap-2026-08-04-vlna10-hotovo.md`): co se změnilo, nové
  labely, schema bump 6→7 + že `runs/mig01` je potřeba znovu nasnímat
  (pre = služby na MX, post = po migraci na EVO), a výsledek ověření
  ze Step 2.

- [ ] **Step 4: Celá suite naposledy** — `.venv/bin/pytest -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/cs/reference.md docs/en/reference.md docs/superpowers/roadmap-2026-08-05-faze2-hotovo.md
git commit -m "docs: faze 2 - evpn_instance_status, MAC count pres count RPC, schema 7"
```

---

## Self-review (provedeno při psaní plánu)

- **Spec 2.1** — pokrývá Task 5 (všechna vyjmenovaná pole: local/IRB
  interfaces vč. l3-context, neighbors, ESI bez `05:`; RPC jméno na EVO
  ověřeno živě, ne odhadem).
- **Spec 2.2** — Task 7 (tabulka pravidel 1:1, „bez dat SKIP" u ESI,
  `--detail` výčty = INFO řádky, které renderer ukazuje v rozbaleném
  bloku — žádná změna rendereru není potřeba).
- **Spec 2.3** — Tasky 1–3 (obě junos RPC + EVO RPC s `count=True`,
  per-VLAN klíč = VLAN id, per-interface přes pár rozhraní, EVO bez
  interface-name → řádek se vynechá, render labely dle zadání).
- **Spec 2.4** — Task 7 `_count_finding` (rovnost počtů), Task 3 union
  per-VLAN; výjimka pro jména interfaců/IRB = INFO řádky bez baseline.
- **Vědomé odchylky od dnešního chování:** vlan-based MAC klíč `"-"` →
  skutečné `learn-vlan` (ověřeno na obou platformách v laborce);
  `default-switch` a `__default_evpn__` se přeskakují (systémové
  instance). Obojí zdůvodněno v kódu.
