# Vlna 9 — deaktivace je nález, a peer se bere ze záměru: implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deaktivovaný prvek konfigurace přestane být tichý — služba, která ho nese, nesmí být PASS — a `BgpSessionStateCheck` přestane peery brát z měření, takže nakonfigurovaný peer bez session je konečně vidět.

**Architecture:** Jedna sdílená funkce `deactivation_outcome()` v `checks/deactivation.py` nese celou sémantiku deaktivace; volají ji tři checky (služba, statická routa, BGP peer) a každý si k ní skládá vlastní zprávu. Deaktivace přestane vyrábět `Outcome.SKIP`, čímž ji `engine.py:145` přestane filtrovat před `Status.worst()` a report blok rozbalí sám — `reporting/` se nemění vůbec. Odděleně od toho `BgpSessionStateCheck` začne iterovat sjednocení čtyř zdrojů peerů místo samotného měření, zrcadlově k `checks/routes.py:100`.

**Tech Stack:** Python 3, pytest, žádné nové závislosti.

**Spec:** [`../specs/2026-08-04-vlna9-deaktivace-je-nalez-design.md`](../specs/2026-08-04-vlna9-deaktivace-je-nalez-design.md)

## Global Constraints

- **Výchozí bod:** `main` na commitu `90f7a8a`, **657 passed, 0 skipped**.
- **Testovací příkaz je vždy** `.venv/bin/python -m pytest -o addopts=""`. Přepínač `-o addopts=""` je povinný — bez něj se sada chová jinak.
- **Zámek parserů:** `diff mx_parser.py evo_parser.py | wc -l` musí zůstat **146**. Vlna 9 se parserů nedotýká.
- **Schema zůstává 5** u inventory i u snapshotu. Vlna 9 čte existující klíče a žádný nový nezavádí. Kdyby úloha potřebovala nový klíč, je to nález — zastav a nahlas, nebumpuj.
- **`migration_validator/reporting/` je beze změny.** Kdyby úloha potřebovala sáhnout do rendereru, je to nález — zastav a nahlas.
- **BFD se nemění.** Test `test_inactive_bfd_override_inherits_group_value` fixuje korektní chování (Junosí `inactive` znamená „příkaz se neuplatní", takže BFD dědí skupinovou hodnotu právem). **Neopravuj ho jako vadu.**
- **ASCII-only neplatí.** Na `main` je 61 řádků s ne-ASCII v šesti souborech a `tests/parsers/test_inactive.py` je psaný česky s diakritikou. Piš komentáře a docstringy česky bez diakritiky tam, kde to dělá okolní kód, a s diakritikou tam, kde to dělá okolní kód — řiď se souborem, do kterého píšeš.
- **Mutant se pouští až nad zacommitovanou prací.** `git checkout -- <soubor>` při revertu mutanta zahodí i neuložené změny téže úlohy. Pořadí v každé úloze je: commit → mutant → revert mutanta → hotovo.
- **Mutant nesmí mířit do téhož souboru, proti kterému test asertuje.** Míří do produkčního kódu, ne do `tests/`.

---

## Výčet míst členství peera — odškrtnutí položku po položce

Spec, bod 4, akceptační kritérium 9. Vlna 8 vyrobila CRITICAL právě tím, že
výčet ze specu přeložila do plánu jen zčásti. Tady je překlad úplný:

| # | místo | co s ním plán dělá | kde |
|---|---|---|---|
| 1 | `models/scope.py:163` — BGP filtr v `Scope.select` | **beze změny.** Filtruje měření podle záměru. Nová větev v úloze 4 bere identitu ze selektorů, ne odsud. | úloha 4, krok 3 to má v komentáři |
| 2 | `models/scope.py:213` — BFD filtr v `Scope.select` | **beze změny.** BFD se nemění. | — |
| 3 | `checks/bgp.py` — iterace peerů | **mění se.** | úloha 4 |
| 4 | `engine.py:_unassigned_bgp_peers` | **beze změny.** Sjednocuje oba seznamy už od vlny 8; nakonfigurovaný peer bez session žádnou session do NEZARAZENO nepřináší. | — |
| 5 | `engine.py:_unassigned_bfd_sessions` | **beze změny.** Vědomá asymetrie vlny 8. | — |

Mění se **jediné z pěti**. Pokud implementer úlohy 4 zjistí, že potřebuje
sáhnout do některého z ostatních čtyř, je to nález — zastav a nahlas.

---

## Rozhodnutí, které plán dělá nad rámec specu

**Řádek 4 tabulky („zapnuto v subjectu, vypnuto v baselinu → WARN") se u
podprvků neuplatní. Uplatní se jen u služby, kde existuje už dnes.**

Důvod: takový prvek **žádný deaktivovaný prvek v konfiguraci nemá** — je
aktivní. Uživatelovo pravidlo („konfigurace by deaktivované prvky obsahovat
neměla") se na něj tedy nevztahuje. Navíc je to **zlepšení** proti baselinu a
projekt má explicitní pravidlo R-2, že zlepšení není varování — zapsané v
`checks/bgp.py:99-104`: *„zmena proti baseline tedy znamena, ze se relace
behem migrace ZLEPSILA, a zlepseni neni varovani (R-2): oranzovy radek na
zdrave sluzbe je falesny poplach"*.

U služby řádek 4 zůstává, protože tam je `DeactivationStateCheck` jediným
nositelem té informace. U routy i peera ji nese jejich vlastní stavový řádek.

**Technicky:** funkce `deactivation_outcome()` implementuje **všech pět
řádků**. Konzumenti nad podprvky ji volají jen tehdy, když je prvek
deaktivovaný v subjectu — řádek 4 tím u nich nenastane. Tabulka zůstává na
jednom místě a úplná.

---

## Struktura souborů

| soubor | odpovědnost | úloha |
|---|---|---|
| `migration_validator/checks/deactivation.py` | **rozšiřuje se** o modulovou funkci `deactivation_outcome()` — jediné místo, kde sémantika deaktivace žije. `DeactivationStateCheck` se na ni převádí. | 1 |
| `migration_validator/checks/routes.py` | volá `deactivation_outcome()` místo bezpodmínečného SKIPu; nově čte baseline záměr. | 2 |
| `migration_validator/checks/bgp.py` | totéž pro peery (úloha 3); sjednocení zdrojů peerů (úloha 4). | 3, 4 |
| `tests/checks/test_deactivation.py` | matice tabulky + přímé testy funkce. | 1 |
| `tests/test_engine.py` | stav služby přes skutečnou cestu. | 1 |
| `tests/test_end_to_end.py` | směr hlášek na sdílených fixtures (úloha 1); bod 14 a souhrnné countery přes `evaluate` + `render` (úloha 5). | 1, 5 |
| `tests/checks/test_routes.py` | větve tabulky nad routou. | 2 |
| `tests/checks/test_bgp.py` | větve tabulky nad peerem (3); větve sjednocení (4). | 3, 4 |

---

## Task 1: Sdílená tabulka deaktivace a její první konzument (služba)

**Files:**
- Modify: `migration_validator/checks/deactivation.py`
- Test: `tests/checks/test_deactivation.py`, `tests/test_engine.py`, `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: nic z předchozích úloh (první úloha vlny).
- Produces: `migration_validator.checks.deactivation.deactivation_outcome(subject_off: bool, baseline_off: bool | None) -> Outcome | None`. Úlohy 2 a 3 ji importují. `None` v návratu znamená „žádný řádek nevzniká". `baseline_off=None` znamená „baseline není k porovnání", což je něco jiného než `False` („v baselinu byl aktivní").

**Kontext, který implementer nemá:** `Outcome` se na `Status` převádí v
`models/result.py:66` funkcí `derive_status`. `DEGRADED` je **vždy** `WARN`
nezávisle na severity; `BROKEN` je `FAIL` při `Severity.CRITICAL` a `WARN`
jinak. `DeactivationStateCheck.default_severity` je `CRITICAL`, takže
`BROKEN` → `FAIL`.

- [ ] **Step 1: Write the failing test for the shared table**

Do `tests/checks/test_deactivation.py` přidej nad existující `test_matrix`:

```python
from migration_validator.checks.deactivation import (
    DeactivationStateCheck,
    deactivation_outcome,
)


@pytest.mark.parametrize(
    "subject_off,baseline_off,expected",
    [
        (True, False, Outcome.BROKEN),
        (True, True, Outcome.DEGRADED),
        (True, None, Outcome.DEGRADED),
        (False, True, Outcome.DEGRADED),
        (False, False, None),
        (False, None, None),
    ],
)
def test_deactivation_outcome_table(subject_off, baseline_off, expected):
    """Vsech sest kombinaci zvlast - aby selhani ukazalo, ktera se rozpojila.

    Rozdil mezi baseline_off=False a baseline_off=None je nosny: prvni
    znamena "v baselinu bezel", druhy "baseline neni k porovnani". Kdyby je
    funkce splacala dohromady, radek (True, None) by dal BROKEN a bezny beh
    bez baseline by kazdou deaktivovanou sluzbu hlasil jako nedokoncenou
    migraci.
    """
    assert deactivation_outcome(subject_off, baseline_off) is expected
```

Uprav import na řádku 13 tak, aby seděl (viz blok výše — nahrazuje původní
`from migration_validator.checks.deactivation import DeactivationStateCheck`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_deactivation.py::test_deactivation_outcome_table -q`
Expected: FAIL — `ImportError: cannot import name 'deactivation_outcome'`

- [ ] **Step 3: Write the minimal implementation**

Do `migration_validator/checks/deactivation.py` přidej pod `ACTIVE = "aktivni"`:

```python
def deactivation_outcome(subject_off: bool, baseline_off: bool | None) -> Outcome | None:
    """Sdilena semantika deaktivace pro sluzbu, statickou routu i BGP peera.

    Rozhodnuti uzivatele z 2026-08-04: deaktivovany prvek konfigurace je sam
    o sobe nalez. Konfigurace by deaktivovane prvky bezne obsahovat nemela,
    takze sluzba, ktera nejaky nese, nesmi byt PASS. Baseline neurcuje
    JESTLI se to hlasi, jen JAK NAHLAS.

    `baseline_off is None` znamena "baseline neni k porovnani" a je to neco
    jineho nez `False` ("v baselinu bezel"). Splacnuti obou dohromady je
    duvod, proc je tahle funkce psana pres `is False` a ne pres `not`.

    Navrat `None` znamena "zadny radek nevznika" - zdravy prvek nema v bloku
    dostat radek, ktery nic nerika (R-1).
    """
    if not subject_off and not baseline_off:
        return None
    if subject_off and baseline_off is False:
        return Outcome.BROKEN
    return Outcome.DEGRADED
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_deactivation.py::test_deactivation_outcome_table -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Update the existing matrix test to the new semantics**

V `tests/checks/test_deactivation.py` uprav parametry `test_matrix` — mění se
**dva** řádky ze čtyř:

```python
@pytest.mark.parametrize(
    "subject_off,baseline_off,expected",
    [
        (True, True, Outcome.DEGRADED),
        (True, False, Outcome.BROKEN),
        (False, True, Outcome.DEGRADED),
        (True, None, Outcome.DEGRADED),
    ],
)
def test_matrix(subject_off, baseline_off, expected):
```

A přepiš modulový docstring souboru (řádky 1–6), protože tvrdí zrušenou
sémantiku:

```python
"""Testy checku deaktivace.

Matice je cela pointa: deaktivovana sluzba neni nikdy PASS, ani kdyz byla
deaktivovana i v baselinu - konfigurace by deaktivovane prvky bezne
obsahovat nemela (rozhodnuti uzivatele z 2026-08-04). Sluzba, ktera na
starem zarizeni bezela a na novem je deaktivovana, je FAIL - migrace
nedokoncena.
"""
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_deactivation.py -q`
Expected: FAIL, 2 failed — `test_matrix[True-True-degraded]` a `test_matrix[True-None-degraded]`

- [ ] **Step 7: Convert `DeactivationStateCheck` to the shared table**

V `migration_validator/checks/deactivation.py` nahraď tělo `run()` (řádky
32–87) tímto. Zprávy zůstávají doslova ty samé u větví, které se nemění —
mění se jen `Outcome` a přibývá jedna zpráva:

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        subject_off = ctx.scope.is_deactivated
        baseline_off = (
            ctx.baseline_scope.is_deactivated
            if ctx.baseline_scope is not None
            else None
        )
        reason = ctx.scope.deactivation_reason

        outcome = deactivation_outcome(subject_off, baseline_off)
        if outcome is None:
            return []

        if subject_off and baseline_off is None:
            return [
                Finding(
                    outcome,
                    f"sluzba je v konfiguraci deaktivovana ({reason}), "
                    "baseline neni k porovnani",
                    label=self.label,
                    value=reason,
                )
            ]

        if subject_off and baseline_off:
            return [
                Finding(
                    outcome,
                    f"sluzba je deaktivovana ({reason}) stejne jako v baseline",
                    label=self.label,
                    value=reason,
                    baseline_value=ctx.baseline_scope.deactivation_reason,
                )
            ]

        if subject_off:
            return [
                Finding(
                    outcome,
                    f"sluzba v baseline bezela, ted je deaktivovana ({reason}) "
                    "- migrace nedokoncena",
                    label=self.label,
                    value=reason,
                    baseline_value=ACTIVE,
                )
            ]

        return [
            Finding(
                outcome,
                "sluzba byla v baseline deaktivovana "
                f"({ctx.baseline_scope.deactivation_reason}), ted je aktivni",
                label=self.label,
                value=ACTIVE,
                baseline_value=ctx.baseline_scope.deactivation_reason,
            )
        ]
```

Uprav i modulový docstring (řádky 1–12), druhá věta tvrdí zrušenou sémantiku
— nahraď „ostatni checky nad deaktivovanou sluzbou SKIPnou (checks/base.py),
tenhle jediny ne, a rekne, co se zmenilo proti baseline" za „ostatni checky
nad deaktivovanou sluzbou SKIPnou (checks/base.py), tenhle jediny ne. Sam
SKIP nikdy nevydava: deaktivovana sluzba neni PASS ani SKIP, je to nalez."

- [ ] **Step 8: Run the full suite to see what else moved**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: FAIL, **2 failed** — `tests/test_engine.py::test_service_deactivated_on_both_sides_is_pass` a `tests/test_end_to_end.py::test_full_migration_run_has_no_unexplained_fail_or_warn`. Oba jsou změřené předem; jiný počet je nález — zastav a nahlas.

- [ ] **Step 9: Fix and rename the engine test**

V `tests/test_engine.py` nahraď `test_service_deactivated_on_both_sides_is_pass`
(řádky 568–583) tímto. **Název se mění povinně** — „is_pass" by po změně lhal:

```python
def test_service_deactivated_on_both_sides_is_warn():
    """Deaktivovano na obou stranach = WARN, ne PASS a ne SKIP.

    Konfigurace by deaktivovane prvky bezne obsahovat nemela, takze
    "nezmenilo se to" neni duvod mlcet - je to duvod hlasit potise
    (rozhodnuti uzivatele z 2026-08-04).

    Zabiji mutanta: navrat Outcome.OK v teto vetvi deactivation.py. S nim by
    Status.worst z jedine ne-SKIP hodnoty dal PASS a sluzba by ve strucnem
    vypisu zmizela mezi zdravymi.
    """
    result = api.evaluate(
        _deactivated(_new()), baseline=_deactivated(_old()), now=NOW
    )

    assert result.scopes
    assert result.scopes[0].status is Status.WARN
```

- [ ] **Step 10: Fix the end-to-end direction assertions**

V `tests/test_end_to_end.py` selhává smyčka na řádcích 58–62: deaktivovaná
služba **bez spárovaného baselinu** nově dá WARN se zprávou „baseline neni k
porovnani", kterou smyčka nezná. Nahraď smyčku (řádky 58–62):

```python
    for check in deactivation_checks:
        if check.status is Status.FAIL:
            assert "migrace nedokoncena" in check.message
        else:
            # Dve ruzne cesty k WARN, a splacnout je dohromady by zakrylo
            # obracene poradi: "ted je aktivni" je zmena proti baselinu,
            # "baseline neni k porovnani" je nesparovana sluzba, ktera je
            # deaktivovana a porovnat se nema s cim. Od vlny 9 je i druha
            # z nich WARN, ne SKIP.
            assert (
                "ted je aktivni" in check.message
                or "baseline neni k porovnani" in check.message
            )
```

- [ ] **Step 11: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: PASS, **663 passed, 0 skipped** (657 výchozích + 6 z nové parametrizované matice)

- [ ] **Step 12: Commit**

```bash
git add migration_validator/checks/deactivation.py tests/checks/test_deactivation.py tests/test_engine.py tests/test_end_to_end.py
git commit -m "feat: deaktivace ma jednu sdilenou tabulku a nikdy nedava PASS

Sluzba deaktivovana i v baselinu prestava byt PASS, deaktivovana sluzba
bez baselinu prestava byt SKIP. Obe nove davaji WARN."
```

- [ ] **Step 13: Run the mutant (over committed work)**

Mutant míří do produkčního kódu, ne do testů:

```bash
# Mutant: splacne rozdil mezi "v baselinu bezel" a "baseline neni".
sed -i 's/    if subject_off and baseline_off is False:/    if subject_off and not baseline_off:/' migration_validator/checks/deactivation.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: FAIL. Musí padnout **jak** `test_deactivation_outcome_table[True-None-...]`, **tak** aspoň jeden test jdoucí přes skutečnou cestu (`tests/test_engine.py` nebo `tests/test_end_to_end.py`). Kdyby padl jen ten první, je nová sémantika pokrytá jen nad datovou strukturou — to je přesně vada, za kterou vlna 8 zaplatila regresí. V tom případě přidej test přes `api.evaluate` a opakuj.

- [ ] **Step 14: Revert the mutant**

```bash
git checkout -- migration_validator/checks/deactivation.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: PASS, 663 passed. `git status --porcelain` musí být prázdný.

---

## Task 2: Statická routa podle sdílené tabulky

**Files:**
- Modify: `migration_validator/checks/routes.py:83-147`
- Test: `tests/checks/test_routes.py`

**Interfaces:**
- Consumes: `deactivation_outcome(subject_off: bool, baseline_off: bool | None) -> Outcome | None` z `migration_validator.checks.deactivation` (úloha 1).
- Produces: nic, co by pozdější úloha volala.

**Kontext, který implementer nemá:** `StaticRouteStatusCheck.run` dnes iteruje
sjednocení `configured | subject | baseline` (`routes.py:100`) a identitu routy
tvoří dvojice `(rib, prefix)`. Baseline **záměr** (ne měření) je dostupný přes
`ctx.baseline_scope.selectors.static_routes`; `ctx.baseline_scope` je `None`
v běhu bez baselinu a u nespárované služby. Příznak se čte
`route.get("active", True) is False` — default `True` je tam schválně, aby
jednotkový test nemusel psát klíč, který netestuje.

**Rozhodnutí plánu, které tahle úloha provádí:** řádek 4 tabulky („zapnuto v
subjectu, vypnuto v baselinu") se u routy **neuplatní** — viz „Rozhodnutí,
které plán dělá nad rámec specu" nahoře. `deactivation_outcome()` se volá jen
pro routu deaktivovanou v subjectu.

- [ ] **Step 1: Write the failing tests**

Do `tests/checks/test_routes.py` přidej za `test_deactivated_route_yields_skip_and_sibling_stays_ok`:

```python
def _scope_with_baseline_routes(subject_routes, baseline_routes):
    """Dva scopy - zamer subjektu a zamer baselinu.

    Baseline zamer nese `ctx.baseline_scope`, ne `ctx.baseline`: v `baseline`
    jsou namerena fakta, priznak deaktivace je v inventory. Test, ktery by
    priznak hledal ve faktech, by meril neco jineho, nez check cte.
    """
    return _scope(subject_routes), _scope(baseline_routes)


def test_route_deactivated_in_subject_but_active_in_baseline_is_broken():
    """V baselinu routa bezela, ted je vypnuta - migrace nedokoncena.

    Tohle je jediny radek tabulky, ktery u routy dava FAIL. Bez nej by
    nedokoncena migrace podprvku vypadala stejne jako vedomy dlouhodoby
    stav.
    """
    route = {"rib": "inet.0", "prefix": "10.0.0.0/8", "next_hop": ["1.1.1.1"]}
    scope, baseline_scope = _scope_with_baseline_routes(
        [{**route, "active": False}], [{**route, "active": True}]
    )
    ctx = CheckContext(
        scope=scope,
        subject={"routes": {}},
        baseline={"routes": {}},
        baseline_scope=baseline_scope,
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)
    by_label = {finding.label: finding for finding in findings}

    assert by_label["inet.0 10.0.0.0/8"].outcome is Outcome.BROKEN
    assert "v baseline bezela" in by_label["inet.0 10.0.0.0/8"].message


def test_route_deactivated_in_both_snapshots_is_degraded():
    """Vypnuta i predtim - porad nalez, jen tissi.

    Zabiji mutanta: navrat Outcome.OK pro tuhle dvojici. S nim by sluzba s
    dlouhodobe vypnutou routou byla PASS a jeji blok by se nerozbalil.
    """
    route = {"rib": "inet.0", "prefix": "10.0.0.0/8", "next_hop": ["1.1.1.1"]}
    scope, baseline_scope = _scope_with_baseline_routes(
        [{**route, "active": False}], [{**route, "active": False}]
    )
    ctx = CheckContext(
        scope=scope,
        subject={"routes": {}},
        baseline={"routes": {}},
        baseline_scope=baseline_scope,
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].outcome is Outcome.DEGRADED


def test_route_active_now_deactivated_in_baseline_gets_no_deactivation_row():
    """Znovuzapnuta routa neni varovani - je to zlepseni (R-2).

    Radek 4 tabulky se u podprvku neuplatnuje: routa, ktera je ted aktivni,
    zadny deaktivovany prvek v konfiguraci nema. Jeji stav nese normalni
    stavovy radek, ne radek o deaktivaci.

    Zabiji mutanta: volani deactivation_outcome() i pro routu s
    `active is not False`. S nim by kazda znovuzapnuta routa pridala WARN na
    zdravou sluzbu.
    """
    route = {"rib": "inet.0", "prefix": "10.0.0.0/8", "next_hop": ["1.1.1.1"]}
    scope, baseline_scope = _scope_with_baseline_routes(
        [{**route, "active": True}], [{**route, "active": False}]
    )
    subject_routes = {
        "inet.0": {"10.0.0.0/8": {"next_hop": ["1.1.1.1"], "via": ["et-0/0/8.13"], "active": True}}
    }
    ctx = CheckContext(
        scope=scope,
        subject={"routes": subject_routes},
        baseline={"routes": subject_routes},
        baseline_scope=baseline_scope,
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].outcome is Outcome.OK
    assert "deaktivovan" not in findings[0].message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_routes.py -q -k "deactivated_in_subject or deactivated_in_both or active_now_deactivated"`
Expected: FAIL — první dva na `Outcome.SKIP != BROKEN/DEGRADED`, třetí projde už teď (větev dnes neexistuje, což je správný výsledek — nechej ho, hlídá, aby ji úloha nepřidala omylem).

- [ ] **Step 3: Read the baseline intent and call the table**

V `migration_validator/checks/routes.py` uprav `run()` — přidej za výpočet
`deactivated` (za řádek 95):

```python
        # Baseline ZAMER, ne baseline mereni. Priznak deaktivace je v
        # inventory, takze `ctx.baseline` (fakta) o nem nevi nic.
        # `ctx.baseline_scope` je None v behu bez baselinu i u nesparovane
        # sluzby - v obou pripadech je spravna odpoved "neni s cim
        # porovnat", ne "v baselinu byla aktivni".
        baseline_routes = (
            ctx.baseline_scope.selectors.static_routes
            if ctx.baseline_scope is not None
            else []
        )
        baseline_deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_routes
            if route.get("active", True) is False
        }
        baseline_configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_routes
        }
```

Do volání `self._finding(...)` (řádky 102–110) přidej argument:

```python
                    deactivated=identity in deactivated,
                    baseline_deactivated=(
                        identity in baseline_deactivated
                        if identity in baseline_configured
                        else None
                    ),
```

V signatuře `_finding` (řádky 113–125) přidej parametr:

```python
        baseline_deactivated: bool | None,
```

A nahraď větev `if deactivated and subject is None:` (řádky 129–147):

```python
        if deactivated and subject is None:
            # Radek 4 tabulky (aktivni ted, vypnuta v baselinu) se sem
            # nedostane a nedostat se nema: taková routa zadny deaktivovany
            # prvek v konfiguraci nenese a jeji stav nese normalni radek.
            # Zlepseni neni varovani (R-2).
            #
            # Kdyz deaktivovana routa v tabulce presto je, sem se nedostane
            # taky - to uz je skutecny rozpor konfigurace se stavem a chova
            # se jako dosud.
            outcome = deactivation_outcome(True, baseline_deactivated)
            message = (
                f"{rib} {prefix}: v baseline bezela, ted je v konfiguraci "
                "deaktivovana - migrace nedokoncena"
                if outcome is Outcome.BROKEN
                else f"{rib} {prefix}: routa je v konfiguraci deaktivovana"
            )
            return Finding(
                outcome,
                message,
                label=label,
                group=group,
                family=family,
                value="deaktivovana",
                baseline_value=was,
                baseline=baseline,
            )
```

Přidej import nahoře v souboru:

```python
from migration_validator.checks.deactivation import deactivation_outcome
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_routes.py -q`
Expected: FAIL, 1 failed — `test_deactivated_route_yields_skip_and_sibling_stays_ok` (očekává SKIP). To je ten test, který úloha mění v dalším kroku.

- [ ] **Step 5: Update and rename the existing test**

V `tests/checks/test_routes.py` přejmenuj
`test_deactivated_route_yields_skip_and_sibling_stays_ok` na
`test_deactivated_route_warns_on_own_row_and_sibling_stays_ok` — „yields_skip"
by po změně lhalo. Uprav docstring (řádky 471–476, druhý odstavec o třetí
routě **zůstává beze změny**, hlídá citlivost na mutanta) a aserci:

```python
def test_deactivated_route_warns_on_own_row_and_sibling_stays_ok():
    """Deaktivovana routa varuje na svem radku a sourozence nestrhne.

    Od vlny 9 uz nedava SKIP: deaktivovany prvek konfigurace je sam o sobe
    nalez, takze sluzba s nim nesmi byt PASS. Co plati dal, je granularita -
    varovani je na jednom radku a zdrava routa vedle zustava OK.
```

(zbytek docstringu ponech doslova) a aserce:

```python
    assert by_label["inet.0 10.0.0.0/8"].outcome is Outcome.DEGRADED
    assert by_label["inet.0 10.0.0.0/8"].value == "deaktivovana"
    assert by_label["inet.0 10.1.0.0/16"].outcome is Outcome.OK
    assert by_label["inet.0 10.2.0.0/16"].outcome is Outcome.BROKEN
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: PASS, **666 passed, 0 skipped**

- [ ] **Step 7: Commit**

```bash
git add migration_validator/checks/routes.py tests/checks/test_routes.py
git commit -m "feat: deaktivovana staticka routa varuje misto SKIPu

Routa vypnuta v subjectu a bezici v baselinu dava FAIL 'migrace
nedokoncena'; ostatni pripady WARN. Znovuzapnuta routa radek o
deaktivaci nedostava - zlepseni neni varovani (R-2)."
```

- [ ] **Step 8: Run the mutant (over committed work)**

```bash
# Mutant: baseline zamer se nikdy neprecte, takze BROKEN vetev zmizi.
sed -i 's/            ctx.baseline_scope.selectors.static_routes/            []  # MUTANT/' migration_validator/checks/routes.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: FAIL — `test_route_deactivated_in_subject_but_active_in_baseline_is_broken`. Kdyby prošel, znamená to, že baseline záměr nikdo neměří — zastav a nahlas.

- [ ] **Step 9: Revert the mutant**

```bash
git checkout -- migration_validator/checks/routes.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: PASS, 666 passed. `git status --porcelain` prázdný.

---

## Task 3: BGP peer podle sdílené tabulky

**Files:**
- Modify: `migration_validator/checks/bgp.py:53-136`
- Test: `tests/checks/test_bgp.py`

**Interfaces:**
- Consumes: `deactivation_outcome(subject_off: bool, baseline_off: bool | None) -> Outcome | None` z `migration_validator.checks.deactivation` (úloha 1).
- Produces: nic; úloha 4 pracuje v témž souboru, ale nad jinou částí `run()`.

**Kontext, který implementer nemá:** `_ctx()` v `tests/checks/test_bgp.py:13`
**nenastavuje `baseline_scope`**, takže je `None` — existující testy tedy
spadnou do větve „baseline není k porovnání" a dostanou `DEGRADED`. Nové testy
na `BROKEN` si `baseline_scope` musí předat.

`CheckContext` je `@dataclass` bez `frozen=True` (`checks/base.py:35`), takže
přiřazení po konstrukci by technicky prošlo. **Nedělej to** — protáhni
`baseline_scope` helperem `_ctx()`, jak to už dělá
`tests/checks/test_deactivation.py:29`. Krok 1 tuhle úpravu helperu obsahuje.

**Tahle úloha sjednocení zdrojů peerů NEDĚLÁ** — to je úloha 4. Tady se mění
jen `Outcome` u peerů, které check už dnes vidí.

- [ ] **Step 1: Write the failing tests**

Nejdřív rozšiř helper `_ctx()` (`tests/checks/test_bgp.py:13-30`) o baseline
záměr. Scope se staví stejným tvarem jako subjektový, jen z jiných seznamů:

```python
def _scope_of(bgp_neighbors, bgp_neighbors_inactive):
    return Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"],
            bgp_neighbors=list(bgp_neighbors),
            bgp_neighbors_inactive=list(bgp_neighbors_inactive),
        ),
    )


def _ctx(
    subject,
    baseline=None,
    config=None,
    bgp_neighbors=None,
    bgp_neighbors_inactive=None,
    baseline_neighbors=None,
    baseline_neighbors_inactive=None,
):
    """Baseline ZAMER se predava sem, ne prirazenim po konstrukci.

    CheckContext neni frozen, takze `ctx.baseline_scope = ...` by proslo, ale
    test, ktery si context prestavuje az po sestaveni, obchazi tvar, ktery
    engine skutecne stavi.
    """
    scope = _scope_of(
        ["198.11.13.2"] if bgp_neighbors is None else bgp_neighbors,
        bgp_neighbors_inactive or [],
    )
    baseline_scope = (
        _scope_of(baseline_neighbors or [], baseline_neighbors_inactive or [])
        if baseline_neighbors is not None or baseline_neighbors_inactive is not None
        else None
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        baseline_scope=baseline_scope,
        config=config or default_config(),
        failed_collectors={},
    )
```

Pak přidej za `test_service_with_only_deactivated_peers_skips_per_peer`:

```python
def test_peer_deactivated_in_subject_but_active_in_baseline_is_fail():
    """V baselinu peer bezel, ted je vypnuty - migrace nedokoncena."""
    ctx = _ctx(
        {"bgp": {}},
        baseline={"bgp": {}},
        bgp_neighbors=[],
        bgp_neighbors_inactive=["198.11.13.9"],
        baseline_neighbors=["198.11.13.9"],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "migrace nedokoncena" in results[0].message


def test_peer_deactivated_in_both_snapshots_is_warn():
    """Vypnuty i predtim - porad nalez, jen tissi.

    Zabiji mutanta: navrat Outcome.OK pro tuhle dvojici. S nim by sluzba s
    dlouhodobe vypnutym peerem byla PASS.
    """
    ctx = _ctx(
        {"bgp": {}},
        baseline={"bgp": {}},
        bgp_neighbors=[],
        bgp_neighbors_inactive=["198.11.13.9"],
        baseline_neighbors_inactive=["198.11.13.9"],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.WARN
```

Ověř, že rozšíření helperu neshodilo žádný existující test v souboru —
`_ctx()` volají desítky testů a nové parametry musí být čistě aditivní:
`.venv/bin/python -m pytest -o addopts="" tests/checks/test_bgp.py -q`

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_bgp.py -q -k "deactivated_in_subject or deactivated_in_both"`
Expected: FAIL, 2 failed — oba na `Status.SKIP`

- [ ] **Step 3: Convert the inactive branch to the shared table**

V `migration_validator/checks/bgp.py` nahraď smyčku na řádcích 123–135:

```python
        # Deaktivovany peer, pro ktery presto prisla session, se sem
        # nedostane (filtr `peer not in peers` vys) a projde normalni vetvi -
        # je to rozpor konfigurace se stavem a ma byt videt.
        #
        # Radek 4 tabulky (aktivni ted, vypnuty v baselinu) se sem nedostane
        # taky: takovy peer je v `peers` nebo v `bgp_neighbors`, ne v
        # `inactive`. Nedostat se tam ma - znovuzapnuty peer zadny
        # deaktivovany prvek nenese a zlepseni neni varovani (R-2).
        baseline_inactive = (
            ctx.baseline_scope.selectors.bgp_neighbors_inactive
            if ctx.baseline_scope is not None
            else []
        )
        baseline_active = (
            ctx.baseline_scope.selectors.bgp_neighbors
            if ctx.baseline_scope is not None
            else []
        )
        for peer in sorted(inactive):
            if peer in baseline_inactive:
                baseline_off = True
            elif peer in baseline_active:
                baseline_off = False
            else:
                baseline_off = None
            outcome = deactivation_outcome(True, baseline_off)
            message = (
                f"peer {peer} v baseline bezel, ted je v konfiguraci "
                "deaktivovan - migrace nedokoncena"
                if outcome is Outcome.BROKEN
                else f"peer {peer} je v konfiguraci deaktivovan"
            )
            findings.append(
                Finding(
                    outcome,
                    message,
                    label=f"BGP status ({peer})",
                    family=peer_family(peer),
                    value="deaktivovan",
                )
            )
        return findings
```

Přidej import nahoře v souboru:

```python
from migration_validator.checks.deactivation import deactivation_outcome
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_bgp.py -q`
Expected: FAIL, 2 failed — `test_deactivated_peer_without_session_yields_skip` a `test_service_with_only_deactivated_peers_skips_per_peer`. Ty úloha mění v dalším kroku.

- [ ] **Step 5: Update and rename the two existing tests**

Oba názvy po změně lžou („yields_skip", „skips_per_peer"). V
`tests/checks/test_bgp.py` nahraď řádky 112–144:

```python
def test_deactivated_peer_without_session_warns():
    """Deaktivovany peer bez session se v reportu objevi jako WARN.

    Bez teto vetve by z reportu zmizel uplne a operator by nepoznal, ze
    sluzba takoveho peera v konfiguraci vubec ma. Od vlny 9 uz to neni SKIP:
    deaktivovany prvek konfigurace je sam o sobe nalez.
    """
    ctx = _ctx(
        {"bgp": {"198.11.13.2": _peer(state="Established")}},
        bgp_neighbors=["198.11.13.2"],
        bgp_neighbors_inactive=["198.11.13.9"],
    )
    results = run_check(BgpSessionStateCheck(), ctx)
    by_label = {result.label: result for result in results}

    assert by_label["BGP status (198.11.13.9)"].status is Status.WARN
    assert by_label["BGP status (198.11.13.2)"].status is Status.PASS


def test_service_with_only_deactivated_peers_warns_per_peer():
    """Jediny peer sluzby je deaktivovany - nesmi to spadnout do 'zadny peer'.

    Zabiji mutanta: ponechany predcasny navrat `if not peers:`.
    """
    ctx = _ctx(
        {"bgp": {}},
        bgp_neighbors=[],
        bgp_neighbors_inactive=["198.11.13.9"],
    )
    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.WARN
    assert results[0].value == "deaktivovan"
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: PASS, **668 passed, 0 skipped**

- [ ] **Step 7: Commit**

```bash
git add migration_validator/checks/bgp.py tests/checks/test_bgp.py
git commit -m "feat: deaktivovany BGP peer varuje misto SKIPu

Peer vypnuty v subjectu a bezici v baselinu dava FAIL 'migrace
nedokoncena'; ostatni pripady WARN."
```

- [ ] **Step 8: Run the mutant (over committed work)**

```bash
# Mutant: baseline zamer se nikdy neprecte.
sed -i 's/            ctx.baseline_scope.selectors.bgp_neighbors$/            []  # MUTANT/' migration_validator/checks/bgp.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: FAIL — `test_peer_deactivated_in_subject_but_active_in_baseline_is_fail`. Ověř, že `sed` opravdu zasáhl (`grep -n MUTANT migration_validator/checks/bgp.py`); kdyby ne, mutant nic neměří a je třeba ho upravit ručně.

- [ ] **Step 9: Revert the mutant**

```bash
git checkout -- migration_validator/checks/bgp.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: PASS, 668 passed. `git status --porcelain` prázdný.

---

## Task 4: Bod 15 — `BgpSessionStateCheck` iteruje sjednocení, ne měření

**Files:**
- Modify: `migration_validator/checks/bgp.py:53-152`
- Test: `tests/checks/test_bgp.py`

**Interfaces:**
- Consumes: `deactivation_outcome()` (úloha 1), převedená větev deaktivovaných peerů (úloha 3).
- Produces: nic.

**Kontext, který implementer NUTNĚ potřebuje — změřeno:**

`Scope.select` (`models/scope.py:163`) filtruje **měření podle záměru**:

```python
bgp = {peer: data for peer, data in (facts.get("bgp") or {}).items()
       if peer in self.selectors.bgp_neighbors
       or peer in self.selectors.bgp_neighbors_inactive}
```

Nakonfigurovaný peer, pro kterého žádná session nepřišla, tedy v
`ctx.subject["bgp"]` **není a být nemůže**. Identitu takového peera lze vzít
**jedině ze selektorů**. Kdo novou větev postaví nad `ctx.subject`, napíše
kód, který nikdy nic nenajde — a test nad ručně složeným `subject` mu to
potvrdí jako správné. Přesně tenhle tvar chyby stál vlnu 8 CRITICAL.

`Scope.select` se v téhle úloze **nemění.** Viz odškrtnutí výčtu nahoře.

**Změřený výchozí stav (spec, Nález 1):**

```
-- aktivni nakonfigurovany peer bez session
   skip      sluzba nema zadne BGP peery      <- lez, v konfiguraci peer JE
-- aktivni peer bez session + jiny se session
   ok        192.0.2.9: Established           <- 192.0.2.1 zmizel beze stopy
-- peer byl v baseline, v subjektu neni
   skip      sluzba nema zadne BGP peery
```

- [ ] **Step 1: Write the failing tests**

Do `tests/checks/test_bgp.py` přidej:

```python
def test_configured_active_peer_without_session_is_fail():
    """Nakonfigurovany peer, ktery nenavazal session, musi byt videt.

    Dnes zmizi beze stopy nebo vyrobi lzivou hlasku 'sluzba nema zadne BGP
    peery'. Zrcadli chovani checks/routes.py: 'nakonfigurovana, ale neni v
    routovaci tabulce' je BROKEN.

    Zabiji mutanta: iterace jen pres `peers` misto pres sjednoceni.
    """
    ctx = _ctx({"bgp": {}}, bgp_neighbors=["198.11.13.2"])

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "session neexistuje" in results[0].message


def test_configured_peer_without_session_does_not_hide_behind_a_sibling():
    """Sourozenec se session nesmi bezsessioveho peera prekryt.

    Bez tohoto testu by mutant, ktery novou vetev spusti jen kdyz je `peers`
    prazdne, prosel: test vys ma `peers` prazdne, takze by ho nechytil.
    """
    ctx = _ctx(
        {"bgp": {"198.11.13.9": _peer(state="Established")}},
        bgp_neighbors=["198.11.13.2", "198.11.13.9"],
    )

    results = run_check(BgpSessionStateCheck(), ctx)
    by_label = {result.label: result for result in results}

    assert by_label["BGP status (198.11.13.2)"].status is Status.FAIL
    assert by_label["BGP status (198.11.13.9)"].status is Status.PASS


def test_peer_measured_only_in_baseline_is_fail():
    """Peer, ktery v baselinu bezel a v subjektu neni ani v konfiguraci.

    Zrcadli routes.py: 'v baseline byla, v subjektu neni'.
    """
    ctx = _ctx(
        {"bgp": {}},
        baseline={"bgp": {"198.11.13.5": _peer(state="Established")}},
        bgp_neighbors=[],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "v baseline byl, v subjektu neni" in results[0].message


def test_configured_peer_without_session_takes_identity_from_selectors():
    """Subject se stavi pres Scope.select(), ne rucne.

    Scope.select filtruje MERENI podle ZAMERU, takze nakonfigurovany peer
    bez session se do ctx.subject nedostane a dostat nemuze. Test nad rucne
    slozenym subjektem by prosel i nad implementaci, ktera identitu bere z
    ctx.subject - a ta by v provozu nenasla nikdy nic.

    Zabiji mutanta: identita brana z `ctx.subject["bgp"]` misto ze
    selektoru.
    """
    scope = Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"],
            bgp_neighbors=["198.11.13.2"],
        ),
    )
    # Fakta zarizeni nesou session UPLNE JINEHO peera - Scope.select ji
    # odfiltruje a subject vyjde prazdny, presne jako v provozu.
    facts = {"bgp": {"203.0.113.7": _peer(state="Established")}}
    ctx = CheckContext(
        scope=scope,
        subject=scope.select(facts),
        baseline=None,
        config=default_config(),
        failed_collectors={},
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].label == "BGP status (198.11.13.2)"
    assert results[0].status is Status.FAIL

```

Hlášku „sluzba nema zadne BGP peery" hlídá po kroku 6 přejmenovaný
`test_service_with_no_peers_at_all_skips` — nový test na ni tady schválně
nepřibývá, jinak by ho krok 6 zase mazal.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_bgp.py -q -k "configured_active_peer or hide_behind or measured_only_in_baseline or identity_from_selectors"`
Expected: FAIL, 4 failed

- [ ] **Step 3: Replace the iteration set**

V `migration_validator/checks/bgp.py` nahraď řádky 53–67 (začátek `run()`):

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        baseline_peers = (ctx.baseline or {}).get("bgp", {})
        configured = list(ctx.scope.selectors.bgp_neighbors)
        inactive = [
            peer
            for peer in ctx.scope.selectors.bgp_neighbors_inactive
            if peer not in peers
        ]

        # Identita peera se bere ze ZAMERU, ne z mereni. Scope.select()
        # (models/scope.py:163) filtruje merena fakta podle clenstvi, takze
        # nakonfigurovany peer bez session se do `peers` nedostane a dostat
        # nemuze. Kdo by iteroval jen `peers`, napsal by vetev, ktera v
        # provozu nikdy nic nenajde.
        universe = (
            set(configured)
            | set(ctx.scope.selectors.bgp_neighbors_inactive)
            | set(peers)
            | set(baseline_peers)
        )

        # Poradi je soucast pozadavku: sluzba, jejiz jediny peer je
        # deaktivovany nebo bez session, nesmi dostat 'nema zadne BGP peery' -
        # to by tvrdilo, ze v konfiguraci zadny neni. Hlaska je pravdiva
        # teprve kdyz je prazdne cele sjednoceni.
        if not universe:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery", value="zadny peer")]
```

- [ ] **Step 4: Add the two new branches**

V témž souboru přidej **před** `return findings` (tedy za smyčku
deaktivovaných peerů z úlohy 3):

```python
        # Peer, ktery ma byt a session pro nej neprisla. Zrcadli
        # checks/routes.py:163-178: 'nakonfigurovana, ale neni v tabulce' vs
        # 'v baseline byla, v subjektu neni'. Deaktivovane peery uz vyresila
        # smycka vys, proto se odectou.
        without_session = universe - set(peers) - set(ctx.scope.selectors.bgp_neighbors_inactive)
        for peer in sorted(without_session):
            in_config = peer in configured
            findings.append(
                Finding(
                    Outcome.BROKEN,
                    f"{peer}: nakonfigurovan, ale session neexistuje"
                    if in_config
                    else f"{peer}: v baseline byl, v subjektu neni",
                    label=f"BGP status ({peer})",
                    family=peer_family(peer),
                    value="bez session" if in_config else "chybi uplne",
                    baseline_value=(
                        str(baseline_peers[peer].get("state", "unknown"))
                        if peer in baseline_peers
                        else None
                    ),
                )
            )
        return findings
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -o addopts="" tests/checks/test_bgp.py -q`
Expected: FAIL, 2 failed — `test_session_findings_carry_peer_family` a `test_no_bgp_peers_skips`. Oba jsou změřené předem a úloha je opravuje v dalším kroku. **Jiný počet je nález — zastav a nahlas.**

- [ ] **Step 6: Fix the two tests built on the old premise**

Obojí je vada fixture, ne nové chování. `_ctx()` má
`bgp_neighbors=["198.11.13.2"]` jako default, takže oba testy měly ve scope
nakonfigurovaného peera, o kterém tvrdily, že tam není.

V `test_session_findings_carry_peer_family` (řádek 80) nahraď volání
`_ctx(subject)` za `_ctx(subject, bgp_neighbors=["152.11.13.2", "2001:abcd:11:13::b"])`
— to jsou přesně ti dva peeři, které má ten test v `subject`. A přidej mu
docstring, který dosud neměl:

```python
def test_session_findings_carry_peer_family():
    """Rodina se odvozuje z adresy peera, ne ze jmena RIB.

    Argument bgp_neighbors musi odpovidat peerum v subjektu: default
    `_ctx()` nese 198.11.13.2, ktery by od vlny 9 pridal radek
    "nakonfigurovan, ale session neexistuje" a do tohoto testu nepatri.
    """
```

`test_no_bgp_peers_skips` (řádky 106–109) **přejmenuj** a oprav jeho premisu —
jeho název lhal, protože přes default `_ctx()` měl scope jednoho peera
nakonfigurovaného:

```python
def test_service_with_no_peers_at_all_skips():
    """Hlaska 'nema zadne BGP peery' plati, jen kdyz zadny peer opravdu neni.

    Puvodni verze tohoto testu mela pres default `_ctx()` ve scope
    nakonfigurovaneho peera a presto tvrdila, ze zadny neni - byla to prave
    ta lez, kvuli ktere bod 15 vznikl.
    """
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {}}, bgp_neighbors=[]))[0]
    assert result.status is Status.SKIP
    assert "BGP" in result.message
