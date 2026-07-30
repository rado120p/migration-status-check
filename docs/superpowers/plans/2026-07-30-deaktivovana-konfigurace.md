# Deaktivovaná konfigurace, aktivita statik a integrita testů — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deaktivovaná routing-instance ani deaktivované rozhraní už službu nevypustí z inventory — deaktivace se zaznamená, je vidět ve výsledku a párování se jí neřídí.

**Architecture:** Parsery čtou `inactive` na rozhraních a dědí ho z kontejnerů; inventory a snímek dostávají schema 4 se dvěma čestnými příznaky; `run_check()` dostane pátý důvod pro SKIP a nový check `deactivation_state` porovná deaktivaci subjektu proti baseline. Aktivita statické routy se konečně čte z měření. Testovací integrita se řeší jako první, aby zbytek plánu stál na testech, které opravdu diskriminují.

**Tech Stack:** Python 3, lxml, pytest, PyYAML. Parsery `mx_parser.py` a `evo_parser.py` jsou skripty v kořeni repozitáře, ne balíček. Nástroj je balíček `migration_validator/`.

**Návrh:** [`../specs/2026-07-30-deaktivovana-konfigurace-design.md`](../specs/2026-07-30-deaktivovana-konfigurace-design.md) (AR‑18 … AR‑29)

## Global Constraints

Platí pro **každou** úlohu, i když to u ní není zopakované.

- **Zámek parserů.** Po každé změně `mx_parser.py` nebo `evo_parser.py` spusť
  `diff mx_parser.py evo_parser.py | wc -l`. Musí vrátit **146**. Jiná
  hodnota obvykle znamená, že se změna aplikovala jen na jednu stranu.
  Kdyby se ukázalo, že rozdíl je legitimní, novou hodnotu **odvoď, zdůvodni
  a zapiš do tohoto plánu** — symetrie se nikdy nedosahuje dopsáním mrtvého
  kódu do druhého parseru.
- **Každý předepsaný test musí zabíjet konkrétní špatnou implementaci.**
  U každého testu je v tomto plánu uvedený mutant. Aplikuj ho, spusť test,
  ověř, že **spadne**, a mutanta vrať. Test, který popisuje správné chování,
  aniž by ho odlišil od nesprávného, se čte jako pokrytí a není jím.
- **Každý mutant se grepem potvrdí, že dopadl.** Po aplikaci mutanta vždy
  `grep -n` na změněný řádek. Neaplikovaný mutant vypadá identicky jako
  nediskriminující test. Vlna 2 na to doplatila dvakrát.
- **U parametrizovaného testu se mutant pouští na každou větev zvlášť.**
  Jeden mutant v `mx_parser.py` neříká nic o `evo_parser.py`.
- **Testy:** `.venv/bin/python -m pytest`. Výchozí stav před úlohou 1 je
  **547 passed, 1 skipped**. Po každé úloze musí být sada zelená.
- **Nikdy `.venv/bin/mig-validate`.** Shebang dá `.venv/bin` na `sys.path[0]`
  a obejde `pythonpath`. Vždy `.venv/bin/python -m migration_validator.cli`.
- **Diakritika:** v `mx_parser.py` a `evo_parser.py` mají komentáře
  a docstringy diakritiku; v `migration_validator/` nemají. Řetězce, které
  jdou do reportu, jsou v celém balíčku **bez diakritiky**. Drž se toho, co
  je v souboru, který upravuješ.
- **Commituj po každé úloze.** Zprávy commitů bez diakritiky.
- **`stash@{0}` se nedropuje** — nese jediný záznam o `EVPN-VPWS-CPE24-UNI`
  před regenerací.

## Mapa souborů

| soubor | co s ním |
|---|---|
| `tests/collectors/test_conformance.py` | úloha 1 — assert na jména fixture |
| `tests/checks/test_bfd.py` | úloha 2 — šev collector→check pro BFD |
| `tests/models/test_inventory.py` | úloha 3 — zpřísnit `match=` |
| `tests/checks/test_routes.py` | úloha 3 — přepsat device-scope test |
| `migration_validator/models/inventory.py` | úloha 4 — `ServiceEntry`, schema 4 |
| `mx_parser.py`, `evo_parser.py` | úlohy 4, 5, 6 — `InterfaceService`, `InterfaceConfig`, čtení `inactive` |
| `migration_validator/models/scope.py` | úloha 7 — `Scope` nese příznaky |
| `migration_validator/models/snapshot.py` | úloha 7 — `SCHEMA_VERSION = 4` |
| `migration_validator/scoping/builder.py` | úloha 7 — opsat příznaky ze `ServiceEntry` |
| `migration_validator/checks/base.py` | úlohy 8, 9 — zkratka v `run_check()`, `baseline_scope` v `CheckContext` |
| `migration_validator/checks/deactivation.py` | úloha 9 — **nový** |
| `migration_validator/checks/all.py` | úloha 9 — registrace |
| `migration_validator/engine.py` | úloha 9 — předat `baseline_scope` |
| `migration_validator/checks/routes.py` | úloha 11 — číst `active` |
| `172.20.20.{4,5}.yml`, `tests/fixtures/172.20.20.{4,5}.yml` | úlohy 4, 12 |
| `tests/fixtures/rpc/junos-evo/interfaces.xml` | úloha 12 |
| `docs/cs/files/parsers.md`, `docs/en/…` | úloha 12 — dokumentace |

---

## Úloha 1: Conformance test nesmí schovat přejmenovanou oblast (AR‑26)

Dnes `_facts_from_recorded_xml` volá `pytest.skip`, když fixture chybí.
Přejmenování `RoutesCollector.name` tak shodí celý modul do skipu a sada hlásí
`527 passed / 19 skipped, nula failů`. „Prošlo" tam znamená „neproběhlo".

**Files:**
- Modify: `tests/collectors/test_conformance.py:63-90`

**Interfaces:**
- Consumes: `COLLECTORS` (existující tuple v témž souboru), `RPC_ROOT`
- Produces: `test_every_collector_has_its_fixtures` — na ničem dalším nestojí

- [ ] **Step 1: Napiš padající test**

Přidej hned za definici `_fixture_paths` (tedy nad `_facts_from_recorded_xml`):

```python
@pytest.mark.parametrize("platform", ("junos", "junos-evo"))
def test_every_collector_has_its_fixtures(platform):
    """Jmena oblasti a jmena nahravek na disku se musi shodovat, oboustranne.

    Zabiji mutanta: prejmenovani RoutesCollector.name na "routes_x". Bez teto
    asertace by _facts_from_recorded_xml sahlo po neexistujici routes_x.xml,
    zavolalo pytest.skip a cely modul by zmizel ze sady jako "19 skipped,
    nula failu" - tedy presne opacny signal, nez jaky ma prejmenovany klic
    vydat.

    Osirela fixture je stejna chyba jako chybejici: znamena, ze se collector
    prestal spoustet a nikdo si toho nevsiml.
    """
    expected = {
        path.name
        for collector in COLLECTORS
        for path in _fixture_paths(platform, collector)
    }
    on_disk = {path.name for path in (RPC_ROOT / platform).glob("*.xml")}

    assert expected == on_disk, (
        f"{platform}: chybi {sorted(expected - on_disk)}, "
        f"osirelo {sorted(on_disk - expected)}"
    )
```

- [ ] **Step 2: Spusť test proti dnešnímu stavu**

Run: `.venv/bin/python -m pytest tests/collectors/test_conformance.py::test_every_collector_has_its_fixtures -v`
Expected: **PASS** pro obě platformy. Fixture na disku dnes odpovídají — test
zatím jen zafixuje pravdu. Když spadne, znamená to, že už teď máš na disku
osiřelou nebo chybějící nahrávku; vyřeš to dřív, než půjdeš dál.

- [ ] **Step 3: Ověř mutantem, že test opravdu diskriminuje — junos**

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/collectors/routes.py")
s = p.read_text()
old = 'name = "routes"'
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, 'name = "routes_x"'))
EOF
grep -n 'name = "routes' migration_validator/collectors/routes.py
```

Expected z grepu: `name = "routes_x"` — bez toho mutant nedopadl a další krok
neříká nic.

- [ ] **Step 4: Spusť sadu s mutantem**

Run: `.venv/bin/python -m pytest tests/collectors/test_conformance.py -q`
Expected: `test_every_collector_has_its_fixtures` **FAILED** pro obě platformy
s hláškou `chybi ['routes_x.xml'], osirelo ['routes.xml']`.

Kdyby prošel, test nediskriminuje a je k ničemu — oprav ho, ne mutanta.

- [ ] **Step 5: Vrať mutanta**

```bash
git checkout migration_validator/collectors/routes.py
grep -n 'name = "routes' migration_validator/collectors/routes.py
```

Expected z grepu: `name = "routes"`.

- [ ] **Step 6: Nahraď `pytest.skip` tvrdým selháním**

V `_facts_from_recorded_xml` (`tests/collectors/test_conformance.py:86`)
nahraď:

```python
            if not path.exists():
                pytest.skip(f"chybi fixture {path}")
```

za:

```python
            # Drive tu byl pytest.skip. Ten z prejmenovane oblasti udelal
            # "19 skipped, nula failu" - tedy signal, ze je vsechno v poradku.
            # Chybejici nahravka je chyba sady, ne duvod ji preskocit.
            assert path.exists(), f"chybi fixture {path}"
```

- [ ] **Step 7: Spusť celou sadu**

Run: `.venv/bin/python -m pytest -q`
Expected: **547 passed, 1 skipped** — přibyly 2 testy z parametrizace, takže
očekávej **549 passed, 1 skipped**. Zapiš skutečné číslo do commit message.

- [ ] **Step 8: Commit**

```bash
git add tests/collectors/test_conformance.py
git commit -m "test(conformance): chybejici fixture je fail, ne skip (AR-26)"
```

---

## Úloha 2: Šev collector→check pro BFD (AR‑27)

Roadmapa chtěla doplnit parametr `("junos", "bfd_session_state")` do
`test_specific_check_sees_data`. **Ověřeno 2026‑07‑30, že to nejde:**

```bash
.venv/bin/python -c "
from lxml import etree
for f in ['tests/fixtures/rpc/junos/bfd.xml',
          'runs/bfd-static-2026-07-29/rpc/172.20.20.4.bfd.xml']:
    r = etree.parse(f).getroot()
    print(f, len(r.findall('.//{*}bfd-session')), 'session')
