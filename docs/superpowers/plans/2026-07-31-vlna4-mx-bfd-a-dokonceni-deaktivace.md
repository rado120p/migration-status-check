# Vlna 4: MX BFD na živých datech a dokončení deaktivace — implementační plán

> **Pro agentní pracovníky:** POVINNÝ SUB-SKILL: použij superpowers:subagent-driven-development (doporučeno) nebo superpowers:executing-plans a proveď plán úlohu po úloze. Kroky používají checkboxy (`- [ ]`).

**Cíl:** Pokrýt MX BFD reálnými daty z laborky, smazat tři guardy, které stíní dědění deaktivace, a doplnit testy, které tím teprve začnou něco měřit.

**Architektura:** Žádná nová komponenta. Tři druhy zásahu: (1) regenerace artefaktů z laborky, (2) smazání redundantního kódu ve dvou parserech držených v zámku, (3) doplnění testů a jedna změna chování checku statik.

**Tech stack:** Python 3.13, pytest, lxml, PyEZ (`jnpr.junos`), YAML inventory schema 4.

**Spec:** [`../specs/2026-07-31-mx-bfd-a-dokonceni-deaktivace-design.md`](../specs/2026-07-31-mx-bfd-a-dokonceni-deaktivace-design.md)

## Globální omezení

- **Zámek parserů:** `diff mx_parser.py evo_parser.py | wc -l` musí vracet **146** po každé změně parseru, ne až na konci.
- **Výchozí stav sady:** 588 passed, 1 skipped. Cílový stav po úloze 4: **590 passed, 0 skipped**.
- **Testovací příkaz:** `.venv/bin/python -m pytest -o addopts="" -q` (bez `-o addopts=""` se přidá coverage a výstup je nečitelný).
- **Každý test musí říct, kterou špatnou implementaci zabíjí**, a mutant se pouští, ne popisuje. Parametrizovaný test = mutant na každou větev.
- **Mutantí bloky končí `git checkout <soubor>`, takže se pouští až PO commitu.** Před commitem si tím implementace smaže vlastní práci.
- **Laborka se během vlny nesmí měnit.** Na `172.20.20.4` musí být deaktivované právě `ge-0/0/3` a instance `EVPN-VPWS-CPE24-UNI`, nic jiného.
- **Přístup do laborky:** `172.20.20.4`, uživatel `admin`, autentizace **heslem**. `MIG_LAB_PASSWORD` je v `~/.bashrc` pod stráží na neinteraktivní shell — načíst přes `eval "$(grep -h MIG_LAB_PASSWORD ~/.bashrc)"`.
- **Větev:** `vlna4-mx-bfd-a-dokonceni-deaktivace`, už existuje a spec je na ní commitnutý.

## Struktura souborů

| soubor | odpovědnost | úlohy |
|---|---|---|
| `tests/parsers/test_inactive.py` | offline testy dědění deaktivace, oba parsery | 1, 2, 3 |
| `mx_parser.py`, `evo_parser.py` | parsery v zámku — mažou se tři guardy | 2 |
| `172.20.20.4.yml`, `tests/fixtures/172.20.20.4.yml` | regenerovaná inventory (bajtově shodné kopie) | 4 |
| `tests/fixtures/rpc/junos/*.xml` | regenerované nahrávky RPC | 4 |
| `tests/collectors/test_bfd.py` | testy BFD collectoru | 4, 5 |
| `tests/collectors/test_conformance.py` | šev collector → check | 5 |
| `migration_validator/checks/routes.py` | check statických rout | 6 |
| `tests/checks/test_routes.py` | testy checku statik | 6, 7 |

## Pořadí úloh a proč

Úloha 1 musí předcházet úloze 2 — jinak se maže guard, jehož jedinou pojistkou by byl test, který ještě neexistuje. Úloha 4 musí předcházet úloze 5, protože testy úlohy 5 čtou nahrávku, kterou vyrábí úloha 4. Úlohy 3, 6 a 7 jsou nezávislé.

---

### Task 1: Deaktivovaná jednotka pod aktivním rozhraním

Mutant „úroveň jednotky se ignoruje" dnes přežije **celou sadu** (588 passed). Žádná fixture nemá `<unit inactive="inactive">` pod aktivním fyzickým rozhraním — všechny deaktivují až fyzické rozhraní. Tahle úloha tu díru zalátá, aby úloha 2 měla o co opřít mazání.

**Soubory:**
- Modify: `tests/parsers/test_inactive.py` (přidat fixture za `DEACTIVATED_CONTAINERS` a test na konec souboru)

**Rozhraní:**
- Konzumuje: `_services(module, parser_class, xml_text)`, `_by_name(services, name)`, `PARSERS` — všechno už v souboru je
- Produkuje: nic, co by další úlohy volaly