```

Tenhle přejmenovaný test je jediné, co hlášku „sluzba nema zadne BGP peery"
hlídá — krok 1 k ní schválně žádný nový nepřidával.

- [ ] **Step 7: Fix the false message in `BgpPrefixCountsCheck`**

`bgp.py:152` nese totéž nepravdivé tvrzení. Check se **nesjednocuje** (peer
bez session countery nemá a řádek by zdvojoval nález ze session checku), mění
se jen znění:

```python
        if not peers:
            return [
                Finding(
                    Outcome.SKIP,
                    "zadna namerena BGP session, neni co porovnat",
                    value="zadna session",
                )
            ]
```

Zkontroluj, jestli na starou hlášku nebo hodnotu `"zadny peer"` neasertuje
nějaký test `BgpPrefixCountsCheck` — `grep -n "zadny peer" tests/` — a uprav ho.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: PASS, **672 passed, 0 skipped** (668 + 4 nové z kroku 1)

- [ ] **Step 9: Commit**

```bash
git add migration_validator/checks/bgp.py tests/checks/test_bgp.py
git commit -m "feat: BGP check iteruje sjednoceni zdroju peeru, ne mereni

Nakonfigurovany peer bez session prestal byt neviditelny a hlaska
'sluzba nema zadne BGP peery' prestala lhat - vydava se, teprve kdyz
je prazdne sjednoceni vsech ctyr zdroju."
```

- [ ] **Step 10: Run two mutants (over committed work)**

Dva, protože každý měří jinou vrstvu:

```bash
# Mutant A: identita z mereni misto ze zameru.
sed -i 's/            set(configured)/            set()  # MUTANT/' migration_validator/checks/bgp.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: FAIL — musí padnout **jak** `test_configured_active_peer_without_session_is_fail`, **tak** `test_configured_peer_without_session_takes_identity_from_selectors`. Druhý je ten, který dokazuje, že test nechodí mimo skutečnou cestu.

