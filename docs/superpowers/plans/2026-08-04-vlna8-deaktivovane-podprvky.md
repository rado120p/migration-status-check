# Vlna 8 — deaktivované podprvky zůstávají v záměru a hlásí SKIP

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deaktivovaná statická routa a deaktivovaný BGP soused přestanou z
konfiguračního záměru mizet beze stopy; zůstanou v inventory s příznakem a v
reportu dostanou SKIP na svém vlastním řádku, aniž by strhly sourozence.

**Architecture:** Parsery přestanou deaktivované prvky vypouštět a místo toho
zapíšou příznak na listu (`StaticRoute.active`) nebo prvek přesunou do
paralelního seznamu (`bgp_neighbor_inactive`). Příznak proteče beze změny tvaru
do inventory YAML, odtud do `Selectors`, a vlastnící check
(`checks/routes.py`, `checks/bgp.py`) z něj vyrobí `Outcome.SKIP` na jednom
nálezu. `engine.py:145` SKIPy odfiltruje před hlasováním `Status.worst()`,
takže sourozenec téže služby zůstane PASS.

**Tech Stack:** Python 3, lxml, PyYAML, pytest. Bez nových závislostí.

**Návrh, ze kterého plán vychází:**
[`../specs/2026-08-04-vlna8-deaktivovane-podprvky-design.md`](../specs/2026-08-04-vlna8-deaktivovane-podprvky-design.md)

## Global Constraints

- **Zámek parserů:** `diff mx_parser.py evo_parser.py | wc -l` musí být
  **146** po každém commitu, který sáhl na parser. Každá úprava se dělá
  **v obou souborech identicky**.
