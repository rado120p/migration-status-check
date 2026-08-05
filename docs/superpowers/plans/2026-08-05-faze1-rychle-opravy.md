# Fáze 1 — Rychlé opravy: implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Opravit čtyři známé vady EVPN/ARP vrstvy: ARP/ND záznamy naučené
přes IRB se ztrácejí ve scope filtru, interface errors běží na unitech bez
counterů, auto-generovaná ESI `05:` znečišťují report, a EVPN-VPWS check
ignoruje `evpn-vpws-sid-pe-status` a nese matoucí label.

**Architecture:** Čtyři nezávislé opravy + jeden nový mechanismus (stav
INFO pro informativní řádky bez hodnocení), na kterém VPWS check staví.
Collectory normalizují data (platformní vrstva), checky hodnotí jednotné
schéma — beze změny této dělby. Snapshot schéma se zvedá z 5 na 6
(nové klíče `learned_via` v arp/nd, nový tvar `evpn_vpws`).

**Tech Stack:** Python 3, lxml, pytest. Testy: `.venv/bin/pytest`.
Fixtures z laborky v `tests/fixtures/rpc/{junos,junos-evo}/` — už obsahují
všechny tvary, které fáze 1 potřebuje (ARP `irb.14[ ae0.14 ]`, VPWS
`sid-pe-info`, ESI `05:`); nic se nenahrává znovu.

**Spec:** `docs/superpowers/specs/2026-08-05-evpn-a-run-management-roadmapa-design.md`, sekce „Fáze 1".

## Global Constraints

- Jazyk kódu, komentářů, hlášek a testů: čeština bez diakritiky (jako
  zbytek repa). Dokumentace v `docs/` s diakritikou.
- Komentáře vysvětlují PROČ (omezení, které kód sám neukáže), ne CO.
- `SCHEMA_VERSION` se zvedá z 5 na 6 **jednou**, v Tasku 2 (první změna
  fact schématu). Task 5 už bump nedělá.
- Každá změna chování = nejdřív failující test (TDD). Test, jehož hodnota
  stojí na zabití mutanta, mutanta skutečně spustí (krok v tasku) —
  tvrzení „test by chytil X" bez spuštění je zakázané
  (viz docs/superpowers/roadmap-2026-08-04-vlna10-hotovo.md).
- Po každém tasku zelená celá suite: `.venv/bin/pytest -q`.
- Necommitovat `172.20.20.4.yml` / `172.20.20.5.yml` v kořeni repa
  (rozpracované inventory uživatele).

---

### Task 1: Stav INFO pro informativní řádky

Informativní řádky (SID hodnoty, mode, role, ESI) nenesou hodnocení —
sloupec STAV má zůstat prázdný a souhrny je nemají počítat. Schváleno
v brainstormu (mockup VPWS výstupu).

**Files:**
- Modify: `migration_validator/models/result.py`
- Modify: `migration_validator/reporting/text_report.py` (konstanta `SYMBOL`)
- Test: `tests/models/test_result.py`, `tests/reporting/test_text_report.py`

**Interfaces:**
- Produces: `Outcome.INFO` (= `"info"`), `Status.INFO` (= `"INFO"`,
  rank −1 — nikdy nevyhraje `Status.worst()`), `derive_status(Outcome.INFO,
  cokoliv) -> Status.INFO`, `count_statuses()` vrací navíc klíč `"info"`.
  Task 6 vyrábí `Finding(Outcome.INFO, ...)`.

- [ ] **Step 1: Failující testy modelu**

Do `tests/models/test_result.py` přidat:

```python
def test_info_outcome_derives_info_status_for_both_severities():
    assert derive_status(Outcome.INFO, Severity.CRITICAL) is Status.INFO
    assert derive_status(Outcome.INFO, Severity.ADVISORY) is Status.INFO


def test_info_never_wins_worst():
    # INFO radek nesmi zhorsit (ani "vylepsit") stav sluzby.
    assert Status.worst([Status.PASS, Status.INFO]) is Status.PASS
    assert Status.worst([Status.INFO, Status.FAIL]) is Status.FAIL


def test_count_statuses_counts_info_separately():
    counts = count_statuses([Status.PASS, Status.INFO, Status.INFO])
    assert counts["pass"] == 1
    assert counts["info"] == 2
```

Doplnit importy (`derive_status`, `Outcome`, `Severity`, `Status`,
`count_statuses` z `migration_validator.models.result`) podle stávající
hlavičky souboru.

- [ ] **Step 2: Ověřit, že testy failují**

Run: `.venv/bin/pytest tests/models/test_result.py -q`
Expected: FAIL / AttributeError (`Status` nemá `INFO`).

- [ ] **Step 3: Implementace v result.py**

```python
class Status(str, Enum):
    PASS = "PASS"
    SKIP = "SKIP"
    WARN = "WARN"
    FAIL = "FAIL"
    INFO = "INFO"
```

```python
_STATUS_RANK: dict[Status, int] = {
    Status.INFO: -1,   # informativni radek nikdy neurcuje stav sluzby
    Status.PASS: 0,
    Status.SKIP: 1,
    Status.WARN: 2,
    Status.FAIL: 3,
}
```

```python
def count_statuses(statuses: Iterable[Status]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0, "info": 0}
    ...  # telo beze zmeny
```

