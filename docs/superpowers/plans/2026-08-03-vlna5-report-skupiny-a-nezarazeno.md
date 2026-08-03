# Vlna 5 — skupiny řádků, sekce NEZARAZENO a sjednocení link-local

> **Pro agentní pracovníky:** POVINNÁ SUB-SKILL: použij
> superpowers:subagent-driven-development (doporučeno) nebo
> superpowers:executing-plans a implementuj plán úlohu po úloze. Kroky mají
> checkbox (`- [ ]`) pro sledování postupu.

**Cíl:** Report přestane tisknout nerozlišitelné řádky BGP, začne vypisovat
`unassigned` a link-local predikáty budou mít jeden výskyt.

**Architektura:** `Finding`/`CheckResult` dostanou pole `group`. `view.py` z něj
poskládá `Group` uvnitř `Section`, `text_report.py` je vysází jako odsazený
nadpis a započítá do šířky bloku. Checky `bgp` a `routes` identitu přesunou
z popisku do skupiny. `NEZARAZENO` je nová sekce vedle `NESPAROVANO`.
Link-local predikáty se přestěhují do nového `migration_validator/addressing.py`.

**Návrh:** [`specs/2026-08-03-report-skupiny-a-nezarazeno-design.md`](../specs/2026-08-03-report-skupiny-a-nezarazeno-design.md)

**Tech stack:** Python 3.13, pytest, žádná nová závislost.

## Globální podmínky

- **Testy se pouští `.venv/bin/python -m pytest -o addopts=""`.** Bez
  `-o addopts=""` se přidá coverage a výstup se hůř čte.
- **Výchozí stav: 605 testů zelených, 0 přeskočených.** Po celé vlně musí být
  zelených **nejméně 605**; žádný se nesmí přeskočit.
- **Zámek parserů:** `diff mx_parser.py evo_parser.py | wc -l` = **146**. Tahle
  vlna se parserů nedotýká, takže číslo musí zůstat 146. Zkontroluj to na konci.
- **Nová diakritika se do souboru, který ji nemá, nezanáší.** Změřeno
  2026‑08‑03: z 94 souborů v `migration_validator/` a `tests/` ji nese
  **devět** — mimo jiné `checks/test_bgp.py`, `checks/test_routes.py`
  a `reporting/text_report.py`, tedy soubory, které tahle vlna mění.
  Převažující konvence je ASCII a `tests/reporting/test_text_report.py`
  měl před vlnou 5 nula výskytů. Řiď se souborem, do kterého píšeš: kde
  diakritika není, tam ji nezaváděj; kde už je, přepisovat ji není úkol
  téhle vlny. (Plán a spec diakritiku mají, ty jsou dokumentace.)
- **Každý test jmenuje mutanta, kterého zabíjí.** Domácí pravidlo z vlny 3.
  Docstring testu má říct, které konkrétní poškození kódu ten test shodí — a
  pokud to sourozenec hlídá lépe, má se toho výslovně zříct.
- **Mutantí bloky se pouštějí až PO commitu úlohy.** Končí
  `git checkout <soubor>`, takže na necommitnuté práci by zahodily samotnou
  implementaci, ne mutanta. Tahle past je zapsaná ve vlně 3 a při psaní tohohle
  plánu sklapla znovu — měření M2 až M4 se muselo dělat dvakrát.
- **Když ti měření nesedí se zadáním, má přednost měření.** Pravidlo z vlny 4.
  Nahlas to a nepředělávej měření, aby vyšlo podle plánu.

## Změřený výchozí stav

Všechno níž je **změřeno na prototypu 2026‑08‑03**, ne odhadnuto. Prototyp byl
postavený, změřený a zahozen (`git reset --hard`); v repu po něm nic není.

### Ripple ze změny popisků je 9 testů ve dvou souborech

Spec (AR‑42) říkal, že `grep` na dotčené popisky vrací 220 řádků, ale že je to
horní odhad. Skutečnost po nasazení celého prototypu:

```
tests/checks/test_bgp.py::test_session_findings_carry_peer_family
tests/checks/test_bgp.py::test_established_passes
tests/checks/test_bgp.py::test_prefix_counts_within_tolerance_pass
tests/checks/test_bgp.py::test_prefix_counts_below_tolerance_warn
tests/checks/test_bgp.py::test_prefix_tolerance_is_configurable
tests/checks/test_bgp.py::test_prefix_growth_is_not_a_problem
tests/checks/test_bgp.py::test_prefix_counts_are_reported_per_rib_not_summed
tests/checks/test_bgp.py::test_prefix_counts_produce_a_row_per_counter
tests/checks/test_routes.py::test_label_carries_rib_and_prefix
```

Žádný test v `tests/reporting/` nespadl. Kdyby ti při úlohách 4 a 5 spadlo
něco jiného nebo něco navíc, **je to nález** — zapiš ho a nahlas, neopravuj ho
tiše.

### Osm změřených mutantů

| # | poškození | co shodí |
|---|---|---|
| M1 | `group = f"BGP {peer}"` (bez RIB) v `checks/bgp.py` | `test_bgp_group_carries_peer_and_rib` |
| M2 | renderer tiskne neseskupené řádky **až za** skupinami | `test_rendered_block_puts_ungrouped_rows_above_the_first_group_header` |
| M3 | nadpisy skupin vypadnou z `max()` ve výpočtu šířky | `test_long_group_title_widens_the_block_frame` |
| M4 | `groups.sort(key=...)` — abecedně místo pořadím výskytu | `test_ungrouped_rows_stand_before_groups` |
| M5 | `all_rows()` vrátí jen `self.rows` | `test_long_group_title_widens_the_block_frame` |
| M6 | `is_link_local` v `addressing.py` vrací vždy `False` | 1 test v `tests/checks/test_reachability.py` **a** 2 v `tests/probes/test_ping.py` |
| M7 | sloupec `(TYP)` se v `NESPAROVANO` nedoplňuje na šířku | `test_unmatched_service_type_column_is_padded` |
| M8 | filtr se pustí i na `NEZARAZENO` | `test_unassigned_survives_a_filter_that_hides_every_scope` |

**M2 je nejdůležitější číslo v tomhle plánu.** První verze AR‑37 měla jediný
test — nad `view.py`. M2 ho **přežil**, protože mutant prohodí pořadí až
v rendereru; datová struktura ve `view.Section` zůstane správná. AR‑37 proto
dostává dva testy, jeden na každou stranu švu. Kdyby plán vznikl bez puštění
mutanta, předepsal by test, který nic neměří.

### Cílový tvar na skutečných datech

Vyrenderováno prototypem ze společných fixtures, ne kresleno:

```
 -- IPv4  152.11.13.1/30 ------------------------------------------------------
 PASS | ARP                                        : ? -> 152.11.13.2         |
 PASS | BFD (152.11.13.2)                          : Up                       |
 PASS | BGP status (152.11.13.2)                   : Established              |
 PASS | Ping                                       : 5/5                      |
   -- BGP 152.11.13.2 / inet.0
 PASS | active-prefix-count                        : 14                       |
 PASS | received-prefix-count                      : 14                       |
 PASS | accepted-prefix-count                      : 14                       |
 PASS | advertised-prefix-count                    : 3                        |
   -- Staticke routy
 PASS | inet.0 198.62.1.0/29                       : 152.11.13.2              |
 PASS | inet.0 198.62.2.0/24                       : 152.11.13.2              |
```

## Struktura souborů

| soubor | odpovědnost | úloha |
|---|---|---|
| `migration_validator/addressing.py` | **nový** — predikáty nad adresou a `Scope`, společné pro capture i evaluate | 1 |
| `migration_validator/models/result.py` | `group` na `Finding` a `CheckResult`, propsání do `to_dict` | 2 |
| `migration_validator/checks/base.py` | `run_check` přenese `finding.group` na `CheckResult` | 2 |
| `migration_validator/reporting/view.py` | `Group`, `Section.groups`, `Section.all_rows()`, zařazení řádků | 3 |
| `migration_validator/reporting/text_report.py` | sazba nadpisu skupiny, šířky, `NESPAROVANO`, `NEZARAZENO` | 4, 6, 7 |
| `migration_validator/checks/bgp.py` | skupina `BGP {peer} / {rib}`, peer v popisku `BGP status` | 5 |
| `migration_validator/checks/routes.py` | skupina `Staticke routy` | 5 |