```bash
git checkout -- migration_validator/checks/bgp.py

# Mutant B: nova vetev se spousti, jen kdyz je `peers` prazdne.
sed -i 's/        for peer in sorted(without_session):/        for peer in (sorted(without_session) if not peers else []):  # MUTANT/' migration_validator/checks/bgp.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: FAIL — `test_configured_peer_without_session_does_not_hide_behind_a_sibling`. Kdyby padl jen `test_configured_active_peer_without_session_is_fail`, sourozenecký test nic neměří — zastav a nahlas.

- [ ] **Step 11: Revert the mutants**

```bash
git checkout -- migration_validator/checks/bgp.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: PASS. `git status --porcelain` prázdný.

---

## Task 5: Bod 14 přes skutečnou cestu a souhrnné countery

**Files:**
- Test: `tests/test_end_to_end.py`
- Modify: **nic v produkčním kódu.** Pokud tahle úloha potřebuje cokoli změnit v `migration_validator/`, je to nález — zastav a nahlas.

**Interfaces:**
- Consumes: chování z úloh 1–4.
- Produces: nic.

**Proč je tahle úloha nejpřísnější v celé vlně:** vlna 8 zaplatila regresí za
dva testy, které stavěly `subject` ručně a `Scope.select` obcházely. Bylo to
**potřetí** v tomhle projektu. Testy téhle úlohy musí jít přes
`api.evaluate` a `render`, ne nad ručně složeným `ServiceView` — jinak by
prošly, i kdyby `engine.py:145` SKIPy dál filtroval a bod 14 zůstal
neopravený.