```python
class Outcome(str, Enum):
    OK = "ok"
    INFO = "info"
    DEGRADED = "degraded"
    BROKEN = "broken"
    SKIP = "skip"
```

V `derive_status` hned za větev `OK`:

```python
    if outcome is Outcome.INFO:
        return Status.INFO
```

- [ ] **Step 4: Failující test rendereru**

Do `tests/reporting/test_text_report.py` přidat test, který postaví
`CheckResult` se `status=Status.INFO` (podle vzoru okolních testů, které
staví `ScopeResult`/`RunResult` ručně) a ověří, že v renderu:

```python
def test_info_row_has_blank_status_column(...):
    # radek se vytiskne, ale sloupec STAV je prazdny
    assert "EVPN VPWS SID local value" in rendered
    line = next(l for l in rendered.splitlines() if "SID local value" in l)
    assert "INFO" not in line
    assert line.lstrip().startswith("|")
```

Pozor (poučení vlny 10): před napsáním aserce si dočasným debug printem
ověř, jak přesně `_block()` řádek tiskne — STAV sloupec má šířku podle
nejdelšího symbolu a prázdný symbol se doplňuje mezerami. Aserci napiš
proti skutečnému tvaru, ne proti odhadu.

- [ ] **Step 5: Ověřit fail, implementovat SYMBOL**

Run: `.venv/bin/pytest tests/reporting/test_text_report.py -q` → FAIL
(KeyError `Status.INFO` v `SYMBOL`).

V `text_report.py`:

```python
SYMBOL = {
    Status.PASS: "PASS",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
    Status.SKIP: "SKIP",
    Status.INFO: "",     # informativni radek: STAV zustava prazdny
}
```

Ověřit, že formátování šířky sloupce prázdný symbol doplní mezerami
(pokud renderer používá pevné `f"{...:<4}"` nebo `ljust`, projde samo;
pokud ne, doplnit zarovnání v místě sazby řádku).

- [ ] **Step 6: Celá suite + commit**

Run: `.venv/bin/pytest -q` → vše zelené (žádný stávající test nesmí
spadnout — `count_statuses` má nový klíč, JSON summary ho ponese taky;
pokud nějaký test porovnává celý dict summary, aktualizovat ho, důvod:
nový stav je součást kontraktu).

```bash
git add migration_validator/models/result.py migration_validator/reporting/text_report.py tests/models/test_result.py tests/reporting/test_text_report.py
git commit -m "feat: stav INFO pro informativni radky bez hodnoceni"
```

---

### Task 2: ARP/ND normalizace `irb.14[ ae0.14 ]` → `learned_via`

ARP záznam naučený přes IRB nese v `interface-name` i L2 rozhraní
v hranaté závorce. `Selectors.matches_interface()` porovnává přesnou
shodu, takže záznam nikdy nespadne do scope a check hlásí „zadny zaznam",
přestože v JSONu je. Totéž platí pro ND.

**Files:**
- Modify: `migration_validator/collectors/arp.py`
- Modify: `migration_validator/collectors/nd.py`
- Modify: `migration_validator/models/snapshot.py` (`SCHEMA_VERSION = 6`)
- Modify: `migration_validator/checks/reachability.py` (render `[via ...]`)
- Test: `tests/collectors/test_arp.py`, `tests/collectors/test_nd.py`,
  `tests/checks/test_reachability.py`

**Interfaces:**
- Produces: záznam arp/nd má nový klíč `learned_via: str | None` —
  `{"ip", "mac", "interface", "routing_instance", "learned_via"}` (arp),
  `{"ip", "mac", "interface", "state", "learned_via"}` (nd). `interface`
  je vždy čisté jméno (`irb.14`). Sdílená funkce
  `split_learned_via(name: str) -> tuple[str, str | None]`
  v `migration_validator/collectors/arp.py` (nd ji importuje).
- Fáze 3 na `learned_via` staví (měření na L2 rozhraní) — klíč je součást
  kontraktu, ne kosmetika.

- [ ] **Step 1: Failující test rozdělovací funkce**

Do `tests/collectors/test_arp.py`:

```python
from migration_validator.collectors.arp import split_learned_via


def test_split_learned_via_plain_name():
    assert split_learned_via("ge-0/0/4.0") == ("ge-0/0/4.0", None)


def test_split_learned_via_irb_bracket():
    assert split_learned_via("irb.14[ ae0.14 ]") == ("irb.14", "ae0.14")


def test_split_learned_via_strips_whitespace():
    assert split_learned_via("irb.14 [ae0.14]") == ("irb.14", "ae0.14")
```

- [ ] **Step 2: Ověřit fail**

Run: `.venv/bin/pytest tests/collectors/test_arp.py -q`
Expected: ImportError (`split_learned_via` neexistuje).

- [ ] **Step 3: Implementace v arp.py**

```python
def split_learned_via(name: str) -> tuple[str, str | None]:
    """Rozdeli 'irb.14[ ae0.14 ]' na ('irb.14', 'ae0.14').

    Junos u zaznamu naucenych pres IRB pripoji v hranate zavorce L2
    rozhrani. Scope filtruje pres presnou shodu jmena, takze neorezany
    tvar zaznam vyradi - v reportu pak 'zadny zaznam' u sluzby, ktera
    ARP ma (JSON ho nese, check ho nevidi).
    """
    base, bracket, rest = name.partition("[")
    if not bracket:
        return name.strip(), None
    return base.strip(), rest.rstrip("]").strip() or None
```