---

## Úloha 1: Jeden výskyt link-local predikátů (AR-41)

Nezávislá na zbytku vlny — nedotýká se rendereru. Dělá se první, protože je
malá a uzavřená.

**Soubory:**
- Vytvořit: `migration_validator/addressing.py`
- Vytvořit: `tests/test_addressing.py`
- Upravit: `migration_validator/checks/reachability.py` — smazat
  `link_local_is_configured` (`:24-40`) a `is_link_local` (`:42-47`), přidat import
- Upravit: `migration_validator/probes/ping.py` — smazat `_is_link_local`
  (`:112-116`) a `_link_local_configured` (`:119-126`), přidat import,
  přejmenovat tři volání (`:160`, `:180`, `:186`)

**Rozhraní:**
- Produkuje: `migration_validator.addressing.is_link_local(address: str) -> bool`
  a `migration_validator.addressing.link_local_is_configured(scope: Scope) -> bool`

- [ ] **Krok 1: Napiš padající test**

Vytvoř `tests/test_addressing.py`:

```python
"""Predikaty nad adresami maji jeden vyskyt (AR-41).

Do vlny 5 existovaly dvakrat - v `checks/reachability.py` a v
`probes/ping.py`. Rozchazeni uz jednou zpusobilo chybu: Task 13b opravoval
docstring, ktery zasel nepravdive tvrzeni, zatimco druha kopie docstring
vubec nemela.
"""

import pytest

from migration_validator.addressing import is_link_local, link_local_is_configured
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _scope(local_ipv6):
    return Scope(
        id="svc:X:Internet",
        kind="service",
        key=ScopeKey("X", "Internet", None),
        selectors=Selectors(interfaces=["et-0/0/8.13"], local_ipv6=list(local_ipv6)),
    )


@pytest.mark.parametrize(
    "address,expected",
    [
        ("fe80::1", True),
        ("2001:db8::1", False),
        ("169.254.1.1", True),
        ("152.11.13.2", False),
        ("", False),
        ("neni adresa", False),
    ],
)
def test_is_link_local_recognises_both_families_and_survives_junk(address, expected):
    """Zabiji mutanta, ktery `except ValueError` zmeni na holy `return`.

    Prazdny retezec a nesmysl musi dat False, ne vyjimku - collector umi
    vratit oboji. Sourozenec `test_link_local_is_configured_*` tohle
    nehlida, ten se diva jen na selektory scopu.
    """
    assert is_link_local(address) is expected


def test_link_local_is_configured_finds_it_next_to_a_routable_address():
    """Zabiji mutanta, ktery podminku zmeni na vylucnost (`all` misto `any`).

    Rozhoduje pritomnost, ne vylucnost: sluzba muze mit link-local vedle
    bezne routovatelne adresy a link-local soused je pak legitimni cil.
    """
    assert link_local_is_configured(_scope(["2001:db8::1/64", "fe80::1/64"])) is True


def test_link_local_is_configured_is_false_without_one():
    assert link_local_is_configured(_scope(["2001:db8::1/64"])) is False


def test_link_local_is_configured_skips_unparsable_entries():
    """Zabiji mutanta, ktery `continue` v `except` zmeni na `return False`.

    Nesmyslny zaznam pred platnym link-localem nesmi hledani ukoncit.
    """
    assert link_local_is_configured(_scope(["nesmysl", "fe80::1/64"])) is True
```

- [ ] **Krok 2: Pusť test a ověř, že padá**

Spusť: `.venv/bin/python -m pytest tests/test_addressing.py -o addopts="" -q`
Očekávej: `ModuleNotFoundError: No module named 'migration_validator.addressing'`

- [ ] **Krok 3: Vytvoř modul**

Vytvoř `migration_validator/addressing.py`:

```python
"""Predikaty nad adresami, spolecne pro capture i evaluate.

Zije mimo `checks/` i `probes/` schvalne: obe vrstvy sem sahaji dolu,
stejne jako obe sahaji do `models/`. Kdyby predikaty vlastnila jedna z
nich, druha by na ni musela zaviset - capture na evaluate, nebo naopak.
"""

from __future__ import annotations

import ipaddress

from migration_validator.models.scope import Scope


def is_link_local(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_link_local
    except ValueError:
        return False


def link_local_is_configured(scope: Scope) -> bool:
    """Ma sluzba link-local adresu primo pod rozhranim?

    Link-local sousede se objevi u kazdeho IPv6 rozhrani a o zakaznicke
    sluzbe nerikaji nic. Existuji ale nasazeni, kde sluzba pouziva link-local
    - staci, aby mela mezi nakonfigurovanymi adresami jednu link-local, klidne
    i vedle bezne routovatelne - pak je link-local soused legitimni cil.
    Rozhoduje konfigurace (pritomnost, ne vylucnost), ne heuristika.
    """
    for address in scope.selectors.local_ipv6:
        try:
            if ipaddress.ip_interface(address).ip.is_link_local:
                return True
        except ValueError:
            continue
    return False
```

- [ ] **Krok 4: Pusť test a ověř, že prochází**

Spusť: `.venv/bin/python -m pytest tests/test_addressing.py -o addopts="" -q`
Očekávej: `9 passed`

- [ ] **Krok 5: Smaž obě kopie a přesměruj volající**

V `migration_validator/checks/reachability.py` smaž celé definice
`link_local_is_configured` a `is_link_local` (řádky 24 až 47 včetně obou
prázdných řádků mezi nimi) a přidej import hned nad `from
migration_validator.checks.base import ...`:

```python
from migration_validator.addressing import is_link_local, link_local_is_configured
```

Volání na `:147` a `:152` se **nemění** — jména jsou stejná.

V `migration_validator/probes/ping.py` smaž definice `_is_link_local`
a `_link_local_configured` (řádky 112 až 126) a přidej stejný import mezi
ostatní importy z `migration_validator`. Pak přejmenuj tři volání:

```python
        keep_link_local = link_local_is_configured(scope)
```
```python
                        str(entry["interface"]) if is_link_local(str(entry["ip"])) else None,
```
```python
                    and (keep_link_local or not is_link_local(str(entry["ip"])))
```

Pokud po smazání zůstane `import ipaddress` v některém souboru nepoužitý,
smaž ho. Ověř: `grep -n "ipaddress" migration_validator/probes/ping.py
migration_validator/checks/reachability.py`.

- [ ] **Krok 6: Pusť celou sadu**

Spusť: `.venv/bin/python -m pytest -o addopts="" -q`
Očekávej: `614 passed` (605 + 9 nových). Ani jeden nesmí spadnout — tahle
úloha nemění chování.

- [ ] **Krok 7: Commit**

```bash
git add migration_validator/addressing.py tests/test_addressing.py \
        migration_validator/checks/reachability.py migration_validator/probes/ping.py
git commit -m "refactor: link-local predikaty maji jeden vyskyt v addressing.py (AR-41)"
```

- [ ] **Krok 8: Pusť mutanta M6 (až teď, po commitu)**

```bash
sed -i 's|        return ipaddress.ip_address(address).is_link_local|        return False|' \
    migration_validator/addressing.py
.venv/bin/python -m pytest tests/checks/test_reachability.py tests/probes/ -o addopts="" -q
git checkout -- migration_validator/addressing.py
```

Očekávej: **3 failed** — jeden v `tests/checks/test_reachability.py`
(`test_nd_ignores_link_local_when_not_configured`) a dva v
`tests/probes/test_ping.py` (`test_link_local_target_carries_interface`,
`test_link_local_ignored_when_not_configured`).

**Proč zrovna tenhle mutant:** AR‑41 netvrdí „predikát je správný", to hlídá
`tests/test_addressing.py`. Tvrdí, že **obě volající místa přes něj opravdu
tečou**. Kdyby jedno z nich mělo dál vlastní kopii, tenhle mutant by shodil
testy jen na jedné straně. Test „modul existuje" by tohle neizměřil.