"
```

Obojí vrací **nula session** — nejen fixture, ale i surový capture z laborky.
MX prostě žádnou BFD session nemá. Asertace „aspoň jeden ne‑SKIP" by na tom
parametru selhala z legitimního důvodu a přenahrání fixture (úloha 12) na tom
nic nezmění.

Šev, o který jde, ale na platformě nezávisí: je to otázka, jestli check čte
**právě tu oblast, kterou collector vydává**. Uzavře se ve dvou půlkách:

- `test_collector_keys_match_contract` (už existuje) tvrdí, že klíče faktů
  odpovídají jménům collectorů — a to na obou platformách.
- Nový test níž ověří, že `BfdSessionStateCheck` čte oblast pojmenovanou
  `BfdCollector().name`, ne literál `"bfd"`.

Dohromady to pokrývá týž šev, jaký na `junos-evo` drží
`test_specific_check_sees_data`. **Co tím pokryté není:** MX‑specifické
parsování BFD odpovědi. To zůstává neověřené, dokud v laborce nebude MX se
session — zapiš to do roadmapy v úloze 12, krok 10.

**Files:**
- Modify: `tests/checks/test_bfd.py`

**Interfaces:**
- Consumes: `BfdCollector` z `migration_validator.collectors.bfd`
- Produces: `test_check_reads_the_area_named_by_its_collector`

- [ ] **Step 1: Napiš test**

Do `tests/checks/test_bfd.py` (import `BfdCollector` doplň do hlavičky):

```python
def test_check_reads_the_area_named_by_its_collector():
    """Sev collector -> check: oblast se jmenuje podle collectoru, ne literalem.

    Conformance test to na MX pokryt neumi - fixture pro junos ma nula session
    (overeno 2026-07-30 i na surovem capturu z laborky), takze "aspon jeden
    ne-SKIP" by na nem selhalo z legitimniho duvodu. Klic ale na platforme
    nezavisi, takze staci overit ho jednou - a proti jmenu collectoru, ne
    proti retezci "bfd" napsanemu podruhe.

    Druhou polovinu sevu drzi test_collector_keys_match_contract, ktery tvrdi,
    ze klice faktu odpovidaji jmenum collectoru.

    Zabiji mutanta: ctx.subject.get("bfd") -> ctx.subject.get("bfd_x")
    v checks/bfd.py. S nim je session neviditelna, zamer zustava, a check
    misto OK vrati SKIP 'BGP neni Established'.
    """
    area = BfdCollector().name
    scope = Scope(
        id="svc:CPE13:IPVPN",
        kind="service",
        key=ScopeKey("CPE13", "IPVPN", None),
        selectors=Selectors(bfd_peers=[{"peer": "198.11.13.2"}]),
    )
    ctx = CheckContext(
        scope=scope,
        subject={area: {"198.11.13.2": {"state": "Up"}}, "bgp": {}},
        baseline=None,
        config=default_config(),
    )

    findings = BfdSessionStateCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK, (
        "check nevidi oblast, kterou BfdCollector vydava - klic se rozesel "
        "mezi collectorem a checkem"
    )
```

Jména `Scope`, `ScopeKey`, `Selectors`, `CheckContext`, `default_config`
a `Outcome` už soubor importuje; ověř to a doplň jen `BfdCollector`.

- [ ] **Step 2: Spusť — musí projít**

Run: `.venv/bin/python -m pytest tests/checks/test_bfd.py::test_check_reads_the_area_named_by_its_collector -v`
Expected: **PASS**. Test zafixovává pravdu, kód se nemění.

- [ ] **Step 3: Ověř mutantem, že diskriminuje**

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/bfd.py")
s = p.read_text()
old = 'ctx.subject.get("bfd", {})'
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, 'ctx.subject.get("bfd_x", {})'))
EOF
grep -n 'subject.get("bfd' migration_validator/checks/bfd.py
.venv/bin/python -m pytest tests/checks/test_bfd.py -q
git checkout migration_validator/checks/bfd.py
grep -n 'subject.get("bfd' migration_validator/checks/bfd.py
```

Expected: grep po mutaci ukáže `bfd_x`; test **FAILED**; poslední grep zase
`bfd`.

Kdyby test s mutantem prošel, nediskriminuje — oprav ho, ne mutanta.

- [ ] **Step 4: Spusť celou sadu a commitni**

Run: `.venv/bin/python -m pytest -q`
Expected: zelená, o jeden test víc.

```bash
git add tests/checks/test_bfd.py
git commit -m "test(bfd): pokryt sev collector->check jmenem oblasti (AR-27)"
```

---

## Úloha 3: Dva nediskriminující testy (AR‑28)

Mutační test z 2026‑07‑30 zúžil zadání roadmapy: verze
`test_device_scope_reports_state_without_intent` v `tests/checks/test_bfd.py`
**diskriminuje** (mutant `not configured and not is_device` → `not configured`
ji shodí) a nechává se být. Opravují se dva testy, ne tři.

**Files:**
- Modify: `tests/models/test_inventory.py:228-245`
- Modify: `tests/checks/test_routes.py:198-210`

**Interfaces:**
- Consumes: `Scope`, `ScopeKey`, `Selectors` (už importované v `test_routes.py`),
  `MISSING_ENTIRELY` / `MISSING_FROM_TABLE` z `checks/routes.py`
- Produces: nic, na čem by stály další úlohy

- [ ] **Step 1: Zpřísni `test_mapping_list_rejects_scalars`**

Problém: `match="mapping"` sedí i na jméno adresáře `tmp_path`, takže test
dnes diskriminuje náhodou, ne návrhem. Hláška z kódu je
`f"ocekavan mapping v seznamu, nalezeno {type(item).__name__}"`
(`models/inventory.py:41`).

Najdi v testu `pytest.raises(..., match="mapping")` a nahraď vzor:

```python
    with pytest.raises(ValueError, match=r"ocekavan mapping v seznamu, nalezeno str"):
```

Doplň do docstringu testu větu:

```
    Vzor je zamerne cela hlaska vcetne jmena typu: samotne "mapping" sedi
    i na jmeno adresare z tmp_path, takze by test prosel i proti vyjimce,
    ktera s tou kontrolou nema nic spolecneho.
```