V `ArpCollector.parse()` nahradit přiřazení interface:

```python
            interface, learned_via = split_learned_via(interface)
            entries.append(
                {
                    "ip": address,
                    "mac": _text(node, "mac-address"),
                    "interface": interface,
                    "learned_via": learned_via,
                    "routing_instance": ...,  # beze zmeny
                }
            )
```

Totéž v `NdCollector.parse()` (import z `arp`), klíč `learned_via` vedle
`state`.

- [ ] **Step 4: Aktualizovat testy klíčů + schema bump**

`tests/collectors/test_arp.py::test_entries_have_expected_keys` rozšířit
množinu o `learned_via`; obdobně v `tests/collectors/test_nd.py`. Přidat
test proti fixture (junos-evo `arp.xml` obsahuje `irb.14[ ae0.14 ]`):

```python
@pytest.mark.parametrize("platform", ("junos-evo",))
def test_irb_entry_is_normalised(rpc_fixture, platform):
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    irb = [entry for entry in result if entry["interface"] == "irb.14"]
    assert irb, "fixture nema ARP zaznam na irb.14"
    assert irb[0]["learned_via"] == "ae0.14"
    assert all("[" not in entry["interface"] for entry in result)
```

V `migration_validator/models/snapshot.py`: `SCHEMA_VERSION = 6`.
(Stávající test na verzi, pokud existuje, aktualizovat; smysl: staré
snapshoty bez `learned_via` a se starým tvarem `evpn_vpws` nesmí
projít novým nástrojem mlčky.)

Run: `.venv/bin/pytest tests/collectors -q` → zelené.

- [ ] **Step 5: Failující test renderu `[via ...]`**

Do `tests/checks/test_reachability.py` (podle vzoru okolních testů, které
staví ctx se `subject={"arp": [...]}`):

```python
def test_arp_value_shows_learned_via():
    ctx = _ctx({"arp": [{"ip": "152.11.14.4", "mac": "0c:00:ca:ea:58:03",
                          "interface": "irb.14", "learned_via": "ae0.14"}]})
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert "[via ae0.14]" in result.value


def test_arp_value_without_learned_via_unchanged():
    ctx = _ctx({"arp": [{"ip": "1.2.3.4", "mac": "aa:bb", "interface": "ge-0/0/4.0",
                          "learned_via": None}]})
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert "[via" not in result.value
```

(Přesný tvar helperu `_ctx` převzít ze souboru — selektory musí obsahovat
`local_ipv4`, jinak check vrátí prázdno, viz `_family_not_configured`.)

- [ ] **Step 6: Ověřit fail, implementovat**

V `ArpPresentCheck.run()` (a zrcadlově `NdPresentCheck`):

```python
        def _value(entry: dict[str, Any]) -> str:
            value = f"{entry.get('mac') or '?'} -> {entry['ip']}"
            if entry.get("learned_via"):
                value = f"{value}  [via {entry['learned_via']}]"
            return value
```

a použít místo dosavadního f-stringu. `learned_via` přidat i do
`subject` findingu (strojový výstup).

Run: `.venv/bin/pytest tests/checks/test_reachability.py -q` → PASS.

- [ ] **Step 7: Celá suite + commit**

`.venv/bin/pytest -q` — pozor na conformance testy
(`tests/collectors/test_conformance.py`): teď nově uvidí ARP záznam na
irb.14 uvnitř scope — pokud některá aserce počítala findings, ověřit,
že nový (správný) výsledek odpovídá záměru testu, a aktualizovat s
odůvodněním v komentáři.

```bash
git add migration_validator/collectors/arp.py migration_validator/collectors/nd.py migration_validator/models/snapshot.py migration_validator/checks/reachability.py tests/collectors/test_arp.py tests/collectors/test_nd.py tests/checks/test_reachability.py
git commit -m "fix: ARP/ND pres IRB - learned_via, zaznam uz neuteka scope filtru"
```

---

### Task 3: Interface errors jen na fyzickém rozhraní

Logické jednotky (unity) reálné error countery nemají — check na nich
nemá běžet, u žádného typu služby. Traffic (pps) na unitech zůstává.

**Files:**
- Modify: `migration_validator/checks/ifaces.py` (jen `InterfaceErrorsCheck`)
- Test: `tests/checks/test_ifaces.py`

**Interfaces:**
- Produces: `is_physical(name: str) -> bool` v `checks/ifaces.py`
  (jméno bez tečky). `InterfaceErrorsCheck` běží jen na
  `is_transit(name) and is_physical(name)`.

- [ ] **Step 1: Failující testy**

```python
def test_errors_skip_logical_units():
    ctx = _ctx({"interfaces": {
        "ge-0/0/4": {"input_errors": 0, "output_errors": 0},
        "ge-0/0/4.0": {"input_errors": 7, "output_errors": 0},
    }})
    findings = run_check(InterfaceErrorsCheck(), ctx)
    labels = [f.label for f in findings]
    assert any("ge-0/0/4)" in label for label in labels)
    assert not any("ge-0/0/4.0" in label for label in labels)


def test_errors_only_units_present_gives_skip():
    # scope muze nest jen unity (fyzicky rodic mimo inventory)
    ctx = _ctx({"interfaces": {"ae0.15": {"input_errors": 0}}})
    findings = run_check(InterfaceErrorsCheck(), ctx)
    assert [f.status for f in findings] == [Status.SKIP]
```