**Kontext, který implementer nemá:** `tests/test_end_to_end.py:118` má helper
`_block_of(rendered, description, service_type)`, který ze zrenderovaného
výstupu vybere blok jedné služby. `render(result)` bez argumentu je **stručný**
výpis; `render(result, detail=True)` je detailní. Blok se rozbaluje podmínkou
`detail or view.status is not Status.PASS` (`text_report.py:390`).

- [ ] **Step 1: Write the failing end-to-end test**

Do `tests/test_end_to_end.py` přidej:

```python
def _deactivate_shared_route(old, new) -> str:
    """Vypne TUTEZ routu TEZE sluzby v obou snimcich a vrati jeji popis.

    Naivni "prvni scope se statickou routou v kazdem snimku" je vada: vnitrni
    break opousti jen vnitrni smycku a poradi scopu se mezi .4 a .5 lisit
    muze, takze by se v kazdem snimku vypnula jina sluzba. Vysledek by byl
    "v baselinu bezela, ted je vypnuta" = FAIL, ne WARN, ktery tenhle test
    meri - a selhani by vypadalo jako vada implementace.

    Parovat se musi i konkretni routa, ne jen sluzba: dve ruzne routy tehoz
    prefixu neexistuji, ale poradi v seznamu garantovane neni.
    """
    by_description = {}
    for snapshot, side in ((old, "old"), (new, "new")):
        for scope in snapshot.scopes:
            if scope.key is None:
                continue
            for route in scope.selectors.static_routes:
                key = (scope.key.description, str(route.get("rib")), str(route.get("prefix")))
                by_description.setdefault(key, {})[side] = route

    shared = sorted(key for key, sides in by_description.items() if len(sides) == 2)
    assert shared, "fixture nema zadnou statickou routu pritomnou v obou snimcich"

    target = shared[0]
    for route in by_description[target].values():
        route["active"] = False
    return target[0]


def test_deactivated_route_is_visible_without_detail(synthetic_snapshot):
    """Bod 14: zdrava sluzba s deaktivovanou routou se rozbali i bez --detail.

    Test jde pres api.evaluate a render, ne nad rucne slozenym ServiceView.
    Test nad ServiceView by prosel i nad rozbitou cestou, protoze by
    obesel prave to misto, kde se stav rozhoduje: engine.py:145 filtruje
    SKIPy pred Status.worst(), takze dokud deaktivace vyrabela SKIP,
    sluzba zustala PASS a text_report.py:390 jeji blok nerozbalil.

    Zabiji mutanta: navrat Outcome.SKIP misto DEGRADED v routes.py. Sluzba
    by zustala PASS, blok by se nerozbalil a SKIP radek by ve strucnem
    vypisu nebyl.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    target = _deactivate_shared_route(old, new)

    result = api.evaluate(new, baseline=old, now=NOW)
    rendered = render(result)

    affected = [scope for scope in result.scopes if scope.identity.get("description") == target]
    assert affected, f"sluzba {target} ve vysledku neni"
    assert affected[0].status is Status.WARN

    block = _block_of(rendered, target, affected[0].identity["service_type"])
    assert "deaktivovana" in block
```