- [ ] **Step 2: Ověř mutantem, že vzor teď rozhoduje**

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/models/inventory.py")
s = p.read_text()
old = 'f"ocekavan mapping v seznamu, nalezeno {type(item).__name__}"'
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, 'f"spatny prvek v seznamu: {type(item).__name__}"'))
EOF
grep -n "spatny prvek v seznamu" migration_validator/models/inventory.py
```

Run: `.venv/bin/python -m pytest tests/models/test_inventory.py::test_mapping_list_rejects_scalars -q`
Expected: **FAILED** — `DID NOT MATCH`.

```bash
git checkout migration_validator/models/inventory.py
grep -n "ocekavan mapping v seznamu" migration_validator/models/inventory.py
```

- [ ] **Step 3: Přepiš device-scope test v `tests/checks/test_routes.py`**

Dnešní test vkládá routu, která v tabulce **je**, takže se do větve
`subject is None` (`checks/routes.py:116`) vůbec nedostane — ověřeno: s
mutantem `configured and not is_device` → `configured` projde celý soubor.

Nahraď celý `test_device_scope_reports_state_without_intent` tímto párem:

```python
def test_device_scope_does_not_claim_a_route_is_missing_from_the_table():
    """Device scope nezna zamer, takze 'neni v tabulce' rict nesmi (AR-17).

    Zabiji mutanta: odebrani 'and not is_device' z checks/routes.py:130.
    Aby ta vetev mela co rozhodovat, musi byt 'configured' pravda - proto se
    tu stavi scope s kind="device" A NEPRAZDNYMI static_routes. Puvodni test
    pouzival device_scope(), ktery ma vzdy prazdne selektory, takze
    'configured' bylo vzdy False a cela podminka nepravda bez ohledu na
    is_device.
    """
    scope = Scope(
        id="dev:172.20.20.4",
        kind="device",
        key=None,
        selectors=Selectors(static_routes=list(CONFIGURED)),
    )
    ctx = CheckContext(
        scope=scope,
        subject={"routes": {}},
        baseline={"routes": _installed()},
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "chybi", (
        "device scope ohlasil 'neni v tabulce' - to tvrdi, ze zna zamer, "
        "a ten v nem znat neni"
    )


def test_service_scope_does_claim_a_route_is_missing_from_the_table():
    """Protejsek predchoziho testu - bez nej by 'chybi' slo vratit vzdycky.

    Zabiji mutanta: zamena cele podminky na 'False' v checks/routes.py:130.
    """
    ctx = _ctx({}, _installed())

    findings = StaticRouteStatusCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "neni v tabulce"
```

- [ ] **Step 4: Ověř oba mutanty**

Mutant A:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/routes.py")
s = p.read_text()
old = "value = MISSING_FROM_TABLE if configured and not is_device else MISSING_ENTIRELY"
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, "value = MISSING_FROM_TABLE if configured else MISSING_ENTIRELY"))
EOF
grep -n "value = MISSING_FROM_TABLE" migration_validator/checks/routes.py
.venv/bin/python -m pytest tests/checks/test_routes.py -q
git checkout migration_validator/checks/routes.py
```

Expected: `test_device_scope_does_not_claim_a_route_is_missing_from_the_table`
**FAILED**.

Mutant B:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/routes.py")
s = p.read_text()
old = "value = MISSING_FROM_TABLE if configured and not is_device else MISSING_ENTIRELY"
assert s.count(old) == 1
p.write_text(s.replace(old, "value = MISSING_ENTIRELY"))
EOF
grep -n "value = MISSING" migration_validator/checks/routes.py
.venv/bin/python -m pytest tests/checks/test_routes.py -q
git checkout migration_validator/checks/routes.py
grep -n "value = MISSING_FROM_TABLE" migration_validator/checks/routes.py
```

Expected: `test_service_scope_does_claim_a_route_is_missing_from_the_table`
**FAILED**. Poslední grep musí ukázat původní řádek s `and not is_device`.

- [ ] **Step 5: Spusť celou sadu a commitni**

Run: `.venv/bin/python -m pytest -q`
Expected: zelená, o jeden test víc než po úloze 2.

```bash
git add tests/models/test_inventory.py tests/checks/test_routes.py
git commit -m "test: opravit dva nediskriminujici testy (AR-28)"
```

---

## Úloha 4: Inventory schema 4 — dvě čestná pole místo `active` (AR‑20)

`ServiceEntry.active` se plní výhradně ze stavu routing-instance
(`mx_parser.py:1245`, `active=instance.active if instance else True`), takže
jméno lže. Se dvěma zdroji deaktivace by navíc nestačilo. Tahle úloha jméno
rozdělí; hodnoty zatím zůstávají takové, jaké jsou —
`interface_active` je všude `True`, protože ho ještě nic nenastavuje. To
přijde v úloze 5.

**Files:**
- Modify: `migration_validator/models/inventory.py:61`, `:92`, `:112`, `:128`
- Modify: `mx_parser.py:156`, `:1245`, `:2183`, `:2218`
- Modify: `evo_parser.py` — táž místa
- Modify: `172.20.20.4.yml`, `172.20.20.5.yml`
- Modify: `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml`
- Modify: `tests/models/test_inventory.py` — `schema_version: 3` → `4`
  ve všech vložených YAML řetězcích
- Test: `tests/models/test_inventory.py`

**Interfaces:**
- Produces:
  - `ServiceEntry.routing_instance_active: bool = True`
  - `ServiceEntry.interface_active: bool = True`
  - `INVENTORY_SCHEMA_VERSION = 4` v `models/inventory.py`, `mx_parser.py`,
    `evo_parser.py`
  - `InterfaceService.routing_instance_active`, `InterfaceService.interface_active`
    v obou parserech
- Consumes: nic z předchozích úloh

- [ ] **Step 1: Napiš padající test**

Do `tests/models/test_inventory.py` přidej:

```python
def test_service_entry_carries_both_deactivation_flags(tmp_path):
    """Dve pole misto jednoho 'active' - zdroje deaktivace jsou dva.

    Zabiji mutanta: ponechani jednoho pole 'active' a jeho namapovani na oba
    stavy. Zaznam nize ma RI zivou a rozhrani deaktivovane, takze jedno
    sdilene pole nemuze mit obe hodnoty spravne.
    """
    path = tmp_path / "inv.yml"
    path.write_text(
        "schema_version: 4\ndevice: r1\n"
        "interfaces:\n"
        "  - interface: ge-0/0/4.0\n"
        "    service_type: IPVPN\n"
        "    routing_instance: L3VPN-CPE14-UNI\n"
        "    routing_instance_active: true\n"
        "    interface_active: false\n",
        encoding="utf-8",
    )

    inventory = load_inventory(str(path))
    entry = inventory.entries[0]

    assert entry.routing_instance_active is True
    assert entry.interface_active is False
    assert not hasattr(entry, "active"), (
        "pole 'active' ma zaniknout - popisovalo stav routing-instance, "
        "ne rozhrani, a se dvema zdroji deaktivace uz nestaci"
    )


def test_inventory_rejects_schema_three():
    """Stara inventory se nemigruje, generuje se znovu."""
    assert INVENTORY_SCHEMA_VERSION == 4
```

Doplň `INVENTORY_SCHEMA_VERSION` do importů v hlavičce testu.

- [ ] **Step 2: Spusť test — musí spadnout**

Run: `.venv/bin/python -m pytest tests/models/test_inventory.py::test_service_entry_carries_both_deactivation_flags -v`
Expected: FAIL — `AttributeError: 'ServiceEntry' object has no attribute 'routing_instance_active'`

- [ ] **Step 3: Uprav model**

V `migration_validator/models/inventory.py`:

```python
    routing_instance: str | None = None
    routing_instance_active: bool = True
    interface_active: bool = True
```

(řádek `active: bool = True` zmizí)

V `from_dict`:

```python
            routing_instance=_as_optional_str(data.get("routing_instance")),
            routing_instance_active=bool(data.get("routing_instance_active", True)),
            interface_active=bool(data.get("interface_active", True)),
```

V `to_dict`:

```python
            "routing_instance": self.routing_instance,
            "routing_instance_active": self.routing_instance_active,
            "interface_active": self.interface_active,
```

A:

```python
INVENTORY_SCHEMA_VERSION = 4
```

- [ ] **Step 4: Spusť test — musí projít**

Run: `.venv/bin/python -m pytest tests/models/test_inventory.py::test_service_entry_carries_both_deactivation_flags -v`
Expected: PASS

- [ ] **Step 5: Uprav oba parsery**

V `mx_parser.py` **i** `evo_parser.py`, na týchž místech:

`InterfaceService` (`:156`) — nahraď `active: bool = True` dvojicí:

```python
    routing_instance_active: bool = True
    interface_active: bool = True
```

`_classify_interface` (`:1245`) — nahraď `active=instance.active if instance else True`:

```python
            routing_instance_active=instance.active if instance else True,
            # Zdroj se doplni v AR-18; do te doby je rozhrani vzdy zive.
            interface_active=True,
```

`INVENTORY_SCHEMA_VERSION` (`:2183`) → `4`.

`clean_service_dict` (`:2218`) — v `ordered_keys` nahraď `"active"` dvojicí:

```python
        "routing_instance",
        "routing_instance_active",
        "interface_active",
```

- [ ] **Step 6: Ověř zámek parserů**

Run: `diff mx_parser.py evo_parser.py | wc -l`
Expected: **146**

- [ ] **Step 7: Přepiš inventory soubory**

Čtyři soubory: `172.20.20.4.yml`, `172.20.20.5.yml`,
`tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml`.

```bash
for f in 172.20.20.4.yml 172.20.20.5.yml tests/fixtures/172.20.20.4.yml tests/fixtures/172.20.20.5.yml; do
  .venv/bin/python - "$f" <<'EOF'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text(encoding="utf-8")
s = s.replace("schema_version: 3\n", "schema_version: 4\n", 1)
# Odsazeni dvema mezerami je tvar, ktery vyrabi write_yaml u polozek seznamu.
new, count = re.subn(
    r"^  active: (true|false)$",
    lambda m: f"  routing_instance_active: {m.group(1)}\n  interface_active: true",
    s,
    flags=re.MULTILINE,
)
print(p, "prepsano zaznamu:", count)
p.write_text(new, encoding="utf-8")
EOF
done
grep -c "interface_active" 172.20.20.4.yml tests/fixtures/172.20.20.4.yml
```

Expected: počet přepsaných záznamů > 0 v každém souboru a `grep -c` vrátí
stejné číslo jako počet služeb.

Kdyby `count` byl 0, odsazení neodpovídá — podívej se do souboru, jak řádek
`active:` doopravdy vypadá, a vzor uprav. **Needituj to ručně po jednom.**

- [ ] **Step 8: Ověř, že se soubory načtou**

Run: `.venv/bin/python -c "from migration_validator.models.inventory import load_inventory; i=load_inventory('172.20.20.4.yml'); print(len(i.entries), i.entries[0].routing_instance_active, i.entries[0].interface_active)"`
Expected: počet služeb a `True True`.

- [ ] **Step 9: Oprav zbytek testů**

Run: `.venv/bin/python -m pytest -q`

Padat budou testy, které mají v sobě `schema_version: 3` nebo `active:`.
Projdi je a přepiš. Nikde neopravuj tak, že bys pole vrátil zpátky.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat(inventory): schema 4 - routing_instance_active a interface_active (AR-20)"
```

- [ ] **Step 11: Ověř mutantem, že test z kroku 1 diskriminuje**

Mutant se pouští **až po commitu**, aby `git checkout` vracel na hotový stav
a nesmazal změny z kroku 3.

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/models/inventory.py")
s = p.read_text()
old = 'interface_active=bool(data.get("interface_active", True)),'
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, 'interface_active=bool(data.get("routing_instance_active", True)),'))
EOF
grep -n "interface_active=bool" migration_validator/models/inventory.py
.venv/bin/python -m pytest tests/models/test_inventory.py::test_service_entry_carries_both_deactivation_flags -q
git checkout migration_validator/models/inventory.py
grep -n "interface_active=bool" migration_validator/models/inventory.py
```

Expected: test **FAILED** s mutantem (obě pole by četla týž klíč), po
`git checkout` zase původní řádek.

---

## Úloha 5: Parser čte `inactive` na `interface` a `unit` (AR‑18)

**Files:**
- Modify: `mx_parser.py:119` (`InterfaceConfig`), `:1010` (`_parse_interfaces`),
  `:1076` (`_build_interface_config`), `:1245` (`_classify_interface`)
- Modify: `evo_parser.py` — táž místa
- Test: `tests/parsers/test_inactive.py` (**nový**)

**Interfaces:**
- Consumes: `InterfaceService.interface_active` z úlohy 4
- Produces: `InterfaceConfig.active: bool = True` v obou parserech

- [ ] **Step 1: Založ testovací soubor s padajícím testem**

Create: `tests/parsers/test_inactive.py`

```python
"""Offline testy deaktivované konfigurace - laborka není potřeba.

Oba parsery se mění v zámku, takže každý test běží proti oběma.

Tvary XML odpovídají skutečné konfiguraci laborky z 2026-07-29
(runs/bfd-static-2026-07-29/cfg/): na 172.20.20.4 jsou deaktivovaná rozhraní
ge-0/0/2, ge-0/0/4 a ge-0/0/5, na 172.20.20.5 pak et-0/0/10.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, filename: str):
    """Parsery jsou skripty v kořeni repozitáře, ne balíček - načteme je podle cesty."""
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


evo = _load("evo_parser_inactive_test", "evo_parser.py")
mx = _load("mx_parser_inactive_test", "mx_parser.py")

PARSERS = (
    pytest.param(evo, evo.JunosEvoAcxServiceParser, id="evo"),
    pytest.param(mx, mx.JunosServiceParser, id="mx"),
)

DEACTIVATED_INTERFACE = """
<configuration>
  <interfaces>
    <interface inactive="inactive">
      <name>ge-0/0/4</name>
      <description>L3VPN-CPE14-UNI</description>
      <unit>
        <name>0</name>
        <description>L3VPN-CPE14-UNI</description>
        <family>
          <inet>
            <address><name>198.11.14.1/30</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE14-UNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>ge-0/0/4.0</name></interface>
    </instance>
  </routing-instances>
