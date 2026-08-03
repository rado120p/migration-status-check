# Vlna 7 — RIB ve sdílených fixtures a zpřísnění trojcestné větve

> **Pro agentní pracovníky:** POVINNÝ SUB-SKILL: použij
> `superpowers:subagent-driven-development` (doporučeno) nebo
> `superpowers:executing-plans` a proveď plán úlohu po úloze. Kroky jsou
> odškrtávací (`- [ ]`).

**Cíl:** Opravit jméno RIB u IPv6 BGP peerů ve sdílených syntetických
fixtures, zviditelnit v ostrém reportu scénář AR-36 (peer se dvěma RIB) a
zpřísnit rozcestí FAIL/DEGRADED u `active` tak, aby R-2 platilo konstrukcí.

**Architektura:** Dvě nezávislé úlohy. Úloha 1 je **test-only** — mění jen
`tests/conftest.py`, který syntetizuje „naměřená data" pro fixtures, a
přidává dva testy nad ostrým průchodem. Úloha 2 je jednořádková změna
produkční větve v `checks/routes.py` plus dva jednotkové testy. Produkční
kód pro BGP (`checks/bgp.py`) se **nemění** — bere `rib_name` beze změny z
dat, která dostane, takže vada bodu 7 sídlí výhradně v generátoru fixtures.

**Tech stack:** Python 3, pytest, `.venv/bin/python`. Spouštěč vždy
s `-o addopts=""`, protože projektové `addopts` přidávají coverage a
zpomalují cyklus.

**Návrh:**
[`../specs/2026-08-03-vlna7-fixtures-a-zprisneni-design.md`](../specs/2026-08-03-vlna7-fixtures-a-zprisneni-design.md).
**Výchozí bod:** `main` na `e3415d6`, 636 testů zelených, 0 přeskočených.