- [ ] **Step 2: Run the test to verify it fails on `main`'s behaviour**

Run: `.venv/bin/python -m pytest -o addopts="" tests/test_end_to_end.py::test_deactivated_route_is_visible_without_detail -q`
Expected: **PASS**, protože úlohy 1–4 už doběhly. To je v pořádku — důkaz, že test opravdu měří, dodá mutant v kroku 6, ne tohle spuštění.

Pokud test **selže**, je to nález: buď fixture nemá statickou routu (pak uprav
výběr cíle a nahlas to), nebo se bod 14 neopravil a úlohy 1–4 mají díru.

- [ ] **Step 3: Write the failing counters test**

Nález 3 specu ukázal, že souhrnné countery u deaktivace nehlídá nikdo.

**Test patří do `tests/test_end_to_end.py`, ne do
`tests/reporting/test_text_report.py`.** Ten soubor staví `RunResult`
synteticky přes `_legacy_check()` a fixture `synthetic_snapshot` vůbec nezná;
test postavený tam by countery měřil nad ručně složeným výsledkem — a to je
přesně ta vrstva, které tahle úloha nevěří. Asertuje se na **zrenderovaný
řádek `Sluzby:`**, protože counter je vlastnost výstupu, ne enginu:

```python
def _counts_of_line(output: str, prefix: str) -> dict[str, int]:
    """Rozebere souhrnny radek na countery.

    Vlastni kopie helperu z tests/reporting/test_text_report.py - ten ho ma
    pro synteticke RunResulty a importovat ho sem by svazalo dva testovaci
    soubory kvuli dvema radkum.
    """
    line = next(l for l in output.splitlines() if l.strip().startswith(prefix))
    return {name: int(count) for count, name in re.findall(r"(\d+) (PASS|WARN|FAIL|SKIP)", line)}


def test_deactivated_element_moves_a_service_from_pass_to_warn(synthetic_snapshot):
    """Souhrn za sluzby musi deaktivovany prvek pocitat do WARN, ne do PASS.

    Nalez 3 specu vlny 9: tuhle vazbu nehlidal zadny test, takze zmena
    semantiky mohla countery rozpojit tise. Meri se na zrenderovanem radku
    'Sluzby:', ne na result.scopes - counter je vlastnost vystupu.

    Zabiji mutanta: navrat Outcome.OK nebo SKIP v routes.py. Sluzba by
    zustala v PASS a souhrn by tvrdil, ze je vsechno v poradku.
    """
    def snapshots():
        return (
            synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration"),
            synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration"),
        )

    old, new = snapshots()
    baseline_run = api.evaluate(new, baseline=old, now=NOW)
    before = _counts_of_line(render(baseline_run), "Sluzby:")

    # Cerstve snimky, ne ty uz vyhodnocene - deaktivace se zapisuje do
    # zameru a sdileny objekt mezi dvema behy by meril poradi volani.
    old, new = snapshots()
    target = _deactivate_shared_route(old, new)

    # Cil MUSI byt PASS pred zmenou, jinak aserce nize nemeri nic: sluzba,
    # ktera uz WARN byla, se do counteru nepresune a rozdil vyjde nula.
    target_before = [
        scope for scope in baseline_run.scopes
        if scope.identity.get("description") == target
    ]
    assert target_before, f"sluzba {target} ve vysledku neni"
    assert target_before[0].status is Status.PASS, (
        f"sluzba {target} nebyla pred zmenou PASS ({target_before[0].status}), "
        "test by presun counteru nemeril"
    )

    after = _counts_of_line(render(api.evaluate(new, baseline=old, now=NOW)), "Sluzby:")

    assert after["PASS"] == before["PASS"] - 1
    assert after["WARN"] == before["WARN"] + 1
```