---

## Úloha 2: Pole `group` na modelu a jeho propsání frameworkem (AR-36)

**Soubory:**
- Upravit: `migration_validator/models/result.py` — `Finding` (`:80-104`),
  `CheckResult` (`:101-137`), `CheckResult.to_dict`
- Upravit: `migration_validator/checks/base.py` — `run_check`, sestavení
  `CheckResult` na konci souboru
- Upravit: `tests/reporting/test_text_report.py` — přidat test na JSON

**Rozhraní:**
- Produkuje: `Finding(..., group: str | None = None)` a
  `CheckResult(..., group: str | None = None)`. `run_check` přenese
  `finding.group` na `CheckResult.group` beze změny. `to_dict` klíč `group`
  přidá jen tehdy, když není `None`.

- [ ] **Krok 1: Napiš padající test**

Přidej na konec `tests/checks/test_base.py`:

```python
def test_group_travels_from_finding_to_check_result():
    """Zabiji mutanta, ktery v run_check() `group=finding.group` vypusti.

    Bez tohohle by skupina koncila u Findingu a renderer by ji nikdy
    nevidel - vsechny radky by spadly mezi neseskupene a nadpisy by nikdy
    nevznikly.
    """

    class _Grouped(Check):
        id = "grouped_probe"
        title = "Zkouska skupiny"
        label = "Zkouska"

        def run(self, ctx):
            return [
                Finding(Outcome.OK, "s", label="a", group="Skupina"),
                Finding(Outcome.OK, "b", label="b"),
            ]

    results = run_check(_Grouped(), _ctx())
    assert [r.group for r in results] == ["Skupina", None]
```

Pokud `tests/checks/test_base.py` nemá pomocník `_ctx()` ani importy `Check`,
`Finding`, `Outcome`, doplň je podle toho, co v tom souboru už je — nevymýšlej
nový tvar.

Přidej na konec `tests/reporting/test_text_report.py`:

```python
def test_group_reaches_the_json_report():
    """Zabiji mutanta, ktery `group` do to_dict() nezapise.

    Strojovy vystup ma nest tutez informaci jako text. Sourozenec
    test_group_travels_from_finding_to_check_result hlida cestu k
    CheckResultu, tenhle az serializaci.
    """
    result = _legacy_result()
    result.scopes[0].checks[0].group = "BGP 198.11.13.2 / inet.0"
    payload = json.loads(to_json(result))
    assert payload["scopes"][0]["checks"][0]["group"] == "BGP 198.11.13.2 / inet.0"


def test_json_report_omits_group_when_there_is_none():
    """Zabiji mutanta, ktery `group` zapise vzdy, i kdyz je None.

    Nefiltrovany beh bez skupin ma zustat presne tim tvarem, ktery uz cte
    okoli - stejne pravidlo, jake plati pro `filtered` a pro `details`.
    """
    payload = json.loads(to_json(_legacy_result()))
    assert "group" not in payload["scopes"][0]["checks"][0]
```

- [ ] **Krok 2: Pusť testy a ověř, že padají**

Spusť:
```bash
.venv/bin/python -m pytest tests/checks/test_base.py::test_group_travels_from_finding_to_check_result \
  "tests/reporting/test_text_report.py::test_group_reaches_the_json_report" \
  "tests/reporting/test_text_report.py::test_json_report_omits_group_when_there_is_none" \
  -o addopts="" -q
```
Očekávej: dva `TypeError` / `AttributeError` na neznámé `group`, třetí
(`omits_group`) projde už teď — to je v pořádku, je to test na nezměněné
chování.

- [ ] **Krok 3: Přidej pole na model**

V `migration_validator/models/result.py` do `Finding` hned za `label`:

```python
    label: str | None = None
    group: str | None = None
```

Do `CheckResult` na stejné místo, hned za `label`:

```python
    label: str | None = None
    group: str | None = None
```

A do `CheckResult.to_dict` hned za blok s `label`:

```python
        if self.label is not None:
            payload["label"] = self.label
        if self.group is not None:
            payload["group"] = self.group
```

- [ ] **Krok 4: Přenes ho ve frameworku**

V `migration_validator/checks/base.py` v posledním `return` funkce `run_check`
přidej řádek hned za `label=`:

```python
            label=finding.label or check.label,
            group=finding.group,
            family=finding.family,
```

**Pozor:** na rozdíl od `label` tu **není** fallback na atribut checku.
Skupina je vlastnost jednotlivého měření, ne celého checku — check, jehož
findingy skupinu nemají, žádnou nedostane.

- [ ] **Krok 5: Pusť celou sadu**

Spusť: `.venv/bin/python -m pytest -o addopts="" -q`
Očekávej: `617 passed` (614 + 3). Nic nesmí spadnout — `group` je zatím
nepoužité pole s výchozí hodnotou `None`.

- [ ] **Krok 6: Commit**

```bash
git add migration_validator/models/result.py migration_validator/checks/base.py \
        tests/checks/test_base.py tests/reporting/test_text_report.py
git commit -m "feat: pole group na Finding a CheckResult (AR-36)"
```

---

## Úloha 3: Skupiny ve `view.py` (AR-36, AR-37 datová strana)

**Soubory:**
- Upravit: `migration_validator/reporting/view.py` — nový `Group`, `Section`
  (`:35-40`), `build_view` (`:119-162`)
- Upravit: `tests/reporting/test_view.py`

**Rozhraní:**
- Consumes: `CheckResult.group` z úlohy 2
- Produkuje: `Group(title: str, rows: list[Row])`,
  `Section.groups: list[Group]`, `Section.all_rows() -> list[Row]`

- [ ] **Krok 1: Napiš padající testy**

`tests/reporting/test_view.py` má pomocník `_check(...)`. Přidej mu parametr
`group`:

```python
def _check(check_id, *, family=None, label="X", value="v", status=Status.PASS,
           mode="state", baseline_value=None, delta=None, message="msg", address=None,
           group=None):
    return CheckResult(
        id=check_id,
        mode=mode,
        status=status,
        severity=Severity.ADVISORY,
        message=message,
        label=label,
        group=group,
        family=family,
        value=value,
        baseline_value=baseline_value,
        delta=delta,
        details={"address": address} if address else {},
    )
```

Přidej na konec souboru:

```python
def test_ungrouped_rows_stand_before_groups():
    """Zabiji dva mutanty najednou.

    (1) `groups.sort(key=lambda g: g.title)` - skupiny by se seradily
    abecedne misto poradim vyskytu, takze 'Skupina A' by predbehla
    'Skupinu B'. (2) neseskupene radky pripojene do posledni skupiny misto
    do `section.rows`.

    NEhlida to, co se opravdu VYTISKNE - poradi v datove strukture umi byt
    spravne a renderer ho presto prohodi. To meri sourozenec
    test_rendered_block_puts_ungrouped_rows_above_the_first_group_header
    v tests/reporting/test_text_report.py; overeno mutantem M2, ktery
    tenhle test prezil.
    """
    view = build_view(
        _scope(
            [
                _check("a", family=4, label="b1", group="Skupina B"),
                _check("b", family=4, label="volny"),
                _check("c", family=4, label="a1", group="Skupina A"),
                _check("d", family=4, label="b2", group="Skupina B"),
            ]
        )
    )
    section = view.sections[0]
    assert [row.label for row in section.rows] == ["volny"]
    assert [(g.title, [r.label for r in g.rows]) for g in section.groups] == [
        ("Skupina B", ["b1", "b2"]),
        ("Skupina A", ["a1"]),
    ]


def test_all_rows_returns_grouped_rows_too():
    """Zabiji mutanta `return list(self.rows)` v all_rows().

    Sirky sloupcu se pocitaji prave z all_rows(); kdyby zapomnela radky ve
    skupinach, dlouha hodnota uvnitr skupiny by prerostla ramec bloku.
    """
    view = build_view(
        _scope(
            [
                _check("a", family=4, label="volny"),
                _check("b", family=4, label="ve skupine", group="S"),
            ]
        )
    )
    assert [row.label for row in view.sections[0].all_rows()] == ["volny", "ve skupine"]


def test_groups_do_not_cross_family_sections():
    """Zabiji mutanta, ktery skupiny sbira globalne misto po sekcich.

    Tataz skupina v IPv4 i IPv6 sekci musi dat dva samostatne nadpisy -
    jinak by radky jedne rodiny spadly pod nadpis v sekci te druhe.
    """
    view = build_view(
        _scope(
            [
                _check("a", family=4, label="v4", group="Staticke routy"),
                _check("b", family=6, label="v6", group="Staticke routy"),
            ]
        )
    )
    families = {section.family: section for section in view.sections}
    assert [r.label for r in families[4].groups[0].rows] == ["v4"]
    assert [r.label for r in families[6].groups[0].rows] == ["v6"]
```