(Helper `_ctx` převzít ze souboru.)

- [ ] **Step 2: Ověřit fail**

Run: `.venv/bin/pytest tests/checks/test_ifaces.py -q`
Expected: první test FAIL — dnes vzniká i řádek pro `ge-0/0/4.0`.

- [ ] **Step 3: Implementace**

```python
def is_physical(interface: str) -> bool:
    """Fyzicke rozhrani (bez unitu). Unity error countery nenesou -
    radek 'bez chyb' na unitu tvrdi mereni, ktere neprobehlo."""
    return "." not in interface
```

V `InterfaceErrorsCheck.run()`:

```python
        names = [name for name in _transit_interfaces(ctx) if is_physical(name)]
        if not names:
            return [_no_transit_finding(ctx)]
```

`InterfaceTrafficCheck` a `TrafficCeasedCheck` se **nemění**.

- [ ] **Step 4: Mutant**

Test musí zabít mutanta, který filtr vrátí zpět:

```bash
# MUTANT: odstranit filtr is_physical (names = _transit_interfaces(ctx))
grep -n "MUTANT" migration_validator/checks/ifaces.py   # musi neco vypsat
.venv/bin/pytest tests/checks/test_ifaces.py -q          # musi FAILNOUT
# vratit zpet a overit PASS
```

- [ ] **Step 5: Celá suite + commit**

`.venv/bin/pytest -q` — conformance testy nyní ztratí error řádky unitů;
případné počty aktualizovat s komentářem proč.

```bash
git add migration_validator/checks/ifaces.py tests/checks/test_ifaces.py
git commit -m "fix: interface errors jen na fyzickem rozhrani, unity countery nemaji"
```

---

### Task 4: Ignorace auto-generovaných ESI `05:`

ESI začínající `05:` generuje box sám (per-IRB), nenesou status a do
reportu nepatří. Dnes `EvpnEsiCollector` bere všechny.

**Files:**
- Modify: `migration_validator/collectors/evpn.py` (`EvpnEsiCollector.parse`)
- Test: `tests/collectors/test_evpn.py`

**Interfaces:**
- Produces: beze změny schématu — jen `05:` ESI ve výstupu nejsou.

- [ ] **Step 1: Failující test**

Fixture `tests/fixtures/rpc/junos-evo/evpn_esi.xml` obsahuje
`00:11:...` i tři `05:...` ESI:

```python
@pytest.mark.parametrize("platform", ("junos", "junos-evo"))
def test_auto_generated_esi_are_ignored(rpc_fixture, platform):
    result = EvpnEsiCollector().parse(rpc_fixture(platform, "evpn_esi"), platform)
    assert result, "fixture nema zadne ESI"
    assert all(not esi.startswith("05:") for esi in result)
```

(Pokud junos fixture `05:` nemá, parametrizovat jen `junos-evo` a do
testu napsat proč.)

- [ ] **Step 2: Ověřit fail**

Run: `.venv/bin/pytest tests/collectors/test_evpn.py -q` → FAIL
(`05:00:00:fd:e8:...` ve výsledku).

- [ ] **Step 3: Implementace**

V `EvpnEsiCollector.parse()` hned po načtení `esi`:

```python
            # ESI zacinajici 05: si box generuje sam (per-IRB). Nenesou
            # status ani DF a v reportu by kazda L3-extended sluzba
            # svitila radkem bez vypovedi.
            if esi.startswith("05:"):
                continue
```

- [ ] **Step 4: Mutant + suite + commit**

```bash
# MUTANT: zakomentovat `continue` -> test musi FAILNOUT, pak vratit
.venv/bin/pytest -q
git add migration_validator/collectors/evpn.py tests/collectors/test_evpn.py
git commit -m "fix: EvpnEsiCollector ignoruje auto-generovana ESI 05:"
```

---

### Task 5: EVPN-VPWS collector — SID/peer schéma

Collector dnes čte jen `interface-status` a dvě SID čísla z prvního
rozhraní; `evpn-vpws-sid-pe-status` tabulky (podstata checku) ignoruje.
Nové schéma nese všechna rozhraní a peery obou SID.

**Files:**
- Modify: `migration_validator/collectors/evpn.py` (`EvpnVpwsCollector`)
- Test: `tests/collectors/test_evpn.py`

**Interfaces:**
- Produces (kontrakt pro Task 6):

```python
instances[name] = {
    "interfaces": [
        {
            "name": "ge-0/0/2.213",
            "status": "Up",              # evpn-vpws-interface-status
            "mode": "single-homed",      # evpn-vpws-interface-mode
            "local_sid": {"value": 1000, "peers": [PEER, ...]},
            "remote_sid": {"value": 2000, "peers": [PEER, ...]},
        },
    ],
}
PEER = {
    "esi": "00:11:12:13:14:00:00:00:00:00",  # evpn-vpws-sid-interface-esi
    "ipaddr": "150.0.0.14",                   # evpn-vpws-sid-pe-ipaddr
    "mode": "single-homed",                   # evpn-vpws-sid-pe-mode
    "role": "Primary",                        # evpn-vpws-sid-pe-role
    "status": "Resolved",                     # evpn-vpws-sid-pe-status
}
```

  Prázdná `sid-pe-status-table` ⇒ `peers: []`. Chybějící SID uzel ⇒
  `{"value": None, "peers": []}`.