**Všechna čísla a výčty shozených testů v tomhle plánu jsou změřené, ne
předpovězené.** Autor plánu provedl obě úlohy nanečisto — včetně červených
fází a všech tří mutantů — a strom po měření vrátil do čistého stavu. Kde se
předpověď od měření lišila, platí měření a text nese naměřený tvar. Pravidlo
z vlny 3 („u každého testu, který plán předepisuje doslova, se mutant pustí
už při psaní plánu") je tedy splněné; implementer měření **přesto opakuje**,
protože poběží nad jiným stavem repa než autor.

## Globální omezení

- **Všechny uživatelsky viditelné řetězce v produkčním kódu jsou ASCII bez
  diakritiky.** Týká se hlášek, `label`, `group` i `value`. Komentáře v
  produkčním kódu drží tutéž konvenci (viz existující `checks/routes.py`).
  Dokazuje se **bajtovým scanem**, ne čtením diffu:
  `grep -nP '[^\x00-\x7F]' <soubor>` musí vrátit prázdný výstup.
  Docstringy testů v `tests/` diakritiku mít smí — existující testy ji mají.
- **Mutant se pouští nad tím stavem repa, ve kterém poběží doopravdy**, tedy
  **až po commitu** své úlohy. Každý mutantí blok končí
  `git checkout <soubor>` a doložením čistého stromu.
- **Pořadí úloh je závazné.** Úloha 1 mění fixtures; mutanti úlohy 2 se měří
  až nad novým `tests/conftest.py`.
- **Měření má přednost před tímhle plánem.** Když naměřené číslo nesedí s
  předpovědí, platí měření a zapíše se do reportu úlohy jako odchylka.
- **Parsery se nemění.** `diff mx_parser.py evo_parser.py | wc -l` musí
  zůstat `146`.
- Spouštěč je `.venv/bin/python`. Holé `python` v tomhle prostředí
  **neexistuje** a tiše selže.

---

## Struktura souborů

| soubor | odpovědnost | úloha |
|---|---|---|
| `tests/conftest.py` | generátor syntetických „naměřených" faktů pro sdílené fixtures; nově rozhoduje o jménu RIB podle rodiny peera a o druhé RIB u jednoho peera | 1 |
| `tests/test_end_to_end.py` | tvrzení nad ostrým průchodem obou fixtures; nově dva testy o RIB | 1 |
| `migration_validator/checks/routes.py` | rozcestí FAIL/DEGRADED u neaktivní routy | 2 |
| `tests/checks/test_routes.py` | jednotkové testy `StaticRouteStatusCheck` | 2 |

---

## Úloha 1: Jméno RIB podle rodiny a druhá RIB u jednoho peera

**Soubory:**
- Modifikovat: `tests/conftest.py:52` (vložit pomocníka nad `def _facts_for`)
  a `tests/conftest.py:110-119` (nahradit napevno psaný slovník `ribs`)
- Test: `tests/test_end_to_end.py` (přidat dva testy na konec)

**Rozhraní:**
- Konzumuje: nic z dřívějších úloh (je první).
- Produkuje: modulové konstanty `DUAL_RIB_PEER: str` a funkci
  `_ribs_for(peer: str, family: int) -> dict[str, dict[str, int]]` v
  `tests/conftest.py`. Úloha 2 na nich **nestaví**, ale běží nad nimi.

**Proč to takhle:** Dnešní `_facts_for` přišije každému peerovi jedinou RIB
jménem `inet.0`. U IPv6 peerů je to špatně (`inet6.0`), a protože žádný peer
nemá dvě RIB, blok, kvůli kterému vlna 5 přepsala BGP report (AR-36), nikdo
nikdy neviděl vyrenderovaný. Peer `152.11.13.2` je vybraný proto, že je na
**obou** fixtures (služba Internet, `ge-0/0/2.13` → `et-0/0/8.13`), takže
baseline i subject nesou tutéž strukturu a `bgp_prefix_counts` je porovná
místo aby hlásil „RIB neni v baseline, nelze porovnat".

- [ ] **Krok 1: Ověř výchozí stav**

```bash
cd /home/rado/Desktop/scripts/migration-status-check
git status --porcelain          # ocekava se prazdny vystup
.venv/bin/python -m pytest -o addopts="" -q | tail -2
```

Očekává se: `636 passed`.

- [ ] **Krok 2: Napiš oba nové testy**

Přidej na **konec** `tests/test_end_to_end.py`. Soubor už má nahoře importy
`api`, `Status`, `Path` a konstanty `NOW`, `DEVICE_4`, `DEVICE_5` — použij
je, nezakládej duplikáty. Nový import je jen `DUAL_RIB_PEER`; přidej ho k
existujícím importům nahoře souboru:

```python
from conftest import DUAL_RIB_PEER
```

Testy:

```python
def _prefix_count_checks(result):
    """Vsechny vysledky checku bgp_prefix_counts napric scopy.

    Vraci dvojice (group, rib). SKIP vysledky bez details se vypousti -
    nesou (None, None) a o jmenu RIB netvrdi nic.
    """
    return {
        (check.group, check.details.get("rib"))
        for scope in result.scopes
        for check in scope.checks
        if check.id == "bgp_prefix_counts" and check.details.get("rib")
    }


def test_ipv6_peers_carry_inet6_rib(synthetic_snapshot):
    """Sdilene fixtures musi u IPv6 peera hlasit inet6.0, ne inet.0.

    Zabiji mutanta: `rib_name` napevno na "inet.0" v `_prefix_finding`
    (migration_validator/checks/bgp.py). S nim by kazdy peer bez ohledu na
    rodinu hlasil inet.0 a report by tvrdil neco, co na zarizeni neplati.

    Test tvrdi nad objekty (group, details["rib"]), ne nad vykreslenym
    textem - hledani retezce kdekoliv ve vystupu by nemerilo sekci.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    ipv6_ribs = {
        rib for group, rib in _prefix_count_checks(result) if ":" in group
    }
    assert ipv6_ribs, "fixture nema zadneho IPv6 BGP peera k overeni"
    assert ipv6_ribs == {"inet6.0"}


def test_dual_rib_peer_yields_two_distinguishable_blocks(synthetic_snapshot):
    """Motivujici scenar AR-36 musi byt na sdilenych fixtures k videni.

    Zabiji tehoz mutanta: `rib_name` napevno na "inet.0". S nim by obe RIB
    tehoz peera splynuly do jedine skupiny a osm nerozlisitelnych radku,
    kvuli kterym vlna 5 report prepsala, by se vratilo.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    ribs = {
        rib
        for group, rib in _prefix_count_checks(result)
        if group.startswith(f"BGP {DUAL_RIB_PEER} / ")
    }
    assert ribs == {"inet.0", "inet6.0"}
```

- [ ] **Krok 3: Spusť je a ověř, že selžou**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/test_end_to_end.py -k "inet6_rib or dual_rib"
```

Očekává se **collection ERROR celého souboru**, ne dva FAILy — změřeno:

```
tests/test_end_to_end.py:9: in <module>
    from conftest import DUAL_RIB_PEER
E   ImportError: cannot import name 'DUAL_RIB_PEER' from 'conftest'
Interrupted: 1 error during collection
```

`import conftest` se rozřeší (pytest vkládá `tests/` do `sys.path`, protože
tam není `__init__.py`) — chybí jen konstanta.

**Tohle není červená fáze měřící chování**, jen „test nemá o co se opřít".
Obě tvrzení po implementaci projdou napoprvé, protože připínají chování,
které si úloha právě vyrobila. Zapiš to do reportu takhle otevřeně — je to
týž tvar jako AR-44 ve vlně 6, ne mezera.

- [ ] **Krok 4: Vlož pomocníka do `tests/conftest.py`**

Nad řádek `def _facts_for(scopes, pps: int) -> dict:` (dnes řádek 54) vlož:

```python
# Peer, ktery jako jediny nese dve RIB. Bez nej by motivujici scenar AR-36
# nebyl na sdilenych fixtures k videni - kazdy peer by mel prave jednu RIB
# a blok, kvuli kteremu vlna 5 report prepsala, by se nikdy nevyrenderoval.
# Vybrany je zamerne: 152.11.13.2 je na obou fixtures (.4 i .5), takze
# baseline i subject nesou tutez strukturu a bgp_prefix_counts je porovna
# misto aby hlasil "RIB neni v baseline". Je to IPv4 peer nesouci navic
# inet6.0, tedy multiprotokolova session, ne fabulace.
DUAL_RIB_PEER = "152.11.13.2"

_RIB_COUNTERS = {
    "received": 14,
    "accepted": 14,
    "advertised": 3,
    "active": 14,
    "suppressed": 0,
}

# Druha RIB ma vlastni cisla, jinak by se dva bloky teho peera lisily jen
# hlavickou. Nic nemeri; podstatne je, ze se lisi od 14/14/14/3 a ze
# accepted < received.
_SECOND_RIB_COUNTERS = {
    "received": 6,
    "accepted": 5,
    "advertised": 2,
    "active": 5,
    "suppressed": 1,
}


def _ribs_for(peer: str, family: int) -> dict:
    """RIB peera podle rodiny; DUAL_RIB_PEER dostane navic druhou.

    Sdilene fixtures drive davaly kazdemu peerovi inet.0 bez ohledu na
    rodinu - u IPv6 peera to bylo v rozporu se sousedni skupinou statickych
    rout ve stejne sekci, ktera inet6.0 pouzivala spravne.
    """
    primary = "inet6.0" if family == 6 else "inet.0"
    ribs = {primary: dict(_RIB_COUNTERS)}
    if peer == DUAL_RIB_PEER:
        ribs["inet6.0"] = dict(_SECOND_RIB_COUNTERS)
    return ribs
```

- [ ] **Krok 5: Zavolej pomocníka místo napevno psaného slovníku**

V `tests/conftest.py` nahraď blok `"ribs": {...}` uvnitř `bgp[peer] = {...}`
(dnes řádky 110-119). Před:

```python
                "ribs": {
                    "inet.0": {
                        "received": 14,
                        "accepted": 14,
                        "advertised": 3,
                        "active": 14,
                        "suppressed": 0,
                    }
                },
```

Po:

```python
                "ribs": _ribs_for(peer, family),
```

Proměnná `family` je o pár řádků výš už spočítaná
(`family = ipaddress.ip_address(peer).version`) — nepočítej ji znovu.

- [ ] **Krok 6: Spusť nové testy a pak celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/test_end_to_end.py -k "inet6_rib or dual_rib"
.venv/bin/python -m pytest -o addopts="" -q | tail -2
```

Očekává se: nejdřív `2 passed`, pak **`638 passed`, 0 skipped, bez ripple**
na existující testy. Ripple byl předem změřen jako nulový; **kdyby nastal,
je to nález** — zapiš ho, neopravuj test kolem něj.

- [ ] **Krok 7: Bajtový scan a commit**

```bash
grep -nP '[^\x00-\x7F]' tests/conftest.py    # ocekava se prazdny vystup
git add tests/conftest.py tests/test_end_to_end.py
git commit -m "test: rozlisit RIB podle rodiny peera a dat jednomu peerovi druhou RIB

Body 7 a 8 roadmapy vlny 6. IPv6 peer nesl inet.0, ackoliv sousedni skupina
statickych rout ve stejne sekci pouzivala inet6.0 spravne. Zaroven zadny peer
nemel dve RIB, takze blok, kvuli kteremu vlna 5 report prepsala (AR-36), nikdo
nikdy nevidel vyrenderovany."
```

- [ ] **Krok 8: Pusť mutanta — a pusť ho do produkčního kódu**

Až **po** commitu. Cíl je `_prefix_finding` v
`migration_validator/checks/bgp.py`, kde se z `rib_name` staví `group` a
`details["rib"]` (dnes řádky ~188 a ~194). **Ne `tests/conftest.py`** —
mutant, který mění týž generátor, proti kterému test asertuje, neprokazuje
nic; test by spadl proto, že mu někdo smazal vstup, ne proto, že hlídá
chování.

```bash
sed -i 's/    group = f"BGP {peer} \/ {rib_name}"/    group = "BGP mutant \/ inet.0"/' migration_validator/checks/bgp.py
sed -i 's/    details = {"rib": rib_name, "tolerance_percent": tolerance}/    details = {"rib": "inet.0", "tolerance_percent": tolerance}/' migration_validator/checks/bgp.py
git diff --stat                 # over, ze mutant SKUTECNE sedl
.venv/bin/python -m pytest -o addopts="" -q | tail -3
git checkout migration_validator/checks/bgp.py
git status --porcelain          # ocekava se prazdny vystup
```

Očekává se přesně **`3 failed, 635 passed`**, jmenovitě:

```
FAILED tests/checks/test_bgp.py::test_bgp_group_carries_peer_and_rib
FAILED tests/test_end_to_end.py::test_ipv6_peers_carry_inet6_rib
FAILED tests/test_end_to_end.py::test_dual_rib_peer_yields_two_distinguishable_blocks
```

Ten třetí je **existující** test AR-36 z vlny 5 — výčet ho uvádí schválně,
aby nikdo nemusel dohadovat, jestli je úplný. Kdyby spadly jen ty dva nové,
bylo by to podezřelé: znamenalo by to, že mutant nesedl celý.

**Tahle úloha je test-only, takže její diff produkční soubor neobsahuje.**
Podle pravidla vlny 6 („test-only diff nemůže doložit tvrzení o produkčním
kódu") tohohle mutanta **nemůže ověřit review úlohy** — musí ho změřit
controller nebo závěrečné ověření vlny. Do reportu úlohy napiš, že měření
proběhlo a kdo ho udělal.

- [ ] **Krok 9: Napiš report úlohy**

Uveď: přesné číslo sady po úloze, výsledek mutanta jménem shozených testů,
prázdný výstup bajtového scanu, čistý strom po mutantovi, a zvlášť sekci
`## Obavy` (i kdyby zněla „Žádné.").

---

## Úloha 2: Zpřísnit rozcestí FAIL/DEGRADED na `is True`

**Soubory:**
- Modifikovat: `migration_validator/checks/routes.py:194`
- Test: `tests/checks/test_routes.py` (přidat dva testy na konec)

**Rozhraní:**
- Konzumuje: nový `tests/conftest.py` z úlohy 1 — ne přímo, ale mutanti
  téhle úlohy se měří nad ním, ne nad starým stavem.
- Produkuje: nic, na čem by stavěla další úloha.

**Proč to takhle:** `StaticRouteStatusCheck._finding` testuje na OK větvi
`was_active is False` (identita), ale rozcestí mezi FAIL a DEGRADED o dvacet
řádků níž je pravdivostní test `if was_active:`. Pro `bool` je to totéž; pro
neboolovské hodnoty ne. Změřeno přímým voláním checku:

| `was_active` | dnes | po `is True` |
|---|---|---|
| `True` | BROKEN | BROKEN |
| `False` | OK | OK |
| klíč chybí (`None`) | DEGRADED | DEGRADED |
| `0` | DEGRADED | DEGRADED |
| `1` | BROKEN | DEGRADED |
| `"false"` | **BROKEN** | DEGRADED |

`"false"` je neprázdný řetězec, tedy pravdivý, a proto dnes eskaluje
nejednoznačnost na tvrdý FAIL — přesně to, co pravidlo R-2 zakazuje. Žádná
hodnota, která dnes reálně nastane (`True`/`False`), se nemění.

- [ ] **Krok 1: Napiš oba nové testy**

Přidej na **konec** `tests/checks/test_routes.py`. Soubor už má nahoře
importy `Outcome`, `StaticRouteStatusCheck` a pomocníky `_ctx`, `_installed`,
`_installed_inactive` — použij je. Nový pomocník `_baseline_with_active`
patří **těsně nad** oba testy:

```python
def _baseline_with_active(value):
    """Baseline s podstrcenou hodnotou 'active'.

    Zadna z techto hodnot dnes nenastane - collectors/routes.py:84 vyrabi
    skutecny bool a YAML round-tripuje bool jako bool. Testy hlidaji, ze
    R-2 plati konstrukci, ne argumentem o nedosazitelnosti.
    """
    routes = _installed()
    routes["inet.0"]["198.62.1.0/29"]["active"] = value
    return routes


def test_nonbool_truthy_active_does_not_escalate_to_fail():
    """Nejednoznacnost se podle R-2 na FAIL neeskaluje ani u nebool hodnot.

    Zabiji mutanta: navrat `if was_active is True:` na `if was_active:`.
    S nim je "false" jako neprazdny retezec pravdivy a check vyda BROKEN,
    tedy tvrdy FAIL za hodnotu, ktera ve skutecnosti tvrdi opak.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _baseline_with_active("false"))
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED


def test_falsy_nonbool_active_does_not_escalate_either():
    """Nula neaktivitu uvadi, takze uz vubec nesmi skoncit jako FAIL.

    Zabiji mutanta: `if was_active is not False:`, tedy blizky preklep
    vlastni opravy. S nim by 0 spadla do BROKEN.

    Navrat zmeny (`if was_active:`) tenhle test NEZABIJE - zmereno, 0 dava
    DEGRADED pred zmenou i po ni. Test tedy nechrani zmenu samotnou, ale
    jeji okoli; pojmenovava se to takhle schvalne, aby nikdo netvrdil vic,
    nez co mereni unese.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _baseline_with_active(0))
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED
```

- [ ] **Krok 2: Spusť je a ověř červenou fázi**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/checks/test_routes.py -k "nonbool"
```

Očekává se přesně **`1 failed, 1 passed, 20 deselected`** — změřeno:

```
FAILED tests/checks/test_routes.py::test_nonbool_truthy_active_does_not_escalate_to_fail
```

`test_falsy_nonbool_active_does_not_escalate_either` **projde už teď**, a je
to v pořádku: `0` dává DEGRADED před změnou i po ní.

**Vlep výstup pytestu do reportu úlohy doslova**, ne prózou — to je červená
fáze a v minulé vlně se právě tohle muselo doměřovat zpětně.

- [ ] **Krok 3: Proveď zpřísnění**

V `migration_validator/checks/routes.py` na řádku 194 změň:

```python
            if was_active:
```

na:

```python
            if was_active is True:
```

Doplň nad ten řádek komentář (ASCII, bez diakritiky):

```python
            # Symetricky s `was_active is False` vyse: pravdivostni test by
            # kazdou nebool hodnotu (napr. retezec "false") precetl jako
            # "forwardovala" a eskaloval nejednoznacnost na FAIL, coz R-2
            # zakazuje. Takova hodnota dnes nenastane - proto zprisneni, ne
            # oprava vady: R-2 ma platit konstrukci, ne argumentem o
            # nedosazitelnosti.
```

**Na hlášku u `0` nesahej.** Zůstane „baseline aktivitu neuvadi", ačkoliv
`0` neaktivitu uvádí. Je to vědomé rozhodnutí ze specu: opravit ji by
znamenalo rozšířit i větev `was_active is False` na „přítomné a nepravdivé",
což by `""`, `[]` a `0.0` prohlásilo za explicitní tvrzení „byla neaktivní i
v baseline" — jedna nepřesná hláška na nedosažitelném vstupu vyměněná za
jinou.

- [ ] **Krok 4: Spusť nové testy a pak celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/checks/test_routes.py -k "nonbool"
.venv/bin/python -m pytest -o addopts="" -q | tail -2
```

Očekává se: `2 passed`, pak **`640 passed`, 0 skipped**. Kdyby se pohnul
některý existující test, je to nález — zapiš ho.

- [ ] **Krok 5: Bajtový scan a commit**

```bash
grep -nP '[^\x00-\x7F]' migration_validator/checks/routes.py   # prazdny vystup
git add migration_validator/checks/routes.py tests/checks/test_routes.py
git commit -m "fix: zprisnit rozcesti FAIL/DEGRADED u active na identitu

Bod 11 roadmapy vlny 6. `if was_active:` cetl kazdou pravdivou nebool hodnotu
jako "routa forwardovala" - retezec "false" tak eskaloval nejednoznacnost na
tvrdy FAIL, coz R-2 zakazuje. Symetricky s `was_active is False` o dvacet
radku vyse. Zadna hodnota, ktera dnes realne nastane, se nemeni."
```

- [ ] **Krok 6: Pusť oba mutanty — nad novým `conftest.py`**

Až **po** commitu. Tím je zároveň splněno pravidlo z vlny 4: úloha 1 už
fixtures změnila, takže mutanti běží nad tím stavem repa, ve kterém kód
poběží doopravdy.

```bash
# Mutant A: navrat zmeny
sed -i '194s/if was_active is True:/if was_active:/' migration_validator/checks/routes.py
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q | tail -3
git checkout migration_validator/checks/routes.py

# Mutant B: blizky preklep vlastni opravy
sed -i '194s/if was_active is True:/if was_active is not False:/' migration_validator/checks/routes.py
git diff --stat
.venv/bin/python -m pytest -o addopts="" -q | tail -3
git checkout migration_validator/checks/routes.py
git status --porcelain          # ocekava se prazdny vystup
```

Očekává se — obojí změřeno předem, výčty jsou **úplné**:

**Mutant A: `1 failed, 639 passed`.**

```
FAILED tests/checks/test_routes.py::test_nonbool_truthy_active_does_not_escalate_to_fail
```

Shodí právě jeden test a ten druhý **ne** — přesně proto je test 4 psaný
proti mutantovi B, ne proti návratu změny.

**Mutant B: `5 failed, 635 passed`.**

```
FAILED tests/checks/test_routes.py::test_inactive_route_without_baseline_is_degraded
FAILED tests/checks/test_routes.py::test_inactive_route_with_silent_baseline_is_degraded
FAILED tests/checks/test_routes.py::test_silent_baseline_has_its_own_message
FAILED tests/checks/test_routes.py::test_nonbool_truthy_active_does_not_escalate_to_fail
FAILED tests/checks/test_routes.py::test_falsy_nonbool_active_does_not_escalate_either
```

Mutant B tedy **není** exkluzivní pro nový test — shodí i tři testy z vln 5 a
6, protože `is not False` rozbije i větev chybějícího klíče. Nový test je
přesto jediný, kdo tvrdí něco o hodnotě `0`. Výčet je tu úplný schválně:
neúplný výčet shozených testů byl vlastní nález vlny 6 (bod 10 její roadmapy).

Pokud naměříš jiná čísla, platí měření — zapiš to jako odchylku.

- [ ] **Krok 7: Napiš report úlohy**

Uveď: vlepenou červenou fázi z kroku 2, číslo sady, jmenovitě shozené testy
u obou mutantů, prázdný bajtový scan, čistý strom, a sekci `## Obavy`.

---

## Závěrečné ověření vlny

- [ ] **Krok 1: Změř obě čísla z „Jak si vyrobit důkazy"**

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # ocekava se 640 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l    # ocekava se 146
```

- [ ] **Krok 2: Vyrenderuj ostrý report a přečti ho očima**

Postup je tu napsaný celý inline schválně — roadmapa vlny 5 ho vedla odkazem
na soubor pod `.superpowers/`, který je v `.gitignore` a mezitím zmizel.

1. Založ dočasný `tests/test_tmp_render.py` (musí být v `tests/`, aby se na
   něj vztáhl fixture `synthetic_snapshot` z `tests/conftest.py`):

```python
from pathlib import Path

from migration_validator import api
from migration_validator.reporting.text_report import render

NOW = "2026-07-24T11:40:02Z"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_tmp_render(synthetic_snapshot):
    old = synthetic_snapshot(str(FIXTURES / "172.20.20.4.yml"), "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(str(FIXTURES / "172.20.20.5.yml"), "172.20.20.5", "post-migration")
    print(render(api.evaluate(new, baseline=old, now=NOW), detail=True))
```

2. `.venv/bin/python -m pytest -o addopts="" -s -q tests/test_tmp_render.py`
3. V sekci `-- IPv4  152.11.13.1/30` musí být **dva** bloky
   `-- BGP 152.11.13.2 / inet.0` a `-- BGP 152.11.13.2 / inet6.0` s
   **různými** countery (`14/14/14/3` vs `6/5/2/5/1`). V sekci
   `-- IPv6  2001:abcd:11:13::a/127` musí být blok
   `-- BGP 2001:abcd:11:13::b / inet6.0`, tedy **žádný `inet.0` u v6 peera**.
4. **Soubor smaž** — je jednorázový a nesmí se objevit v diffu.
5. `git status --porcelain` musí být prázdný.

- [ ] **Krok 3: Napiš roadmapu vlny 7**

Do `docs/superpowers/roadmap-2026-08-03-vlna7-hotovo.md`, ve tvaru
předchozích roadmap. Musí obsahovat:

- **Co vlna přinesla** — body 7, 8 a 11 s čísly commitů.
- **Jak si vyrobit důkazy** — obě čísla a render, celý postup inline.
- **Co vyšlo jinak** — včetně toho, co se změřilo už při psaní specu: mutanti
  bodů 7 a 8 původně mířili do `tests/conftest.py` (mutant do téhož
  generátoru, proti kterému test asertuje, neprokazuje nic) a mutant testu na
  `{"active": 0}` byl v prvním znění specu návrat změny, který ho ve
  skutečnosti nezabíjí.
- **Co zbývá** — body 1, 2, 5 a 9 roadmapy vlny 6 beze změny; **nově** bod
  „rozrůznění BGP counterů napříč peery" (uniformní `14/14/14/3` u všech
  ostatních peerů); a **výslovně**, že bod 2 z „Co vyšlo jinak" vlny 6
  (routa bez klíče `active` není na fixtures k vidění) má **jinou spouštěcí
  podmínku** než bod 8 a doplněné fixtures ho **nezavírají**.
- **Předrozhodnutý směr pro bod 1** — deaktivovaná jednotlivá
  `route`/`bfd-liveness-detection`/`neighbor` má zůstat v záměru a hlásit
  SKIP, symetricky s deaktivovanou službou. Rozhodnuto uživatelem
  2026-08-03; vlna, která bod 1 vezme, tuhle otázku už řešit nemusí.
- **Pravidla do plánu vlny 8** — šest přenesených z vlny 6 plus vyhodnocení,
  které se v téhle vlně uplatnilo a které ne.