- [ ] **Krok 1: Napiš padající test**

Do `tests/parsers/test_inactive.py` přidej fixture a test:

```python
DEACTIVATED_UNIT_ONLY = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit inactive="inactive">
        <name>113</name>
        <description>L3VPN-CPE13-NNI</description>
        <family><inet><address><name>198.11.13.1/30</name></address></inet></family>
      </unit>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family><inet><address><name>152.11.13.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_unit_under_active_interface_is_flagged(module, parser_class):
    """Deaktivace jednotky nesmí spadnout ani nahoru, ani na sousední jednotku.

    Zabíjí mutanta: `active=not physical_inactive` v _parse_interfaces, tedy
    "úroveň jednotky se ignoruje". Ten dnes přežije celou sadu - všechny
    ostatní fixtures deaktivují až fyzické rozhraní, takže se zděděná
    a vlastní deaktivace nedají rozlišit.
    """
    services = _services(module, parser_class, DEACTIVATED_UNIT_ONLY)

    assert _by_name(services, "ge-0/0/2").interface_active is True
    assert _by_name(services, "ge-0/0/2.113").interface_active is False
    assert _by_name(services, "ge-0/0/2.13").interface_active is True
```

- [ ] **Krok 2: Spusť ho — musí PROJÍT**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/test_inactive.py -k unit_under_active
```

Očekávej: **2 passed**. Tohle je regresní test, ne TDD červená — chování je správné už dnes, chybí jen jeho pojistka. Kdyby padl, je to nález: parser dědí deaktivaci špatným směrem a plán se zastaví.

- [ ] **Krok 3: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej: **590 passed, 1 skipped** (588 + 2 nové parametrizace).

- [ ] **Krok 4: Commit**

```bash
git add tests/parsers/test_inactive.py
git commit -m "test(parsers): deaktivovana jednotka pod aktivnim rozhranim (AR-34b)"
```

- [ ] **Krok 5: Mutant — až po commitu**

```bash
sed -i 's/active=not (physical_inactive or self._is_inactive(unit_node)),/active=not physical_inactive,/' mx_parser.py evo_parser.py
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/test_inactive.py -k unit_under_active
git checkout mx_parser.py evo_parser.py
```

Očekávej: **2 failed** (`evo` i `mx`). Když mutant přežije, test neměří to, co slibuje — zastav se a nepokračuj na úlohu 2.

---

### Task 2: Smazat tři guardy, které stíní dědění

Tři místa volají `_is_inactive` na uzlu, který atribut `inactive` nese přímo. Po AR‑19 jsou redundantní — a zároveň **stíní** chůzi po předcích: vrátí `True` i tehdy, kdyby se dědění rozbilo. Dokud tam jsou, žádný test dědění na těchto cestách neměří.

Změřeno 2026‑07‑31 mutantem `_is_inactive` → `return self._node_is_inactive(node)`: s guardy 8 padlých testů v `test_inactive.py`, po smazání disjunktu 10, a testy na `rib`/`group` začnou zabíjet mutanta až po smazání guardů.

**Soubory:**
- Modify: `mx_parser.py:697`, `mx_parser.py:817`, `mx_parser.py:1103`
- Modify: `evo_parser.py:697`, `evo_parser.py:817`, `evo_parser.py:1103`
- Modify: `tests/parsers/test_inactive.py` (dvě fixtures + dva testy)

**Rozhraní:**
- Konzumuje: `_services`, `_by_name`, `PARSERS` z `test_inactive.py`
- Produkuje: nic

- [ ] **Krok 1: Napiš testy na `rib` a `group`**

Do `tests/parsers/test_inactive.py`:

```python
INACTIVE_RIB = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/4</name>
      <unit>
        <name>0</name>
        <description>L3VPN-CPE14-UNI</description>
        <family><inet><address><name>198.11.14.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE14-UNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>ge-0/0/4.0</name></interface>
      <routing-options>
        <rib inactive="inactive">
          <name>L3VPN-CPE14-UNI.inet.0</name>
          <static>
            <route><name>10.8.8.0/24</name><next-hop>198.11.14.2</next-hop></route>
          </static>
        </rib>
        <static>
          <route><name>10.9.9.0/24</name><next-hop>198.11.14.2</next-hop></route>
        </static>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""