- [ ] **Krok 2: Pusť testy a ověř, že padají**

Spusť: `.venv/bin/python -m pytest tests/reporting/test_view.py -o addopts="" -q`
Očekávej: `3 failed` s `AttributeError: 'Section' object has no attribute 'groups'`
(a `all_rows`).

- [ ] **Krok 3: Přidej `Group` a `Section.groups`**

V `migration_validator/reporting/view.py` nad `class Section` vlož:

```python
@dataclass
class Group:
    """Pojmenovana skupina radku uvnitr sekce rodiny.

    Nadpis skupinu OTEVIRA a nic ji nezavira - proto plati AR-37: radky bez
    skupiny stoji nahore, pred prvnim nadpisem. Kdyby stal radek bez
    skupiny za posledni skupinou, cetl by se jako jeji soucast.
    """

    title: str
    rows: list[Row] = field(default_factory=list)
```

A rozšiř `Section`:

```python
@dataclass
class Section:
    family: int | None
    addresses: list[str] = field(default_factory=list)
    virtual_gw: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)

    def all_rows(self) -> list[Row]:
        """Vsechny radky sekce, neseskupene i ve skupinach.

        Sirky sloupcu se pocitaji odsud. Kdyby vracela jen `rows`, dlouha
        hodnota uvnitr skupiny by prerostla ramec bloku.
        """
        return [*self.rows, *(row for group in self.groups for row in group.rows)]
```

- [ ] **Krok 4: Zařaď řádky v `build_view`**

V `build_view` nahraď tělo cyklu přes `FAMILY_ORDER` — konkrétně dva řádky
`own = ...` / `rows = ...` a volání `sections.append(...)`:

```python
        own = addresses.get(family, [])
        qualify = len(own) > 1
        rows: list[Row] = []
        groups: list[Group] = []
        by_title: dict[str, Group] = {}
        for check in checks:
            row = _row(check, qualify=qualify)
            if check.group is None:
                rows.append(row)
                continue
            group = by_title.get(check.group)
            if group is None:
                group = Group(title=check.group)
                by_title[check.group] = group
                groups.append(group)
            group.rows.append(row)
        sections.append(
            Section(
                family=family,
                addresses=own,
                virtual_gw=gateways.get(family, []),
                rows=rows,
                groups=groups,
            )
        )
```

`by_title` je lokální uvnitř cyklu přes rodiny, ne nad ním — to je přesně to,
co měří `test_groups_do_not_cross_family_sections`.

- [ ] **Krok 5: Pusť celou sadu**

Spusť: `.venv/bin/python -m pytest -o addopts="" -q`
Očekávej: `620 passed` (617 + 3).

- [ ] **Krok 6: Commit**

```bash
git add migration_validator/reporting/view.py tests/reporting/test_view.py
git commit -m "feat: skupiny radku ve view.py (AR-36, AR-37)"
```

- [ ] **Krok 7: Pusť mutanty M4 a M5 (po commitu)**

```bash
sed -i 's|                groups.append(group)|                groups.append(group); groups.sort(key=lambda g: g.title)|' \
    migration_validator/reporting/view.py
.venv/bin/python -m pytest tests/reporting/ -o addopts="" -q
git checkout -- migration_validator/reporting/view.py

sed -i 's|        return \[\*self.rows, \*(row for group in self.groups for row in group.rows)\]|        return list(self.rows)|' \
    migration_validator/reporting/view.py
.venv/bin/python -m pytest tests/reporting/ -o addopts="" -q
git checkout -- migration_validator/reporting/view.py
```

Očekávej u M4: padne `test_ungrouped_rows_stand_before_groups`.
Očekávej u M5: padne `test_all_rows_returns_grouped_rows_too`. (Na prototypu
M5 shodil i test šířky z úlohy 4 — ten ale ještě neexistuje, takže teď padne
jen tenhle.)

---

## Úloha 4: Sazba skupin a šířky (AR-37 sazbová strana, AR-38)

**Soubory:**
- Upravit: `migration_validator/reporting/text_report.py` — import (`:16`),
  nový `_group_header`, `_block` (`:94-168`)
- Upravit: `tests/reporting/test_text_report.py`

**Rozhraní:**
- Consumes: `Group`, `Section.groups`, `Section.all_rows()` z úlohy 3
- Produkuje: nadpis skupiny ve tvaru `"   -- {title}"` (tři mezery, dvě
  pomlčky, mezera, název; **bez** doplnění pomlčkami na šířku)

- [ ] **Krok 1: Napiš padající testy**

`tests/reporting/test_text_report.py` už má pomocníky na sestavení výsledku.
Přidej na konec souboru:

```python
_LONG_GROUP = "BGP 2001:db8:11:13::b / VELMI-DLOUHE-JMENO-ROUTING-INSTANCE.inet6.0"


def test_rendered_block_puts_ungrouped_rows_above_the_first_group_header():
    """Zabiji mutanta M2: renderer tiskne neseskupene radky az ZA skupinami.

    Sesterny test test_ungrouped_rows_stand_before_groups ve
    test_view.py tohohle mutanta PREZIL - poradi v datove strukture zustane
    spravne, prohodi se az sazba. Zmereno na prototypu 2026-08-03.
    """
    result = _grouped_result(
        [
            _grouped_check("a", label="b1", group="Skupina B"),
            _grouped_check("b", label="volny"),
        ]
    )
    lines = render(result, detail=True).splitlines()
    free = next(i for i, line in enumerate(lines) if "volny" in line)
    header = next(i for i, line in enumerate(lines) if line.strip() == "-- Skupina B")
    grouped = next(i for i, line in enumerate(lines) if "b1" in line)
    assert free < header < grouped


def test_long_group_title_widens_the_block_frame():
    """Zabiji mutanta M3: nadpisy skupin vypadnou z max() ve vypoctu sirky.

    Nadpis je schvalne DELSI nez cela tabulka sloupcu - s kratkym nadpisem
    by test prosel i tehdy, kdyby se do sirky nezapocitaval, protoze ramec
    uz je siroky z jinych duvodu. Ctvrty vyskyt tehoz tvaru chyby; tri
    predchozi jsou popsane v komentari text_report.py:140.
    """
    result = _grouped_result([_grouped_check("a", label="x", group=_LONG_GROUP)])
    lines = render(result, detail=True).splitlines()
    frame = [line for line in lines if line and set(line) == {"="}]
    assert frame, "blok nema ramec"
    assert len(frame[0]) >= len(f"   -- {_LONG_GROUP}")


def test_group_header_is_not_padded_with_dashes():
    """Zabiji mutanta, ktery nadpis skupiny doplni pomlckami jako sekci.

    Dve urovne nadpisu maji zustat rozlisitelne: sekce rodiny drzi caru pres
    celou sirku, skupina ne.
    """
    result = _grouped_result([_grouped_check("a", label="x", group="S")])
    lines = render(result, detail=True).splitlines()
    header = next(line for line in lines if line.strip().startswith("-- S"))
    assert header == "   -- S"
```

Pomocníky `_grouped_check` a `_grouped_result` napiš takhle (dej je nad ty
tři testy):