</configuration>
"""


def _services(module, parser_class, xml_text):
    return parser_class(etree.fromstring(xml_text.encode())).parse()


def _by_name(services, name):
    found = [service for service in services if service.interface == name]
    assert found, f"služba {name} v inventory není - deaktivace ji nesmí vypustit"
    return found[0]


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_interface_stays_in_inventory(module, parser_class):
    """Deaktivované rozhraní se z inventory nevypouští - jen se označí.

    Zabíjí mutanta: `continue` na deaktivovaném rozhraní v _parse_interfaces.
    Dočasně deaktivovaná služba se pořád musí zmigrovat, takže vypustit ji
    z inventory je chyba, ne oprava.
    """
    services = _services(module, parser_class, DEACTIVATED_INTERFACE)

    assert _by_name(services, "ge-0/0/4.0")


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_interface_is_flagged(module, parser_class):
    """interface_active je False u fyzického rozhraní i u jeho jednotky.

    Zabíjí mutanta: `interface_active=True` natvrdo v _classify_interface.
    Jednotka atribut inactive sama nemá - dědí ho po fyzickém rodiči.
    """
    services = _services(module, parser_class, DEACTIVATED_INTERFACE)

    assert _by_name(services, "ge-0/0/4").interface_active is False
    assert _by_name(services, "ge-0/0/4.0").interface_active is False


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_interface_does_not_touch_routing_instance_flag(module, parser_class):
    """Zdroje deaktivace se nemíchají - RI je tu živá.

    Zabíjí mutanta: naplnění obou polí z téhož zdroje.
    """
    services = _services(module, parser_class, DEACTIVATED_INTERFACE)

    assert _by_name(services, "ge-0/0/4.0").routing_instance_active is True
```

- [ ] **Step 2: Spusť — musí spadnout**

Run: `.venv/bin/python -m pytest tests/parsers/test_inactive.py -v`
Expected: `test_deactivated_interface_is_flagged` FAIL pro `evo` i `mx`
(`assert True is False`). `test_deactivated_interface_stays_in_inventory`
a `..._does_not_touch_routing_instance_flag` už teď projdou — to je v pořádku,
zafixovávají chování, které se nesmí rozbít.

- [ ] **Step 3: Přidej pole do `InterfaceConfig`**

V obou parserech, do `InterfaceConfig` (`:119`):

```python
    active: bool = True
```

Jméno `active` je tu pravdivé — `InterfaceConfig` je konfigurace rozhraní,
takže popisuje stav rozhraní. Matoucí bylo jen na `InterfaceService`.

- [ ] **Step 4: Předej stav z `_parse_interfaces`**

V `_parse_interfaces` (`:1010`) v obou parserech. Fyzické rozhraní:

```python
            physical_inactive = self._is_inactive(interface_node)

            # Fyzické rozhraní přidáme vždy.
            results.append(
                self._build_interface_config(
                    physical_name=physical_name,
                    logical_name=physical_name,
                    physical_description=physical_description,
                    physical_encapsulation=physical_encapsulation,
                    node=interface_node,
                    active=not physical_inactive,
                )
            )
```

Jednotka — deaktivace se dědí po fyzickém rodiči:

```python
                results.append(
                    self._build_interface_config(
                        physical_name=physical_name,
                        logical_name=logical_name,
                        physical_description=physical_description,
                        physical_encapsulation=physical_encapsulation,
                        node=unit_node,
                        # Jednotka pod deaktivovaným rodičem je deaktivovaná
                        # taky, i když sama atribut nemá - Junos to tak i
                        # vyhodnocuje.
                        active=not (physical_inactive or self._is_inactive(unit_node)),
                    )
                )
```

- [ ] **Step 5: Přidej parametr do `_build_interface_config`**

V obou parserech doplň do signatury (`:1076`) `active: bool = True` a do
vytvářeného `InterfaceConfig` `active=active`.

- [ ] **Step 6: Donés to do `InterfaceService`**

V `_classify_interface` (`:1245`) v obou parserech nahraď řádek
`interface_active=True` z úlohy 4:

```python
            interface_active=interface.active,
```

Parametr se jmenuje `interface`, ne `config` — signatura je
`_classify_interface(self, interface: InterfaceConfig)` (`mx_parser.py:1194`,
ověřeno 2026‑07‑30).

- [ ] **Step 7: Spusť test — musí projít**

Run: `.venv/bin/python -m pytest tests/parsers/test_inactive.py -v`
Expected: 6 PASSED (3 testy × 2 parsery)

- [ ] **Step 8: Ověř zámek parserů**

Run: `diff mx_parser.py evo_parser.py | wc -l`
Expected: **146**

- [ ] **Step 9: Ověř mutanta zvlášť pro každý parser**

MX:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("mx_parser.py")
s = p.read_text(encoding="utf-8")
old = "interface_active=interface.active,"
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, "interface_active=True,"), encoding="utf-8")
EOF
grep -n "interface_active=" mx_parser.py
.venv/bin/python -m pytest tests/parsers/test_inactive.py -q
git checkout mx_parser.py
```

Expected: FAIL **jen** u `[mx]` parametrů, `[evo]` zůstane zelený. To je
důkaz, že parametrizace opravdu odděluje parsery.

EVO — totéž se souborem `evo_parser.py`. Expected: FAIL jen u `[evo]`.

Po obou: `grep -n "interface_active=" mx_parser.py evo_parser.py` musí
ukázat `interface.active` v obou.

- [ ] **Step 10: Spusť celou sadu a commitni**

Run: `.venv/bin/python -m pytest -q`
Expected: zelená, o 6 testů víc.

```bash
git add mx_parser.py evo_parser.py tests/parsers/test_inactive.py
git commit -m "feat(parser): cist inactive na interface a unit (AR-18)"
```

---

## Úloha 6: Příznak `inactive` se dědí z kontejnerů (AR‑19)

Dnes si úroveň kontejneru a úroveň položky odporují: deaktivovat jednu VRF
statiky vypustí, deaktivovat **všechny** VRF (`<routing-instances inactive>`)
neudělá nic. Totéž u `<interfaces>`, `<protocols>` a `<bgp>`.

**Files:**
- Modify: `mx_parser.py:404` (`_is_inactive`)
- Modify: `evo_parser.py` — totéž
- Test: `tests/parsers/test_inactive.py`

**Interfaces:**
- Consumes: `InterfaceConfig.active` z úlohy 5
- Produces: `_is_inactive` nově zohledňuje předky

- [ ] **Step 1: Napiš padající testy**

Do `tests/parsers/test_inactive.py` přidej konstanty a testy:

```python
DEACTIVATED_CONTAINERS = """
<configuration>
  <interfaces inactive="inactive">
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>113</name>
        <description>L3VPN-CPE13-NNI</description>
        <family>
          <inet>
            <address><name>198.11.13.1/30</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances inactive="inactive">
    <instance>
      <name>L3VPN-TEST</name>
      <instance-type>vrf</instance-type>
      <interface><name>ge-0/0/2.113</name></interface>
      <routing-options>
        <static>
          <route>
            <name>10.9.9.0/24</name>
            <next-hop>198.11.13.2</next-hop>
          </route>
        </static>
      </routing-options>
      <protocols>
        <bgp>
          <group>
            <name>CPE13</name>
            <neighbor><name>198.11.13.2</name></neighbor>
          </group>
        </bgp>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_interfaces_container_flags_every_interface(module, parser_class):
    """<interfaces inactive> označí všechna rozhraní pod sebou.

    Zabíjí mutanta: _is_inactive, které se dívá jen na uzel a ne na předky.
    Bez dědění vrátí interface_active=True, protože samo <interface>
    atribut nemá.
    """
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    assert _by_name(services, "ge-0/0/2.113").interface_active is False


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_routing_instances_container_flags_the_service(module, parser_class):
    """<routing-instances inactive> označí služby všech VRF pod sebou."""
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    assert _by_name(services, "ge-0/0/2.113").routing_instance_active is False


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_routing_instances_container_drops_static_routes(module, parser_class):
    """Statiky pod deaktivovaným kontejnerem se vypustí ze záměru.

    Roadmapa to ověřila na `<routing-instances inactive="inactive">`: statika
    ('L3VPN-TEST.inet.0', '10.9.9.0/24') se dnes vrací jako živá. Deaktivovaná
    VRF žádnou routu do tabulky nedá, takže záměr z ní vzniknout nesmí -
    jinak check hlásí FAIL za routu, kterou nikdo nechce.

    Zabíjí mutanta: dědění zavedené jen pro rozhraní a ne pro statiky.
    """
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    routes = [route for service in services for route in service.static_route]

    assert routes == [], f"deaktivovaný kontejner vyrobil záměr: {routes}"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_bgp_container_drops_neighbors_and_bfd(module, parser_class):
    """<protocols>/<bgp> pod deaktivovanou VRF nedá souseda ani BFD záměr.

    Zabíjí mutanta: dědění zavedené jen pro statiky a ne pro BGP.
    """
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    peers = [peer for service in services for peer in service.bgp_neighbor]
    bfd = [intent for service in services for intent in service.bfd]

    assert peers == [], f"deaktivovaný kontejner vyrobil BGP záměr: {peers}"
    assert bfd == [], f"deaktivovaný kontejner vyrobil BFD záměr: {bfd}"
```

- [ ] **Step 2: Spusť — musí spadnout**

Run: `.venv/bin/python -m pytest tests/parsers/test_inactive.py -v -k container`
Expected: všechny čtyři nové testy FAIL pro `evo` i `mx`.

- [ ] **Step 3: Rozšiř `_is_inactive` o předky**

V obou parserech, `_is_inactive` (`:404`). Dnešní tělo zůstane a stane se
z něj `_node_is_inactive`; nová `_is_inactive` projde uzel a jeho předky:

```python
    def _is_inactive(
        self,
        node: etree._Element,
    ) -> bool:
        """Deaktivace se dědí z kontejneru dolů.

        Junos označí atributem `inactive` jen ten uzel, na kterém se příkaz
        `deactivate` vykonal. `deactivate routing-instances` proto označí
        kontejner a jednotlivé `instance` pod ním zůstanou bez atributu —
        ale neplatí ani jedna z nich.

        Bez chození po předcích si obě úrovně odporují: deaktivovat jednu VRF
        statiky vypustí, deaktivovat všechny je nechá naživu. To je chyba za
        jakékoli politiky.
        """
        current: etree._Element | None = node

        while current is not None:
            if self._node_is_inactive(current):
                return True
            current = current.getparent()

        return False

    def _node_is_inactive(
        self,
        node: etree._Element,
    ) -> bool:
```

…a za tuhle novou hlavičku dej **beze změny** celé dosavadní tělo
`_is_inactive` (kontrola atributu `inactive`, `active="false"` a namespaced
`active`).

- [ ] **Step 4: Spusť testy — musí projít**

Run: `.venv/bin/python -m pytest tests/parsers/test_inactive.py -v`
Expected: všech 14 PASSED (7 testů × 2 parsery).

- [ ] **Step 5: Spusť celou sadu**

Run: `.venv/bin/python -m pytest -q`

Pozor na regrese: `_is_inactive` se teď volá i na uzly hluboko v hierarchii
(`route`, `neighbor`, `rib`), takže deaktivovaný předek nově zabírá i tam.
To je záměr. Kdyby něco spadlo, přečti si, **co** ten test tvrdí — jestli
tvrdí, že deaktivovaný předek se má ignorovat, je to test proti záměru
a přepisuje se on, ne kód.

- [ ] **Step 6: Ověř zámek parserů**

Run: `diff mx_parser.py evo_parser.py | wc -l`
Expected: **146**

- [ ] **Step 7: Ověř mutanta zvlášť pro každý parser**

MX:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("mx_parser.py")
s = p.read_text(encoding="utf-8")
old = "            current = current.getparent()"
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, "            current = None"), encoding="utf-8")
EOF
grep -n "current = None" mx_parser.py
.venv/bin/python -m pytest tests/parsers/test_inactive.py -q
git checkout mx_parser.py
```

Expected: FAIL jen u `[mx]` parametrů.

EVO — totéž se souborem `evo_parser.py`. Expected: FAIL jen u `[evo]`.

Po obou: `grep -n "current.getparent()" mx_parser.py evo_parser.py` musí
najít řádek v obou souborech.

- [ ] **Step 8: Commit**

```bash
git add mx_parser.py evo_parser.py tests/parsers/test_inactive.py
git commit -m "feat(parser): dedit inactive z kontejneru dolu (AR-19)"
```

---

## Úloha 7: `Scope` nese příznaky, snapshot schema 4 (AR‑21)

Příznaky musí do snímku, jinak baseline svůj stav nenese a úloha 9 nemá co
porovnávat.

**Files:**
- Modify: `migration_validator/models/scope.py:96` a `to_dict`/`from_dict`
- Modify: `migration_validator/models/snapshot.py:17`
- Modify: `migration_validator/scoping/builder.py:64-86`
- Test: `tests/models/test_scope.py`, `tests/scoping/test_builder.py`

**Interfaces:**
- Consumes: `ServiceEntry.routing_instance_active`, `ServiceEntry.interface_active`
- Produces:
  - `Scope.routing_instance_active: bool = True`
  - `Scope.interface_active: bool = True`
  - `Scope.is_deactivated -> bool`
  - `Scope.deactivation_reason -> str | None` (`"RI deactivated"`,
    `"interface deactivated"`, `"RI + interface deactivated"`, jinak `None`)
  - `SCHEMA_VERSION = 4` ve `models/snapshot.py`

- [ ] **Step 1: Napiš padající testy**

Do `tests/models/test_scope.py`:

```python
def test_scope_reports_why_it_is_deactivated():
    """Duvod jmenuje oba zdroje, aby operator vedel, co odaktivovat.

    Zabiji mutanta: is_deactivated postavene jen na routing_instance_active.
    """
    live = Scope(id="s", kind="service", key=None, selectors=Selectors())
    off_ri = Scope(
        id="s", kind="service", key=None, selectors=Selectors(),
        routing_instance_active=False,
    )
    off_if = Scope(
        id="s", kind="service", key=None, selectors=Selectors(),
        interface_active=False,
    )
    off_both = Scope(
        id="s", kind="service", key=None, selectors=Selectors(),
        routing_instance_active=False, interface_active=False,
    )

    assert live.is_deactivated is False
    assert live.deactivation_reason is None
    assert off_ri.deactivation_reason == "RI deactivated"
    assert off_if.deactivation_reason == "interface deactivated"
    assert off_both.deactivation_reason == "RI + interface deactivated"


def test_scope_round_trips_deactivation_flags():
    """Bez serializace by baseline svuj stav nenesla a AR-23 by nemel co porovnat.

    Zabiji mutanta: vynechani obou klicu z to_dict.
    """
    scope = Scope(
        id="svc:CPE14:IPVPN", kind="service", key=None, selectors=Selectors(),
        routing_instance_active=True, interface_active=False,
    )

    restored = Scope.from_dict(scope.to_dict())

    assert restored.routing_instance_active is True
    assert restored.interface_active is False
```

Do `tests/scoping/test_builder.py`:

```python
def test_scope_inherits_deactivation_from_the_inventory_entry():
    """Prevod je 1:1 - build_scopes dela jeden scope na jeden zaznam.

    Zabiji mutanta: build_scopes, ktere priznaky neopise a necha default True.
    """
    inventory = Inventory(
        device="r1",
        entries=[
            _entry(
                interface="ge-0/0/4.0",
                description="L3VPN-CPE14-UNI",
                service_type="IPVPN",
                routing_instance="L3VPN-CPE14-UNI",
                routing_instance_active=True,
                interface_active=False,
            )
        ],
    )

    scopes = build_scopes(inventory)

    assert len(scopes) == 1
    assert scopes[0].routing_instance_active is True
    assert scopes[0].interface_active is False
```