Doplň `import re` na začátek `tests/test_end_to_end.py`, pokud tam ještě není.

- [ ] **Step 4: Run the counters test**

Run: `.venv/bin/python -m pytest -o addopts="" tests/test_end_to_end.py -q -k moves_a_service_from_pass_to_warn`
Expected: PASS.

Pokud selže aserce `sluzba ... nebyla pred zmenou PASS`, vybral helper první
sdílenou routu služby, která už je z jiného důvodu WARN nebo FAIL. Rozšiř
`_deactivate_shared_route` o volitelný filtr na množinu přípustných popisů a
předej mu popisy PASS služeb z `baseline_run` — a **nahlas, že fixture
PASS službu se sdílenou statickou routou nenabízí zadarmo**.

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```
Expected: PASS, 0 skipped

```bash
git add tests/test_end_to_end.py
git commit -m "test: bod 14 pres skutecnou cestu a countery u deaktivace

Oba testy jdou pres api.evaluate a render, ne nad rucne slozenym
ServiceView ani RunResultem - ty by prosly i nad rozbitou cestou."
```

- [ ] **Step 6: Run the layering mutant — the most important one in the wave**

Tenhle mutant **měří rozdíl mezi vrstvami**, místo aby ho tvrdil. Vrací
`engine.py:145` do stavu, kdy SKIPy filtroval, a přidává zpět SKIP v
`routes.py`:

```bash
# Vraci deaktivovanou routu na SKIP - tedy presne do stavu, kdy ji
# engine.py:145 odfiltroval pred Status.worst() a sluzba zustala PASS.
sed -i 's/            outcome = deactivation_outcome(True, baseline_deactivated)/            outcome = Outcome.SKIP  # MUTANT/' migration_validator/checks/routes.py
grep -n MUTANT migration_validator/checks/routes.py   # musi neco vypsat
.venv/bin/python -m pytest -o addopts="" -q
```

Pokud `grep` nic nevypíše, `sed` neseděl na odsazení — uprav řádek ručně.
Mutant, který nezasáhl, nic neměří.

Expected: FAIL. Musí padnout **oba** nové testy z téhle úlohy
(`test_deactivated_route_is_visible_without_detail`,
`test_service_with_deactivated_element_counts_as_warn`).

**Zapiš do commit message dalšího kroku, kolik testů mutant shodil a které.**
Pokud `test_deactivated_route_is_visible_without_detail` **neshodí**, test
nechodí přes skutečnou cestu a je bezcenný — zastav a nahlas.

- [ ] **Step 7: Revert the mutant**

```bash
git checkout -- migration_validator/checks/routes.py
.venv/bin/python -m pytest -o addopts="" -q
git status --porcelain
```

Expected: PASS, `git status --porcelain` prázdný.

---

## Závěrečné ověření celé vlny

Po dokončení úlohy 5, před whole-branch review:

- [ ] `.venv/bin/python -m pytest -o addopts="" -q` → **0 failed, 0 skipped**, počet ≥ 657
- [ ] `diff mx_parser.py evo_parser.py | wc -l` → **146**
- [ ] `git diff main --stat -- migration_validator/reporting/` → **prázdné** (akceptační kritérium 7)
- [ ] `grep -rn "schema_version" migration_validator/models/inventory.py migration_validator/models/snapshot.py` → **5** u obou (kritérium 8)
- [ ] `grep -rn "Outcome.SKIP" migration_validator/checks/deactivation.py migration_validator/checks/routes.py migration_validator/checks/bgp.py` → žádný výskyt **za deaktivovaný prvek** (kritérium 4). SKIPy z jiných důvodů (chybějící měření, „sluzba nema zadne BGP peery", „bez baseline") zůstávají a jsou v pořádku.
- [ ] `grep -rn "if not subject_off\|baseline_off is False" migration_validator/checks/` → jen v `deactivation.py`, tedy jedna implementace tabulky (kritérium 3)
- [ ] Ruční doklad ke kritériu 5:

```bash
.venv/bin/python -c "
from migration_validator.checks.bgp import BgpSessionStateCheck
from migration_validator.checks.base import CheckContext
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.config import default_config
scope = Scope(id='s1', kind='service', key=ScopeKey('ACME','IPVPN'),
              selectors=Selectors(bgp_neighbors=['192.0.2.1']))
ctx = CheckContext(scope=scope, subject={'bgp': {}}, baseline=None, config=default_config())
for f in BgpSessionStateCheck().run(ctx): print(f.outcome.value, '|', f.message)
"
```

Expected: `broken | 192.0.2.1: nakonfigurovan, ale session neexistuje`

---

## Poznámka pro whole-branch review

Vlna 8 našla svůj CRITICAL až v závěrečné review, protože vada vznikla v
**překladu specu do plánu**, ne v žádné úloze — každá z šesti prošla review
čistě. Reviewer téhle vlny má proto dvě věci navíc:

1. **Projít odškrtnutí výčtu pěti míst členství** (sekce nahoře) a u každého
   z pěti ověřit **měřením přes skutečnou cestu**, ne odvozením z kódu, že s
   ním plán naložil, jak tvrdí.
2. **Ověřit, že řádek 4 tabulky opravdu u podprvků nenastává** — plán to
   rozhodl nad rámec specu a odůvodnil pravidlem R-2. Pokud reviewer změří,
   že u routy nebo peera přece jen vzniká, je to nález.