```python
def _grouped_check(check_id, *, label, group=None):
    return CheckResult(
        id=check_id,
        mode="state",
        status=Status.PASS,
        severity=Severity.ADVISORY,
        message="msg",
        label=label,
        group=group,
        family=4,
        value="v",
    )


def _grouped_result(checks) -> RunResult:
    return RunResult(
        evaluated_at="2026-07-24T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration"},
        baseline={"address": "172.20.20.4", "phase": "pre-migration"},
        summary={
            "pass": 1, "warn": 0, "fail": 0, "skip": 0,
            "scopes_matched": 1, "unmatched_baseline": 0, "unmatched_subject": 0,
        },
        scopes=[
            ScopeResult(
                scope_id="svc:X:Internet",
                key={"description": "X", "service_type": "Internet"},
                status=Status.PASS,
                match=MatchInfo(
                    status="matched",
                    baseline_interfaces=["ge-0/0/2.13"],
                    subject_interfaces=["et-0/0/8.13"],
                ),
                checks=checks,
                identity={
                    "description": "X",
                    "service_type": "Internet",
                    "routing_instance": None,
                    "interfaces": ["et-0/0/8.13"],
                    "ipv4": ["152.11.13.1/30"],
                    "ipv6": [],
                    "virtual_gw_v4": [],
                    "virtual_gw_v6": [],
                },
            )
        ],
    )
```

- [ ] **Krok 2: Pusť testy a ověř, že padají**

Spusť: `.venv/bin/python -m pytest tests/reporting/test_text_report.py -o addopts="" -q -k "group"`
Očekávej: `3 failed` — `StopIteration` na hledání nadpisu, protože se netiskne.

- [ ] **Krok 3: Rozšiř import a přidej `_group_header`**

V `migration_validator/reporting/text_report.py` nahraď import:

```python
from migration_validator.reporting.view import (
    Group,
    Section,
    ServiceView,
    build_view,
    change_text,
)
```

Nad `def _block(...)` vlož:

```python
def _group_header(group: Group) -> str:
    """Nadpis skupiny. Nedoplnuje se pomlckami na sirku bloku.

    Sekce rodiny caru pres celou sirku ma; skupina ne, aby zustaly obe
    urovne nadpisu rozlisitelne. Do SIRKY bloku ale nadpis vstupuje
    (AR-38) - jen se do ni nedoplnuje.
    """
    return f"   -- {group.title}"
```

- [ ] **Krok 4: Zapoj skupiny do `_block`**

Tři úpravy uvnitř `_block`:

Za prvé, sběr řádků pro výpočet šířek sáhne i do skupin:

```python
    rows = [row for section in view.sections for row in section.all_rows()]
```

Za druhé, nadpisy skupin vstupují do šířky bloku vlastní délkou. Nahraď
výpočet `width`:

```python
    headers = [_section_header(section) for section in view.sections]
    # Nadpisy skupin taky - ctvrty vyskyt tehoz tvaru, ktery komentar vys
    # popisuje u hlavicky bloku, souhrnne tabulky a nadpisu sekce. Tady
    # nesou jmeno peeru a RIB, coz u dlouheho jmena routing instance
    # prekona celou tabulku sloupcu.
    group_titles = [
        _group_header(group) for section in view.sections for group in section.groups
    ]
    width = max(
        [table_width, len(header_line)]
        + [len(text) for text in headers]
        + [len(text) for text in group_titles]
    )
```

Za třetí, sazba. Za stávající cyklus `for row in section.rows:` přidej cyklus
přes skupiny — **pořadí je součástí požadavku (AR-37)**, neseskupené řádky
jdou první:

```python
        for row in section.rows:
            lines.append(
                line(SYMBOL[row.status].strip(), row.label, row.value, changes[id(row)])
            )
        for group in section.groups:
            lines.append(_group_header(group))
            for row in group.rows:
                lines.append(
                    line(SYMBOL[row.status].strip(), row.label, row.value, changes[id(row)])
                )
```

- [ ] **Krok 5: Pusť celou sadu**

Spusť: `.venv/bin/python -m pytest -o addopts="" -q`
Očekávej: `623 passed` (620 + 3).

- [ ] **Krok 6: Commit**

```bash
git add migration_validator/reporting/text_report.py tests/reporting/test_text_report.py
git commit -m "feat: sazba skupin radku a jejich zapocteni do sirky bloku (AR-37, AR-38)"
```

- [ ] **Krok 7: Pusť mutanty M2, M3 a M5 (po commitu)**

M2 — prohoď v `_block` pořadí obou cyklů (neseskupené až za skupinami), pusť
`tests/reporting/`, pak `git checkout -- migration_validator/reporting/text_report.py`.
Očekávej pád `test_rendered_block_puts_ungrouped_rows_above_the_first_group_header`
a **průchod** `test_ungrouped_rows_stand_before_groups` — to je celý smysl toho
sesterského testu.

M3:
```bash
python3 - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/reporting/text_report.py")
p.write_text(p.read_text().replace("        + [len(text) for text in group_titles]\n", ""))
EOF
.venv/bin/python -m pytest tests/reporting/ -o addopts="" -q
git checkout -- migration_validator/reporting/text_report.py
```
Očekávej pád `test_long_group_title_widens_the_block_frame`.

M5 znovu (teď už má zabít i test šířky):
```bash
sed -i 's|        return \[\*self.rows, \*(row for group in self.groups for row in group.rows)\]|        return list(self.rows)|' \
    migration_validator/reporting/view.py
.venv/bin/python -m pytest tests/reporting/ -o addopts="" -q
git checkout -- migration_validator/reporting/view.py
```
Očekávej pád `test_all_rows_returns_grouped_rows_too` **i**
`test_long_group_title_widens_the_block_frame`.

---

## Úloha 5: Skupiny u BGP a statických rout (AR-36)

Tady se rozhýbe 9 stávajících testů. Seznam je změřený, ne odhadnutý — viz
„Změřený výchozí stav" nahoře.

**Soubory:**
- Upravit: `migration_validator/checks/bgp.py` — `label="BGP status"` (`:80`
  a `:120`), `_prefix_finding` (`:176-215`)
- Upravit: `migration_validator/checks/routes.py` — `:113`
- Upravit: `tests/checks/test_bgp.py` — 8 testů
- Upravit: `tests/checks/test_routes.py` — 1 test

**Rozhraní:**
- Consumes: `Finding(group=...)` z úlohy 2
- Produkuje: `CheckResult.group == f"BGP {peer} / {rib_name}"` u
  `bgp_prefix_counts`, `"Staticke routy"` u `static_route_status`

- [ ] **Krok 1: Napiš padající test**

Přidej na konec `tests/checks/test_bgp.py`:

```python
def test_bgp_group_carries_peer_and_rib():
    """Zabiji mutanta M1: `group = f"BGP {peer}"` bez jmena RIB.

    Peer ma schvalne DVE RIB - s jedinou by mutant prosel, protoze jedna
    skupina je porad jedna skupina. Tvar fixture i pocet vysledku (8) je
    overeny proti skutecnemu kodu 2026-08-03.
    """
    peer = _peer()
    peer["ribs"]["bgp.l3vpn.0"] = {
        "received": 9, "accepted": 9, "advertised": 2, "active": 9, "suppressed": 0,
    }
    facts = {"bgp": {"198.11.13.2": peer}}
    results = run_check(BgpPrefixCountsCheck(), _ctx(facts, baseline=facts))

    assert len(results) == 8
    assert {r.group for r in results} == {
        "BGP 198.11.13.2 / inet.0",
        "BGP 198.11.13.2 / bgp.l3vpn.0",
    }
    assert {r.label for r in results} == {
        "active-prefix-count",
        "received-prefix-count",
        "accepted-prefix-count",
        "advertised-prefix-count",
    }


def test_bgp_status_label_carries_the_peer():
    """Zabiji mutanta, ktery peera z popisku BGP status vypusti.

    Dva peery tehoz rodiny v jedne sluzbe by daly dva nerozlisitelne radky.
    Skupinu tenhle radek NEDOSTAVA schvalne - je jeden na peera a RIB se ho
    netyka, takze by nadpis stal nad jedinym radkem.
    """
    facts = {"bgp": {"198.11.13.2": _peer(), "198.11.13.6": _peer()}}
    results = run_check(BgpSessionStateCheck(), _ctx(facts, baseline=facts))

    assert len(results) == 2
    assert {r.label for r in results} == {
        "BGP status (198.11.13.2)",
        "BGP status (198.11.13.6)",
    }
    assert {r.group for r in results} == {None}
```