`_entry(**kwargs)` je existující helper v tom souboru — obal nad
`ServiceEntry.from_dict`. Používej ho, ne přímý konstruktor.

- [ ] **Step 2: Spusť — musí spadnout**

Run: `.venv/bin/python -m pytest tests/models/test_scope.py tests/scoping/test_builder.py -v -k deactiv`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'routing_instance_active'`

- [ ] **Step 3: Rozšiř `Scope`**

V `migration_validator/models/scope.py`, do dataclass `Scope`:

```python
@dataclass
class Scope:
    id: str
    kind: str  # service | device
    key: ScopeKey | None
    selectors: Selectors
    # Deaktivace neni selektor, je to vlastnost sluzby - proto tady, ne
    # v Selectors. Sluzba se pri deaktivaci z inventory nevypousti (docasne
    # deaktivovana sluzba se porad musi zmigrovat), takze priznak je jediny
    # zpusob, jak se to da poznat.
    routing_instance_active: bool = True
    interface_active: bool = True

    @property
    def is_deactivated(self) -> bool:
        return not (self.routing_instance_active and self.interface_active)

    @property
    def deactivation_reason(self) -> str | None:
        """Kratky duvod do sloupce hodnot. None, kdyz je sluzba ziva."""
        reasons = []
        if not self.routing_instance_active:
            reasons.append("RI")
        if not self.interface_active:
            reasons.append("interface")
        return f"{' + '.join(reasons)} deactivated" if reasons else None
```

Do `Scope.to_dict()` přidej oba klíče, do `Scope.from_dict()` je přečti
s defaultem `True`. Přesný tvar obou metod si přečti v souboru — drž se
stylu, který tam je.

- [ ] **Step 4: Opiš příznaky v `build_scopes`**

`migration_validator/scoping/builder.py`, do konstrukce `Scope(...)` za
`selectors=Selectors(...)`:

```python
                routing_instance_active=entry.routing_instance_active,
                interface_active=entry.interface_active,
```

- [ ] **Step 5: Zvedni schema snímku**

`migration_validator/models/snapshot.py:17`:

```python
SCHEMA_VERSION = 4
```

- [ ] **Step 6: Spusť testy**

Run: `.venv/bin/python -m pytest -q`

Padnou testy se `schema_version: 3` ve snímcích. Přepiš je na 4. Kdyby na
disku byl uložený snímek se schématem 3, nemigruje se — vyrobí se znovu.

- [ ] **Step 7: Ověř mutanty**

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/scoping/builder.py")
s = p.read_text()
old = "                interface_active=entry.interface_active,\n"
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, ""))
EOF
grep -n "interface_active" migration_validator/scoping/builder.py
.venv/bin/python -m pytest tests/scoping/test_builder.py -q
git checkout migration_validator/scoping/builder.py
grep -n "interface_active" migration_validator/scoping/builder.py
```

Expected: `test_scope_inherits_deactivation_from_the_inventory_entry` FAILED,
po checkoutu je řádek zpátky.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(scope): Scope nese priznaky deaktivace, snapshot schema 4 (AR-21)"
```

---

## Úloha 8: Deaktivovaná služba SKIPuje na všech checcích (AR‑22)

**Files:**
- Modify: `migration_validator/checks/base.py:113-141` (`run_check`)
- Test: `tests/checks/test_base.py`

**Interfaces:**
- Consumes: `Scope.is_deactivated`, `Scope.deactivation_reason` z úlohy 7
- Produces: `DEACTIVATION_CHECK_ID = "deactivation_state"` v `checks/base.py`

- [ ] **Step 1: Napiš padající test**

Do `tests/checks/test_base.py`:

```python
def test_deactivated_scope_skips_every_check():
    """Deaktivovana sluzba nevyrabi FAILy - jen rekne, ze je deaktivovana.

    Zabiji mutanta: vynechani nove zkratky z run_check. Bez ni by sluzba na
    deaktivovanem rozhrani hlasila FAIL na vsem (ping down, BGP down) bez
    jakehokoli vysvetleni - presne to, co v laborce dnes dela ge-0/0/4.
    """
    scope = Scope(
        id="svc:CPE14:IPVPN",
        kind="service",
        key=ScopeKey(description="CPE14", service_type="IPVPN"),
        selectors=Selectors(interfaces=["ge-0/0/4.0"]),
        interface_active=False,
    )
    ctx = CheckContext(
        scope=scope,
        subject={"interfaces": {}},
        baseline=None,
        config=default_config(),
    )

    results = run_check(InterfaceStateCheck(), ctx)

    assert results
    assert all(result.status is Status.SKIP for result in results)
    assert results[0].value == "interface deactivated"


def test_live_scope_still_runs_its_checks():
    """Protejsek - bez nej by slo zkratku napsat tak, ze SKIPuje vzdycky.

    Zabiji mutanta: podminka zmenena na `if True`.
    """
    scope = Scope(
        id="svc:CPE13:IPVPN",
        kind="service",
        key=ScopeKey("CPE13", "IPVPN", None),
        selectors=Selectors(interfaces=["ge-0/0/2.113"]),
    )
    ctx = CheckContext(
        scope=scope,
        subject={
            "interfaces": {
                "ge-0/0/2.113": {"admin_status": "up", "oper_status": "up"}
            }
        },
        baseline=None,
        config=default_config(),
    )

    results = run_check(InterfaceStateCheck(), ctx)

    assert results
    assert all(result.status is Status.PASS for result in results)
```

`InterfaceStateCheck` vydá dva výsledky (admin a oper status) — viz
`test_interface_state_up_passes` v `tests/checks/test_ifaces.py`. Do hlavičky
`tests/checks/test_base.py` doplň, co chybí: `InterfaceStateCheck`,
`run_check`, `Status`, `Scope`, `ScopeKey`, `Selectors`, `default_config`.

- [ ] **Step 2: Spusť — musí spadnout**

Run: `.venv/bin/python -m pytest tests/checks/test_base.py -v -k deactivated`
Expected: `test_deactivated_scope_skips_every_check` FAIL — check se spustí
a vrátí něco jiného než SKIP.

- [ ] **Step 3: Přidej zkratku do `run_check`**

V `migration_validator/checks/base.py`, nad `run_check` přidej konstantu:

```python
# Check, ktery deaktivaci hlasi, se sam preskocit nesmi - jinak by nebylo co
# porovnat a sluzba by v reportu zmizela do SKIPu bez duvodu.
DEACTIVATION_CHECK_ID = "deactivation_state"
```

Uvnitř `run_check` vlož novou zkratku **za** gate `Mode.COMPARE` a **před**
smyčku přes `check.requires`:

```python
    if check.mode is Mode.COMPARE and not ctx.has_baseline:
        return _skip(
            check, severity, "porovnavaci check bez baseline snapshotu", "bez baseline"
        )

    # Poradi je soucast pozadavku: zkratka jde az za requires_inventory, takze
    # v device scope preskoci uz ten - device scope inventory nema a nema tedy
    # ani z ceho priznak vzit.
    if check.id != DEACTIVATION_CHECK_ID and ctx.scope.is_deactivated:
        reason = ctx.scope.deactivation_reason
        return _skip(
            check,
            severity,
            f"sluzba je v konfiguraci deaktivovana ({reason})",
            reason,
        )

    for area in check.requires:
```

- [ ] **Step 4: Spusť testy — musí projít**

Run: `.venv/bin/python -m pytest tests/checks/test_base.py -v -k deactivated`
Expected: PASS

- [ ] **Step 5: Spusť celou sadu**

Run: `.venv/bin/python -m pytest -q`
Expected: zelená. Dnešní fixture mají všude `interface_active: true`
a `routing_instance_active: true`, takže se zkratka zatím nikde nespustí.

- [ ] **Step 6: Ověř mutanty**

Mutant A — zkratka vynechána:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/base.py")
s = p.read_text()
old = "    if check.id != DEACTIVATION_CHECK_ID and ctx.scope.is_deactivated:"
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, "    if False:"))
EOF
grep -n "if False:" migration_validator/checks/base.py
.venv/bin/python -m pytest tests/checks/test_base.py -q
git checkout migration_validator/checks/base.py
```

Expected: `test_deactivated_scope_skips_every_check` FAILED.

Mutant B — zkratka vždy:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/base.py")
s = p.read_text()
old = "    if check.id != DEACTIVATION_CHECK_ID and ctx.scope.is_deactivated:"
assert s.count(old) == 1
p.write_text(s.replace(old, "    if check.id != DEACTIVATION_CHECK_ID:"))
EOF
grep -n "if check.id != DEACTIVATION_CHECK_ID" migration_validator/checks/base.py
.venv/bin/python -m pytest tests/checks/test_base.py -q
git checkout migration_validator/checks/base.py
grep -n "is_deactivated" migration_validator/checks/base.py
```

Expected: `test_live_scope_still_runs_its_checks` FAILED, po checkoutu je
podmínka zase celá.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(checks): deaktivovana sluzba SKIPuje na vsech checcich (AR-22)"
```

---

## Úloha 9: Check `deactivation_state` (AR‑23)

`CheckContext` dnes nese baseline **data**, ne baseline **scope** — a příznak
deaktivace leží na scopu. Bez něj se matice porovnat nedá, takže úloha nejdřív
protáhne `baseline_scope` z enginu do kontextu.

**Files:**
- Create: `migration_validator/checks/deactivation.py`
- Modify: `migration_validator/checks/base.py:35-47` (`CheckContext`)
- Modify: `migration_validator/checks/all.py:9`
- Modify: `migration_validator/engine.py:113-134` (`_run_scope`)
- Test: `tests/checks/test_deactivation.py` (**nový**)

**Interfaces:**
- Consumes: `Scope.is_deactivated`, `Scope.deactivation_reason`,
  `DEACTIVATION_CHECK_ID` z úloh 7 a 8
- Produces:
  - `CheckContext.baseline_scope: Scope | None = None`
  - `DeactivationStateCheck` s `id = "deactivation_state"`

- [ ] **Step 1: Napiš padající testy**

Create: `tests/checks/test_deactivation.py`