- [ ] **Step 1: Failující testy nového schématu**

Nahradit stávající testy schématu `EvpnVpwsCollector` (jsou vázané na
starý tvar — přepsání je záměr, ne vedlejší škoda; do commit message
uvést):

```python
@pytest.mark.parametrize("platform", PLATFORMS)
def test_vpws_instances_carry_interface_list(rpc_fixture, platform):
    result = EvpnVpwsCollector().parse(rpc_fixture(platform, "evpn_vpws"), platform)
    assert result, "fixture nema zadnou vpws instanci"
    for data in result.values():
        assert set(data) == {"interfaces"}
        for iface in data["interfaces"]:
            assert set(iface) == {"name", "status", "mode", "local_sid", "remote_sid"}
            for sid in (iface["local_sid"], iface["remote_sid"]):
                assert set(sid) == {"value", "peers"}
                for peer in sid["peers"]:
                    assert set(peer) == {"esi", "ipaddr", "mode", "role", "status"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_vpws_remote_peer_resolved_from_fixture(rpc_fixture, platform):
    result = EvpnVpwsCollector().parse(rpc_fixture(platform, "evpn_vpws"), platform)
    iface = next(iter(result.values()))["interfaces"][0]
    assert iface["remote_sid"]["value"] is not None
    assert iface["remote_sid"]["peers"], "fixture nese sid-pe-info, parser ho nevraci"
    assert iface["remote_sid"]["peers"][0]["status"] == "Resolved"


def test_vpws_empty_pe_table_gives_empty_peers():
    xml = etree.fromstring(
        """<evpn-vpws-information><evpn-vpws-instance>
        <evpn-vpws-instance-name>X</evpn-vpws-instance-name>
        <evpn-vpws-interface-status-table><evpn-vpws-interface>
          <evpn-vpws-interface-name>ge-0/0/2.213</evpn-vpws-interface-name>
          <evpn-vpws-interface-mode>single-homed</evpn-vpws-interface-mode>
          <evpn-vpws-interface-status>Up</evpn-vpws-interface-status>
          <evpn-vpws-service-id-local-status-table><evpn-vpws-sid-local>
            <evpn-vpws-sid-local-value>1000</evpn-vpws-sid-local-value>
            <evpn-vpws-sid-pe-status-table/>
          </evpn-vpws-sid-local></evpn-vpws-service-id-local-status-table>
          <evpn-vpws-service-id-remote-status-table><evpn-vpws-sid-remote>
            <evpn-vpws-sid-remote-value>2000</evpn-vpws-sid-remote-value>
            <evpn-vpws-sid-pe-status-table/>
          </evpn-vpws-sid-remote></evpn-vpws-service-id-remote-status-table>
        </evpn-vpws-interface></evpn-vpws-interface-status-table>
        </evpn-vpws-instance></evpn-vpws-information>"""
    )
    result = EvpnVpwsCollector().parse(xml, "junos")
    iface = result["X"]["interfaces"][0]
    assert iface["local_sid"] == {"value": 1000, "peers": []}
    assert iface["remote_sid"] == {"value": 2000, "peers": []}
```