`_peer()`, `_ctx()` i `run_check` už `tests/checks/test_bgp.py` importuje
(`:1-10`, `:13-53`) — nové pomocníky nepotřebuješ.

Přidej na konec `tests/checks/test_routes.py` — konstanty a pomocník patří
nahoru k `CONFIGURED` a `_installed()`, test na konec:

```python
CONFIGURED_TWO_RIBS = [
    {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]},
    {
        "rib": "L3VPN-CPE13-NNI.inet6.0",
        "prefix": "2001:eeee::/64",
        "next_hop": ["2001:db8:11:13::b"],
    },
]


def _installed_two_ribs():
    return {
        "inet.0": {
            "198.62.1.0/29": {
                "next_hop": ["152.11.13.2"],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        },
        "L3VPN-CPE13-NNI.inet6.0": {
            "2001:eeee::/64": {
                "next_hop": ["2001:db8:11:13::b"],
                "via": ["et-0/0/8.113"],
                "active": True,
            }
        },
    }


def test_static_routes_from_different_ribs_share_one_group():
    """Zabiji mutanta, ktery skupinu odvodi z RIB misto konstanty.

    Dve routy ve dvou RUZNYCH RIB musi skoncit v JEDNE skupine - deleni po
    RIB uz nese popisek radku. S jedinou RIB by mutant `group = rib` prosel.
    Sourozenec test_label_carries_rib_and_prefix hlida popisek, tenhle
    skupinu. Tvar fixture i pocet vysledku (2) overen proti kodu 2026-08-03.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_two_ribs(), scope=_scope(CONFIGURED_TWO_RIBS))
    )

    assert len(findings) == 2
    assert {f.group for f in findings} == {"Staticke routy"}
    assert {f.label for f in findings} == {
        "inet.0 198.62.1.0/29",
        "L3VPN-CPE13-NNI.inet6.0 2001:eeee::/64",
    }
```

- [ ] **Krok 2: Pusť testy a ověř, že padají**

Spusť: `.venv/bin/python -m pytest tests/checks/test_bgp.py tests/checks/test_routes.py -o addopts="" -q`
Očekávej: `3 failed` (nové testy), zbytek prochází.

- [ ] **Krok 3: Uprav `checks/bgp.py`**

`label="BGP status",` je v `BgpSessionStateCheck.run` **dvakrát a pokaždé
jinak odsazeně** — `:80` je uvnitř větve pro ne-Established (24 mezer), `:120`
uvnitř větve pro Established (20 mezer). Hromadné najdi-a-nahraď jedním blokem
rozbije odsazení u druhého. Nahraď každý zvlášť a **nech mu jeho odsazení**:

```python
                        label=f"BGP status ({peer})",
```
```python
                    label=f"BGP status ({peer})",
```

V `_prefix_finding` nahraď řádek `label = f"BGP {key}-prefix-count"` za:

```python
    label = f"{key}-prefix-count"
    group = f"BGP {peer} / {rib_name}"
```

A do závěrečného `return Finding(...)` přidej `group=group,` hned za
`label=label,`.

**Nedotýkej se** `label="BGP prefixy"` na `:142` ani
`label=f"BGP prefixy ({rib_name})"` na `:160`. To jsou souhrnné SKIPy, ne
naměřené countery — do skupiny s countery téže RIB nepatří, protože neříkají
„counter je takový", ale „RIB tu nemá protějšek". Spec to řeší v AR‑36.

- [ ] **Krok 4: Uprav `checks/routes.py`**

Na `:113` nahraď `label = f"{self.label} ({rib} {prefix})"` za:

```python
        label = f"{rib} {prefix}"
        group = "Staticke routy"
```

Pak přidej `group=group,` do **každého** `Finding(...)` v metodě, který nese
`label=label` — je jich šest (`:140`, `:165`, `:179`, `:198`, `:210`, `:221`
v původním číslování). Souhrnný SKIP celého checku (ten, který vrací
`_skip()` z `base.py` při deaktivované službě) se nemění — ten Findingem
vůbec neprochází.

- [ ] **Krok 5: Pusť celou sadu a srovnej ripple se změřeným seznamem**

Spusť: `.venv/bin/python -m pytest -o addopts="" -q`
Očekávej: **přesně 9 padlých**, a to těch devět ze seznamu v „Změřený výchozí
stav". Kdyby padlo něco jiného nebo něco navíc, **nahlas to** — je to nález,
ne úklid.

- [ ] **Krok 6: Srovnej těch 9 testů s novými popisky**

Uprav očekávání v `tests/checks/test_bgp.py` a `tests/checks/test_routes.py`.
Jsou to mechanické změny řetězců (`"BGP status"` → `"BGP status (198.11.13.2)"`,
`"BGP active-prefix-count"` → `"active-prefix-count"`,
`"Staticka routa (inet.0 10.0.0.0/8)"` → `"inet.0 10.0.0.0/8"`).
**Neměň, co test tvrdí** — jen řetězec, který porovnává.

- [ ] **Krok 7: Pusť celou sadu**

Spusť: `.venv/bin/python -m pytest -o addopts="" -q`
Očekávej: `626 passed` (623 + 3), 0 padlých.

- [ ] **Krok 8: Commit**

```bash
git add migration_validator/checks/bgp.py migration_validator/checks/routes.py \
        tests/checks/test_bgp.py tests/checks/test_routes.py
git commit -m "feat: BGP countery a staticke routy nesou skupinu (AR-36)"
```

- [ ] **Krok 9: Pusť mutanta M1 (po commitu)**

```bash
sed -i 's|    group = f"BGP {peer} / {rib_name}"|    group = f"BGP {peer}"|' \
    migration_validator/checks/bgp.py
.venv/bin/python -m pytest tests/checks/test_bgp.py -o addopts="" -q
git checkout -- migration_validator/checks/bgp.py
```

Očekávej: padne `test_bgp_group_carries_peer_and_rib`.

---

## Úloha 6: Sloupec TYP v NESPAROVANO (AR-40)

**Soubory:**
- Upravit: `migration_validator/reporting/text_report.py:303-310`
- Upravit: `tests/reporting/test_text_report.py`

- [ ] **Krok 1: Napiš padající test**

Přidej na konec `tests/reporting/test_text_report.py`:

```python
def test_unmatched_service_type_column_is_padded():
    """Zabiji mutanta M7: sloupec (TYP) se nedoplnuje na sirku.

    Puvodni F-14 z 2026-07-28. Test se diva na POZICI sloupce s duvodem, ne
    na pritomnost mezer - dva ruzne dlouhe typy sluzby ('Core', 'Internet')
    musi dat duvod ve stejnem sloupci.
    """
    result = _grouped_result([_grouped_check("a", label="x")])
    result.unmatched = {
        "baseline": [
            {
                "scope_id": "s1",
                "description": "clab-pop-migration-P1;et-0/0/0",
                "service_type": "Core",
                "reason": "zadny kandidat na subject",
            }
        ],
        "subject": [
            {
                "scope_id": "s2",
                "description": "svc:et-0/0/10.0:Internet",
                "service_type": "Internet",
                "reason": "nova sluzba, chybi baseline",
            }
        ],
    }
    lines = render(result).splitlines()
    rows = [
        line for line in lines
        if line.startswith("  baseline") or line.startswith("  subject")
    ]
    assert len(rows) == 2
    starts = {
        line.index("zadny") if "zadny" in line else line.index("nova") for line in rows
    }
    assert len(starts) == 1, f"sloupec s duvodem nestoji v jedne linii: {rows}"
```

- [ ] **Krok 2: Pusť test a ověř, že padá**

Spusť: `.venv/bin/python -m pytest tests/reporting/test_text_report.py::test_unmatched_service_type_column_is_padded -o addopts="" -q`
Očekávej: `1 failed` — `sloupec s duvodem nestoji v jedne linii`.

- [ ] **Krok 3: Doplň sloupec na šířku**

V `render()` v bloku `NESPAROVANO` nahraď dva řádky za výpočtem `label_width`:

```python
        label_width = max(len(label) for _, label, _, _ in rows)
        # Sirka z obsahu, ne napevno - stejne pravidlo jako u popisku o radek
        # vys (AR-5). Zavorky se pocitaji do sirky, ne kolem ni: jinak by se
        # o dva znaky rozesly radky s ruzne dlouhym typem.
        type_width = max(len(service_type) for _, _, service_type, _ in rows) + 2
        for side, label, service_type, reason in rows:
            typed = f"({service_type})"
            lines.append(
                f"  {side:<9} {label:<{label_width}} {typed:<{type_width}}  {reason}"
            )
```

- [ ] **Krok 4: Pusť celou sadu**

Spusť: `.venv/bin/python -m pytest -o addopts="" -q`
Očekávej: `627 passed`.

- [ ] **Krok 5: Commit**

```bash
git add migration_validator/reporting/text_report.py tests/reporting/test_text_report.py
git commit -m "fix: sloupec TYP v NESPAROVANO se doplnuje na sirku (AR-40)"
```

- [ ] **Krok 6: Pusť mutanta M7 (po commitu)**

```bash
sed -i 's|{typed:<{type_width}}  {reason}|{typed}  {reason}|' \
    migration_validator/reporting/text_report.py
.venv/bin/python -m pytest tests/reporting/ -o addopts="" -q
git checkout -- migration_validator/reporting/text_report.py
```

Očekávej: padne `test_unmatched_service_type_column_is_padded`.

---

## Úloha 7: Sekce NEZARAZENO (AR-39)

**Soubory:**
- Upravit: `migration_validator/reporting/text_report.py` — nový
  `_unassigned_lines`, volání na konci `render()`
- Upravit: `tests/reporting/test_text_report.py`

**Rozhraní:**
- Produkuje: `_unassigned_lines(result: RunResult) -> list[str]`

- [ ] **Krok 1: Napiš padající testy**

Přidej na konec `tests/reporting/test_text_report.py`:

```python
def _unassigned_result():
    result = _grouped_result([_grouped_check("a", label="x")])
    result.unassigned = {
        "bgp_peers": [
            {"peer": "10.9.9.9", "routing_instance": "MGMT", "snapshot": "subject"}
        ],
        "static_routes": [
            {
                "rib": "inet.0",
                "prefix": "10.0.0.0/8",
                "next_hop": ["172.20.20.1"],
                "via": [],
                "snapshot": "subject",
            }
        ],
        "bfd_sessions": [
            {
                "peer": "10.9.9.9",
                "interface": "et-0/0/2",
                "state": "Up",
                "snapshot": "subject",
            }
        ],
    }
    return result


def test_unassigned_objects_reach_the_text_report():
    """Zabiji mutanta, ktery `unassigned` necha jen v JSON.

    Do vlny 5 se retezec 'unassigned' v reporting/ nevyskytoval ani jednou,
    takze pojistka proti mezeram v parsovani byla videt jen strojove.
    """
    out = render(_unassigned_result())
    assert "NEZARAZENO" in out
    assert "10.9.9.9" in out and "MGMT" in out
    assert "inet.0 10.0.0.0/8" in out and "172.20.20.1" in out
    assert "et-0/0/2" in out


def test_unassigned_section_is_printed_even_when_empty():
    """Zabiji mutanta, ktery sekci pri prazdnem obsahu vynecha.

    Chybejici sekce se cte jinak nez sekce s '(nic)': prvni nerika nic,
    druha rika 'meril jsem a nic tam neni'. Totez pravidlo drzi NESPAROVANO.
    """
    out = render(_grouped_result([_grouped_check("a", label="x")]))
    assert "NEZARAZENO (jen subject)" in out
    assert "(nic)" in out


def test_unassigned_detail_column_stands_in_one_line():
    """Zabiji mutanta, ktery `identity_width` z formatovani vypusti.

    Sourozenec test_unassigned_objects_reach_the_text_report hlida, ze se
    data vypisou; tenhle, ze stoji ve sloupcich. Tri druhy objektu maji
    ruzne dlouhou identitu ('10.9.9.9' vs 'inet.0 10.0.0.0/8'), takze bez
    doplneni by podrobnost skoncila ve trech ruznych sloupcich - tataz vada,
    jakou AR-40 opravuje v NESPAROVANO.
    """
    lines = render(_unassigned_result()).splitlines()
    start = lines.index("NEZARAZENO (jen subject)")
    rows = [line for line in lines[start + 1 :] if line.startswith("  ")]
    assert len(rows) == 3
    starts = {
        line.index("RI ") if "RI " in line else
        line.index("-> ") if "-> " in line else
        line.index("et-0/0/2")
        for line in rows
    }
    assert len(starts) == 1, f"sloupec s podrobnosti nestoji v jedne linii: {rows}"


def test_unassigned_survives_a_filter_that_hides_every_scope():
    """Zabiji mutanta M8: filtr se pusti i na NEZARAZENO.

    Je to pojistka, ne data - stejne jako NESPAROVANO, ktere filter_result
    schvalne neprepocitava. Objekty bez sluzby navic zadny status nemaji,
    takze --status fail by je schoval vzdycky.
    """
    out = render(filter_result(_unassigned_result(), statuses={Status.FAIL}))
    assert "10.9.9.9" in out, "filtr smazal pojistku"
```

- [ ] **Krok 2: Pusť testy a ověř, že padají**

Spusť: `.venv/bin/python -m pytest tests/reporting/test_text_report.py -o addopts="" -q -k unassigned`
Očekávej: `4 failed` — `NEZARAZENO` ve výstupu není.

- [ ] **Krok 3: Napiš sazbu sekce**

V `migration_validator/reporting/text_report.py` nad `def render(...)` vlož:

```python
# Poradi je soucast pozadavku: sekce se cte shora dolu a BGP peer je
# nejcastejsi pripad.
UNASSIGNED_TITLES = (
    ("bgp_peers", "BGP peer"),
    ("static_routes", "Staticka routa"),
    ("bfd_sessions", "BFD session"),
)


def _unassigned_row(kind: str, item: dict[str, object]) -> tuple[str, str]:
    """Rozpad na identitu a podrobnost, ne jeden neprusvitny retezec.

    Kdyby to byl jeden retezec, tri druhy objektu by daly tri ruzne dlouhe
    identity a podrobnost by skoncila ve trech ruznych sloupcich - presne ta
    vada, kterou AR-40 opravuje o kus vys v NESPAROVANO.

    `via` se od `next_hop` odlisuje slovem, ne jen sipkou: `-> et-0/0/8.13`
    by vydavalo rozhrani za branu.
    """
    if kind == "bgp_peers":
        return item["peer"], f"RI {item.get('routing_instance') or '-'}"
    if kind == "static_routes":
        hops = item.get("next_hop") or []
        detail = (
            f"-> {', '.join(hops)}"
            if hops
            else f"via {', '.join(item.get('via') or ['-'])}"
        )
        return f"{item['rib']} {item['prefix']}", detail
    return item["peer"], f"{item.get('interface') or '-'}   {item.get('state') or '-'}"


def _unassigned_lines(result: RunResult) -> list[str]:
    """Objekty, ktere si nenarokovala zadna sluzba.

    Vypisuje se VZDY, i prazdna, a filtrovani se na ni nevztahuje - je to
    pojistka proti mezeram v parsovani (routa, kterou parser neumel precist,
    se do selektoru nedostane, ale v tabulce ji videt je). Pojistka, kterou
    je nutne si vyzadat prepinacem, chyti min. Totez odduvodneni nese
    docstring filter_result u NESPAROVANO.

    '(jen subject)' v nadpisu neni kosmetika: engine plni vsechny tri
    seznamy jen ze subjectu a v device scope vraci prazdno. Bez teto
    poznamky by prazdna sekce tvrdila 'nic nezarazeneho neni', zatimco se
    ve skutecnosti nesbiralo.
    """
    lines = ["NEZARAZENO (jen subject)"]
    rows = [
        (title, *_unassigned_row(kind, item))
        for kind, title in UNASSIGNED_TITLES
        for item in result.unassigned.get(kind, [])
    ]
    if not rows:
        lines.append("  (nic)")
        return lines
    # Obe sirky z obsahu, stejne jako v NESPAROVANO o kus vys (AR-5).
    title_width = max(len(title) for title, _, _ in rows)
    identity_width = max(len(identity) for _, identity, _ in rows)
    for title, identity, detail in rows:
        lines.append(f"  {title:<{title_width}}  {identity:<{identity_width}}  {detail}")
    return lines
```