```python
"""Testy checku deaktivace.

Matice je cela pointa: sluzba deaktivovana na obou stranach je PASS (stav se
nezmenil), ne SKIP. Sluzba, ktera na starem zarizeni bezela a na novem je
deaktivovana, je FAIL - migrace nedokoncena.
"""

from __future__ import annotations

import pytest

from migration_validator.checks.base import CheckContext
from migration_validator.checks.deactivation import DeactivationStateCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _scope(deactivated: bool) -> Scope:
    return Scope(
        id="svc:CPE14:IPVPN",
        kind="service",
        key=ScopeKey(description="CPE14", service_type="IPVPN"),
        selectors=Selectors(interfaces=["ge-0/0/4.0"]),
        interface_active=not deactivated,
    )


def _ctx(subject_off: bool, baseline_off: bool | None) -> CheckContext:
    return CheckContext(
        scope=_scope(subject_off),
        subject={},
        baseline={} if baseline_off is not None else None,
        baseline_scope=_scope(baseline_off) if baseline_off is not None else None,
        config=default_config(),
    )


@pytest.mark.parametrize(
    "subject_off,baseline_off,expected",
    [
        (True, True, Outcome.OK),
        (True, False, Outcome.BROKEN),
        (False, True, Outcome.DEGRADED),
        (True, None, Outcome.SKIP),
    ],
)
def test_matrix(subject_off, baseline_off, expected):
    """Kazda bunka matice zvlast - aby selhani ukazalo, ktera se rozpojila.

    Zabiji mutanta: kterakoli zamena dvojice vetvi. Radek (True, False) je
    ten, ktery odlisuje "deaktivovano i drive" od "deaktivovano az ted" -
    bez nej by slo obe hlasit jako PASS.
    """
    findings = DeactivationStateCheck().run(_ctx(subject_off, baseline_off))

    assert len(findings) == 1
    assert findings[0].outcome is expected


@pytest.mark.parametrize("baseline_off", [False, None])
def test_healthy_service_gets_no_row(baseline_off):
    """Zdrava sluzba nema v bloku pribyt radek, ktery nic nerika.

    Zabiji mutanta: navrat Outcome.OK misto prazdneho seznamu. S nim by
    kazdy zdravy blok narostl o radek "sluzba je aktivni".
    """
    findings = DeactivationStateCheck().run(_ctx(False, baseline_off))

    assert findings == []


def test_reason_names_both_sources():
    """Duvod jmenuje, co odaktivovat - RI, rozhrani, nebo oboji."""
    scope = Scope(
        id="svc:CPE14:IPVPN",
        kind="service",
        key=None,
        selectors=Selectors(),
        routing_instance_active=False,
        interface_active=False,
    )
    ctx = CheckContext(
        scope=scope, subject={}, baseline=None, baseline_scope=None,
        config=default_config(),
    )

    findings = DeactivationStateCheck().run(ctx)

    assert findings[0].value == "RI + interface deactivated"
```

- [ ] **Step 2: Spusť — musí spadnout**

Run: `.venv/bin/python -m pytest tests/checks/test_deactivation.py -v`
Expected: FAIL — `ModuleNotFoundError: migration_validator.checks.deactivation`

- [ ] **Step 3: Přidej `baseline_scope` do `CheckContext`**

`migration_validator/checks/base.py`:

```python
@dataclass
class CheckContext:
    scope: Scope
    subject: dict[str, Any]
    baseline: dict[str, Any] | None
    config: CheckConfig
    failed_collectors: dict[str, str] = field(default_factory=dict)
    # Priznak deaktivace lezi na scopu, ne ve faktech, takze bez baseline
    # scopu nejde porovnat "deaktivovano i drive" proti "deaktivovano az ted".
    baseline_scope: Scope | None = None
```

Default `None` je nutný — desítky existujících testů `CheckContext` staví bez
něj.

- [ ] **Step 4: Předej ho z enginu**

`migration_validator/engine.py`, v `_run_scope` do konstrukce `CheckContext`:

```python
        failed_collectors=subject.capture.failed_collectors(),
        baseline_scope=baseline_scope,
```

- [ ] **Step 5: Napiš check**

Create: `migration_validator/checks/deactivation.py`

```python
"""Check deaktivace sluzby.

Deaktivovana sluzba z inventory nemizi - docasne deaktivovana sluzba se porad
musi zmigrovat, takze vypustit ji je chyba, ne oprava. Tenhle check je misto,
kde se to rozhodnuti promitne do vysledku: ostatni checky nad deaktivovanou
sluzbou SKIPnou (checks/base.py), tenhle jediny ne, a rekne, co se zmenilo
proti baseline.

Zdrava sluzba tu radek nedostane. R-1 rika, ze co se nekontroluje, se
v bloku neobjevi; radek "sluzba je aktivni" by u kazdeho zdraveho bloku
pribyl a nerekl nic.
"""

from __future__ import annotations

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

ACTIVE = "aktivni"


@register
class DeactivationStateCheck(Check):
    id = "deactivation_state"
    title = "Stav deaktivace sluzby"
    label = "Deaktivace"
    mode = Mode.BOTH
    requires_inventory = True
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        subject_off = ctx.scope.is_deactivated
        baseline_off = (
            ctx.baseline_scope.is_deactivated
            if ctx.baseline_scope is not None
            else None
        )
        reason = ctx.scope.deactivation_reason

        if not subject_off and baseline_off is not True:
            return []

        if subject_off and baseline_off is None:
            return [
                Finding(
                    Outcome.SKIP,
                    f"sluzba je v konfiguraci deaktivovana ({reason}), "
                    "baseline neni k porovnani",
                    label=self.label,
                    value=reason,
                )
            ]

        if subject_off and baseline_off:
            return [
                Finding(
                    Outcome.OK,
                    f"sluzba je deaktivovana ({reason}) stejne jako v baseline",
                    label=self.label,
                    value=reason,
                    baseline_value=ctx.baseline_scope.deactivation_reason,
                )
            ]

        if subject_off:
            return [
                Finding(
                    Outcome.BROKEN,
                    f"sluzba v baseline bezela, ted je deaktivovana ({reason}) "
                    "- migrace nedokoncena",
                    label=self.label,
                    value=reason,
                    baseline_value=ACTIVE,
                )
            ]

        return [
            Finding(
                Outcome.DEGRADED,
                "sluzba byla v baseline deaktivovana "
                f"({ctx.baseline_scope.deactivation_reason}), ted je aktivni",
                label=self.label,
                value=ACTIVE,
                baseline_value=ctx.baseline_scope.deactivation_reason,
            )
        ]
```

- [ ] **Step 6: Zaregistruj modul**

`migration_validator/checks/all.py`:

```python
from migration_validator.checks import (  # noqa: F401
    bfd,
    bgp,
    deactivation,
    evpn,
    ifaces,
    reachability,
    routes,
)
```

- [ ] **Step 7: Spusť testy — musí projít**

Run: `.venv/bin/python -m pytest tests/checks/test_deactivation.py -v`
Expected: 7 PASSED

- [ ] **Step 8: Ověř matici end-to-end přes engine**

Do `tests/test_engine.py` přidej test, který ověří, že se z matice opravdu
stane status služby — tedy že `run_check` a `_run_scope` spolu hrají:

Soubor už má helpery `_scope`, `_snapshot`, `_old` a `_new`. Přidej:

```python
def _deactivated(snapshot):
    """Oznaci vsechny service scopy snimku za deaktivovane."""
    for scope in snapshot.scopes:
        scope.interface_active = False
    return snapshot


def test_service_deactivated_on_both_sides_is_pass():
    """Deaktivovano na obou stranach = PASS, ne SKIP - stav se nezmenil.

    Ostatni checky SKIPnou (AR-22), projde jen OK z deactivation_state,
    a Status.worst z jedine ne-SKIP hodnoty da PASS.

    Zabiji mutanta: vyjmuti deactivation_state ze zkratky v run_check. Pak by
    SKIPl i on, `reported` by byl prazdny a sluzba by spadla do SKIP - tedy
    "nic se nezmerilo" misto "je to v poradku".
    """
    result = api.evaluate(
        _deactivated(_new()), baseline=_deactivated(_old()), now=NOW
    )

    assert result.scopes
    assert result.scopes[0].status is Status.PASS


def test_service_deactivated_only_in_subject_is_fail():
    """Bezela na starem zarizeni, na novem je deaktivovana - migrace nedokoncena.

    Zabiji mutanta: zamena BROKEN za OK v teto vetvi deactivation.py. Bez
    tohoto testu by "deaktivovano az ted" bylo k nerozeznani od
    "deaktivovano i drive".
    """
    result = api.evaluate(_deactivated(_new()), baseline=_old(), now=NOW)

    assert result.scopes
    assert result.scopes[0].status is Status.FAIL


def test_service_reactivated_after_migration_is_warn():
    """V baseline deaktivovana, ted nahozena - zmena proti baseline."""
    result = api.evaluate(_new(), baseline=_deactivated(_old()), now=NOW)

    assert result.scopes
    assert result.scopes[0].status is Status.WARN
```

Pozor u posledního testu: subjekt je živý, takže SKIPu z AR‑22 nepodléhá
a běží mu i ostatní checky. `_new()` je postavené tak, aby byly zelené —
`Status.worst` proto vyjde z DEGRADED z `deactivation_state`. Kdyby test
vrátil FAIL, znamená to, že `_new()` má vlastní problém; podívej se, který
check ho hlásí, dřív než sáhneš na `deactivation.py`.

- [ ] **Step 9: Ověř, že se důvod dostane až do textového reportu**

Celá vlna stojí na tom, že operátor místo holého FAILu uvidí, **proč** se
služba nekontroluje. Úloha 8 asertuje `CheckResult.value` — to je datová
struktura, ne vykreslený výstup. Renderer se dosud ověřuje jen v úloze 12
kroku 8, který potřebuje živou laborku. Tenhle test to uzavře offline.

Do `tests/reporting/test_text_report.py`:

```python
def test_deactivated_service_shows_the_reason_in_the_report():
    """Duvod deaktivace musi byt videt, ne jen ulozeny v CheckResult.

    Bez toho by operator videl SKIP bez vysvetleni - tedy presne ten stav,
    kvuli kteremu se cela vlna dela. deactivation_state ma family=None, takze
    radek spada do bezhlavickove sekce; tenhle test hlida, ze tam opravdu
    dojde a nese hodnotu.

    Zabiji mutanta: vynechani `value` z Findingu v checks/deactivation.py.
    """
    result = RunResult(
        evaluated_at="2026-07-30T12:00:00Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline=None,
        summary={
            "pass": 0, "warn": 0, "fail": 0, "skip": 1,
            "scopes_matched": 0, "unmatched_baseline": 0, "unmatched_subject": 0,
        },
        scopes=[
            ScopeResult(
                scope_id="svc:L3VPN-CPE14-UNI:IPVPN",
                key={"description": "L3VPN-CPE14-UNI", "service_type": "IPVPN"},
                status=Status.SKIP,
                match=None,
                checks=[
                    CheckResult(
                        id="deactivation_state",
                        mode="both",
                        status=Status.SKIP,
                        severity=Severity.CRITICAL,
                        message="sluzba je v konfiguraci deaktivovana "
                        "(interface deactivated), baseline neni k porovnani",
                        label="Deaktivace",
                        value="interface deactivated",
                    )
                ],
            )
        ],
    )

    text = render(result)

    assert "L3VPN-CPE14-UNI" in text
    assert "interface deactivated" in text, (
        "duvod deaktivace se do textoveho reportu nedostal - operator vidi "
        "SKIP bez vysvetleni"
    )
```

Konstruktor `RunResult` a `ScopeResult` porovnej s `_legacy_result()` v témž
souboru a doplň, co tam navíc je. Podpis `render()` ověř — v souboru se už
volá.

- [ ] **Step 10: Spusť a ověř mutantem**

Run: `.venv/bin/python -m pytest tests/reporting/test_text_report.py -v -k deactivated`
Expected: PASS

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("tests/reporting/test_text_report.py")
s = p.read_text()
old = 'value="interface deactivated",'
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, "value=None,"))
EOF
grep -n "value=None," tests/reporting/test_text_report.py
.venv/bin/python -m pytest tests/reporting/test_text_report.py -q -k deactivated
git checkout tests/reporting/test_text_report.py
```

Expected: FAILED — hodnota se v reportu neobjeví. Tenhle mutant se aplikuje
na **test**, ne na kód: ověřuje, že asertace visí na `value`, a ne na tom, že
se řetězec náhodou objeví v `message`.

Kdyby prošel, renderer bere řetězec odjinud — najdi odkud a asertaci uprav
tak, aby visela na sloupci hodnot.

- [ ] **Step 11: Ověř mutanty**

Mutant A — `deactivation_state` není výjimka ze zkratky:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/base.py")
s = p.read_text()
old = "    if check.id != DEACTIVATION_CHECK_ID and ctx.scope.is_deactivated:"
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, "    if ctx.scope.is_deactivated:"))
EOF
grep -n "if ctx.scope.is_deactivated:" migration_validator/checks/base.py
.venv/bin/python -m pytest tests/test_engine.py -q
git checkout migration_validator/checks/base.py
```