- **Znaková sada se řídí souborem, který upravuješ — ne repem.** První znění
  plánu tvrdilo „ASCII-only platí pro `migration_validator/` a `tests/`" a
  **bylo to špatně**; vzniklo to tak, že autor plánu přečetl podmíněnou větu
  roadmapy vlny 7 („*je-li* ASCII-only tvrdá podmínka…") jako tvrzení a
  neověřil ji. Doměřeno až během úlohy 1, kdy to nahlásil implementer:
  repo‑wide grep prázdný **není**, na `main` je 61 řádků s ne‑ASCII znaky v
  šesti souborech.

  Co platí doopravdy, po souborech:

  | soubor | stav | co psát |
  |---|---|---|
  | `mx_parser.py`, `evo_parser.py` | 172 řádků česky | **s diakritikou** |
  | `tests/parsers/test_inactive.py` | 54 řádků česky | **s diakritikou** |
  | `tests/checks/test_routes.py`, `tests/checks/test_bgp.py` | 1 a 3 řádky | drž se okolí v místě zásahu |
  | všechny ostatní soubory téhle vlny (`migration_validator/**`, `tests/parsers/test_static_routes.py`, `tests/models/**`) | čisté ASCII | **ASCII** |

  Ověř si soubor před psaním: `grep -cP '[^\x00-\x7F]' <soubor>`.
  **Existující ne‑ASCII obsah se neopravuje** — je předchozí a s vlnou 8
  nesouvisí.
- **Celá sada zelená, 0 přeskočených**, po každém commitu:
  `.venv/bin/python -m pytest -o addopts="" -q`. Výchozí stav je
  **640 passed**.
- **Interpret je `.venv/bin/python`**, ne systémový `python`.
- **Mutant se pouští nad tím stavem repa, ve kterém poběží doopravdy** — tedy
  až po krocích téže úlohy, které mění dotčené soubory.
- **Mutant se neadresuje číslem řádku.** Používej `python - <<'EOF'` s
  `str.replace` a `assert text.count(old) == 1`, ne `sed -i '194s/…/…/'`.
  Po každém mutantovi je `git diff --stat` **povinný krok**: prázdný výstup
  znamená, že mutant nesedl, a měření neplatí.
- **Měření má přednost před zadáním.** Když ti výsledek nesedí s tím, co
  plán tvrdí, platí tvoje měření — zapiš to do reportu úlohy.
- **Sémantika se neotvírá.** Rozhodnutí uživatele z 2026‑08‑03: deaktivovaný
  prvek zůstává v záměru a hlásí SKIP. Neřeš, jestli je to správně.

## File Structure

| soubor | odpovědnost | úloha |
|---|---|---|
| `mx_parser.py`, `evo_parser.py` | čtení konfigurace do záměru; **musí zůstat vzájemně identické až na 146 řádků** | 1, 2, 3 |
| `migration_validator/models/inventory.py` | `ServiceEntry` — tvar inventory YAML na straně validátoru | 1, 4 |
| `migration_validator/models/snapshot.py` | `SCHEMA_VERSION`; snapshot vnořuje scopy, proto ho bump zasahuje taky | 1 |
| `migration_validator/models/scope.py` | `Selectors` — co check vidí ze záměru | 4 |
| `migration_validator/scoping/builder.py` | překlad `ServiceEntry` → `Scope` | 4 |
| `migration_validator/engine.py` | `_unassigned_bgp_peers`, `_unassigned_bfd_sessions` — komu peer „patří" | 4 |
| `migration_validator/checks/routes.py` | SKIP za deaktivovanou statiku | 5 |
| `migration_validator/checks/bgp.py` | SKIP za deaktivovaného peera | 6 |
| `tests/fixtures/172.20.20.{4,5}.yml` | sdílené fixtures, schema | 1, 2, 4 |
| `tests/parsers/test_static_routes.py` | šest testů starého kontraktu k přepsání | 2 |
| `tests/parsers/test_inactive.py` | tři testy k rozšíření + zámek chování BFD | 3 |

---

### Task 1: Schema 4 → 5

Čistě mechanická úloha, žádná změna chování. Dělá se první, aby další úlohy
už nemusely řešit verzi.

**Proč se schema zvedá:** inventory ani snapshot vyrobené starým nástrojem
nové klíče nemají. `.get()` by dosadil default a **každý deaktivovaný prvek
by se tiše přečetl jako aktivní** — přesně ta vada, kterou vlna opravuje, o
patro výš. Hlasitý pád je smysl bumpu, ne jeho cena.

**Files:**
- Modify: `mx_parser.py` (`INVENTORY_SCHEMA_VERSION`, řádek ~2215)
- Modify: `evo_parser.py` (tentýž řádek)
- Modify: `migration_validator/models/snapshot.py` (`SCHEMA_VERSION`, řádek ~17)
- Modify: `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml` (první řádek)

**Interfaces:**
- Consumes: nic
- Produces: `INVENTORY_SCHEMA_VERSION == 5`, `SCHEMA_VERSION == 5`

- [ ] **Step 1: Ověř výchozí stav**

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts="" -q | tail -2
diff mx_parser.py evo_parser.py | wc -l
```

Očekávané: `640 passed`, `146`. Když nesedí, **zastav a nahlas to** — plán
stojí na tomhle výchozím bodě.

- [ ] **Step 2: Napiš test, který zafixuje, že snapshot verzi hlídá**

Do `tests/models/test_snapshot.py` přidej (jestli soubor neexistuje, založ ho
se stejnými importy, jaké má `tests/models/test_inventory.py`):

```python
def test_snapshot_rejects_previous_schema_version():
    """Stary snimek se musi odmitnout hlasite, ne precist s prazdnymi klici.

    Vlna 8 pridala do Selectors klic bgp_neighbors_inactive. Snapshot scopy
    vnoruje, takze stary snimek by ho precetl jako prazdny a deaktivovany
    peer by se tise stal aktivnim - prave ta vada, kterou vlna opravuje.
    """
    with pytest.raises(SnapshotVersionError):
        Snapshot.from_dict({"schema_version": 4})
```

- [ ] **Step 3: Spusť ho a ověř, že padá**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/models/test_snapshot.py::test_snapshot_rejects_previous_schema_version
```

Očekávané: FAIL — dokud je `SCHEMA_VERSION == 4`, verze 4 se přijme a
`SnapshotVersionError` nepadne. (Padne `KeyError: 'device'` až za kontrolou
verze; to je taky FAIL, ale z jiného důvodu — po Step 4 musí projít.)

- [ ] **Step 4: Zvedni obě konstanty**

```bash
.venv/bin/python - <<'EOF'
edits = [
    ("mx_parser.py", "INVENTORY_SCHEMA_VERSION = 4", "INVENTORY_SCHEMA_VERSION = 5"),
    ("evo_parser.py", "INVENTORY_SCHEMA_VERSION = 4", "INVENTORY_SCHEMA_VERSION = 5"),
    ("migration_validator/models/snapshot.py", "SCHEMA_VERSION = 4", "SCHEMA_VERSION = 5"),
]
for path, old, new in edits:
    text = open(path, encoding="utf-8").read()
    assert text.count(old) == 1, (path, text.count(old))
    open(path, "w", encoding="utf-8").write(text.replace(old, new))
    print(path, "ok")
EOF
```

- [ ] **Step 5: Zvedni verzi v obou fixtures**

```bash
.venv/bin/python - <<'EOF'
for path in ("tests/fixtures/172.20.20.4.yml", "tests/fixtures/172.20.20.5.yml"):
    text = open(path, encoding="utf-8").read()
    assert text.startswith("schema_version: 4\n"), path
    open(path, "w", encoding="utf-8").write(
        text.replace("schema_version: 4\n", "schema_version: 5\n", 1)
    )
    print(path, "ok")
EOF
```

- [ ] **Step 6: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q | tail -3
diff mx_parser.py evo_parser.py | wc -l
```

Očekávané: `641 passed` (640 + nový test), `146`.

Když padne něco jiného, je to nález: znamená to, že nějaký test drží verzi
napevno. Oprav ten test na 5 a **zapiš do reportu, který to byl** — plán s
ním nepočítal.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: inventory i snapshot schema 5 kvuli priznakum deaktivace"
```

---

### Task 2: Statické routy zůstanou v záměru s příznakem

**Files:**
- Modify: `mx_parser.py`, `evo_parser.py` (`StaticRoute`, `_parse_static_routes`, `_static_routes_under`)
- Modify: `tests/parsers/test_static_routes.py` (čtyři testy)
- Modify: `tests/parsers/test_inactive.py` (dva testy)
- Modify: `tests/fixtures/172.20.20.{4,5}.yml` (klíč `active` u statik)

**Interfaces:**
- Consumes: schema 5 z úlohy 1
- Produces: `StaticRoute(rib, prefix, next_hop, active)` — `active: bool = True`.
  V inventory YAML se objeví jako klíč `active` uvnitř každé položky
  `static_route` (jde tam přes `asdict`, viz `_assign_static_routes`).
  Úloha 5 na tenhle klíč staví.

**Pozor — nález z psaní specu:** čtyři kontejnerové guardy, které tahle úloha
maže, **dnes nedělají vůbec nic**. Změřeno: jejich samotné odstranění dá
`640 passed`, nulový ripple, protože `_is_inactive` chodí po předcích a
listový guard je úplně zastíní. Proto v téhle úloze **není samostatný krok
„smaž guardy a spusť testy"** — takové kritérium by splnilo i nicnedělání.
Guardy padnou spolu s listovým příznakem v jednom kroku.

- [ ] **Step 1: Napiš failující testy — přepiš čtyři testy v `test_static_routes.py`**

Helper `_configured` vrací dnes `set[tuple[str, str]]` a příznak by zahodil.
Přidej vedle něj druhý helper a **starý nech být** — používají ho i testy,
kterých se vlna netýká:

```python
def _configured_by_identity(parser_class, xml: str) -> dict[tuple[str, str], bool]:
    """Zamer klicovany (rib, prefix) s hodnotou 'je aktivni'.

    Oproti _configured neztrati priznak, takze test pozna rozdil mezi
    'routa v zameru neni' a 'routa v zameru je a je deaktivovana'.
    """
    parser = parser_class(etree.XML(xml.encode()))
    parser.parse()
    return {(route.rib, route.prefix): route.active for route in parser.static_routes}
```

Přepiš čtyři testy — nová jména, protože `..._yields_no_route` by lhalo:

```python
@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_static_stanza_keeps_route_as_inactive(module, parser_class):
    """Deaktivovany `static` nechá routu v zameru, ale oznaci ji.

    Vypustit ji beze stopy je vada: check by pak nemel co preskocit a
    operator by nevedel, ze routa v konfiguraci vubec je. Zaroven nesmi
    zustat aktivni - to by dalo 'FAIL ... neni v tabulce' za routu, kterou
    operator vedome vyradil.
    """
    found = _configured_by_identity(parser_class, DEACTIVATED_CONTAINERS)

    assert found[("inet.0", "198.62.1.0/29")] is False
    # Kontrola, ze priznak nesebral i to, co ma zustat zive.
    assert found[("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29")] is True


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_rib_marks_its_routes_inactive(module, parser_class):
    """Deaktivovany `rib` bere s sebou i `static` pod sebou - pres predky."""
    found = _configured_by_identity(parser_class, DEACTIVATED_CONTAINERS)

    assert found[("inet6.0", "2001:aaaa::/64")] is False


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_global_routing_options_marks_routes_inactive(module, parser_class):
    """Deaktivovane globalni `routing-options` oznaci statiky default instance."""
    found = _configured_by_identity(parser_class, DEACTIVATED_ROUTING_OPTIONS)

    assert found[("inet.0", "198.62.1.0/29")] is False


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_instance_routing_options_marks_routes_inactive(module, parser_class):
    """Deaktivovane `routing-options` uvnitr instance - druha, samostatna smycka.

    Jsou to dve ruzne smycky v `_parse_static_routes`, takze jeden test na
    obe by nechal jednu z nich nepokrytou.
    """
    found = _configured_by_identity(parser_class, DEACTIVATED_ROUTING_OPTIONS)

    assert found[("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29")] is False
```

- [ ] **Step 2: Přepiš dva testy v `test_inactive.py`**

Najdi `test_deactivated_routing_instances_container_drops_static_routes` a
`test_inactive_rib_drops_only_its_own_routes`. První asertuje `routes == []`,
druhý seznam prefixů. Nahraď je:

```python
@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_routing_instances_container_marks_static_routes_inactive(
    module, parser_class
):
    """Deaktivovany kontejner `routing-instances` oznaci statiky, nevypousti je.

    Zabiji mutanta: `_is_inactive` bez chuze po predcich. Jednotlive
    `instance` pod deaktivovanym kontejnerem atribut nemaji.
    """
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)
    routes = [route for service in services for route in service.static_route]

    assert routes, "deaktivovany kontejner nesmi routu vypustit"
    assert all(route["active"] is False for route in routes), routes


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_inactive_rib_marks_only_its_own_routes(module, parser_class):
    """Deaktivovana `rib` oznaci jen sve routy, sousedni RIB zustane ziva."""
    services = _services(module, parser_class, INACTIVE_RIB)
    routes = [route for service in services for route in service.static_route]
    by_prefix = {route["prefix"]: route["active"] for route in routes}

    assert by_prefix["10.9.9.0/24"] is True
```

**Poznámka k druhému testu:** původní verze asertovala
`[route["prefix"] for route in routes] == ["10.9.9.0/24"]`, tedy že
deaktivovaná routa v seznamu **není**. Po změně tam bude — proto se aserce
mění na „ta živá je pořád živá". Konstanta `INACTIVE_RIB` a přesná jména
prefixů: **přečti si je v souboru**, plán je cituje z paměti jen pro
`10.9.9.0/24`, které je v původní asertaci vidět. Když druhý prefix
existuje, přidej k němu `is False`.

- [ ] **Step 3: Spusť a ověř, že padají**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/ 2>&1 | tail -15
```

Očekávané: 12 failed (6 testů × 2 platformy) — `KeyError` nebo
`AssertionError`, podle testu. **Zapiš si přesný počet**; po Step 5 musí být 0.

- [ ] **Step 4: Přidej pole `active` do `StaticRoute` v obou parserech**

```bash
.venv/bin/python - <<'EOF'
old = """class StaticRoute:
    \"\"\"Jedna statická routa z konfigurace — záměr, ne stav routovací tabulky.\"\"\"

    rib: str
    prefix: str
    next_hop: list[str] = field(default_factory=list)
"""
new = """class StaticRoute:
    \"\"\"Jedna statická routa z konfigurace — záměr, ne stav routovací tabulky.\"\"\"

    rib: str
    prefix: str
    next_hop: list[str] = field(default_factory=list)
    # Deaktivovaná routa se ze záměru **nevypouští**. Kdyby zmizela, check
    # by neměl co přeskočit a operátor by z reportu nepoznal, že v
    # konfiguraci vůbec je. Příznak se čte na listu, protože `_is_inactive`
    # chodí po předcích — pokryje tím deaktivaci na libovolné úrovni nad
    # routou, včetně celého `routing-options`.
    active: bool = True
"""
for path in ("mx_parser.py", "evo_parser.py"):
    text = open(path, encoding="utf-8").read()
    assert text.count(old) == 1, (path, text.count(old))
    open(path, "w", encoding="utf-8").write(text.replace(old, new))
    print(path, "StaticRoute ok")
EOF
```

- [ ] **Step 5: Zruš čtyři kontejnerové guardy a zapiš příznak na listu**

Jeden krok, ne dva — viz poznámka nad úlohou.

```bash
.venv/bin/python - <<'EOF'
subs = [
    # 1) globalni routing-options
    ('''"./*[local-name()='routing-options']"
        ):
            if self._is_inactive(options_node):
                continue
''', '''"./*[local-name()='routing-options']"
        ):
'''),
    # 2) instance
    ('''/*[local-name()='instance']"
        ):
            if self._is_inactive(instance_node):
                continue
''', '''/*[local-name()='instance']"
        ):
'''),
    # 3) routing-options uvnitr instance
    ('''"./*[local-name()='routing-options']"
            ):
                if self._is_inactive(options_node):
                    continue
''', '''"./*[local-name()='routing-options']"
            ):
'''),
    # 4) static
    ('''for rib_name, static_node in containers:
            # Jediné místo pro oba tvary: `static` přímo pod
            # routing-options i `static` uvnitř `rib`.
            if self._is_inactive(static_node):
                continue
''', '''for rib_name, static_node in containers:
'''),
    # 5) listovy guard -> priznak
    ('''            for route_node in static_node.xpath(
                "./*[local-name()='route']"
            ):
                if self._is_inactive(route_node):
                    continue
''', '''            for route_node in static_node.xpath(
                "./*[local-name()='route']"
            ):
'''),
]
for path in ("mx_parser.py", "evo_parser.py"):
    text = open(path, encoding="utf-8").read()
    for old, new in subs:
        assert text.count(old) == 1, (path, repr(old[:50]), text.count(old))
        text = text.replace(old, new)
    open(path, "w", encoding="utf-8").write(text)
    print(path, "5 guardu zruseno")
EOF
git diff --stat
```

`git diff --stat` **musí** ukázat obě jména souborů se stejným počtem
odebraných řádků. Prázdný nebo asymetrický výstup znamená, že se něco
nepovedlo — zastav.

Teď doplň příznak do konstruktoru `StaticRoute`. **Taky skriptem, ne ručně** —
je to jediné místo v celé vlně, kde se mx a evo můžou tiše rozejít, a zámek
146 by to odhalil až o krok později:

```bash
.venv/bin/python - <<'EOF'
old = """                        next_hop=all_texts(
                            route_node,
                            "./*[local-name()='next-hop']/text()",
                        ),
                    )
"""
new = """                        next_hop=all_texts(
                            route_node,
                            "./*[local-name()='next-hop']/text()",
                        ),
                        active=not self._is_inactive(route_node),
                    )
"""
for path in ("mx_parser.py", "evo_parser.py"):
    t = open(path, encoding="utf-8").read()
    assert t.count(old) == 1, (path, t.count(old))
    open(path, "w", encoding="utf-8").write(t.replace(old, new))
    print(path, "konstruktor ok")
EOF
git diff --stat
```

`git diff --stat` musí ukázat **oba** soubory se **stejnými** čísly. Když se
liší, zastav — parsery se rozešly.

- [ ] **Step 6: Spusť testy parserů**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/ 2>&1 | tail -5
diff mx_parser.py evo_parser.py | wc -l
```

Očekávané: vše zelené, `146`.

- [ ] **Step 7: Doplň klíč `active` do statik ve fixtures**

Fixtures nesou po šesti statikách. Nový klíč se dopisuje explicitně, ne
spoléhá na default — soubor, který se načte, je verze 5 a má nést plný tvar.

```bash
.venv/bin/python - <<'EOF'
import re
for path in ("tests/fixtures/172.20.20.4.yml", "tests/fixtures/172.20.20.5.yml"):
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    # `active: true` se vklada hned za radek `prefix:` na urovni odsazeni
    # statik (ctyri mezery). Klic uvnitr polozky je poradove nezavisly,
    # takze staci trefit spravnou polozku, ne spravne misto v ni.
    out, added = [], 0
    for line in lines:
        out.append(line)
        m = re.match(r"^(    )prefix: ", line)
        if m:
            out.append(f"{m.group(1)}active: true\n")
            added += 1
    open(path, "w", encoding="utf-8").write("".join(out))
    print(path, "doplneno", added)
EOF
```

Očekávané: `doplneno 6` u obou souborů. Když vyjde jiné číslo, **zastav** —
odsazení je jiné, než plán předpokládá; podívej se do souboru a oprav
regulární výraz.

- [ ] **Step 8: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q | tail -3
```

Očekávané: `641 passed`.

- [ ] **Step 9: Pusť mutanta a ověř, že testy z Step 1 opravdu hlídají příznak**

Mutant míří do **produkčního kódu**, ne do generátoru fixtures:

```bash
.venv/bin/python - <<'EOF'
old = "active=not self._is_inactive(route_node),"
new = "active=True,"
for path in ("mx_parser.py", "evo_parser.py"):
    t = open(path, encoding="utf-8").read()
    assert t.count(old) == 1, (path, t.count(old))
    open(path, "w", encoding="utf-8").write(t.replace(old, new))
EOF
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q 2>&1 | tail -3
git checkout -- mx_parser.py evo_parser.py
```

`git diff --stat` musí být **neprázdný** (jinak mutant nesedl a měření
neplatí). Očekávané: nejméně 12 failed. **Vlep skutečný výstup do reportu
úlohy**, ne jeho popis prózou.

- [ ] **Step 10: Ověř zámek a ASCII a commitni**

```bash
diff mx_parser.py evo_parser.py | wc -l
git diff --name-only | xargs -r -I{} sh -c 'printf "%-50s %s\n" {} $(grep -cP "[^\x00-\x7F]" {})'
.venv/bin/python -m pytest -o addopts="" -q | tail -2
git add -A
git commit -m "feat: deaktivovana statika zustava v zameru s priznakem active"
```

Očekávané: `146`, prázdný grep, `641 passed`.

---

### Task 3: Deaktivovaný BGP soused jde do paralelního seznamu

**Files:**
- Modify: `mx_parser.py`, `evo_parser.py` (`RoutingInstance`, `InterfaceService`, `_parse_bgp_neighbors`, `_parse_routing_instances`, `_parse_default_bgp_neighbors`, `_assign_bgp_neighbors`, `clean_service_dict`)
- Modify: `tests/parsers/test_inactive.py` (tři testy + jeden nový)

**Interfaces:**
- Consumes: schema 5 z úlohy 1
- Produces:
  - `JunosServiceParser._parse_bgp_neighbors(node, bgp_xpath) -> tuple[list[str], list[str]]`
    — `(aktivní, neaktivní)`. **Změna návratového typu**, oba volající se musí
    upravit.
  - `RoutingInstance.bgp_neighbors_inactive: list[str]`
  - `JunosServiceParser.default_bgp_neighbors_inactive: list[str]`
  - `InterfaceService.bgp_neighbor_inactive: list[str]` → klíč
    `bgp_neighbor_inactive` v inventory YAML. Úloha 4 na něj staví.

**Co se v téhle úloze NEMĚNÍ a proč:** `_parse_bfd` (guard na `neighbor_node`)
ani `_bfd_node` (guard na `bfd-liveness-detection`). BFD je vlastnost
**relace**; deaktivovaný soused žádnou relaci nemá, takže jeho BFD záměr nemá
co popisovat. A deaktivované `bfd-liveness-detection` pod živou skupinou
**správně** dědí hodnotu ze skupiny — Junosí `inactive` znamená „příkaz se
neuplatní", takže BFD na krabici opravdu poběží se skupinovou hodnotou.
Step 6 tohle chování zafixuje testem, aby to příští vlna neopravovala jako
vadu.

- [ ] **Step 1: Ověř předpověď plánu — tři testy o sousedech**

Spec u těchhle tří testů tvrdí **předpověď, ne měření**: že pod paralelním
seznamem zůstanou zelené, protože `service.bgp_neighbor` bude dál prázdné.
Ověř to až po Step 3 (implementaci) v Step 5. Teď si jen poznamenej výchozí
stav:

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/test_inactive.py 2>&1 | tail -2
```

- [ ] **Step 2: Napiš failující test na nové chování**

**Pozor na znakovou sadu:** `tests/parsers/test_inactive.py` je psaný
**česky s diakritikou** (54 řádků s ne‑ASCII znaky). Kódové bloky téhle
úlohy jsou pro jednoduchost napsané bez diakritiky — **při vkládání do
souboru diakritiku doplň**, ať docstringy sedí k okolí. Týká se to všech
testů v téhle úloze; v `migration_validator/**` naopak zůstává ASCII.

Do `tests/parsers/test_inactive.py`:

```python
@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_neighbor_lands_in_inactive_list(module, parser_class):
    """Deaktivovany soused se ze zameru neztrati, jen se prestehuje.

    Zabiji mutanta: `bgp_neighbor_inactive` se plni z aktivnich sousedu.
    Bez tehle aserce by test, ktery tvrdi jen `bgp_neighbor == []`, prosel
    i kdyby se soused vypustil uplne - tedy pri starem chovani.
    """
    services = _services(module, parser_class, DEACTIVATED_TOP_LEVEL_PROTOCOLS)
    service = _by_name(services, "ge-0/0/2.13")

    assert service.bgp_neighbor == []
    assert service.bgp_neighbor_inactive == ["152.11.13.2"]
```

**Adresu peera si ověř v souboru** — konstanta `DEACTIVATED_TOP_LEVEL_PROTOCOLS`
je v `tests/parsers/test_inactive.py` a plán ji cituje z okolního testu
`test_default_bgp_neighbors_reach_service_without_instance`. Když nesedí,
platí soubor.

- [ ] **Step 3: Spusť a ověř, že padá**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/test_inactive.py::test_deactivated_neighbor_lands_in_inactive_list
```

Očekávané: FAIL, `AttributeError: 'InterfaceService' object has no attribute
'bgp_neighbor_inactive'`.

- [ ] **Step 4: Implementuj — pět míst v obou parserech**

Všechny úpravy **identicky v `mx_parser.py` i `evo_parser.py`**.

**4a) `RoutingInstance` a `InterfaceService` dostanou pole.** Za
`bgp_neighbors: list[str] = field(default_factory=list)` v `RoutingInstance`
přidej:

```python
    # Deaktivovaný soused se ze záměru nevypouští, jen se drží zvlášť.
    # Paralelní seznam, ne mapa: `bgp_neighbors` slouží jako **selektor**
    # (podle něj se k službě párují naměřené session), a přepis na mapu by
    # rozbil čtyři testy členství v enginu a ve scopu bez užitku.
    bgp_neighbors_inactive: list[str] = field(default_factory=list)
```

Za `bgp_neighbor: list[str] = field(default_factory=list)` v
`InterfaceService` přidej `bgp_neighbor_inactive: list[str] = field(default_factory=list)`.

**4b) `_parse_bgp_neighbors` vrací dvojici.** Změň signaturu na
`-> tuple[list[str], list[str]]`, veď druhý seznam a nahraď guard:

```python
        neighbors: list[str] = []
        inactive: list[str] = []

        for bgp_node in node.xpath(bgp_xpath):
            for neighbor_node in bgp_node.xpath(
                ".//*[local-name()='neighbor']"
            ):
                neighbor = first_text(
                    neighbor_node,
                    "./*[local-name()='name']/text()",
                ) or first_text(neighbor_node, "./text()")

                if not neighbor:
                    continue

                if self._is_inactive(neighbor_node):
                    inactive.append(neighbor)
                else:
                    neighbors.append(neighbor)

        return unique(neighbors), unique(inactive)
```

**4c) Volající v `_parse_routing_instances`.** Dnes je to
`bgp_neighbors=self._parse_bgp_neighbors(node, "…")` uvnitř konstruktoru
`RoutingInstance`. Konstruktor volání dvojice nepobere, takže se výpočet
vytáhne **před** konstruktor a předají se obě pole:

```python
            instance_neighbors, instance_neighbors_inactive = (
                self._parse_bgp_neighbors(
                    node,
                    "./*[local-name()='protocols']"
                    "/*[local-name()='bgp']",
                )
            )
```

a v konstruktoru `bgp_neighbors=instance_neighbors,`
`bgp_neighbors_inactive=instance_neighbors_inactive,`.

**4d) `_parse_default_bgp_neighbors`.** V `__init__` přidej
`self.default_bgp_neighbors_inactive: list[str] = []` hned za
`self.default_bgp_neighbors`. Pak:

```python
        (
            self.default_bgp_neighbors,
            self.default_bgp_neighbors_inactive,
        ) = self._parse_bgp_neighbors(
            self.config_xml,
            "./*[local-name()='protocols']"
            "/*[local-name()='bgp']",
        )
```

**4e) `_assign_bgp_neighbors`.** Vedle `candidate_neighbors` veď
`candidate_inactive` ze stejných zdrojů
(`self.default_bgp_neighbors_inactive` pro Internet,
`instance.bgp_neighbors_inactive` pro VRF), profiltruj ho **týmž**
`self._bgp_neighbor_matches_interface` a přiřaď:

```python
            matched_inactive = [
                neighbor
                for neighbor in candidate_inactive
                if self._bgp_neighbor_matches_interface(neighbor, interface)
            ]

            if matched_inactive:
                service.bgp_neighbor_inactive = unique(
                    service.bgp_neighbor_inactive + matched_inactive
                )

            if not matched_neighbors:
                continue
```

**Pořadí je závazné:** přiřazení neaktivních musí být **nad** existujícím
`if not matched_neighbors: continue`. Pod ním by se služba, jejíž jediný
soused je deaktivovaný, přeskočila a nový seznam by zůstal prázdný — přesně
případ, který test ze Step 2 měří.

`service.protocol` ani `service.detection_reason` se z neaktivních
sousedů **neplní**. Rozšířit detekci služby o deaktivované peery by měnilo,
které služby se vůbec rozpoznají; to je jiná vlna.

**4f) `clean_service_dict`.** Do `ordered_keys` přidej
`"bgp_neighbor_inactive",` hned za `"bgp_neighbor",`. **Bez tohohle kroku
se klíč z YAML tiše ztratí** — funkce staví výstup z pevného seznamu klíčů,
ne z toho, co v dataclassu je.

- [ ] **Step 5: Spusť testy a ověř předpověď ze Step 1**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/ 2>&1 | tail -6
diff mx_parser.py evo_parser.py | wc -l
```

Očekávané: vše zelené včetně tří testů
(`test_deactivated_bgp_container_drops_neighbors_and_bfd`,
`test_inactive_bgp_group_drops_its_neighbors`,
`test_deactivated_top_level_protocols_drop_neighbors_and_bfd`), zámek `146`.

**Když některý z těch tří padne, platí tvoje měření, ne plán.** Předpověď
specu byla, že zůstanou zelené. Uprav je tak, aby tvrdily nové chování
(peer je v `bgp_neighbor_inactive`, ne že zmizel), a **zapiš do reportu, že
předpověď nevyšla** — je to nález, ne komplikace.

- [ ] **Step 6: Rozšiř ty tři testy a zafixuj chování BFD**

Ty tři testy zůstávají zelené, ale jejich název slibuje víc, než tvrdí:
„drops" už neplatí, soused se jen přestěhoval. Ke každému přidej aserci na
nový seznam a přejmenuj:

```python
@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_bgp_container_moves_neighbors_and_drops_bfd(module, parser_class):
    """<protocols>/<bgp> pod deaktivovanou VRF: soused se prestehuje, BFD zmizi.

    Zabiji mutanta: dedeni zavedene jen pro statiky a ne pro BGP.
    """
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    peers = [peer for service in services for peer in service.bgp_neighbor]
    inactive = [
        peer for service in services for peer in service.bgp_neighbor_inactive
    ]
    bfd = [intent for service in services for intent in service.bfd]

    assert peers == [], f"deaktivovany kontejner vyrobil zivy BGP zamer: {peers}"
    assert inactive, "deaktivovany soused se ztratil misto aby se prestehoval"
    assert bfd == [], f"deaktivovany kontejner vyrobil BFD zamer: {bfd}"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_inactive_bgp_group_moves_its_neighbors(module, parser_class):
    """Deaktivovana `group` nedá ziveho souseda, ale zamer neztrati.

    Zabiji mutanta: `_is_inactive` bez chuze po predcich. `neighbor` sam
    atribut nema.
    """
    services = _services(module, parser_class, INACTIVE_GROUP)
    peers = [peer for service in services for peer in service.bgp_neighbor]
    inactive = [
        peer for service in services for peer in service.bgp_neighbor_inactive
    ]

    assert peers == [], peers
    assert inactive, "deaktivovana group souseda ztratila misto prestehovani"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_top_level_protocols_move_neighbors_and_drop_bfd(
    module, parser_class
):
    """<protocols inactive> na top-level urovni: soused se prestehuje, BFD zmizi.

    Zabiji mutanta: `_is_inactive` bez chuze po predcich. K top-level
    kontejneru vede jina cesta nez k tomu pod routing-instances
    (_parse_default_bgp_neighbors), takze existujici testy tenhle mutant
    na tehle ceste nechyti.
    """
    services = _services(module, parser_class, DEACTIVATED_TOP_LEVEL_PROTOCOLS)
    service = _by_name(services, "ge-0/0/2.13")

    assert service.bgp_neighbor == []
    assert service.bgp_neighbor_inactive
    assert service.bfd == []
```

A nový test, který zafixuje chování BFD, aby se z něj nestala „vada" příští
vlny:

```python
BFD_INACTIVE_OVERRIDE = """
<configuration>
  <protocols>
    <bgp>
      <group>
        <name>G1</name>
        <bfd-liveness-detection>
          <minimum-interval>1000</minimum-interval>
        </bfd-liveness-detection>
        <neighbor>
          <name>192.0.2.3</name>
          <bfd-liveness-detection inactive="inactive">
            <minimum-interval>300</minimum-interval>
          </bfd-liveness-detection>
        </neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_inactive_bfd_override_inherits_group_value(module, parser_class):
    """Deaktivovany override BFD dedi hodnotu ze skupiny - a je to spravne.

    Junosi `inactive` znamena 'prikaz se neuplatni', tedy jako by tam nebyl.
    Skupinova hodnota se pak na souseda skutecne vztahuje a BFD na krabici
    opravdu bezi s minimum-interval 1000. Test to fixuje proto, ze to na
    prvni pohled vypada jako tiche podstrceni spatne hodnoty; bez nej by to
    nekdo pristi vlnu 'opravil' na 300 nebo na zadne BFD.
    """
    parser = parser_class(etree.XML(BFD_INACTIVE_OVERRIDE.encode()))
    parser.parse()
    intents = parser.default_bfd

    assert intents["192.0.2.3"]["minimum_interval"] == 1000
    assert intents["192.0.2.3"]["source"] == "group"
```

- [ ] **Step 7: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q | tail -3
```

Očekávané: `645 passed` (641 + 4 nové případy: 2 nové testy × 2 platformy).
Když ti vyjde jiné číslo, spočítej si, kolik testů jsi doopravdy přidal, a
**řiď se svým číslem**.

- [ ] **Step 8: Pusť mutanta**

```bash
.venv/bin/python - <<'EOF'
old = """                if self._is_inactive(neighbor_node):
                    inactive.append(neighbor)
                else:
                    neighbors.append(neighbor)"""
new = """                neighbors.append(neighbor)"""
for path in ("mx_parser.py", "evo_parser.py"):
    t = open(path, encoding="utf-8").read()
    assert t.count(old) == 1, (path, t.count(old))
    open(path, "w", encoding="utf-8").write(t.replace(old, new))
EOF
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q 2>&1 | tail -8
git checkout -- mx_parser.py evo_parser.py
```

`git diff --stat` musí být neprázdný. Očekávané: padnou nejméně
`test_deactivated_neighbor_lands_in_inactive_list` a tři přejmenované testy
ze Step 6. **Vlep skutečný výstup do reportu úlohy.**

- [ ] **Step 9: Zámek, ASCII, commit**

```bash
diff mx_parser.py evo_parser.py | wc -l
git diff --name-only | xargs -r -I{} sh -c 'printf "%-50s %s\n" {} $(grep -cP "[^\x00-\x7F]" {})'
.venv/bin/python -m pytest -o addopts="" -q | tail -2
git add -A
git commit -m "feat: deaktivovany BGP soused jde do paralelniho seznamu misto zahozeni"
```

Očekávané: `146`, prázdný grep, `645 passed`.

---

### Task 4: Příznak doteče z inventory do `Selectors` a do enginu

Až sem je nové chování jen v parseru. Tahle úloha ho protáhne na stranu
validátoru, aby na něj checky vůbec dosáhly.

**Files:**
- Modify: `migration_validator/models/inventory.py` (`ServiceEntry`)
- Modify: `migration_validator/models/scope.py` (`Selectors`)
- Modify: `migration_validator/scoping/builder.py`
- Modify: `migration_validator/engine.py` (`_unassigned_bgp_peers`, `_unassigned_bfd_sessions`)
- Modify: `tests/fixtures/172.20.20.{4,5}.yml`
- Test: `tests/models/test_inventory.py`, `tests/test_engine.py`

**Interfaces:**
- Consumes: klíč `bgp_neighbor_inactive` v inventory YAML (úloha 3), klíč
  `active` u statik (úloha 2)
- Produces: `Selectors.bgp_neighbors_inactive: list[str]`. Úloha 6 na něj
  staví. `Selectors.static_routes` už příznak nese uvnitř položek.

- [ ] **Step 1: Napiš failující testy**

Do `tests/models/test_inventory.py`:

```python
def test_entry_reads_inactive_bgp_neighbors(tmp_path):
    """Deaktivovany soused se cte do vlastniho seznamu, ne do zivych."""
    path = tmp_path / "inv.yml"
    path.write_text(
        "schema_version: 5\n"
        "device: dev\n"
        "interfaces:\n"
        "- interface: ge-0/0/2.13\n"
        "  service_type: Internet\n"
        "  bgp_neighbor: [198.11.13.2]\n"
        "  bgp_neighbor_inactive: [198.11.13.9]\n",
        encoding="utf-8",
    )
    entry = load_inventory(str(path)).entries[0]

    assert entry.bgp_neighbor == ["198.11.13.2"]
    assert entry.bgp_neighbor_inactive == ["198.11.13.9"]
```

Do `tests/test_engine.py`:

```python
def test_inactive_peer_is_assigned_not_unassigned():
    """Ziva session deaktivovaneho peera patri sve sluzbe, ne do NEZARAZENO.

    Deaktivovany peer je porad peer teto sluzby - kdyz pro nej presto prijde
    session, je to nalez o teto sluzbe. Spadnout do NEZARAZENO by ten vztah
    zahodilo.

    Zabiji mutanta: `assigned` postavene jen z `bgp_neighbors`.
    """
    scope = Scope(
        id="s1",
        kind="service",
        key=ScopeKey(service_type="Internet"),
        selectors=Selectors(bgp_neighbors_inactive=["198.11.13.9"]),
    )
    snapshot = _snapshot_with_bgp({"198.11.13.9": {"state": "Established"}})

    assert _unassigned_bgp_peers(snapshot, [scope]) == []
```

**Pomocníky si přizpůsob souboru.** `tests/test_engine.py` už nějaký způsob
stavby snapshotu má — použij ten, `_snapshot_with_bgp` je jméno z plánu, ne
z repa. Když nic vhodného není, postav `Snapshot` přímo a fakta dej do
`facts["bgp"]`.

- [ ] **Step 2: Spusť a ověř, že padají**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/models/test_inventory.py tests/test_engine.py 2>&1 | tail -6
```

Očekávané: oba nové testy FAIL.

- [ ] **Step 3: `ServiceEntry`**

V `migration_validator/models/inventory.py` za
`bgp_neighbor: list[str] = field(default_factory=list)` přidej:

```python
    # Deaktivovany soused. Drzi se zvlast od bgp_neighbor, protoze ten
    # slouzi jako selektor merenych session; slit je do jednoho seznamu by
    # znamenalo drzet je v synchronu.
    bgp_neighbor_inactive: list[str] = field(default_factory=list)
```

V `from_dict` přidej
`bgp_neighbor_inactive=_as_list(data.get("bgp_neighbor_inactive")),` a v
`to_dict` `"bgp_neighbor_inactive": list(self.bgp_neighbor_inactive),`.

- [ ] **Step 4: `Selectors`**

V `migration_validator/models/scope.py` za `bgp_neighbors` přidej
`bgp_neighbors_inactive: list[str] = field(default_factory=list)` a do
`to_dict` `"bgp_neighbors_inactive": list(self.bgp_neighbors_inactive),`.

`Selectors.from_dict` je `cls(**{name: list(data.get(name, [])) for name in
cls().to_dict()})` — čte klíče z `to_dict`, takže se **nemění**.

- [ ] **Step 5: `builder.py`**

Za `bgp_neighbors=list(entry.bgp_neighbor),` přidej
`bgp_neighbors_inactive=list(entry.bgp_neighbor_inactive),`.

- [ ] **Step 6: `engine.py` — obě funkce**

V `_unassigned_bgp_peers` i `_unassigned_bfd_sessions` nahraď řádek

```python
    assigned = {peer for scope in scopes for peer in scope.selectors.bgp_neighbors}
```

za

```python
    # Deaktivovany peer je porad peer sve sluzby. Kdyz pro nej presto prijde
    # session, je to nalez o teto sluzbe - do NEZARAZENO patri jen peer,
    # ktery ke zadne sluzbe nesedi.
    assigned = {
        peer
        for scope in scopes
        for peer in (
            *scope.selectors.bgp_neighbors,
            *scope.selectors.bgp_neighbors_inactive,
        )
    }
```

Je to na dvou místech (`engine.py` ~169 a ~223) a na obou stejně.

- [ ] **Step 7: Doplň klíč do fixtures**

```bash
.venv/bin/python - <<'EOF'
import re
for path in ("tests/fixtures/172.20.20.4.yml", "tests/fixtures/172.20.20.5.yml"):
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    out, added = [], 0
    for line in lines:
        out.append(line)
        # Pokryje jen inline tvar `bgp_neighbor: []`. Blokovy tvar
        # (`bgp_neighbor:` a pod tim `  - adresa`) skript zamerne nechava
        # byt - kontrola pod skriptem ho odhali a doplni se rucne.
        if re.match(r"^  bgp_neighbor:", line) and line.rstrip().endswith("[]"):
            out.append("  bgp_neighbor_inactive: []\n")
            added += 1
    open(path, "w", encoding="utf-8").write("".join(out))
    print(path, "doplneno", added)
EOF
grep -c "bgp_neighbor_inactive" tests/fixtures/172.20.20.4.yml
grep -c "bgp_neighbor:" tests/fixtures/172.20.20.4.yml
```

**Ta dvě čísla se musí rovnat.** Když ne, část položek `bgp_neighbor` je
psaná blokově (`bgp_neighbor:` a pod tím `  - adresa`) a skript je
nepokryl — doplň je ručně, klíč patří **za** poslední položku seznamu, na
stejné odsazení jako `bgp_neighbor:`.

- [ ] **Step 8: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q | tail -3
```

Očekávané: `647 passed` (645 + 2 nové).

- [ ] **Step 9: Pusť mutanta na engine**

```bash
.venv/bin/python - <<'EOF'
old = """        for peer in (
            *scope.selectors.bgp_neighbors,
            *scope.selectors.bgp_neighbors_inactive,
        )"""
new = """        for peer in scope.selectors.bgp_neighbors"""
path = "migration_validator/engine.py"
t = open(path, encoding="utf-8").read()
assert t.count(old) == 2, t.count(old)
open(path, "w", encoding="utf-8").write(t.replace(old, new))
EOF
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q 2>&1 | tail -4
git checkout -- migration_validator/engine.py
```

Očekávané: padne `test_inactive_peer_is_assigned_not_unassigned`. Vlep
výstup do reportu.

- [ ] **Step 10: Commit**

```bash
git diff --name-only | xargs -r -I{} sh -c 'printf "%-50s %s\n" {} $(grep -cP "[^\x00-\x7F]" {})'
.venv/bin/python -m pytest -o addopts="" -q | tail -2
git add -A
git commit -m "feat: priznak deaktivace protece z inventory do Selectors a enginu"
```

---

### Task 5: `checks/routes.py` dá SKIP za deaktivovanou statiku

**Files:**
- Modify: `migration_validator/checks/routes.py` (`StaticRouteStatusCheck.run`, `_finding`)
- Test: `tests/checks/test_routes.py`, `tests/reporting/` (test nad vyrenderovaným reportem)

`_finding` dostane **povinný** parametr `deactivated`. Je to bezpečné:
`grep -rn "_finding(" tests/` vrací **prázdno**, jediný volající je `run`
ve stejné třídě. Ověřeno při psaní plánu; kdyby ti grep vrátil něco jiného,
platí tvoje měření.

**Interfaces:**
- Consumes: `Selectors.static_routes` — položky nesou klíč `active` (úloha 2)
- Produces: `Finding(Outcome.SKIP, …, value="deaktivovana")` pro deaktivovanou
  routu

**Kde přesně se větev vkládá:** v `_finding`, **před** větev
`if subject is None:`. Tím se pokryje případ „deaktivovaná a v tabulce není",
což je ten obvyklý. Když deaktivovaná routa v tabulce **přesto je**, SKIP se
nevydá a nález se chová jako dosud — je to rozpor konfigurace se stavem a má
být vidět.

- [ ] **Step 1: Napiš failující test nad objekty**

Do `tests/checks/test_routes.py` (přizpůsob se tomu, jak soubor staví
`CheckContext` — plán jména pomocníků nezná):

```python
def test_deactivated_route_yields_skip_and_sibling_stays_ok():
    """Deaktivovana routa preskoci na svem radku a sourozence nestrhne.

    Tohle je vlastnost, kterou nazev 'per-radkovy SKIP' slibuje: kdyby SKIP
    hlasoval, cela sluzba by zesedla a zdrava routa vedle by prestala byt
    videt. `engine.py` SKIPy odfiltruje pred Status.worst(), takze staci,
    aby check vydal SKIP jen na tom jednom nalezu.
    """
    ctx = _context(
        static_routes=[
            {"rib": "inet.0", "prefix": "10.0.0.0/8", "next_hop": ["1.1.1.1"],
             "active": False},
            {"rib": "inet.0", "prefix": "10.1.0.0/16", "next_hop": ["2.2.2.2"],
             "active": True},
        ],
        subject_routes={"inet.0": {"10.1.0.0/16": {"active": True, "nh": ["2.2.2.2"]}}},
    )
    findings = StaticRouteStatusCheck().run(ctx)
    by_prefix = {finding.label: finding for finding in findings}

    assert by_prefix["inet.0 10.0.0.0/8"].outcome is Outcome.SKIP
    assert by_prefix["inet.0 10.0.0.0/8"].value == "deaktivovana"
    assert by_prefix["inet.0 10.1.0.0/16"].outcome is Outcome.OK
```

Tvar `subject_routes` si **ověř v `collectors/routes.py`** — plán ho uvádí
přibližně a platí to, co vyrábí collector.

- [ ] **Step 2: Napiš druhý test — nad vyrenderovaným reportem**

Test nad datovou strukturou **neměří, co se vykreslí**. Vlna 5 na tenhle
šev narazila třikrát. Proto k němu patří test nad výstupem, a hledá se
**uvnitř sekce**, ne `"x" in output`.

**Proč `detail=True`, a co z toho plyne** — je to vědomé, ne z lenosti.
Změřeno v kódu reportu: `text_report.py:390` rozbaluje blok služby jen když
`detail or view.status is not Status.PASS`, a `engine.py:145` SKIPy
odfiltruje před hlasováním `Status.worst()`. Zdravá služba s jednou
deaktivovanou routou tedy zůstane **PASS a v základním výhledu se
nerozbalí** — řádek existuje, ale operátor ho uvidí až pod `--detail` nebo v
JSON.

Není to vada téhle vlny, je to platný návrh reportu (blok se rozbaluje na
stav, ne na obsah) a **měnit ho tady by znamenalo, že SKIP zase strhává
službu** — přesně to, čemu se úloha vyhýbá. Zapiš to do „Co zbývá" roadmapy
vlny 8 jako otázku pro reportovou vlnu: *má se deaktivovaný prvek nějak
projevit i na sbaleném řádku služby?* Rozhodnutí patří uživateli, ne téhle
vlně.

```python
def test_deactivated_route_renders_as_skip_in_its_section():
    """Report musi SKIP ukazat, ne ho jen mit v datech.

    Hleda se uvnitr sekce statickych rout, ne kdekoliv ve vystupu: retezec
    'deaktivovana' se objevi i u deaktivovane sluzby, takze `in output` by
    prosel i kdyby se radek routy vubec nevykreslil.
    """
    output = render(result, detail=True)
    section = _section(output, "Staticke routy")

    assert "SKIP" in section
    assert "deaktivovana" in section
```

`_section` je pomocník, který ze `output` vyřízne řádky mezi hlavičkou
skupiny a další hlavičkou. **Podívej se do `tests/reporting/`, jestli tam
už takový pomocník je** (vlna 5 podobný zaváděla) — když ano, použij ho,
nový nepiš.

- [ ] **Step 3: Spusť oba a ověř, že padají**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/checks/test_routes.py tests/reporting/ 2>&1 | tail -6
```

- [ ] **Step 4: Implementuj**

V `run` se dnes staví `configured` jako množina identit. Přidej vedle ní
mapu příznaků a předej ji do `_finding`:

```python
        configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in ctx.scope.selectors.static_routes
        }
        # Chybejici klic 'active' znamena zamer od parseru pred vlnou 8.
        # Snapshot i inventory maji od te vlny schema 5, takze se takovy
        # zamer nenacte - default je tu jen proto, aby jednotkovy test
        # nemusel psat klic, ktery netestuje.
        deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in ctx.scope.selectors.static_routes
            if route.get("active", True) is False
        }
```

a v cyklu `deactivated=identity in deactivated,`.

V `_finding` přidej parametr `deactivated: bool` a **před** větev
`if subject is None:` vlož:

```python
        if deactivated and subject is None:
            # Deaktivovanou routu operator vedome vyradil, takze v tabulce
            # byt nema. Bez teto vetve by spadla do 'nakonfigurovana, ale
            # neni v routovaci tabulce' a dala tvrdy FAIL za stav, ktery je
            # v poradku.
            #
            # Kdyz deaktivovana routa v tabulce presto je, sem se nedostane
            # a nalez se chova jako dosud - to uz je skutecny rozpor
            # konfigurace se stavem a ma byt videt.
            return Finding(
                Outcome.SKIP,
                f"{rib} {prefix}: routa je v konfiguraci deaktivovana",
                label=label,
                group=group,
                family=family,
                value="deaktivovana",
                baseline_value=was,
                baseline=baseline,
            )
```

- [ ] **Step 5: Spusť testy**

```bash
.venv/bin/python -m pytest -o addopts="" -q | tail -3
```

Očekávané: `649 passed`.

- [ ] **Step 6: Pusť dva mutanty**

První ověřuje, že SKIP vůbec hlídá někdo:

```bash
.venv/bin/python - <<'EOF'
old = "        if deactivated and subject is None:"
new = "        if False:"
path = "migration_validator/checks/routes.py"
t = open(path, encoding="utf-8").read()
assert t.count(old) == 1, t.count(old)
open(path, "w", encoding="utf-8").write(t.replace(old, new))
EOF
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q 2>&1 | tail -5
git checkout -- migration_validator/checks/routes.py
```

Druhý míří přesně na to, co slibuje název „sourozence nestrhne" — bez něj
by tu vlastnost nehlídal nikdo:

```bash
.venv/bin/python - <<'EOF'
old = '''            if route.get("active", True) is False'''
new = '''            if True'''
path = "migration_validator/checks/routes.py"
t = open(path, encoding="utf-8").read()
assert t.count(old) == 1, t.count(old)
open(path, "w", encoding="utf-8").write(t.replace(old, new))
EOF
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q 2>&1 | tail -5
git checkout -- migration_validator/checks/routes.py
```

Oba `git diff --stat` musí být neprázdné. **Vlep oba výstupy do reportu.**
Když druhý mutant nikoho nezabije, aserce o sourozenci neměří, co má —
oprav test, ne mutanta.

- [ ] **Step 7: Commit**

```bash
git diff --name-only | xargs -r -I{} sh -c 'printf "%-50s %s\n" {} $(grep -cP "[^\x00-\x7F]" {})'
git add -A
git commit -m "feat: deaktivovana statika dava SKIP na svem radku"
```

---

### Task 6: `checks/bgp.py` dá SKIP za deaktivovaného peera

**Files:**
- Modify: `migration_validator/checks/bgp.py` (`BgpSessionStatusCheck.run`)
- Test: `tests/checks/test_bgp.py`, `tests/reporting/`

**Interfaces:**
- Consumes: `Selectors.bgp_neighbors_inactive` (úloha 4)
- Produces: nic, co by další úloha potřebovala

**Dvě věci k opravě, ne jedna:**

1. `run` iteruje přes **naměřené** peery (`ctx.subject["bgp"]`), takže
   deaktivovaný peer bez session se do iterace nikdy nedostane. Doplní se
   nálezy za peery z `bgp_neighbors_inactive`, které session nemají.
2. Předčasný návrat `if not peers:` — služba, jejíž **jediný** peer je
   deaktivovaný, dnes dostane jeden nález „sluzba nema zadne BGP peery".
   Nově musí dostat per‑peer SKIP.

**Vědomé omezení:** vlna **nemění**, co se stane s **aktivním**
nakonfigurovaným peerem bez session — ten zůstává v reportu neviditelný.
Sjednotit `checks/bgp.py` se statikami (které iterují přes sjednocení tří
zdrojů) je samostatná práce a patří do „Co zbývá" roadmapy vlny 8.

- [ ] **Step 1: Napiš failující testy nad objekty**

```python
def test_deactivated_peer_without_session_yields_skip():
    """Deaktivovany peer bez session se v reportu objevi jako SKIP.

    Bez teto vetve by z reportu zmizel uplne a operator by nepoznal, ze
    sluzba takoveho peera v konfiguraci vubec ma.
    """
    ctx = _context(
        bgp_neighbors=["198.11.13.2"],
        bgp_neighbors_inactive=["198.11.13.9"],
        subject_bgp={"198.11.13.2": {"state": "Established"}},
    )
    findings = BgpSessionStatusCheck().run(ctx)
    by_peer = {finding.label: finding for finding in findings}

    assert by_peer["BGP status (198.11.13.9)"].outcome is Outcome.SKIP
    assert by_peer["BGP status (198.11.13.2)"].outcome is Outcome.OK


def test_service_with_only_deactivated_peers_skips_per_peer():
    """Jediny peer sluzby je deaktivovany - nesmi to spadnout do 'zadny peer'.

    Zabiji mutanta: ponechany predcasny navrat `if not peers:`.
    """
    ctx = _context(
        bgp_neighbors=[],
        bgp_neighbors_inactive=["198.11.13.9"],
        subject_bgp={},
    )
    findings = BgpSessionStatusCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
    assert findings[0].value == "deaktivovan"


def test_deactivated_peer_with_live_session_is_reported_normally():
    """Deaktivovany peer, ktery presto bezi, neni SKIP - je to rozpor.

    Symetricke se statikami: konfigurace rika 'vypnuto', tabulka rika
    'bezi', a prave to ma byt videt.
    """
    ctx = _context(
        bgp_neighbors=[],
        bgp_neighbors_inactive=["198.11.13.9"],
        subject_bgp={"198.11.13.9": {"state": "Established"}},
    )
    findings = BgpSessionStatusCheck().run(ctx)

    assert findings[0].outcome is not Outcome.SKIP
```

Přesný tvar `label` (`"BGP status (…)"`) si **ověř v `checks/bgp.py`** —
plán ho cituje z popisu v roadmapě vlny 7, ne z kódu.

- [ ] **Step 2: Napiš test nad vyrenderovaným reportem**

```python
def test_deactivated_peer_renders_as_skip_in_its_family_section():
    """Radek deaktivovaneho peera se musi vykreslit, ne jen existovat v datech."""
    output = render(result, detail=True)
    section = _section(output, "IPv4")

    assert "SKIP" in section
    assert "deaktivovan" in section
```

Použij tentýž pomocník `_section` jako v úloze 5. `detail=True` je i tady
vědomé — důvod a jeho důsledek pro operátora viz úloha 5, Step 2.

- [ ] **Step 3: Spusť a ověř, že padají**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/checks/test_bgp.py tests/reporting/ 2>&1 | tail -8
```

- [ ] **Step 4: Implementuj**

Nahraď předčasný návrat a doplň cyklus za stávající:

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        inactive = [
            peer
            for peer in ctx.scope.selectors.bgp_neighbors_inactive
            if peer not in peers
        ]

        # Poradi je soucast pozadavku: sluzba, jejiz jediny peer je
        # deaktivovany, nesmi dostat 'nema zadne BGP peery' - to by tvrdilo,
        # ze v konfiguraci zadny neni.
        if not peers and not inactive:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery", value="zadny peer")]
```

a na konec `run`, před `return findings`:

```python
        # Deaktivovany peer, pro ktery presto prisla session, se sem
        # nedostane (filtr `peer not in peers` vys) a projde normalni vetvi -
        # je to rozpor konfigurace se stavem a ma byt videt.
        for peer in sorted(inactive):
            findings.append(
                Finding(
                    Outcome.SKIP,
                    f"peer {peer} je v konfiguraci deaktivovan",
                    label=f"BGP status ({peer})",
                    family=peer_family(peer),
                    value="deaktivovan",
                )
            )
```

`peer_family` je jméno z plánu. **Podívej se, jak rodinu určuje existující
cyklus** (vlna 5 zavedla `migration_validator/addressing.py`) a použij
totéž — bez rodiny by řádek spadl do bezhlavičkové sekce nad IPv4 i IPv6.
Stejně tak `group`: když ho aktivní peeři nesou, nastav ho i tady.

- [ ] **Step 5: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q | tail -3
```

Očekávané: `653 passed`.

- [ ] **Step 6: Pusť dva mutanty**

```bash
.venv/bin/python - <<'EOF'
old = "        if not peers and not inactive:"
new = "        if not peers:"
path = "migration_validator/checks/bgp.py"
t = open(path, encoding="utf-8").read()
assert t.count(old) == 1, t.count(old)
open(path, "w", encoding="utf-8").write(t.replace(old, new))
EOF
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q 2>&1 | tail -5
git checkout -- migration_validator/checks/bgp.py
```

```bash
.venv/bin/python - <<'EOF'
old = "            if peer not in peers"
new = "            if True"
path = "migration_validator/checks/bgp.py"
t = open(path, encoding="utf-8").read()
assert t.count(old) == 1, t.count(old)
open(path, "w", encoding="utf-8").write(t.replace(old, new))
EOF
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q 2>&1 | tail -5
git checkout -- migration_validator/checks/bgp.py
```

První musí zabít `test_service_with_only_deactivated_peers_skips_per_peer`,
druhý `test_deactivated_peer_with_live_session_is_reported_normally`.
**Vlep oba výstupy do reportu.**

- [ ] **Step 7: Vyrenderuj ostrý report a podívej se na něj očima**

Postup je tu napsaný celý schválně, ne odkazem:

1. Založ dočasný soubor `tests/test_tmp_render.py` (**musí** být v `tests/`,
   aby se na něj vztáhl fixture `synthetic_snapshot` z `tests/conftest.py`).
2. V testu si vyrob dvojici snapshotů
   `synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")` a
   `synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")` (konstanty
   `DEVICE_4`/`DEVICE_5` viz `tests/test_end_to_end.py:12-14`), prožeň je
   `api.evaluate(new, baseline=old, now=NOW)` a výsledek předej
   `migration_validator.reporting.text_report.render(result, detail=True)`.
3. Výstup vytiskni a spusť
   `.venv/bin/python -m pytest -o addopts="" -s -q tests/test_tmp_render.py`.
4. **Soubor po přečtení smaž** — je jednorázový a nesmí se objevit v diffu.

Sdílené fixtures deaktivovaný prvek **nenesou**, takže žádný nový SKIP
neuvidíš. To je očekávané. Co se ověřuje: že report vypadá **beze změny**
proti vlně 7 — žádný nový řádek, žádná zmizelá routa. Kdyby se změnil, je to
regrese. Zapiš do reportu, co jsi viděl.

- [ ] **Step 8: Commit**

```bash
git diff --name-only | xargs -r -I{} sh -c 'printf "%-50s %s\n" {} $(grep -cP "[^\x00-\x7F]" {})'
git status --short   # tests/test_tmp_render.py tu nesmi byt
.venv/bin/python -m pytest -o addopts="" -q | tail -2
git add -A
git commit -m "feat: deaktivovany BGP peer dava SKIP misto aby z reportu zmizel"
```

---

## Závěrečné ověření větve

Až jsou všechny úlohy hotové, **před** whole-branch review:

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts="" | tail -3   # zelene, 0 skipped
diff mx_parser.py evo_parser.py | wc -l              # 146
# znakova sada: viz Global Constraints, ridi se souborem
grep -rn "schema_version" tests/fixtures/*.yml       # obojí 5
git status --short                                   # cisty strom
```

**Kdyby se ve stromu objevil neverzovaný `mx1-pop1.yml`:** je to únik z
testů, které inventory zapisují do aktuálního adresáře místo do `tmp_path`.
Při psaní tohohle plánu se objevil jednou, a to během běhu nad **rozbitými**
parsery (mutant); nad zdravým stromem se nereprodukoval. Smaž ho, necommituj
a **zapiš do roadmapy jako nový bod** — s vlnou 8 nesouvisí a opravovat ho
tady by bylo šíření rozsahu.

Whole-branch review má hledat právě to, co per‑úlohová review vidět nemohla.
Dvě konkrétní věci, na které se u téhle vlny ptát:

1. **Nemá některý test název, který slibuje víc než jeho aserce?** Vlna 7 to
   zaplatila (`test_dual_rib_peer_yields_two_distinguishable_blocks` tvrdil
   jen jména RIB). Recept: pusť mutanta právě na tu vlastnost, kterou název
   slibuje. Zvlášť podezřelé jsou tady přejmenované testy z úlohy 3 a
   `test_deactivated_route_yields_skip_and_sibling_stays_ok` z úlohy 5.
2. **Tvrzení úloh 2 a 3 o produkčních mutantech nejde ověřit z diffu té
   úlohy**, když je diff test-only. Tady test-only nejsou, ale platí obecně:
   co reviewer ověřit nemůže, **musí doměřit někdo mimo review té úlohy**.