(Inline XML je zde na místě: fixtures z laborky prázdnou remote tabulku
nemají — je to přesně scénář „remote strana není nakonfigurovaná".)

- [ ] **Step 2: Ověřit fail**

Run: `.venv/bin/pytest tests/collectors/test_evpn.py -q` → FAIL
(staré schéma `{"local_sid", "remote_sid", "status"}`).

- [ ] **Step 3: Implementace**

Přepsat `EvpnVpwsCollector.parse()`:

```python
    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}
        for node in xml.iter("evpn-vpws-instance"):
            name = _text(node, "evpn-vpws-instance-name")
            if not name:
                continue
            interfaces = [
                self._interface(iface)
                for iface in node.iter("evpn-vpws-interface")
            ]
            instances[name] = {"interfaces": interfaces}
        return instances

    def _interface(self, iface: etree._Element) -> dict[str, Any]:
        return {
            "name": _text(iface, "evpn-vpws-interface-name"),
            "status": _text(iface, "evpn-vpws-interface-status") or "unknown",
            "mode": _text(iface, "evpn-vpws-interface-mode"),
            "local_sid": self._sid(
                iface,
                "evpn-vpws-service-id-local-status-table/evpn-vpws-sid-local",
                "evpn-vpws-sid-local-value",
            ),
            "remote_sid": self._sid(
                iface,
                "evpn-vpws-service-id-remote-status-table/evpn-vpws-sid-remote",
                "evpn-vpws-sid-remote-value",
            ),
        }

    @staticmethod
    def _sid(iface: etree._Element, path: str, value_tag: str) -> dict[str, Any]:
        sid = iface.find(path)
        if sid is None:
            return {"value": None, "peers": []}
        peers = [
            {
                "esi": _text(peer, "evpn-vpws-sid-interface-esi"),
                "ipaddr": _text(peer, "evpn-vpws-sid-pe-ipaddr"),
                "mode": _text(peer, "evpn-vpws-sid-pe-mode"),
                "role": _text(peer, "evpn-vpws-sid-pe-role"),
                "status": _text(peer, "evpn-vpws-sid-pe-status"),
            }
            for peer in sid.iter("evpn-vpws-sid-pe-info")
        ]
        return {"value": _int(sid, value_tag), "peers": peers}
```

Smazat starou metodu `_status` a zastaralou část docstringu o „prvnim
rozhrani" (dokumentuje omezení, které tímto padá).

- [ ] **Step 4: Suite (očekávaný dočasný pád checku)**

`.venv/bin/pytest -q` — testy `EvpnVpwsStatusCheck` v
`tests/checks/test_evpn.py` a conformance teď spadnou (check čte staré
schéma). To je v pořádku **jen pokud Task 6 následuje okamžitě** —
commit až spolu s Taskem 6, NEBO (preferováno) Task 5 a 6 dělá jeden
implementer v jedné větvi a commitne po zelené suite Tasku 6. Rozhodnutí:
**necommitovat samostatně**; červený mezistav nesmí zůstat v historii.

---

### Task 6: EVPN-VPWS check — hodnocení per SID + peer

**Files:**
- Modify: `migration_validator/checks/evpn.py` (`EvpnVpwsStatusCheck`,
  `_vpws_value` smazat)
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes: schéma z Tasku 5, `Outcome.INFO` z Tasku 1.
- Produces: findings s labely (přesná znění, viz spec/mockup):
  - `EVPN VPWS local interface status` — OK když `Up`, jinak BROKEN
  - `EVPN VPWS SID local value` / `EVPN VPWS SID remote value` — INFO,
    hodnota `SID <value>` (chybějící value: `SID ?`)
  - per peer: `EVPN VPWS SID local peer PE` (INFO tam nepatří — viz
    pravidlo níže) → tři řádky na peer:
    `EVPN VPWS SID {local,remote} PE` — OK/BROKEN dle statusu peera,
    hodnota ipaddr; `EVPN VPWS SID {local,remote} status` — OK když
    `status == "Resolved"`, jinak BROKEN; INFO řádky
    `... mode`, `... ESI`, `... role`.
  - remote SID **bez jediného peera**: dva BROKEN řádky
    `EVPN VPWS SID remote PE : Neznamy peer` a
    `EVPN VPWS SID remote status : Unresolved / Chybi`.
  - local SID bez peera: INFO řádek `EVPN VPWS SID local mode :
    <interface mode> (multi-homing peer ve vypisu nenalezen)` — local
    peery existují jen u multihomingu, jejich absence není vada.
  - Víc rozhraní v instanci: labely kvalifikovat jménem
    (`... (ae0.224)`) přes `qualified()` z `checks.ifaces`, jen když
    `len(interfaces) > 1`.

- [ ] **Step 1: Přepsat testy checku**

Nahradit stávající `EvpnVpwsStatusCheck` testy (staré schéma). Nový
subject helper:

```python
def _vpws_subject(*, status="Up", mode="single-homed",
                  local_peers=(), remote_peers=(), remote_value=2000):
    return {"evpn_vpws": {"EVPN-VPWS-X": {"interfaces": [{
        "name": "ge-0/0/2.213", "status": status, "mode": mode,
        "local_sid": {"value": 1000, "peers": list(local_peers)},
        "remote_sid": {"value": remote_value, "peers": list(remote_peers)},
    }]}}}


PEER_OK = {"esi": "00:00:00:00:00:00:00:00:00:00", "ipaddr": "150.0.0.14",
           "mode": "single-homed", "role": "Primary", "status": "Resolved"}
```

Testy (minimálně):

```python
def test_local_interface_status_row_renamed():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    row = _by_label(findings, "EVPN VPWS local interface status")
    assert row.outcome is Outcome.OK and row.value == "Up"


def test_remote_peer_resolved_passes_per_peer():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    assert _by_label(findings, "EVPN VPWS SID remote PE").value == "150.0.0.14"
    assert _by_label(findings, "EVPN VPWS SID remote status").outcome is Outcome.OK


def test_remote_peer_unresolved_fails():
    peer = {**PEER_OK, "status": "Unresolved"}
    findings = run_findings(_vpws_subject(remote_peers=[peer]))
    assert _by_label(findings, "EVPN VPWS SID remote status").outcome is Outcome.BROKEN


def test_missing_remote_peer_fails_with_unknown_peer():
    findings = run_findings(_vpws_subject(remote_peers=[]))
    pe = _by_label(findings, "EVPN VPWS SID remote PE")
    assert pe.outcome is Outcome.BROKEN and pe.value == "Neznamy peer"
    status = _by_label(findings, "EVPN VPWS SID remote status")
    assert status.outcome is Outcome.BROKEN and status.value == "Unresolved / Chybi"


def test_informative_rows_are_info():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    for label in ("EVPN VPWS SID local value", "EVPN VPWS SID remote value",
                  "EVPN VPWS SID remote mode", "EVPN VPWS SID remote role"):
        assert _by_label(findings, label).outcome is Outcome.INFO


def test_two_remote_peers_two_row_sets():
    peer2 = {**PEER_OK, "ipaddr": "150.0.0.2", "mode": "all-active",
             "esi": "00:11:12:13:14:00:00:00:00:00"}
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK, peer2]))
    pe_rows = [f for f in findings if f.label == "EVPN VPWS SID remote PE"]
    assert [f.value for f in pe_rows] == ["150.0.0.14", "150.0.0.2"]


def test_local_multihoming_peer_rows():
    peer = {**PEER_OK, "ipaddr": "150.0.0.2", "mode": "all-active",
            "esi": "00:11:12:13:14:00:00:00:00:00"}
    findings = run_findings(_vpws_subject(mode="all-active", local_peers=[peer]))
    assert _by_label(findings, "EVPN VPWS SID local peer PE").value == "150.0.0.2"
    assert _by_label(findings, "EVPN VPWS SID local status").outcome is Outcome.OK


def test_local_single_homed_info_note():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    row = _by_label(findings, "EVPN VPWS SID local mode")
    assert row.outcome is Outcome.INFO
    assert "multi-homing peer ve vypisu nenalezen" in row.value


def test_interface_down_still_broken():
    findings = run_findings(_vpws_subject(status="Down", remote_peers=[PEER_OK]))
    row = _by_label(findings, "EVPN VPWS local interface status")
    assert row.outcome is Outcome.BROKEN


def test_two_interfaces_qualify_labels():
    subject = _vpws_subject(remote_peers=[PEER_OK])
    ifaces = subject["evpn_vpws"]["EVPN-VPWS-X"]["interfaces"]
    ifaces.append({**ifaces[0], "name": "ae0.224"})
    findings = run_findings(subject)
    assert any(f.label == "EVPN VPWS local interface status (ge-0/0/2.213)"
               for f in findings)
```

Helpery:

```python
def run_findings(subject):
    return EvpnVpwsStatusCheck().run(_vpws_ctx(subject))


def _by_label(findings, label):
    hits = [f for f in findings if f.label == label]
    assert len(hits) == 1, f"label {label!r}: {len(hits)} radku"
    return hits[0]
```

Pozn.: testy čtou `finding.outcome`/`label`/`value` přímo z
`Check.run()` — ne přes `run_check` — protože INFO řádky se hodnotí na
úrovni Outcome. Pokud okolní testy používají `run_check` + `Status`,
držet jejich vzor a asertovat `Status.INFO`.

- [ ] **Step 2: Ověřit fail**

Run: `.venv/bin/pytest tests/checks/test_evpn.py -q` → FAIL/ERROR.

- [ ] **Step 3: Implementace checku**

Přepsat `EvpnVpwsStatusCheck.run()`:

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_vpws", {})
        if not instances:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-vpws",
                            value="bez dat")]

        findings: list[Finding] = []
        for name in sorted(instances):
            interfaces = instances[name].get("interfaces", [])
            many = len(interfaces) > 1
            for iface in interfaces:
                findings.extend(self._interface_findings(name, iface, many))
        return findings

    def _interface_findings(self, instance: str, iface: dict[str, Any],
                            qualify: bool) -> list[Finding]:
        def label(text: str) -> str:
            return qualified(text, iface["name"]) if qualify else text

        findings = []
        status = str(iface.get("status", "unknown"))
        findings.append(Finding(
            Outcome.OK if _is_up(status) else Outcome.BROKEN,
            f"{instance}: stav rozhrani {status}"
            + ("" if _is_up(status) else f", ocekavano {UP}"),
            label=label("EVPN VPWS local interface status"),
            value=status,
            subject={"interface": iface["name"], "status": status},
        ))
        findings.extend(self._sid_findings(instance, iface, "local", label))
        findings.extend(self._sid_findings(instance, iface, "remote", label))
        return findings