INACTIVE_GROUP = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family><inet><address><name>152.11.13.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <protocols>
    <bgp>
      <group inactive="inactive">
        <name>CPE13</name>
        <neighbor><name>152.11.13.2</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_inactive_rib_drops_only_its_own_routes(module, parser_class):
    """Deaktivovaný `rib` vypustí své statiky a sousední `static` nechá být.

    Zabíjí mutanta (až po smazání guardu na rib_node): `_is_inactive` bez
    chůze po předcích. Routa uvnitř `rib` sama atribut nemá, takže bez dědění
    unikne do záměru.

    Next-hop uvnitř deaktivovaného `rib` musí ležet v subnetu rozhraní -
    jinak ji `_assign_static_routes` ke službě nepřipne a test by prošel
    i pod mutantem, protože by únik neviděl.
    """
    services = _services(module, parser_class, INACTIVE_RIB)
    routes = [route for service in services for route in service.static_route]

    assert [route["prefix"] for route in routes] == ["10.9.9.0/24"], routes


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_inactive_bgp_group_drops_its_neighbors(module, parser_class):
    """Deaktivovaná `group` nedá souseda.

    Zabíjí mutanta (až po smazání guardu na group_node): `_is_inactive` bez
    chůze po předcích. `neighbor` sám atribut nemá.
    """
    services = _services(module, parser_class, INACTIVE_GROUP)
    peers = [peer for service in services for peer in service.bgp_neighbor]

    assert peers == [], peers
```

- [ ] **Krok 2: Spusť je — musí PROJÍT (guardy jsou ještě na místě)**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/test_inactive.py -k "inactive_rib or inactive_bgp_group"
```

Očekávej: **4 passed**.

- [ ] **Krok 3: Smaž guard na `unit` v obou parserech**

```bash
sed -i 's/active=not (physical_inactive or self._is_inactive(unit_node)),/active=not self._is_inactive(unit_node),/' mx_parser.py evo_parser.py
diff mx_parser.py evo_parser.py | wc -l
```

Očekávej: `146`.

- [ ] **Krok 4: Smaž guardy na `rib` a `group` v obou parserech**

V `mx_parser.py` i `evo_parser.py` odstraň tyto dva bloky (v obou souborech jsou na stejných řádcích):

```python
            if self._is_inactive(rib_node):
                continue

```

```python
                if self._is_inactive(group_node):
                    continue

```

Zůstane tedy `for rib_node in …:` následované rovnou `rib_name = first_text(` a `for group_node in …:` následované rovnou `containers.append(`.

- [ ] **Krok 5: Zkontroluj zámek a spusť celou sadu**

```bash
diff mx_parser.py evo_parser.py | wc -l
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej: `146` a **594 passed, 1 skipped** (590 z úlohy 1 + 4 nové parametrizace).

- [ ] **Krok 6: Commit**

```bash
git add mx_parser.py evo_parser.py tests/parsers/test_inactive.py
git commit -m "refactor(parsers): smazat tri guardy stinici dedeni deaktivace (AR-34)"
```

- [ ] **Krok 7: Mutant — až po commitu**

```bash
python3 - <<'EOF'
import pathlib
old = """        current: etree._Element | None = node

        while current is not None:
            if self._node_is_inactive(current):
                return True
            current = current.getparent()

        return False"""
new = """        return self._node_is_inactive(node)"""
for f in ("mx_parser.py", "evo_parser.py"):
    p = pathlib.Path(f); t = p.read_text()
    assert t.count(old) == 1, f
    p.write_text(t.replace(old, new))
EOF
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/test_inactive.py
git checkout mx_parser.py evo_parser.py
```

Očekávej: **nejméně 12 failed** — a mezi nimi obě parametrizace `test_inactive_rib_drops_only_its_own_routes` a `test_inactive_bgp_group_drops_its_neighbors`. Před smazáním guardů padalo 8; když číslo nevzroste, guardy se nesmazaly ve všech šesti místech.

`test_deactivated_unit_under_active_interface_is_flagged` z úlohy 1 pod tímhle mutantem **projde**, a je to správně: jeho fixture nese `inactive` přímo na `<unit>`, takže chůze po předcích a přímý dotaz dají stejnou odpověď. Ten test zabíjí jiného mutanta (`active=not physical_inactive`, úloha 1, krok 5). Dva testy, které smazání disjunktu na `:1103` nově zabíjí, jsou obě parametrizace `test_deactivated_interface_is_flagged` — tam `<unit>` atribut nemá a dědí ho po fyzickém rozhraní.

---

### Task 3: Top-level `<protocols inactive>`

`tests/parsers/test_inactive.py` procvičuje `<protocols>`/`<bgp>` jen vnořené pod deaktivovanou `routing-instances`. K top-level kontejneru vede samostatná cesta: `_parse_default_bgp_neighbors()` (`mx_parser.py:952`) plní `self.default_bgp_neighbors` a `self.default_bfd`, které konzumují služby **bez** routing-instance (`mx_parser.py:1570`).

**Soubory:**
- Modify: `tests/parsers/test_inactive.py`

**Rozhraní:**
- Konzumuje: `_services`, `_by_name`, `PARSERS`
- Produkuje: nic

- [ ] **Krok 1: Napiš fixture a dva testy**

```python
DEACTIVATED_TOP_LEVEL_PROTOCOLS = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family><inet><address><name>152.11.13.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <protocols inactive="inactive">
    <bgp>
      <group>
        <name>CPE13</name>
        <neighbor>
          <name>152.11.13.2</name>
          <bfd-liveness-detection>
            <minimum-interval>3000</minimum-interval>
            <multiplier>3</multiplier>
          </bfd-liveness-detection>
        </neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""