Expected: `test_service_deactivated_on_both_sides_is_pass` FAILED.

Mutant B — zdravá služba dostane řádek:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/deactivation.py")
s = p.read_text()
old = "        if not subject_off and baseline_off is not True:\n            return []"
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, "        if not subject_off and baseline_off is not True:\n            return [Finding(Outcome.OK, \"sluzba je aktivni\", label=self.label, value=ACTIVE)]"))
EOF
grep -n "sluzba je aktivni" migration_validator/checks/deactivation.py
.venv/bin/python -m pytest tests/checks/test_deactivation.py -q
git checkout migration_validator/checks/deactivation.py
```

Expected: `test_healthy_service_gets_no_row` FAILED pro oba parametry.

Mutant C — `baseline_scope` se z enginu nepředá:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/engine.py")
s = p.read_text()
old = "        baseline_scope=baseline_scope,\n"
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, ""))
EOF
grep -n "baseline_scope=" migration_validator/engine.py
.venv/bin/python -m pytest tests/test_engine.py -q
git checkout migration_validator/engine.py
grep -n "baseline_scope=" migration_validator/engine.py
```

Expected: `test_service_deactivated_on_both_sides_is_pass` FAILED, po
checkoutu je řádek zpátky.

- [ ] **Step 12: Spusť celou sadu a commitni**

Run: `.venv/bin/python -m pytest -q`

```bash
git add -A
git commit -m "feat(checks): novy check deactivation_state (AR-23)"
```

---

## Úloha 10: Párování se deaktivací neřídí (AR‑24)

Dnes to platí náhodou — `scoping/matcher.py` příznaky nečte, protože žádné
nebyly. Po úloze 7 existují, takže to musí platit záměrně a být pokryté.

**Files:**
- Test: `tests/scoping/test_matcher.py`
- Modify: `migration_validator/scoping/matcher.py` — **jen pokud** test odhalí
  problém; očekává se, že ne

**Interfaces:**
- Consumes: `Scope` s příznaky z úlohy 7
- Produces: nic

- [ ] **Step 1: Napiš test**

Do `tests/scoping/test_matcher.py`:

```python
def test_deactivation_does_not_block_pairing():
    """Sluzba deaktivovana na jedne strane se porad musi sparovat.

    Docasne deaktivovana sluzba se porad migruje, takze deaktivace nesmi
    rozhodovat o tom, jestli se najde protejsek - jinak by zmizela do
    NESPAROVANO a operator by prisel prave o ten radek, kvuli kteremu se
    priznak zavadi.

    Zabiji mutanta: doplneni podminky na priznak do klicovaci funkce nebo do
    filtru kandidatu v matcher.py. Dnes tam neni; tenhle test hlida, aby se
    tam nedostala.
    """
    baseline = [_scope("ge-0/0/2.113", "L3VPN-CPE13-NNI", "IPVPN")]
    subject = [_scope("et-0/0/8.113", "L3VPN-CPE13-NNI", "IPVPN")]
    baseline[0].interface_active = False

    result = match_scopes(baseline, subject)

    assert len(result.pairs) == 1
    assert result.pairs[0].method == "description+service_type"
    assert not result.unmatched_baseline
    assert not result.unmatched_subject


def test_pairing_works_when_both_sides_are_deactivated():
    """Sluzba deaktivovana na obou zarizenich se taky musi sparovat.

    Bez tohoto testu by slo AR-24 splnit podminkou "sparuj, jen kdyz se
    priznaky lisi" - tedy poloviately.
    """
    baseline = [_scope("ge-0/0/2.113", "L3VPN-CPE13-NNI", "IPVPN")]
    subject = [_scope("et-0/0/8.113", "L3VPN-CPE13-NNI", "IPVPN")]
    baseline[0].interface_active = False
    subject[0].interface_active = False

    result = match_scopes(baseline, subject)

    assert len(result.pairs) == 1
```

Jména `unmatched_baseline` / `unmatched_subject` na `MatchSet` ověř v
`migration_validator/scoping/matcher.py` a použij ta, která tam jsou.

- [ ] **Step 2: Spusť — musí projít hned**

Run: `.venv/bin/python -m pytest tests/scoping/test_matcher.py -v -k deactivation`
Expected: **PASS** bez jakékoli změny kódu.

Kdyby spadl, matcher příznaky čte — najdi kde a odstraň to, tohle je pak
oprava chyby.

- [ ] **Step 3: Ověř mutantem, že test hlídá**

Najdi klíčovací funkci v `migration_validator/scoping/matcher.py` (ta, která
ze `Scope` dělá klíč pro spárování) a dočasně do ní vlož podmínku, která
u deaktivovaného scopu vrátí jiný klíč:

```bash
grep -n "def .*key\|scope.key" migration_validator/scoping/matcher.py
```

Podle výsledku uprav klíčovací funkci tak, aby pro `scope.is_deactivated`
vrátila `None` (nebo klíč s příponou). Pak:

```bash
grep -n "is_deactivated" migration_validator/scoping/matcher.py
.venv/bin/python -m pytest tests/scoping/test_matcher.py -q
git checkout migration_validator/scoping/matcher.py
grep -n "is_deactivated" migration_validator/scoping/matcher.py
```

Expected: test FAILED s mutantem, poslední grep nevrátí nic.

- [ ] **Step 4: Commit**

```bash
git add tests/scoping/test_matcher.py
git commit -m "test(matcher): parovani se deaktivaci neridi (AR-24)"
```

---

## Úloha 11: `StaticRouteStatusCheck` čte aktivitu routy (AR‑25)

Collector u každé statiky ukládá `active` = zda má routa v tabulce hvězdičku
(`collectors/routes.py:84`). Check to pole ignoruje, takže routa, kterou
přebil jiný zdroj, projde jako PASS.

Nedosažitelný next-hop routu z tabulky vyhodí úplně, takže „v tabulce, ale bez
hvězdičky" znamená, že ji přebil jiný zdroj — proto to není totéž co
`neni v tabulce` a má to vlastní text.

**Files:**
- Modify: `migration_validator/checks/routes.py:103-169` (`_finding`)
- Test: `tests/checks/test_routes.py`

**Interfaces:**
- Consumes: nic z předchozích úloh
- Produces: konstanta `NOT_ACTIVE = "neni aktivni"` v `checks/routes.py`

- [ ] **Step 1: Napiš padající testy**

Do `tests/checks/test_routes.py` — helper `_installed` už bere `next_hop`,
přidej vedle něj druhý:

```python
def _installed_inactive(next_hop="152.11.13.2"):
    """Routa v tabulce je, ale hvezdicku nema - prebil ji jiny zdroj."""
    routes = _installed(next_hop)
    routes["inet.0"]["198.62.1.0/29"]["active"] = False
    return routes


def test_inactive_route_that_was_inactive_before_passes():
    """Stav se nezmenil, takze to neni nalez.

    Zabiji mutanta: hlaseni BROKEN pri kazde neaktivni route bez ohledu na
    baseline.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _installed_inactive())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK


def test_route_that_stopped_being_active_is_broken():
    """Na starem zarizeni forwardovala, na novem uz ne.

    Zabiji mutanta: vynechani cteni klice "active" - bez nej je next-hop
    stejny, takze by check vratil OK.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _installed())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "neni aktivni"


def test_inactive_route_without_baseline_is_degraded():
    """Bez baseline neni z ceho poznat, ze neaktivni byla i predtim.

    FAIL by tvrdil, ze se neco zhorsilo, a to doloneno neni - podle R-2 se
    nejednoznacnost na FAIL neeskaluje. SKIP by naopak znamenal, ze
    `evaluate --snapshot X` bez --baseline o neaktivni route mlci uplne.

    Zabiji mutanta: BROKEN misto DEGRADED v teto vetvi.
    """
    findings = StaticRouteStatusCheck().run(_ctx(_installed_inactive()))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "neni aktivni"


def test_route_that_became_active_passes():
    """Zlepseni neni nalez (R-2).

    Zabiji mutanta: porovnani na nerovnost misto na smer zmeny.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed(), _installed_inactive())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
```

- [ ] **Step 2: Spusť — musí spadnout**

Run: `.venv/bin/python -m pytest tests/checks/test_routes.py -v -k active`
Expected: `test_route_that_stopped_being_active_is_broken`
a `test_inactive_route_without_baseline_is_degraded` FAIL (dostanou OK).
Zbylé dva projdou už teď.

- [ ] **Step 3: Implementuj**

V `migration_validator/checks/routes.py` přidej ke konstantám:

```python
NOT_ACTIVE = "neni aktivni"
```

A do `_finding`, **za** větev `if subject is None:` a **před** porovnání
next-hopu (tedy hned za `now = _next_hop_text(subject)`):

```python
        now = _next_hop_text(subject)

        # Routa v tabulce bez hvezdicky forwarding nedela. Neni to totez co
        # "neni v tabulce": nedosazitelny next-hop routu z tabulky vyhodi
        # uplne, takze tenhle stav znamena, ze ji prebil jiny zdroj.
        if not subject.get("active", True):
            was_active = baseline.get("active", True) if baseline else None

            if was_active is False:
                return Finding(
                    Outcome.OK,
                    f"{rib} {prefix}: neni aktivni, stejne jako v baseline",
                    label=label,
                    family=family,
                    value=NOT_ACTIVE,
                    baseline_value=NOT_ACTIVE,
                    baseline=baseline,
                    subject=subject,
                )

            # Bez baseline neni z ceho poznat, ze neaktivni byla i predtim -
            # podle R-2 se nejednoznacnost na FAIL neeskaluje.
            outcome = Outcome.BROKEN if was_active else Outcome.DEGRADED
            message = (
                f"{rib} {prefix}: v baseline forwardovala, ted neni aktivni"
                if was_active
                else f"{rib} {prefix}: je v tabulce, ale neni aktivni"
            )
            return Finding(
                outcome,
                message,
                label=label,
                family=family,
                value=NOT_ACTIVE,
                baseline_value=was,
                baseline=baseline,
                subject=subject,
            )
```

Pozor na `was_active`: `None` znamená „bez baseline", `False` znamená
„v baseline taky neaktivní", `True` znamená „v baseline forwardovala".
`if was_active` je pravda jen pro `True`, což je přesně ta větev pro FAIL.

- [ ] **Step 4: Spusť testy — musí projít**

Run: `.venv/bin/python -m pytest tests/checks/test_routes.py -v`
Expected: všechny PASS

- [ ] **Step 5: Ověř mutanty**

Mutant A — klíč se nečte:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/routes.py")
s = p.read_text()
old = 'if not subject.get("active", True):'
assert s.count(old) == 1, f"ocekavan 1 vyskyt, nalezeno {s.count(old)}"
p.write_text(s.replace(old, "if False:"))
EOF
grep -n "if False:" migration_validator/checks/routes.py
.venv/bin/python -m pytest tests/checks/test_routes.py -q
git checkout migration_validator/checks/routes.py
```

Expected: `test_route_that_stopped_being_active_is_broken`
i `test_inactive_route_without_baseline_is_degraded` FAILED.

Mutant B — bez baseline se eskaluje na FAIL:

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/checks/routes.py")
s = p.read_text()
old = "            outcome = Outcome.BROKEN if was_active else Outcome.DEGRADED"
assert s.count(old) == 1
p.write_text(s.replace(old, "            outcome = Outcome.BROKEN"))
EOF
grep -n "outcome = Outcome.BROKEN" migration_validator/checks/routes.py
.venv/bin/python -m pytest tests/checks/test_routes.py -q
git checkout migration_validator/checks/routes.py
grep -n "outcome = Outcome.BROKEN if was_active" migration_validator/checks/routes.py
```