```

`_sid_findings` (jádro; stav výhradně z `peer["status"]`):

```python
    def _sid_findings(self, instance: str, iface: dict[str, Any],
                      side: str, label) -> list[Finding]:
        sid = iface.get(f"{side}_sid") or {"value": None, "peers": []}
        value = sid.get("value")
        peers = sid.get("peers") or []
        prefix = f"EVPN VPWS SID {side}"

        findings = [Finding(
            Outcome.INFO, f"{instance}: {side} SID {value if value is not None else '?'}",
            label=label(f"{prefix} value"),
            value=f"SID {value if value is not None else '?'}",
        )]

        if not peers:
            if side == "remote":
                # Remote peer musi existovat vzdy - jeho absence znamena
                # nenakonfigurovanou nebo spadlou druhou stranu.
                findings.append(Finding(
                    Outcome.BROKEN, f"{instance}: remote peer chybi",
                    label=label(f"{prefix} PE"), value="Neznamy peer",
                ))
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{instance}: remote SID nema zadny Resolved zaznam",
                    label=label(f"{prefix} status"), value="Unresolved / Chybi",
                ))
            else:
                # Local peery nese jen multihoming - u single-homed jde
                # o ocekavany stav, ne o vadu.
                findings.append(Finding(
                    Outcome.INFO, f"{instance}: local strana bez multi-homing peeru",
                    label=label(f"{prefix} mode"),
                    value=f"{iface.get('mode') or 'unknown'} "
                          "(multi-homing peer ve vypisu nenalezen)",
                ))
            return findings

        peer_label = f"{prefix} peer PE" if side == "local" else f"{prefix} PE"
        for peer in peers:
            resolved = (peer.get("status") or "").strip().lower() == "resolved"
            outcome = Outcome.OK if resolved else Outcome.BROKEN
            findings.append(Finding(
                outcome, f"{instance}: {side} peer {peer.get('ipaddr')}",
                label=label(peer_label), value=str(peer.get("ipaddr") or "?"),
                subject=dict(peer),
            ))
            findings.append(Finding(
                outcome,
                f"{instance}: {side} peer {peer.get('ipaddr')} "
                f"status {peer.get('status') or 'chybi'}",
                label=label(f"{prefix} status"),
                value=str(peer.get("status") or "Unresolved / Chybi"),
            ))
            for info_label, key in (("mode", "mode"), ("ESI", "esi"), ("role", "role")):
                if peer.get(key):
                    findings.append(Finding(
                        Outcome.INFO, f"{instance}: {side} peer {key} {peer[key]}",
                        label=label(f"{prefix} {info_label}"),
                        value=str(peer[key]),
                    ))
        return findings