ACTIVE_TOP_LEVEL_PROTOCOLS = DEACTIVATED_TOP_LEVEL_PROTOCOLS.replace(
    '<protocols inactive="inactive">', "<protocols>"
)


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_active_top_level_protocols_produce_intent(module, parser_class):
    """Kontrolní test: bez něj by test níž mohl měřit prázdnou fixture.

    Zabíjí mutanta: `self.default_bfd = {}` v _parse_default_bgp_neighbors.
    Služba bez routing-instance sahá do default_bgp_neighbors a default_bfd -
    kdyby se neplnily, byl by test na deaktivaci zelený z nesprávného důvodu.
    """
    services = _services(module, parser_class, ACTIVE_TOP_LEVEL_PROTOCOLS)
    service = _by_name(services, "ge-0/0/2.13")

    assert service.bgp_neighbor == ["152.11.13.2"]
    assert service.bfd


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_top_level_protocols_drop_neighbors_and_bfd(module, parser_class):
    """<protocols inactive> na top-level úrovni nedá souseda ani BFD záměr.

    Zabíjí mutanta: `_is_inactive` bez chůze po předcích. K top-level
    kontejneru vede jiná cesta než k tomu pod routing-instances
    (_parse_default_bgp_neighbors), takže existující testy tenhle mutant
    na téhle cestě nechytí.
    """
    services = _services(module, parser_class, DEACTIVATED_TOP_LEVEL_PROTOCOLS)
    service = _by_name(services, "ge-0/0/2.13")

    assert service.bgp_neighbor == []
    assert service.bfd == []
```

- [ ] **Krok 2: Spusť je**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/test_inactive.py -k top_level
```

Očekávej: **4 passed**.

- [ ] **Krok 3: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej: **598 passed, 1 skipped**.

- [ ] **Krok 4: Commit**

```bash
git add tests/parsers/test_inactive.py
git commit -m "test(parsers): top-level <protocols inactive> nedava zamer (AR-33)"
```

- [ ] **Krok 5: Mutant — až po commitu**