Expected: `test_inactive_route_without_baseline_is_degraded` FAILED, po
checkoutu původní řádek.

- [ ] **Step 6: Spusť celou sadu a commitni**

Run: `.venv/bin/python -m pytest -q`

```bash
git add -A
git commit -m "feat(checks): staticka routa bez hvezdicky uz neprojde jako PASS (AR-25)"
```

---

## Úloha 12: Regenerace inventory a fixture (AR‑29)

Až teď, protože předchozí úlohy mění formát i obsah.

**Files:**
- Modify: `172.20.20.4.yml`, `172.20.20.5.yml`
- Modify: `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml`
- Modify: `tests/fixtures/rpc/junos-evo/interfaces.xml`
- Modify: `docs/cs/files/parsers.md` a jeho anglický protějšek
- Modify: testy s očekáváním počtu služeb

**Interfaces:**
- Consumes: všechny předchozí úlohy
- Produces: nic

- [ ] **Step 1: Načti heslo do laborky**

`MIG_LAB_PASSWORD` je v `~/.bashrc` **pod** stráží na neinteraktivní shell,
takže se v neinteraktivním běhu nenačte. Načti ho explicitně:

```bash
eval "$(grep -h 'MIG_LAB_PASSWORD' ~/.bashrc)"
test -n "$MIG_LAB_PASSWORD" && echo "heslo nacteno"
```

Laborka: `172.20.20.4` (vMX, platforma `junos`), `172.20.20.5`
(PTX10002‑36QDD, `junos-evo`), uživatel `admin`, autentizace **heslem, ne
klíčem**.

- [ ] **Step 2: Ověř dosažitelnost**

```bash
ping -c1 -W2 172.20.20.4 && ping -c1 -W2 172.20.20.5
```

Když laborka není dosažitelná, **zastav se a řekni to** — tuhle úlohu nejde
udělat offline a předstírat ji je horší než ji odložit.

- [ ] **Step 3: Regeneruj inventory**

Autentizace je **heslem**, výchozí režim parserů je klíč — přepínač je nutný:

```bash
.venv/bin/python mx_parser.py 172.20.20.4 --auth password -u admin -o 172.20.20.4.yml
.venv/bin/python evo_parser.py 172.20.20.5 --auth password -u admin -o 172.20.20.5.yml
```

Kdyby parser heslo chtěl interaktivně a nevzal si `MIG_LAB_PASSWORD` z okolí,
podívej se do `parse_arguments()` (`mx_parser.py:2263`) a `main()` (`:2437`),
jak se heslo předává, a použij ten způsob.

- [ ] **Step 4: Zkontroluj, co se změnilo**

```bash
git diff --stat 172.20.20.4.yml 172.20.20.5.yml
grep -n "schema_version" 172.20.20.4.yml 172.20.20.5.yml
grep -c "interface_active: false" 172.20.20.4.yml 172.20.20.5.yml
grep -n "CPE24" 172.20.20.4.yml 172.20.20.5.yml
```

Očekávání, každé ověř zvlášť:

- `schema_version: 4` v obou
- `interface_active: false` **třikrát** v `.4` (`ge-0/0/2`, `ge-0/0/4`,
  `ge-0/0/5` — a jejich jednotky, takže číslo bude vyšší; spočítej fyzická
  rozhraní i unity) a **jednou** (plus unity) v `.5` (`et-0/0/10`)
- služba `EVPN-VPWS-CPE24-UNI` je nově přítomná: `ge-0/0/3` na `.4`,
  `ae0.224` na `.5`

Kdyby některé očekávání nesedělo, **nepiš to ručně do YAML.** Znamená to, že
se laborka změnila, nebo že parser dělá něco jiného, než plán předpokládá.
Zjisti co, a zapiš to.

- [ ] **Step 5: Zkopíruj do fixtures**

```bash
cp 172.20.20.4.yml tests/fixtures/172.20.20.4.yml
cp 172.20.20.5.yml tests/fixtures/172.20.20.5.yml
```

- [ ] **Step 6: Přenahraj `interfaces.xml` pro junos-evo**

Dnešní fixture předchází přestavbě laborky a nemá `irb.15` / `ae0.15`, takže
conformance test službu CPE14 nevidí. Nahraj RPC `get-interface-information`
z `172.20.20.5` a ulož ho do `tests/fixtures/rpc/junos-evo/interfaces.xml`.

Cestu, kterou capture k nahrávání RPC používá, najdi v `capture.py`
(funkce `_record`, `:53`) — použij tentýž mechanismus, ne ruční `ssh`.

```bash
grep -n "irb.15\|ae0.15" tests/fixtures/rpc/junos-evo/interfaces.xml
```

Expected: obojí nalezeno.

- [ ] **Step 7: Spusť celou sadu a oprav očekávání**

Run: `.venv/bin/python -m pytest -q`

**Tohle není překvapení, je to předpovězený dopad.** Ověřeno 2026‑07‑30 na
`runs/bfd-static-2026-07-29/cfg/172.20.20.4.inherit.xml` — tedy na
konfiguraci, kterou parser opravdu konzumuje: deaktivovaná jsou `ge-0/0/2`,
`ge-0/0/4` **i** `ge-0/0/5`. Na `ge-0/0/2` přitom visí skoro všechny
zákaznické služby MX (`.13`, `.113`, `.213`, `.313`, `.413`). Po regeneraci
proto většina služeb `junos` dostane `interface_active: false`, spadne do
SKIPu podle AR‑22 a conformance testy pro `junos` to uvidí.

Očekávaný dopad, ověř každou položku zvlášť:

Na `junos-evo` je dopad menší, ale nenulový: `et-0/0/10` je v capturu taky
deaktivované a `et-0/0/10.0` je scopovaná služba typu `Internet`
(`172.20.20.5.yml:374`). Přibude naopak `EVPN-VPWS-CPE24-UNI` na `ae0.224`
a přenahraná `interfaces.xml` přinese `irb.15` / `ae0.15`.

Fixture `tests/fixtures/172.20.20.{4,5}.yml` čtou **čtyři** soubory —
`tests/collectors/test_conformance.py`, `tests/test_capture.py`,
`tests/test_end_to_end.py` a `tests/scoping/test_builder.py`. Projdi všechny,
ne jen conformance.

| test | co se stane | co s tím |
|---|---|---|
| počty služeb (`test_builder`, `test_capture`) | vzrostou o `EVPN-VPWS-CPE24-UNI` na obou zařízeních | oprav čísla |
| `test_end_to_end` | vykreslené bloky a countery se posunou — většina služeb `junos` je SKIP | oprav očekávání; když test asertuje konkrétní status služby, ověř, že nový status odpovídá matici z AR‑23 |
| `test_checks_produce_real_verdicts_not_all_skip[junos]` | může spadnout — asertuje „aspoň jeden ne‑SKIP" napříč celým zařízením | živá zůstává nová služba na `ge-0/0/3` a jádrová rozhraní; když ani to nestačí, **zapiš proč** a přeformuluj test na „aspoň jeden ne‑SKIP mezi službami, které deaktivované nejsou" |
| `test_specific_check_sees_data[junos-static_route_status]` | **spadne** — statiky visí na `L3VPN-CPE13-NNI`, tedy na `ge-0/0/2.113` | ten parametr už nemá živou službu; odstraň ho a **v commit message napiš, že důvodem je deaktivace v laborce, ne regrese** |
| `test_specific_check_sees_data[junos-interface_state]` | pravděpodobně projde díky `ge-0/0/3` a jádru | ověř |
| `test_specific_check_sees_data[junos-evo-*]` | evo přišlo o `et-0/0/10.0`, ale ostatní služby zůstávají | ověř každý parametr zvlášť |
| `test_interfaces_reach_their_scopes[junos-evo]` | po přenahrání `interfaces.xml` musí vidět i `irb.15` / `ae0.15` | ověř — to je celý důvod kroku 6 |
| `test_every_collector_has_its_fixtures` | musí dál platit i po přenahrání `interfaces.xml` | ověř |

**Rozliš dvě věci a v commit message to napiš:** test, který padl kvůli
deaktivaci v laborce, je správné chování a jeho očekávání se opravuje. Test,
který padl z jiného důvodu, je regrese a opravuje se **kód**. Když si nejsi
jistý, který to je, nespravuj očekávání — zjisti to.

- [ ] **Step 8: Ověř, že se deaktivace projeví v reportu**

```bash
.venv/bin/python -m migration_validator.cli capture --help
```

Udělej snímek `.4` a vyhodnoť ho bez baseline. Ve výstupu musí být u služeb na
`ge-0/0/4` a `ge-0/0/5` vidět SKIP s důvodem `interface deactivated`.

To je jediné místo v celém plánu, kde se ověřuje, že to celé dohromady dělá,
co má. Nepřeskakuj ho.

- [ ] **Step 9: Aktualizuj dokumentaci**

`docs/cs/files/parsers.md`, sekce „Deaktivovaná konfigurace nevyrábí záměr" —
dnes obsahuje výčet neošetřených kontejnerů. Ten výčet po AR‑19 neplatí.
Přepiš sekci tak, aby rozlišila dvě věci:

- **služba** se při deaktivaci z inventory nevypouští, jen se označí
  (`routing_instance_active`, `interface_active`)
- **záměry** (statiky, BFD, BGP sousedé) se vypouštějí dál, na všech
  úrovních včetně kontejnerů

Zkontroluj i anglický protějšek v `docs/en/`.

- [ ] **Step 10: Zapiš stav do roadmapy**

Založ `docs/superpowers/roadmap-2026-07-30-vlna3-hotovo.md` podle vzoru
předchozích: co vlna přinesla, co z ní zbylo, jak si vyrobit důkazy, stav
laborky. Zapiš skutečný počet testů a skutečnou hodnotu
`diff mx_parser.py evo_parser.py | wc -l`.

Do „co zbývá" patří minimálně tohle:

- **Příznaky na hlubších úrovních konfigurace** — deaktivovaná jednotlivá
  routa, `bfd-liveness-detection` a `neighbor` se dál vypouštějí ze záměru
  beze stopy. Vědomě odloženo (spec, „Mimo rozsah").
- **MX‑specifické parsování BFD zůstává nepokryté** — laborka na `172.20.20.4`
  nemá ani jednu BFD session (ověřeno 2026‑07‑30 na fixture i na surovém
  capturu). Šev collector→check pokrytý je (úloha 2), samotné parsování MX
  odpovědi ne. Pokryje se, až v laborce MX session bude.
- **Report** — F‑2 (podřádky se jménem RIB), F‑11 (dvě kopie logiky pro
  link-local), F‑14 a `unassigned` v textovém reportu. Vlastní spec.
- Cokoli, co v úloze 12 kroku 7 vyšlo jinak, než plán čekal.

Nepiš do něj to, co už popisuje `docs/cs/` a `docs/en/`.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "chore: regenerovat inventory a fixture proti laborce (AR-29)"
```

---

## Poznámky k dokončení větve

Po úloze 12 použij skill `superpowers:finishing-a-development-branch`.

Před tím ověř všechny tři výchozí veličiny a **zapiš skutečné hodnoty**, ne
očekávané:

```bash
.venv/bin/python -m pytest              # skutecny pocet
diff mx_parser.py evo_parser.py | wc -l # skutecna hodnota, ocekavano 146
git stash list                          # stash@{0} tam porad musi byt
```