- [ ] **Krok 4: Zavolej ji na konci `render()`**

Nahraď poslední řádek `render()`. **Oba nové řádky patří na úroveň těla
`render()` (4 mezery), tedy VEN z `else:`, který obsluhuje neprázdné
`unmatched`** — jinak by běh s prázdným `unmatched` celou sekci ztratil:

```python
    lines.append("")
    lines.extend(_unassigned_lines(result))

    return "\n".join(lines) + "\n"
```

`_unassigned_lines` bere `result`, ne profiltrovanou kopii — `filter_result`
`unassigned` nesahá vůbec, takže se sem dostane celý.

- [ ] **Krok 5: Pusť celou sadu**

Spusť: `.venv/bin/python -m pytest -o addopts="" -q`
Očekávej: `631 passed` (627 + 4).

- [ ] **Krok 6: Commit**

```bash
git add migration_validator/reporting/text_report.py tests/reporting/test_text_report.py
git commit -m "feat: sekce NEZARAZENO v textovem reportu (AR-39)"
```

- [ ] **Krok 7: Pusť mutanta M8 (po commitu)**

```bash
python3 - <<'EOF'
import pathlib
p = pathlib.Path("migration_validator/reporting/text_report.py")
p.write_text(p.read_text().replace(
    "    lines.extend(_unassigned_lines(result))",
    "    lines.extend(_unassigned_lines(result) if not result.filtered "
    "else ['NEZARAZENO (jen subject)', '  (nic)'])"))
EOF
.venv/bin/python -m pytest tests/reporting/ -o addopts="" -q
git checkout -- migration_validator/reporting/text_report.py
```

Očekávej: padne `test_unassigned_survives_a_filter_that_hides_every_scope`.

---

## Úloha 8: Ostré ověření a roadmapa

Report se má přečíst očima, ne jen testem. Tři z nejostřejších chyb na větvi
vlny 1 našlo právě tohle.

**Soubory:**
- Vytvořit: `docs/superpowers/roadmap-2026-08-03-vlna5-hotovo.md`

- [ ] **Krok 1: Vyrenderuj ostrý report ze společných fixtures**

Vytvoř dočasný `tests/test_zz_render_tmp.py`:

```python
from pathlib import Path

from migration_validator import api
from migration_validator.reporting.text_report import render

FIX = Path("tests/fixtures")


def test_dump(synthetic_snapshot):
    old = synthetic_snapshot(str(FIX / "172.20.20.4.yml"), "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(str(FIX / "172.20.20.5.yml"), "172.20.20.5", "post-migration")
    result = api.evaluate(new, baseline=old, now="2026-07-24T11:40:02Z")
    Path("/tmp/report.txt").write_text(render(result, detail=True))
```

Spusť `.venv/bin/python -m pytest tests/test_zz_render_tmp.py -o addopts="" -q`,
přečti `/tmp/report.txt` a soubor **smaž** (`rm tests/test_zz_render_tmp.py`).

**Vytiskni si k tomu i naplněnou sekci `NEZARAZENO`.** Na společných fixtures
je prázdná, takže by se její sazba jinak nikdy neviděla očima — a právě tudy
propadlo zarovnání při psaní tohohle plánu. Stačí jeden řádek v témž dočasném
testu, s pomocníkem `_unassigned_result()` z úlohy 7:

```python
    from tests.reporting.test_text_report import _unassigned_result
    print(render(_unassigned_result()))
```

Pokud ten import nejde (pytest `tests/` jako balík neexportuje), zkopíruj tělo
`_unassigned_result()` do dočasného testu — je to soubor na vyhození.

- [ ] **Krok 2: Zkontroluj čtyři věci a zapiš, co jsi viděl**

1. Nadpis skupiny je odsazený a **není** doplněný pomlčkami, na rozdíl od
   nadpisu sekce rodiny.
2. Za posledním řádkem skupiny nestojí žádný řádek bez skupiny.
3. Rámec bloku (`=` čára) obaluje i nejdelší nadpis skupiny.
4. Sekce `NEZARAZENO` je na konci a na dnešních fixtures je prázdná — obsahuje
   `(nic)`. To je správně: `engine.py` na téhle dvojici nic nezařazeného
   nenajde. Kdyby chyběla úplně, byla by to chyba.
5. V **naplněné** sekci stojí všechny tři sloupce v linii a routa bez
   `next_hop` má `via et-0/0/8.13`, ne `-> et-0/0/8.13`.

Ověřený tvar z prototypu 2026‑08‑03 je v plánu výš, v „Cílový tvar na
skutečných datech" — porovnej s ním.

- [ ] **Krok 3: Ověř globální podmínky**

```bash
.venv/bin/python -m pytest -o addopts="" -q     # 631 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l         # 146
```

Kdyby zámek parserů nebyl 146, něco se dotklo parserů — a nemělo.

- [ ] **Krok 4: Napiš roadmapu vlny 5**

`docs/superpowers/roadmap-2026-08-03-vlna5-hotovo.md` ve tvaru, jaký mají
roadmapy vln 1 až 4: co vlna přinesla, jak si vyrobit důkazy, co vyšlo jinak
než plán čekal, co zbývá, pravidla do další vlny.

Do „Co zbývá" přenes nevyřízené body z
[`roadmap-2026-07-31-vlna4-hotovo.md`](../roadmap-2026-07-31-vlna4-hotovo.md):
příznaky na hlubších úrovních konfigurace (bod 1), resync `.5` (bod 3),
baseline bez `active` eskaluje na FAIL (bod 4), priorita SKIP nad PASS
(bod 5) a drobnosti ze závěrečného review (bod 6). Report z „Co zbývá" mizí.

Do „Co vyšlo jinak" patří minimálně dvě věci, obě už změřené při psaní plánu:

- **Ripple byl 9 testů, ne 220 řádků grepu.** Spec u AR‑42 vedl grep jako
  horní odhad a zakázal z něj dělat seznam; skutečnost byla o dva řády menší
  a soustředěná do dvou souborů.
- **Mutant M2 přežil první návrh testu na AR‑37.** Šev `view.py` ↔
  `text_report.py` potřebuje test na obou stranách; test nad datovou
  strukturou sám o sobě prohozené pořadí v rendereru nechytí.

Zapiš i všechno, co ti při implementaci vyšlo jinak, než plán čekal.

- [ ] **Krok 5: Commit**

```bash
git add docs/superpowers/roadmap-2026-08-03-vlna5-hotovo.md
git commit -m "docs: roadmapa vlny 5"
```

- [ ] **Krok 6: Uzavření větve**

Použij skill `superpowers:finishing-a-development-branch`.

---

## Co plán vědomě nedělá

- **Skupiny nedostávají ostatní checky.** ARP a ND u služby s víc adresami
  ani countery rozhraní na fyzickém vs. logickém. Mechanismus je obecný, ale
  zavádí se tam, kde dnes vzniká skutečná záměna. Rozšíření je levné a udělá
  se, až bude pro co.
- **Peer je v popisku `BGP status` bezpodmínečně**, i když má rodina jediného
  peera. `_row()` umí kvalifikovat podle počtu adres (`qualify`), ale počet
  peerů nezná — musel by ho spočítat check a předat rendereru. Na ostrém
  výstupu to znamená `BGP status (152.11.13.2)` v sekci, jejíž hlavička už
  `152.11.13.1/30` nese. Je to vidět a je to vědomé; kdyby to vadilo, je to
  vstup pro další vlnu, ne důvod měnit AR‑36 při implementaci.
- **`checks/routes.py:169` se nemění.** Chybějící `active` v baseline dál
  eskaluje na FAIL. Je to bod 4 roadmapy vlny 4 a je mimo rozsah — tahle vlna
  mění, jak se výsledek zobrazuje, ne co znamená.