Použij tentýž blok jako v úloze 2, krok 7, ale spusť jen:

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/parsers/test_inactive.py -k top_level
git checkout mx_parser.py evo_parser.py
```

Očekávej: **2 failed, 2 passed** — padnou obě parametrizace `test_deactivated_top_level_protocols_drop_neighbors_and_bfd`, kontrolní test projde.

---

### Task 4: Regenerace `.4` proti laborce

**Soubory:**
- Modify: `172.20.20.4.yml`, `tests/fixtures/172.20.20.4.yml`
- Modify: `tests/fixtures/rpc/junos/*.xml` (všech 10 souborů)
- Modify: `tests/collectors/test_bfd.py` (modulový docstring + `test_empty_output_is_a_valid_state`)

**Rozhraní:**
- Produkuje: neprázdnou `tests/fixtures/rpc/junos/bfd.xml`, kterou čte úloha 5

- [ ] **Krok 1: Ověř stav laborky, než na cokoliv sáhneš**

```bash
eval "$(grep -h MIG_LAB_PASSWORD ~/.bashrc)"
.venv/bin/python - <<'EOF'
import os
from jnpr.junos import Device
d = Device(host="172.20.20.4", user="admin", passwd=os.environ["MIG_LAB_PASSWORD"], port=22)
d.open()
cfg = d.rpc.get_config(options={"format": "xml"})
for n in cfg.iter():
    if n.get("inactive"):
        nm = n.find("name")
        print("INACTIVE:", n.tag, nm.text if nm is not None else "")
d.close()
EOF
```

Očekávej **přesně**:

```
INACTIVE: interface ge-0/0/3
INACTIVE: instance EVPN-VPWS-CPE24-UNI
```

Cokoliv jiného znamená, že se laborka změnila. **Zastav se a zeptej se uživatele** — regenerovat proti jinému stavu znehodnotí všechna čísla v tomhle plánu.

- [ ] **Krok 2: Regeneruj inventory**

```bash
eval "$(grep -h MIG_LAB_PASSWORD ~/.bashrc)"
printf '%s\n' "$MIG_LAB_PASSWORD" | .venv/bin/python mx_parser.py 172.20.20.4 --auth password -u admin -o 172.20.20.4.yml
cp 172.20.20.4.yml tests/fixtures/172.20.20.4.yml
```

`mx_parser.py` čte heslo přes `getpass`, proto to `printf`. Obě kopie inventory musí zůstat bajtově shodné.

- [ ] **Krok 3: Ověř obsah inventory**

```bash
.venv/bin/python -c "
import yaml
d = yaml.safe_load(open('172.20.20.4.yml'))
print('schema', d['schema_version'], 'sluzeb', len(d['interfaces']))
for e in d['interfaces']:
    if not e.get('interface_active', True) or not e.get('routing_instance_active', True):
        print(' ', e['interface'], e.get('interface_active'), e.get('routing_instance_active'))
"
```

Očekávej:

```
schema 4 sluzeb 24
  ge-0/0/3 False True
  ge-0/0/3.0 False False
```

- [ ] **Krok 4: Přenahraj RPC fixtures**

```bash
eval "$(grep -h MIG_LAB_PASSWORD ~/.bashrc)"
.venv/bin/python -m migration_validator.cli record \
  --device 172.20.20.4 --output-dir /tmp/rec-mx \
  --username admin --auth password --password "$MIG_LAB_PASSWORD"
cp /tmp/rec-mx/junos/*.xml tests/fixtures/rpc/junos/
grep -c "<bfd-session>" tests/fixtures/rpc/junos/bfd.xml
```

Očekávej: `2`. Kdyby vyšlo `0`, BFD session mezitím spadly — zastav se, tohle je celý smysl úlohy.

- [ ] **Krok 5: Spusť sadu a podívej se, co padlo**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej **přesně jeden** pád:

```
FAILED tests/collectors/test_bfd.py::test_empty_output_is_a_valid_state
```

Změřeno 2026‑07‑31 na týchž artefaktech. Padne-li cokoliv navíc, rozhodni u každého testu, jestli jde o **(a)** změněnou realitu laborky, nebo **(b)** regresi kódu, a rozhodnutí zapiš do commit message. Kategorie (b) je důvod se zastavit, ne opravit očekávání.

- [ ] **Krok 6: Odvaž test prázdného výpisu od laborky**

Prázdný výpis je platný stav (docstring `collectors/bfd.py:10`) a nesmí zůstat nepokrytý. Nahrávka ho ale už nenese, takže si test vyrobí vlastní XML.

V `tests/collectors/test_bfd.py` nahraď `test_empty_output_is_a_valid_state` tímto:

```python
EMPTY_OUTPUT = """
<bfd-session-information style="detail">
  <sessions>0</sessions>
  <clients>0</clients>
  <cumulative-transmission-rate>0.0</cumulative-transmission-rate>
  <cumulative-reception-rate>0.0</cumulative-reception-rate>
</bfd-session-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_empty_output_is_a_valid_state(platform):
    """Nula session neni selhani collectoru - collector nema co interpretovat.

    XML je synteticke, ne nahravka. Drive se tenhle stav bral z `junos`
    fixture, ktera zadnou session nemela - jenze to znamenalo, ze stav
    laborky rozhodoval o tom, jestli je tenhle pripad vubec testovany.
    """
    result = BfdCollector().parse(etree.fromstring(EMPTY_OUTPUT.encode()), platform)

    assert result == {}
```

Do importů souboru přidej `from lxml import etree`.

- [ ] **Krok 7: Oprav modulový docstring**

Nahoře v `tests/collectors/test_bfd.py` je docstring tvrdící, že `junos` fixture je záměrně prázdná. Nahraď ho:

```python
"""Testy collectoru BFD proti nahranemu XML z laborky.

Obe nahravky maji session: na 172.20.20.4 (junos) jsou dve na ge-0/0/2,
na 172.20.20.5 (junos-evo) dve na et-0/0/8. Tvar odpovedi se mezi rodinami
nelisi - same elementy, jen jina jmena rozhrani.

Prazdny vypis je platny stav a testuje se na syntetickem XML (EMPTY_OUTPUT),
ne na nahravce, aby o pokryti nerozhodoval stav laborky.
"""
```

- [ ] **Krok 8: Spusť sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej: **600 passed, 0 skipped**. Skipovaný test zmizel proto, že `test_esi_interface_matches_a_scope[junos]` po regeneraci najde ESI segment a začne běžet — to je pokrytí navíc, ne chyba.

- [ ] **Krok 9: Commit**

```bash
git add 172.20.20.4.yml tests/fixtures/172.20.20.4.yml tests/fixtures/rpc/junos tests/collectors/test_bfd.py
git commit -m "chore: regenerovat inventory a fixtures .4 proti laborce (AR-30)"
```

---

### Task 5: MX BFD se dostane k reálnému verdiktu

**Soubory:**
- Modify: `tests/collectors/test_conformance.py` (parametrizace `test_specific_check_sees_data`)
- Modify: `tests/collectors/test_bfd.py` (dva nové testy, zrušení výjimky na `junos`)

**Rozhraní:**
- Konzumuje: `tests/fixtures/rpc/junos/bfd.xml` z úlohy 4

- [ ] **Krok 1: Přidej `junos` do parametrizace conformance testu**

V `tests/collectors/test_conformance.py` nahraď tento blok:

```python
        # bfd_session_state schvalne jen pro junos-evo: fixture pro junos je
        # zamerne prazdny vypis (BGP je u obou peeru Idle), takze check tam
        # spravne vraci same SKIP a "aspon jeden ne-SKIP" by na nem selhalo
        # z legitimniho duvodu.
        ("junos-evo", "bfd_session_state"),
```

tímto:

```python
        # Obe platformy: po regeneraci .4 (AR-30) ma junos dve realne session
        # na ge-0/0/2 a sluzby, ktere je nesou, uz nejsou deaktivovane.
        # "Aspon jeden ne-SKIP" tady drzi sev poctive - overeno mutantem
        # ctx.subject.get("bfd_x"), ktery shodil obe parametrizace. Na rozdil
        # od statik (viz komentar vyse) tu tedy neni potreba test vyzadujici
        # konkretne PASS.
        ("junos-evo", "bfd_session_state"),
        ("junos", "bfd_session_state"),
```

> **Oprava po provedení:** komentář v Kroku 1 tvrdil, že "aspoň jeden ne-SKIP"
> je ověřeno mutantem `ctx.subject.get("bfd_x")`, který shodil obě
> parametrizace, a proto tam prý není potřeba test vyžadující konkrétně PASS.
> Toto tvrzení bylo při provádění změřeno jako nepravdivé: mutant na `junos`
> přežívá, protože BGP je tam Established, takže check vyrobí slepé
> `FAIL "bez session"`, které asertaci "aspoň jeden ne-SKIP" splní i bez
> skutečné session. Původní měření běželo nad smíšenou sadou fixture (nové
> `bfd.xml` proti starému `bgp.xml`, kde bylo BGP ještě Idle). Commitovaný
> kód i opravená spec (AR-31) odrážejí skutečné chování; tento odstavec plánu
> zůstává jako záznam toho, co se tehdy věřilo.

- [ ] **Krok 2: Spusť conformance test**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/collectors/test_conformance.py::test_specific_check_sees_data
```

Očekávej: **8 passed**.

- [ ] **Krok 3: Přidej testy MX session do collectoru**

V `tests/collectors/test_bfd.py` přidej:

```python
def test_mx_sessions_are_recorded_verbatim(rpc_fixture):
    """Odpoved z MX ma tentyz tvar jako z EVO - jen jina jmena rozhrani.

    Roadmapa vlny 3 predpokladala MX-specificke parsovani; nahravka z
    2026-07-31 ukazala, ze zadne neni. Tenhle test to drzi: kdyby MX odpoved
    vlastni tvar dostala, spadne tady, ne az v conformance.
    """
    result = BfdCollector().parse(rpc_fixture("junos", "bfd"), "junos")

    assert result["152.11.13.2"]["state"] == "Up"
    assert result["152.11.13.2"]["interface"] == "ge-0/0/2.13"
    assert result["152.11.13.2"]["remote_state"] == "Up"
    assert result["198.11.13.2"]["state"] == "Down"
    assert result["198.11.13.2"]["remote_state"] == "AdminDown"


def test_mx_client_names_are_collected(rpc_fixture):
    """Bez klienta nejde odlisit session drzenou BGP od jine.

    Zabíjí mutanta: vypusteni smycky pres bfd-client, po nemz zustane
    `clients` prazdny seznam. Je to duvod, proc collector vubec pouziva
    detail variantu RPC.
    """
    result = BfdCollector().parse(rpc_fixture("junos", "bfd"), "junos")

    assert result["152.11.13.2"]["clients"] == ["BGP"]
    assert result["198.11.13.2"]["clients"] == ["BGP"]
```

- [ ] **Krok 4: Zruš výjimku na `junos` v existujícím testu**

V `test_returns_mapping_keyed_by_neighbor` nahraď:

```python
    assert isinstance(result, dict)
    # Jmeno testu slibuje klicovani adresou peeru, tak to i asertujme.
    # Fixture pro junos je zamerne prazdna, tam neni co overit.
    if platform == "junos-evo":
        assert "152.11.13.2" in result
```

za bezpodmínečnou verzi:

```python
    assert isinstance(result, dict)
    # Obe nahravky nesou session se stejnym peerem, takze vyjimka na
    # platformu uz neni potreba (AR-30).
    assert "152.11.13.2" in result
```

- [ ] **Krok 5: Spusť sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej: **604 passed, 0 skipped**. (Plán původně počítal 603; opravné kolo AR-31 přidalo test vyžadující PASS, viz spec.)

- [ ] **Krok 6: Commit**

```bash
git add tests/collectors/test_bfd.py tests/collectors/test_conformance.py
git commit -m "test(bfd): MX session maji realny verdikt i vlastni test (AR-31, AR-32)"
```

- [ ] **Krok 7: Mutanti — až po commitu**

```bash
sed -i 's/sessions: dict\[str, Any\] = ctx.subject.get("bfd", {})/sessions: dict[str, Any] = ctx.subject.get("bfd_x", {})/' migration_validator/checks/bfd.py
.venv/bin/python -m pytest -o addopts="" -q tests/collectors/test_conformance.py::test_specific_check_sees_data
git checkout migration_validator/checks/bfd.py
```

Očekávej: **2 failed** — `junos-bfd_session_state` i `junos-evo-bfd_session_state`.

> **Oprava po provedení:** toto očekávání bylo měřeno nesprávně (viz oprava
> u Kroku 1) — mutant na `junos` ve skutečnosti přežívá, protože BGP je tam
> Established a check vyrobí slepé `FAIL "bez session"`, které projde jako
> ne-SKIP i bez detekce mutantu. Skutečný výsledek je proto **1 failed**
> (jen `junos-evo-bfd_session_state`). Viz opravená spec AR-31.

```bash
python3 - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/collectors/bfd.py"); t = p.read_text()
old = """            clients = []
            for client in node.iter("bfd-client"):
                name = _text(client, "client-name")
                if name:
                    clients.append(name)
"""
assert t.count(old) == 1
p.write_text(t.replace(old, "            clients = []\n"))
EOF
.venv/bin/python -m pytest -o addopts="" -q tests/collectors/test_bfd.py
git checkout migration_validator/collectors/bfd.py
```

Očekávej: padne `test_mx_client_names_are_collected` i `test_client_names_are_collected`.

---

### Task 6: Chybějící `active` v měření není PASS

`checks/routes.py:152` čte `subject.get("active", True)` — regrese collectoru (přejmenovaný nebo vypuštěný klíč) by se přečetla jako **PASS**, ne jako chybějící kontrola. Je to jediné takové místo v repu.

`:153` (`baseline.get("active", True)`) se **nemění** — je to mimo rozsah této vlny, ne proto, že by byl správný. Původní zdůvodnění bylo chybné na obou premisách: baseline nemůže pocházet ze staršího schematu (`Snapshot.from_dict` na neshodu `schema_version` vždy vyhodí výjimku, baseline i subjekt se načítají stejnou cestou) a znaménko bylo obráceně — s defaultem chybějící klíč vrátí `True` → `BROKEN`, bez defaultu by to bylo `None` → `DEGRADED`. Ponechání defaultu tedy BROKEN způsobuje, nikoli mu brání. Otevřená otázka pro příští vlnu (má chybějící `active` v baseline dávat `DEGRADED` podle R‑2?) zůstává zapsaná v AR‑34c.

**Soubory:**
- Modify: `migration_validator/checks/routes.py:152`
- Modify: `tests/checks/test_routes.py`

- [ ] **Krok 1: Napiš padající test**

Do `tests/checks/test_routes.py` přidej pomocníka k ostatním a test:

```python
def _installed_without_active():
    """Mereni bez klice 'active' - tvar, ktery by vyrobila regrese collectoru."""
    routes = _installed()
    del routes["inet.0"]["198.62.1.0/29"]["active"]
    return routes


def test_route_without_active_key_is_skipped():
    """Chybejici klic je chybejici kontrola, ne PASS.

    Zabíjí mutanta: `subject.get("active", True)`, tedy default "aktivni".
    S nim by check vydal OK a regrese collectoru by se cetla jako uspech.
    """
    findings = StaticRouteStatusCheck().run(_ctx(_installed_without_active()))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
```

- [ ] **Krok 2: Spusť ho — musí SPADNOUT**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/checks/test_routes.py::test_route_without_active_key_is_skipped
```

Očekávej: **1 failed** — `Outcome.OK is not Outcome.SKIP`. Kdyby prošel rovnou, test neměří to, co slibuje.

- [ ] **Krok 3: Implementuj**

V `migration_validator/checks/routes.py` nahraď řádek

```python
        if not subject.get("active", True):
```

blokem:

```python
        # Default "aktivni" tady byl jedine misto v repu, kde by se regrese
        # collectoru precetla jako PASS misto jako chybejici kontrola.
        # Na `baseline` niz default zustava zamerne: baseline muze pochazet
        # ze starsiho schematu a bez defaultu by chybejici klic skoncil jako
        # BROKEN, tedy eskalace chybejiciho udaje na FAIL proti R-2.
        if "active" not in subject:
            return Finding(
                Outcome.SKIP,
                f"{rib} {prefix}: mereni neobsahuje aktivitu routy",
                label=label,
                family=family,
                value="bez dat",
                baseline=baseline,
                subject=subject,
            )

        if not subject["active"]:
```

- [ ] **Krok 4: Spusť test a pak celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/checks/test_routes.py::test_route_without_active_key_is_skipped
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej: **1 passed**, pak **605 passed, 0 skipped**.

- [ ] **Krok 5: Commit**

```bash
git add migration_validator/checks/routes.py tests/checks/test_routes.py
git commit -m "fix(checks): chybejici aktivita routy je SKIP, ne PASS (AR-34c)"
```

- [ ] **Krok 6: Mutant — až po commitu**

```bash
sed -i 's/        if "active" not in subject:/        if False:/' migration_validator/checks/routes.py
.venv/bin/python -m pytest -o addopts="" -q tests/checks/test_routes.py
git checkout migration_validator/checks/routes.py
```

Očekávej: **1 failed** — `test_route_without_active_key_is_skipped`. (Mutant shodí i `KeyError` v následujícím řádku; obojí je platné zabití, důležité je, že test není zelený.)

---

### Task 7: Docstring, který slibuje víc, než zabíjí

`test_inactive_route_that_was_inactive_before_passes` slibuje mutanta „hlášení BROKEN při každé neaktivní routě **bez ohledu na baseline**". Změřeno 2026‑07‑31: doslovná čtení téhle věty test **zabíjí** (obě varianty), ale varianta „bez baseline taky BROKEN místo DEGRADED" ho **přežije** — tu chytá až sousední `test_inactive_route_without_baseline_is_degraded`. Docstring si tedy přisvojuje cizí pokrytí.

**Soubory:**
- Modify: `tests/checks/test_routes.py:58-62`

- [ ] **Krok 1: Oprav docstring**

Nahraď:

```python
    """Stav se nezmenil, takze to neni nalez.

    Zabiji mutanta: hlaseni BROKEN pri kazde neaktivni route bez ohledu na
    baseline.
    """
```

za:

```python
    """Stav se nezmenil, takze to neni nalez.

    Zabiji mutanta: vypusteni vetve `was_active is False`, tedy hlaseni
    nalezu i u routy, ktera nebyla aktivni uz v baseline.

    Vetev bez baseline (DEGRADED misto BROKEN) tenhle test nehlida - ta ma
    vlastni test test_inactive_route_without_baseline_is_degraded. Overeno
    mutaci 2026-07-31: `outcome = Outcome.BROKEN` natvrdo tenhle test prezije
    a shodi az ten druhy.
    """
```

- [ ] **Krok 2: Spusť sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej: **605 passed, 0 skipped** — změna je jen v docstringu.

- [ ] **Krok 3: Commit**

```bash
git add tests/checks/test_routes.py
git commit -m "docs(test): docstring popisuje mutanta, ktereho test opravdu zabiji (AR-34d)"
```

---

## Závěrečná kontrola větve

- [ ] **Celá sada**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávej: **605 passed, 0 skipped**.

- [ ] **Zámek parserů**

```bash
diff mx_parser.py evo_parser.py | wc -l
```

Očekávej: `146`.

- [ ] **Čistý strom**

```bash
git status --porcelain
```

Očekávej: prázdný výstup. Neprázdný znamená, že některý mutantí blok neskončil `git checkout`.

- [ ] **Napiš roadmapu vlny 5** do `docs/superpowers/roadmap-2026-<datum>-vlna4-hotovo.md` ve stejném duchu jako `roadmap-2026-07-30-vlna3-hotovo.md`: co vlna přinesla, jak si vyrobit důkazy, co v provádění vyšlo jinak, než plán čekal, a co zbývá. Do „co zbývá" patří minimálně bod 1 roadmapy vlny 3 (příznaky na hlubších úrovních konfigurace, včetně top-level `<routing-options inactive>`), bod 3 (report: F‑2, F‑11, F‑14) a resync `172.20.20.5.yml`.