```

Import `qualified` z `migration_validator.checks.ifaces`. Funkci
`_vpws_value` smazat (nikdo jiný ji nepoužívá — ověřit grepem). Docstring
checku aktualizovat: stav per SID+peer podle `evpn-vpws-sid-pe-status`;
zmínka o baseline hodnotách padá (STATE-podoba řádků; `mode = Mode.BOTH`
zůstává, `baseline_value` u řádků teď nevzniká — sloupec ZMENA u state
řádků zůstane prázdný, viz `change_text`). Pozn. k dřívějšímu chování:
řádek „chybi remote SID" nahrazují BROKEN řádky remote PE/status.

Pozor na `test_two_interfaces_qualify_labels` + `_by_label`: u dvou
rozhraní má každý label kvalifikaci, `_by_label` s prostým labelem nesmí
najít nic — testy které používají `_by_label` drž na single-interface
subjektech.

- [ ] **Step 4: Ověřit PASS + mutant na jádro pravidla**

Run: `.venv/bin/pytest tests/checks/test_evpn.py -q` → PASS.

```bash
# MUTANT: v _sid_findings zamenit podminku resolved za `True`
# (outcome vzdy OK) -> test_remote_peer_unresolved_fails musi FAILNOUT
grep -n "MUTANT" migration_validator/checks/evpn.py
.venv/bin/pytest tests/checks/test_evpn.py -q
# vratit zpet, overit PASS
```

- [ ] **Step 5: Conformance + celá suite + commit (spolu s Taskem 5)**

`.venv/bin/pytest -q` — conformance testy projedou nové schéma přes
skutečné fixtures; opravit aserce vázané na staré VPWS řádky (očekávaný
posun: místo jednoho řádku `<instance> : Up SID 1000 -> 2000` sada
řádků podle nového kontraktu).

```bash
git add migration_validator/collectors/evpn.py migration_validator/checks/evpn.py tests/collectors/test_evpn.py tests/checks/test_evpn.py
git commit -m "feat: EVPN-VPWS hodnoceni per SID+peer podle sid-pe-status

Nove schema collectoru (interfaces/local_sid/remote_sid/peers) nahrazuje
puvodni trojici status+local_sid+remote_sid; stare testy schema prepsany
zamerne."
```

---

### Task 7: Dokumentace + závěrečné ověření

**Files:**
- Modify: `docs/cs/files/collectors.md` (arp: `learned_via`, evpn_vpws:
  nové schéma, evpn_esi: ignorace `05:`)
- Modify: `docs/cs/files/checks.md` (interface_errors: jen fyzická
  rozhraní; evpn_vpws_status: per SID+peer, INFO řádky)
- Modify: `docs/cs/files/models.md` nebo ekvivalent zmiňující
  `SCHEMA_VERSION`/stavy (INFO, schema 6) — dohledat grepem
  `grep -rn "SCHEMA_VERSION\|Status" docs/cs/files/`
- Modify: `docs/cs/files/reporting.md` (stav INFO: prázdný sloupec STAV,
  souhrny INFO nepočítají)

**Interfaces:** žádné — jen dokumentace skutečného stavu po Taskech 1–6.

- [ ] **Step 1: Aktualizovat dokumenty**

Každou změněnou oblast popsat větou–dvěma podle skutečného kódu (číst
kód, ne plán). Nepopisovat historii („dříve bylo"), popisovat stav.

- [ ] **Step 2: Závěrečný běh a kontrola**

```bash
.venv/bin/pytest -q                      # cela suite zelena
grep -rn "MUTANT" migration_validator/   # nesmi nic vypsat
git status                                # zadne zapomenute soubory;
                                          # 172.20.20.*.yml zustavaji necommitnute
```

- [ ] **Step 3: Commit**

```bash
git add docs/cs/files/collectors.md docs/cs/files/checks.md docs/cs/files/reporting.md docs/cs/files/models.md
git commit -m "docs: learned_via, VPWS SID/peer schema, stav INFO, errors jen na fyzice"
```
