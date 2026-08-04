# Vlna 10 — tichý blok deaktivované služby a pravdivá hláška o peeru: implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blok deaktivované služby přestane tisknout deset řádků, které říkají
totéž, hláška o peeru přestane tvrdit „v subjektu neni" o peeru se živou
session, a zbylých pět otevřených bodů z vln 4 až 9 se uzavře.

**Architecture:** Deaktivační SKIP dostane **strukturální značku** už tam, kde
vzniká (`checks/base.py`), a `reporting/view.py` podle ní při `detail=False`
sloučí všechny takové řádky do jednoho s počtem. Odděleně od toho se
v `checks/bgp.py` mění jediná hláška z tvrzení o existenci na tvrzení
o členství ve službě. Zbytek vlny jsou přejmenování, komentáře, rozrůznění
counterů ve fixtures a capture z laborky.

**Tech Stack:** Python 3, pytest, žádné nové závislosti.

**Spec:** [`../specs/2026-08-04-vlna10-tichy-blok-a-pravdiva-hlaska-design.md`](../specs/2026-08-04-vlna10-tichy-blok-a-pravdiva-hlaska-design.md)

## Global Constraints

- **Výchozí bod:** `main` na commitu `5ce3b27`, **676 passed, 0 skipped**.
- **Testovací příkaz je vždy** `.venv/bin/python -m pytest -o addopts=""`.
  Přepínač `-o addopts=""` je povinný — bez něj se sada chová jinak.
- **Zámek parserů:** `diff mx_parser.py evo_parser.py | wc -l` musí zůstat
  **146**. Vlna 10 mění v parserech **jen komentář** a musí ho změnit v obou
  souborech doslova stejně.
- **Schema zůstává 5** u inventory i u snapshotu. Vlna 10 nezavádí jediný nový
  klíč. Kdyby úloha nový klíč potřebovala, je to nález — zastav a nahlas,
  nebumpuj.
- **BFD se nemění.** Test `test_inactive_bfd_override_inherits_group_value`
  fixuje korektní chování (Junosí `inactive` znamená „příkaz se neuplatní",
  takže BFD dědí skupinovou hodnotu právem). **Neopravuj ho jako vadu.**
- **ASCII-only neplatí.** Řiď se souborem, do kterého píšeš: `migration_validator/`
  a `tests/` jsou psané česky **bez** diakritiky, `mx_parser.py`/`evo_parser.py`
  a `docs/` **s** diakritikou.
- **Mutant se pouští až nad zacommitovanou prací.** `git checkout -- <soubor>`
  při revertu mutanta zahodí i neuložené změny téže úlohy. Pořadí v každé
  úloze je: commit → mutant → revert mutanta → hotovo.
- **Mutant nesmí mířit do téhož souboru, proti kterému test asertuje.** Smysl
  pravidla je, aby mutant nezabil sám sebe: kdyby mířil do souboru, ve kterém
  žije i aserce, mohl by shodit test způsobem, který o produkčním kódu nic
  neříká. Prakticky to znamená „do produkčního kódu, ne do `tests/`" —
  s jedinou výjimkou, kterou uživatel 2026‑08‑04 potvrdil: v **úloze 4** je
  produkčním kódem úlohy `tests/conftest.py` a asertující testy leží
  v `tests/test_end_to_end.py`, takže smysl pravidla porušený není.
- **Každý mutant končí `grep -n MUTANT <soubor>`, který musí něco vypsat.**
  Když nevypíše, `sed` se neaplikoval, mutant nic neměří a je třeba ho upravit
  ručně. Nečinný mutant je horší než žádný — vypadá jako důkaz a není.
- **Tvrzení o mutantovi je kód, ne komentář.** Každý docstring, který jmenuje
  konkrétní `Outcome`, konkrétní `Status` nebo konkrétní zabíjející test, se
  ověří **spuštěním toho mutanta** — a nahlásí se i tehdy, když je oprava mimo
  rozsah úlohy. Vlna 9 takhle našla devět zastaralých tvrzení.
- **V kořeni repa leží netrackovaný `mx1-pop1.yml`** (výstup uživatelova
  parseru z měření qualified-next-hopů). **Nesahej na něj** a nepřidávej ho
  do žádného commitu — používej cílené `git add <cesta>`, nikdy `git add -A`.

---

## Pořadí úloh a proč zrovna takové

```
1 (bod 19)  ->  2 (bod 18)  ->  3 (body 16, 17, 9)  ->  4 (bod 12)  ->  5 (bod 2)  ->  6 (roadmapa)
```

Úlohy **4 a 5 mění fixtures**, a po přegenerování fixtures se **každý mutant
musí pustit znovu** — mutant změřený nad jinou sadou fixtures nedokazuje nic.
Kdyby fixtures přišly doprostřed, všechny dosavadní důkazy o citlivosti testů
by se zneplatnily. Úloha 5 má na tenhle přeběh vlastní krok.

---

## Struktura souborů

| soubor | odpovědnost | úloha |
|---|---|---|
| `migration_validator/models/result.py` | **rozšiřuje se** o konstanty značky přeskočení — sdílené místo, na které smí sáhnout `checks/` i `reporting/` bez toho, aby na sebe ty dva balíky viděly | 2 |
| `migration_validator/checks/base.py` | `_skip()` umí značku vydat; deaktivační větev ji jako jediná používá | 2 |
| `migration_validator/checks/bgp.py` | hláška o členství místo hlášky o existenci (1); komentář k bodu 9 (3) | 1, 3 |
| `migration_validator/checks/routes.py` | komentář k bodu 17 | 3 |
| `migration_validator/reporting/view.py` | slučování označených SKIPů při `detail=False` | 2 |
| `migration_validator/reporting/text_report.py` | protažení `detail` do `build_view` | 2 |
| `mx_parser.py`, `evo_parser.py` | oprava komentáře o qualified-next-hop, **v obou identicky** | 3 |
| `tests/conftest.py` | rozrůzněné countery per peer | 4 |
| `tests/checks/test_bgp.py` | očekávání nové hlášky | 1 |
| `tests/reporting/test_view.py` | jednotkové testy slučování | 2 |
| `tests/test_end_to_end.py` | bod 19 i bod 18 přes skutečnou cestu; zámek counterů | 1, 2, 4 |
| `tests/models/test_inventory.py` | přejmenování testu (bod 16) | 3 |
| `docs/superpowers/roadmap-2026-08-04-vlna10-hotovo.md` | roadmapa vlny | 6 |

---

## Task 1: Bod 19 — hláška o členství, ne o existenci

**Files:**
- Modify: `migration_validator/checks/bgp.py:186-202`
- Test: `tests/test_end_to_end.py`, `tests/checks/test_bgp.py:634-650`

**Interfaces:**
- Consumes: nic z předchozích úloh (první úloha vlny).
- Produces: nic, co by pozdější úloha volala. Úloha 3 sahá do téhož souboru,
  ale jen do komentáře u popisku, ne do téhle větve.

**Kontext, který implementer nemá:**

Peer se do reportu dostává **dvěma nezávislými cestami**, které se navzájem
neptají:

1. **Blok služby** — `checks/bgp.py` iteruje sjednocení čtyř zdrojů
   (`universe`, `bgp.py:69`). Jedním z nich je `ctx.baseline["bgp"]`, což
   nejsou surová fakta baseline snímku, ale fakta profiltrovaná **baseline**
   selektory. Peer, kterého nárokuje baseline scope a nenárokuje subjektový,
   je proto v `universe` a chybí v `peers` — spadne do `without_session`.
2. **NEZAŘAZENO** — `engine.py:_unassigned_bgp_peers` (`:168`) staví množinu
   `assigned` **jen ze subjektových scopů** a prochází **surová**
   `subject.facts["bgp"]`. Peer, kterého žádný subjektový scope nenárokuje,
   tam tedy je, i když má živou session.

Obě cesty jsou uvnitř sebe konzistentní; dohromady dnes tvrdí opak. Rozdíl je
v tom, co znamená „subjekt": profiltrovaný pohled služby versus surová fakta
zařízení.

**Rozlišit „peer ze zařízení zmizel" od „peer je na zařízení, jen ho tahle
služba nenárokuje" nejde** — `Scope.select` (`models/scope.py:163`) filtruje
měření podle záměru, takže `ctx.subject` surová fakta neobsahuje a
`CheckContext` je nenese. Proto se mění **formulace**, která je pravdivá
v obou případech, a nepřidává se nová vazba do `CheckContext`. Kdyby ses
přistihl, že do `CheckContext` protahuješ nefiltrovaná fakta, je to nález —
zastav a nahlas.

**Past v pořadí kroků, změřená při psaní specu.** `_facts_for()`
(`tests/conftest.py:98`) odvozuje `facts["bgp"]` **ze selektorů**. Kdo peera
odebere ze `selectors.bgp_neighbors` **před** stavbou snímku, nedostane pro
něj žádnou session — peer se do NEZAŘAZENO nedostane, aserce na něj projde
vakuově a test nedokazuje nic. Selektor se mění **až nad hotovým snímkem**,
který `synthetic_snapshot` vrátil. Krok 8 to ověřuje měřením.

- [ ] **Step 1: Write the failing end-to-end test**

Do `tests/test_end_to_end.py` přidej na konec souboru:

```python
def test_peer_moved_out_of_service_is_not_claimed_to_be_missing(synthetic_snapshot):
    """Bod 19: peer se zivou session nesmi byt hlasen jako 'v subjektu neni'.

    Blok sluzby a NEZARAZENO jsou dve nezavisle cesty. Blok jde pres
    ctx.baseline, coz jsou fakta profiltrovana BASELINE selektory, takze
    peera vidi. NEZARAZENO jde pres surova subject.facts['bgp'] a mnozinu
    assigned jen ze SUBJEKTOVYCH scopu, takze ho vidi taky. Dokud blok
    tvrdil 'v subjektu neni', rekly ty dve sekce o jednom peeru dve
    neslucitelne veci.

    Test asertuje OBE sekce v jednom behu. Kdyby asertoval jen blok, prosel
    by i nad implementaci, ktera peera z NEZARAZENO vyhodi - a to je jina
    varianta, kterou uzivatel vedome odmitl.

    Zabiji mutanta: navrat hlasky 'v baseline byl, v subjektu neni'.

    Aserce `peer in new.facts['bgp']` nize je POJISTKA PROTI VAKUOVOSTI a
    nesmi se odstranit. _facts_for() (tests/conftest.py) odvozuje
    facts['bgp'] ZE SELEKTORU, takze kdyby nekdo odebrani peera presunul
    pred stavbu snimku, zadna session by pro nej nevznikla - peer by se do
    NEZARAZENO nedostal a obe aserce nize by prosly, aniz by cokoli
    dokazaly. Tahle jedina aserce ten presun odhali.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    # Selektor se meni AZ NAD HOTOVYM SNIMKEM - viz docstring.
    target = next(
        scope for scope in new.scopes
        if scope.id == "svc:INTERNET-CPE13-NNI:Internet"
    )
    peer = "152.11.13.2"
    assert peer in new.facts["bgp"], "fixture nema session peera, test by byl vakuovy"
    target.selectors.bgp_neighbors = [
        neighbor for neighbor in target.selectors.bgp_neighbors if neighbor != peer
    ]

    result = api.evaluate(new, baseline=old, now=NOW)
    rendered = render(result)

    # Cela hlaska je v souhrnne tabulce ve sloupci NALEZ, ne v bloku: blok
    # tiskne status/label/value/change (_block v text_report.py), message
    # nikdy. Zmereno.
    summary_row = next(
        line
        for line in rendered.splitlines()
        if line.startswith("FAIL") and "INTERNET-CPE13-NNI" in line
    )
    assert "v baseline patril k teto sluzbe, v subjektu uz ne" in summary_row
    assert "v subjektu neni" not in rendered

    # V bloku je vidět `value`, a ta se meni taky.
    block = _block_of(rendered, "INTERNET-CPE13-NNI", "Internet")
    assert "BGP status (152.11.13.2)" in block
    assert "neni ve sluzbe" in block

    # Hledat uvnitr sekce, ne kdekoli ve vystupu: NEZARAZENO sdili
    # formatovaci literaly se sousednimi sekcemi, takze `x in rendered` by
    # proslo i kdyby se sekce vubec nevytiskla (pravidlo vlny 5).
    unassigned = rendered.split("NEZARAZENO")[-1]
    assert peer in unassigned
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest -o addopts="" tests/test_end_to_end.py::test_peer_moved_out_of_service_is_not_claimed_to_be_missing -q`

Expected: FAIL — `assert "v baseline patril k teto sluzbe, v subjektu uz ne" in block`.
Kdyby padl na `assert peer in new.facts["bgp"]`, fixture se změnila a test
neměří, co má — zastav a nahlas.

- [ ] **Step 3: Change the message and the value**

V `migration_validator/checks/bgp.py` nahraď řádky 186–202 (větev
`without_session`) tímto. Mění se **jen** druhá hláška a druhá hodnota:

```python
            in_config = peer in configured
            findings.append(
                Finding(
                    Outcome.BROKEN,
                    # Tvrzeni o CLENSTVI, ne o existenci. Peer, ktereho
                    # nenarokuje zadny subjektovy scope, muze mit na
                    # zarizeni zivou session - engine.py:_unassigned_bgp_peers
                    # ji ukaze v NEZARAZENO. Hlaska "v subjektu neni" tam
                    # tedy lhala. Nova formulace je pravdiva v obou
                    # pripadech, ktere sem spadnou (peer ze zarizeni zmizel
                    # i peer presel pod jinou sluzbu), takze se check nemusi
                    # ptat na nefiltrovana fakta, ktera nema.
                    f"{peer}: nakonfigurovan, ale session neexistuje"
                    if in_config
                    else f"{peer}: v baseline patril k teto sluzbe, v subjektu uz ne",
                    label=f"BGP status ({peer})",
                    family=peer_family(peer),
                    value="bez session" if in_config else "neni ve sluzbe",
                    baseline_value=(
                        str(baseline_peers[peer].get("state", "unknown"))
                        if peer in baseline_peers
                        else None
                    ),
                )
            )
        return findings
```

Uprav i komentář nad `without_session` (`bgp.py:178-182`) — zmiňuje starou
hlášku doslova:

```python
        # Peer, ktery ma byt a session pro nej neprisla. Zrcadli vetev
        # `if subject is None:` v checks/routes.py, ale jen tvarem, ne
        # hlaskou: routa v baseline byla a v subjektu neni, kdezto peer
        # muze na zarizeni dal bezet - jen ho tahle sluzba uz nenarokuje.
        # Deaktivovane peery uz vyresila smycka vys, proto se odectou.
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `.venv/bin/python -m pytest -o addopts="" tests/test_end_to_end.py::test_peer_moved_out_of_service_is_not_claimed_to_be_missing -q`
Expected: PASS

- [ ] **Step 5: Run the full suite to see what else moved**

Run: `.venv/bin/python -m pytest -o addopts="" -q`

Expected: FAIL, **1 failed** — `tests/checks/test_bgp.py::test_peer_measured_only_in_baseline_is_fail`.
Změřeno předem; **jiný počet je nález — zastav a nahlas.**

- [ ] **Step 6: Update the existing unit test**

V `tests/checks/test_bgp.py` nahraď `test_peer_measured_only_in_baseline_is_fail`
(řádky 634–650). **Název zůstává** — „measured_only_in_baseline" po změně dál
platí, mění se jen očekávaná hláška. Docstring se mění povinně, protože
tvrdí zrcadlení `routes.py`, které po změně neplatí:

```python
def test_peer_measured_only_in_baseline_is_fail():
    """Peer, ktery v baselinu bezel a zadny subjektovy scope si ho nenarokuje.

    Hlaska mluvi o CLENSTVI ve sluzbe, ne o existenci na zarizeni. Zrcadlo
    routes.py je tu jen tvarem vetve, ne znenim: routa, ktera v subjektu
    neni, tam opravdu neni, kdezto peer muze dal bezet a byt jen
    v NEZARAZENO.

    Zabiji mutanta: navrat hlasky 'v baseline byl, v subjektu neni'.
    """
    ctx = _ctx(
        {"bgp": {}},
        baseline={"bgp": {"198.11.13.5": _peer(state="Established")}},
        bgp_neighbors=[],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "v baseline patril k teto sluzbe, v subjektu uz ne" in results[0].message
    assert results[0].value == "neni ve sluzbe"
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: PASS, **677 passed, 0 skipped** (676 výchozích + 1 nový)

- [ ] **Step 8: Commit**

```bash
git add migration_validator/checks/bgp.py tests/checks/test_bgp.py tests/test_end_to_end.py
git commit -m "fix: hlaska o peeru mluvi o clenstvi ve sluzbe, ne o existenci

Peer, ktereho nenarokuje zadny subjektovy scope, muze mit na zarizeni
zivou session a NEZARAZENO ji ukaze. Hlaska 'v subjektu neni' tedy
lhala; nova formulace je pravdiva i kdyz peer jen presel jinam."
```

- [ ] **Step 9: Run the mutant (over committed work)**

```bash
sed -i 's|                    else f"{peer}: v baseline patril k teto sluzbe, v subjektu uz ne",|                    else f"{peer}: v baseline byl, v subjektu neni",  # MUTANT|' migration_validator/checks/bgp.py
grep -n MUTANT migration_validator/checks/bgp.py
.venv/bin/python -m pytest -o addopts="" -q
```

`grep` **musí** vypsat řádek; když nevypíše, `sed` se neaplikoval a mutant nic
neměří — uprav ho ručně a opakuj.

Expected: FAIL — musí padnout **jak** `tests/checks/test_bgp.py::test_peer_measured_only_in_baseline_is_fail`,
**tak** `tests/test_end_to_end.py::test_peer_moved_out_of_service_is_not_claimed_to_be_missing`.
Kdyby padl jen ten jednotkový, nová sémantika není pokrytá přes skutečnou
cestu — zastav a nahlas.

Mutant mění **jen hlášku**, ne `value`, takže end-to-end test ho zabíjí
výhradně přes aserci na souhrnný řádek. Aserce na `neni ve sluzbe` v bloku
hlídá druhou polovinu změny; tu by zabil mutant měnící `value`.

- [ ] **Step 10: Revert the mutant**

```bash
git checkout -- migration_validator/checks/bgp.py
.venv/bin/python -m pytest -o addopts="" -q
git status --porcelain
```

Expected: PASS, 677 passed. `git status --porcelain` vypíše **jen**
`?? mx1-pop1.yml` — ten tam patří a není tvůj.

---

## Task 2: Bod 18 — jeden souhrnný řádek místo N deaktivačních SKIPů

**Files:**
- Modify: `migration_validator/models/result.py`, `migration_validator/checks/base.py:95-113` a `:146-153`, `migration_validator/reporting/view.py`, `migration_validator/reporting/text_report.py:347`
- Test: `tests/reporting/test_view.py`, `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: nic z úlohy 1 (jiný soubor, jiná větev).
- Produces: `migration_validator.models.result.SKIPPED_BECAUSE: str` (klíč
  v `CheckResult.details`) a `migration_validator.models.result.SKIP_DEACTIVATED: str`
  (jeho hodnota pro deaktivační SKIP). Pozdější úlohy je nevolají.

**Kontext, který implementer nemá:**

**Proč nestačí `Status.SKIP`.** Změřeno na renderovaném výstupu bloku
`svc:et-0/0/10.0:Internet`: mezi deaktivačními SKIPy sedí
`BGP prefixy : bez baseline`, což je SKIP porovnávacího checku bez baseline
snapshotu (`checks/base.py:138`). Slít ho mezi ostatní by zahodilo informaci,
kterou nic jiného nenese. Blok má **deset** SKIPů — devět deaktivačních
a jeden cizí.

**Proč nestačí text zprávy.** Vázalo by potlačení na řetězec, který nic
nehlídá; přeformulování hlášky by potlačení tiše rozbilo.

**Kde deaktivační SKIP vzniká.** Na jediném místě — `checks/base.py:146`,
větev `if check.id != DEACTIVATION_CHECK_ID and ctx.scope.is_deactivated:`.
Ostatní volání `_skip()` (chybějící inventory, compare bez baseline, selhaný
collector, výjimka v checku) značku **nedostávají**.

**Kam sloučený řádek padne.** Deaktivační SKIPy vznikají přes `_skip()`, které
`family` nevyplňuje, takže mají `family=None` a padají do bezhlavičkové sekce
nad rodinovými (`FAMILY_ORDER = (None, 4, 6)`, `view.py:18`). Sloučený řádek
patří tam taky.

**Pořadí checků.** `all_checks()` (`checks/registry.py:21`) vrací checky
seřazené podle `id`, takže `scope.checks` má stabilní pořadí a sloučený řádek
připojený na konec sekce je deterministický.

**JSON se nemění.** `RunResult.to_dict()` sestavuje výstup z `CheckResult`ů,
ne z `ServiceView`. Sloučení je čistě zobrazovací.
**Pozor:** `CheckResult.to_dict()` propisuje `details` do JSON
(`models/result.py:141`), takže **klíč `skipped_because` v JSON přibude**. Je
to aditivní a smysluplné — strojový konzument se dozví, proč byl check
přeskočen. Krok 11 to fixuje testem.

**Souhrnný řádek `Checky: … 42 SKIP` se nemění.** Bere se z `result.summary`,
který engine počítá z `CheckResult`ů (`text_report.py:332`), ne z view.

- [ ] **Step 1: Write the failing unit tests for merging**

Nejdřív rozšiř import na řádku 1 `tests/reporting/test_view.py`:

```python
from migration_validator.models.result import (
    CheckResult,
    MatchInfo,
    ScopeResult,
    Severity,
    SKIPPED_BECAUSE,
    SKIP_DEACTIVATED,
    Status,
)
```

Pak rozšiř helper `_check()` (řádky 4–21) o parametr `skipped_because` — bez
něj nejde deaktivační SKIP od cizího odlišit. Změna je čistě aditivní, takže
desítky existujících volání zůstávají beze změny:

```python
def _check(check_id, *, family=None, label="X", value="v", status=Status.PASS,
           mode="state", baseline_value=None, delta=None, message="msg", address=None,
           group=None, skipped_because=None):
    details = {}
    if address:
        details["address"] = address
    if skipped_because:
        details[SKIPPED_BECAUSE] = skipped_because
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
        details=details,
    )
```

Pak přidej na konec souboru:

```python
def _deactivation_skip(label):
    return _check(
        f"check_{label}",
        label=label,
        status=Status.SKIP,
        value="interface deactivated",
        message="sluzba je v konfiguraci deaktivovana (interface deactivated)",
        skipped_because=SKIP_DEACTIVATED,
    )


def test_deactivation_skips_collapse_into_one_row():
    """Bez --detail se deaktivacni SKIPy slevaji do jednoho radku s poctem.

    Blok deaktivovane sluzby jich mel v laborce sedm az deset a vsechny
    rikaly doslova totez co radek Deaktivace nad nimi.
    """
    view = build_view(
        _scope(
            [
                _check("deactivation_state", label="Deaktivace", status=Status.WARN,
                       value="interface deactivated"),
                _deactivation_skip("BFD"),
                _deactivation_skip("Interface status"),
                _deactivation_skip("Staticka routa"),
            ]
        ),
        detail=False,
    )

    labels = [row.label for row in view.sections[0].rows]

    assert labels == ["Deaktivace", "Ostatni checky"]
    assert view.sections[0].rows[1].value == "3 preskoceno"
    assert view.sections[0].rows[1].status is Status.SKIP


def test_detail_keeps_every_deactivation_skip():
    """S --detail se nesleva nic - zasada 'detail rozbali vsechno'.

    Zabiji mutanta: slevani bez ohledu na priznak detail. S nim by
    --detail prestal byt uplnym vypisem a operator by se k jednotlivym
    preskocenym checkum nedostal nikde.
    """
    view = build_view(
        _scope(
            [
                _check("deactivation_state", label="Deaktivace", status=Status.WARN,
                       value="interface deactivated"),
                _deactivation_skip("BFD"),
                _deactivation_skip("Interface status"),
                _deactivation_skip("Staticka routa"),
            ]
        ),
        detail=True,
    )

    labels = [row.label for row in view.sections[0].rows]

    assert labels == ["Deaktivace", "BFD", "Interface status", "Staticka routa"]


def test_foreign_skip_is_not_collapsed():
    """SKIP z jineho duvodu zustava samostatne, i kdyz sedi mezi deaktivacnimi.

    Zmereno na bloku svc:et-0/0/10.0:Internet: mezi devíti deaktivacnimi
    SKIPy tam sedi 'BGP prefixy : bez baseline', coz je SKIP porovnavaciho
    checku bez baseline snapshotu. Slit ho dohromady by zahodilo informaci,
    kterou nic jineho nenese.

    Zabiji mutanta: slevani podle Status.SKIP misto podle znacky. Predchozi
    dva testy by pod nim zustaly zelene - tenhle je jediny, ktery ten
    rozdil meri.
    """
    view = build_view(
        _scope(
            [
                _check("deactivation_state", label="Deaktivace", status=Status.WARN,
                       value="interface deactivated"),
                _deactivation_skip("BFD"),
                _check("bgp_prefix_counts", label="BGP prefixy", status=Status.SKIP,
                       value="bez baseline", message="porovnavaci check bez baseline snapshotu"),
                _deactivation_skip("Staticka routa"),
            ]
        ),
        detail=False,
    )

    labels = [row.label for row in view.sections[0].rows]

    assert labels == ["Deaktivace", "BGP prefixy", "Ostatni checky"]
    assert view.sections[0].rows[2].value == "2 preskoceno"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -o addopts="" tests/reporting/test_view.py -q`
Expected: FAIL — `ImportError: cannot import name 'SKIPPED_BECAUSE'`

- [ ] **Step 3: Add the shared constants**

V `migration_validator/models/result.py` přidej pod importy (nad první
`@dataclass`):

```python
# Klic v CheckResult.details, kterym check rekne, PROC byl preskocen.
# Zije v models, ne v checks/ ani v reporting/: pisou ho checky a cte ho
# renderer, a ani jeden z tech balíku nema na druhy videt.
SKIPPED_BECAUSE = "skipped_because"

# Jedina hodnota, kterou renderer sleva. Ostatni SKIPy (chybejici
# inventory, compare bez baseline, selhany collector, vyjimka v checku)
# znacku nedostavaji, protoze kazdy z nich nese vlastni informaci.
SKIP_DEACTIVATED = "service_deactivated"
```

- [ ] **Step 4: Mark the deactivation skip at its source**

V `migration_validator/checks/base.py` uprav `_skip()` (řádky 95–113) —
přidává se **volitelný** parametr, takže ostatní čtyři volání zůstávají beze
změny:

```python
def _skip(
    check: Check,
    severity: Severity,
    message: str,
    value: str,
    *,
    skipped_because: str | None = None,
) -> list[CheckResult]:
    """Skip, ktery vznikl mimo check - a presto je to plnohodnotny radek.

    `value` je kratky duvod do sloupce hodnot, `message` zustava celou
    vetou pro sloupec NALEZ a strojovy vystup. Drive tu obe pole chybela,
    takze renderer sahl po id checku a po cele vete; u selhaneho collectoru
    to byla veta o RPC chybe, ktera roztahla blok na 270 znaku sirky.

    `skipped_because` je STRUKTURALNI znacka pro renderer. Vyplnuje ji
    jedina vetva (deaktivovana sluzba), protoze jedine ta vyrabi N radku,
    ktere rikaji doslova totez. Renderer podle ni sleva - ne podle
    Status.SKIP a ne podle textu zpravy: v jednom bloku sedi vedle sebe
    devet deaktivacnich SKIPu a jeden 'bez baseline', a ten druhy nese
    informaci, kterou nic jineho nenese.
    """
    return [
        CheckResult(
            id=check.id,
            mode=check.mode.value,
            status=derive_status(Outcome.SKIP, severity),
            severity=severity,
            message=message,
            label=check.label,
            value=value,
            details={SKIPPED_BECAUSE: skipped_because} if skipped_because else {},
        )
    ]
```

V téže souboru uprav deaktivační větev (řádky 146–153):

```python
    if check.id != DEACTIVATION_CHECK_ID and ctx.scope.is_deactivated:
        reason = ctx.scope.deactivation_reason
        return _skip(
            check,
            severity,
            f"sluzba je v konfiguraci deaktivovana ({reason})",
            reason,
            skipped_because=SKIP_DEACTIVATED,
        )
```

A rozšiř import z `models.result` nahoře v souboru o `SKIPPED_BECAUSE`
a `SKIP_DEACTIVATED`.

- [ ] **Step 5: Collapse the marked skips in the view**

V `migration_validator/reporting/view.py` rozšiř import na řádku 13:

```python
from migration_validator.models.result import (
    CheckResult,
    ScopeResult,
    Severity,
    SKIPPED_BECAUSE,
    SKIP_DEACTIVATED,
    Status,
)
```

Přidej pod `change_text()` (tedy nad `_row`):

```python
MERGED_LABEL = "Ostatni checky"


def _merge_deactivation_skips(checks: list[CheckResult]) -> list[CheckResult]:
    """Deaktivacni SKIPy jedne sekce nahradi jednim radkem s poctem.

    Slevaji se JEN radky se znackou, ne vsechny SKIPy. Blok deaktivovane
    sluzby umi nest i 'BGP prefixy : bez baseline', coz je SKIP z docela
    jineho duvodu a nese informaci, kterou nic jineho nenese.

    Pocet je pocet SLOUCENYCH radku, ne vsech SKIPu v sekci. Kdo napise
    len(skips), dostane v bloku svc:et-0/0/10.0:Internet deset misto devíti.
    """
    marked = {
        id(check)
        for check in checks
        if check.details.get(SKIPPED_BECAUSE) == SKIP_DEACTIVATED
    }
    if not marked:
        return checks

    # Deli se podle IDENTITY objektu, ne podle rovnosti: CheckResult je
    # dataclass s vygenerovanym __eq__, takze dva radky se shodnymi poli by
    # se pres `check not in marked` odstranily oba.
    kept = [check for check in checks if id(check) not in marked]
    return [
        *kept,
        CheckResult(
            id="deactivation_skips",
            # mode='state' schvalne: sloupec ZMENA ma u souhrnneho radku
            # zustat prazdny, protoze zadnou baseline hodnotu nenese.
            mode="state",
            status=Status.SKIP,
            severity=Severity.ADVISORY,
            message=f"{len(marked)} dalsich checku preskoceno, sluzba je deaktivovana",
            label=MERGED_LABEL,
            value=f"{len(marked)} preskoceno",
        ),
    ]
```

Uprav signaturu a tělo `build_view` (`view.py:141`):

```python
def build_view(scope: ScopeResult, *, detail: bool = False) -> ServiceView:
    """Slozi z vysledku sluzby vse, co report vypisuje.

    Sekce prazdne rodiny se nevytvari - sluzba bez IPv6 nema mit prazdnou
    IPv6 sekci.

    `detail` rozhoduje o slevani deaktivacnich SKIPu. Zije az tady, ne
    v engine: sloucení je vlastnost ZOBRAZENI, ne vysledku behu, takze
    RunResult.to_dict() vydava vsechny checky dal bez ohledu na nej.
    """
```

a hned za `if not checks: continue` (`view.py:157`) přidej:

```python
        if not detail:
            checks = _merge_deactivation_skips(checks)
```

- [ ] **Step 6: Run the unit tests to verify they pass**

Run: `.venv/bin/python -m pytest -o addopts="" tests/reporting/test_view.py -q`
Expected: PASS

- [ ] **Step 7: Pass `detail` through the renderer**

V `migration_validator/reporting/text_report.py` uprav řádek 347:

```python
    views = [(scope, build_view(scope, detail=detail)) for scope in result.scopes]
```

- [ ] **Step 8: Run the full suite to see what else moved**

Run: `.venv/bin/python -m pytest -o addopts="" -q`

Expected: PASS, **676 passed, 0 skipped** — ripple do existujících testů je
změřený jako **nulový**. Změřeno při psaní tohohle plánu tím, že se kroky 3
až 7 dočasně provedly nad `main` a zase vrátily; slučování bylo ověřeně
aktivní (blok se opravdu zkrátil na dva řádky, viz krok 9).

**Nula je tady nález, ne potvrzení.** Znamená, že vykreslený obsah bloku
deaktivované služby dnes nehlídá **žádný** z 676 testů — právě proto k téhle
úloze patří tři end-to-end testy z kroku 9, ne jen jednotkové z kroku 1.

Kdyby cokoli spadlo, změřený předpoklad neplatí — zastav a nahlas, neopravuj
to potichu. Zvlášť platí, že testy o počtu SKIPů v **souhrnu** spadnout
nesmí: souhrn se bere z `result.summary`, ne z view, takže jejich pád by
znamenal, že slučování proteklo do výsledku běhu.

- [ ] **Step 9: Write the end-to-end test for the collapsed block**

Do `tests/test_end_to_end.py` přidej:

```python
def _deactivate_shared_service(old, new):
    """Vypne tutez sluzbu v obou snimcich a vrati jeji description.

    Parovani podle DVOJICE (description, service_type): samotny description
    nestaci, fixtures nesou EVPN-VLAN-AWARE-INTERNET dvakrat - jednou jako
    Internet a jednou jako E-LAN. Podle samotneho description by se v kazdem
    snimku mohla vypnout jina a test by meril nesparovanou sluzbu.
    """
    target = ("EVPN-VLAN-AWARE-CPE13-NNI", "E-LAN")
    hit = 0
    for snapshot in (old, new):
        for scope in snapshot.scopes:
            if scope.kind != "service":
                continue
            if (scope.key.description, scope.key.service_type) == target:
                scope.interface_active = False
                hit += 1
    assert hit == 2, "sluzba neni v obou snimcich, test by meril nesparovanou"
    return target[0]


def test_deactivated_service_block_has_one_skip_row_without_detail(synthetic_snapshot):
    """Bod 18: blok deaktivovane sluzby prestane tisknout N stejnych radku.

    Test jde pres api.evaluate a render, ne nad rucne slozenym ServiceView.
    Nad ServiceView by prosel i tehdy, kdyby text_report.py build_view
    priznak detail vubec nepredaval - a to je presne to misto, kde se
    slevani rozhoduje.

    Zabiji mutanta: `build_view(scope)` bez detail v text_report.py.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    target = _deactivate_shared_service(old, new)
    result = api.evaluate(new, baseline=old, now=NOW)

    block = _block_of(render(result), target, "E-LAN")

    assert "Ostatni checky" in block
    assert "preskoceno" in block
    # Sedm deaktivacnich SKIPu se slilo, radek Deaktivace zustava.
    assert block.count("interface deactivated") == 1
    assert "Deaktivace" in block


def test_detail_expands_the_deactivated_service_block(synthetic_snapshot):
    """S --detail ma tyz blok vsechny puvodni radky.

    Zabiji mutanta: slevani bez ohledu na priznak detail.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    target = _deactivate_shared_service(old, new)
    result = api.evaluate(new, baseline=old, now=NOW)

    block = _block_of(render(result, detail=True), target, "E-LAN")

    assert "Ostatni checky" not in block
    assert block.count("interface deactivated") > 1


def test_json_report_keeps_every_check_regardless_of_detail(synthetic_snapshot):
    """Slevani je vlastnost textoveho reportu, ne vysledku behu.

    RunResult.to_dict() staví vystup z CheckResultu, ne z ServiceView.
    Kdyby slevani proteklo do nej, strojovy konzument by o preskocenych
    checkach prisel a nic by mu to nereklo.

    Zabiji mutanta: slevani presunute do engine.py misto do view.py.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    target = _deactivate_shared_service(old, new)
    result = api.evaluate(new, baseline=old, now=NOW)

    payload = result.to_dict()
    scope = next(
        item for item in payload["scopes"]
        if item.get("identity", {}).get("description") == target
        and item.get("identity", {}).get("service_type") == "E-LAN"
    )
    labels = [check.get("label") for check in scope["checks"]]

    assert "Ostatni checky" not in labels
    assert len([label for label in labels if label]) > 2
```

- [ ] **Step 10: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" tests/test_end_to_end.py -q -k "deactivated_service_block or detail_expands or json_report_keeps"`
Expected: PASS, 3 passed

**Změřený cílový výstup**, proti kterému se dá porovnat, když něco nesedí
(pořízeno při psaní plánu dočasnou aplikací kroků 3 až 7):

```
 STAV | CHECK          : POST (et-0/0/8.313)   | ZMENA PROTI ge-0/0/2.313
 -----+----------------+-----------------------+-------------------------
 WARN | Deaktivace     : interface deactivated |
 SKIP | Ostatni checky : 7 preskoceno          |
```

Sloupec ZMENA je u řádku `Deaktivace` **prázdný**, protože u služby vypnuté
v obou snímcích je `value == baseline_value` a `change_text` (`view.py:95`)
v tom případě vrací prázdný řetězec. Právě proto vychází
`block.count("interface deactivated") == 1`.

A blok s cizím SKIPem, změřený týmž průchodem:

```
 STAV | CHECK          : POST (et-0/0/10.0)    | ZMENA PROTI -
 -----+----------------+-----------------------+--------------
 SKIP | BGP prefixy    : bez baseline          |
 WARN | Deaktivace     : interface deactivated | bez baseline
 SKIP | Ostatni checky : 9 preskoceno          |
```

Kdyby `assert block.count("interface deactivated") == 1` selhalo na jiném
čísle, počet checků nad tou službou se změnil — přepiš očekávání podle
skutečnosti a **zapiš změřenou hodnotu do docstringu**, ať tvrzení nezastará.

- [ ] **Step 11: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: PASS, **683 passed, 0 skipped** (677 + 3 jednotkové + 3 end-to-end).
Kdyby krok 8 vyžádal úpravy existujících testů, počet sedí dál — úpravy
očekávání počet nemění.

- [ ] **Step 12: Commit**

```bash
git add migration_validator/models/result.py migration_validator/checks/base.py \
        migration_validator/reporting/view.py migration_validator/reporting/text_report.py \
        tests/reporting/test_view.py tests/test_end_to_end.py
git commit -m "feat: blok deaktivovane sluzby sleva SKIPy do jednoho radku

Deaktivacni SKIP dostava strukturalni znacku uz tam, kde vznika, a
renderer podle ni sleva - ne podle Status.SKIP a ne podle textu zpravy.
--detail rozepisuje po jednom, JSON vydava vsechny checky dal."
```

- [ ] **Step 13: Run the layer-discriminating mutant (over committed work)**

Tenhle mutant je jádro úlohy: musí ukázat **rozdíl mezi slučováním podle
značky a podle stavu**.

```bash
sed -i 's|        if check.details.get(SKIPPED_BECAUSE) == SKIP_DEACTIVATED|        if check.status is Status.SKIP  # MUTANT|' migration_validator/reporting/view.py
grep -n MUTANT migration_validator/reporting/view.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: FAIL — musí padnout `tests/reporting/test_view.py::test_foreign_skip_is_not_collapsed`,
zatímco `test_deactivation_skips_collapse_into_one_row` a
`test_detail_keeps_every_deactivation_skip` **zůstanou zelené**. Teprve to ten
rozdíl měří. Kdyby padly všechny tři, mutant je hrubší, než má být; kdyby
neprošel žádný, `sed` netrefil odsazení — uprav ručně.

- [ ] **Step 14: Run the detail-flag mutant**

```bash
git checkout -- migration_validator/reporting/view.py
sed -i 's|    views = \[(scope, build_view(scope, detail=detail)) for scope in result.scopes\]|    views = [(scope, build_view(scope)) for scope in result.scopes]  # MUTANT|' migration_validator/reporting/text_report.py
grep -n MUTANT migration_validator/reporting/text_report.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: FAIL — musí padnout `tests/test_end_to_end.py::test_detail_expands_the_deactivated_service_block`,
zatímco jednotkové testy ve `test_view.py` **zůstanou zelené** (volají
`build_view` přímo, renderer obcházejí). Přesně ten rozdíl mezi vrstvami,
kvůli kterému test 9 jde přes `render`.

- [ ] **Step 15: Revert the mutants**

```bash
git checkout -- migration_validator/reporting/text_report.py
.venv/bin/python -m pytest -o addopts="" -q
git status --porcelain
```

Expected: PASS, 683 passed. `git status --porcelain` vypíše jen `?? mx1-pop1.yml`.

---

## Task 3: Body 16, 17 a 9 — přejmenování a zapsaná odůvodnění

**Files:**
- Modify: `tests/models/test_inventory.py:305-307`, `migration_validator/checks/routes.py:87-97`, `migration_validator/checks/bgp.py`, `mx_parser.py:747-752`, `evo_parser.py:747-752`

**Interfaces:**
- Consumes: nic. Úloha 1 sahala do `checks/bgp.py` do větve `without_session`,
  tahle do komentáře u popisku — nekolidují.
- Produces: nic.

**Kontext, který implementer nemá:**

Tahle úloha **nemění chování**. Všechny tři body se uzavírají tím, že se
zapíše, proč je současný stav správný, případně se přejmenuje test, jehož
název neodpovídá tělu. Kdybys zjistil, že některý z bodů chování změnit
potřebuje, je to nález — zastav a nahlas.

**Bod 17 — měření, o které se opírá.** Uživatel na živé laborce
(`clab-pop-migration-MX1-POP1`) 2026‑08‑04 nakonfiguroval:

```
routing-options static route 198.62.254.0/29
    next-hop 152.11.14.4;
    inactive: qualified-next-hop 152.11.14.3;
    qualified-next-hop 152.11.14.2;
```

a parser z toho vydal **jediný** záznam (`next_hop: [152.11.14.4]`,
`active: true`). Kolize `(rib, prefix)` v množině `deactivated` tedy nastat
nemůže.

**Bod 17b — co je na komentáři špatně.** `discard`, `reject` a `next-table`
adresu opravdu nemají. `qualified-next-hop` nese **buď adresu, nebo
`interface-name`** — do výčtu tvarů bez adresy nepatří. Vlna 10 chování
parserů nemění, opravuje jen tvrzení.

**Bod 9 — proč kvalifikátor zůstává.** Změřeno: laboratorní fixtures mají
sedm rodinových sekcí a všechny nesou právě jednoho peera, takže kvalifikátor
je tam vždy redundantní. Podmíněný kvalifikátor by ale znamenal, že popisek
závisí na datech, a `label` je identifikátor řádku i v JSON — přibytí druhého
peera by přejmenovalo řádek toho prvního.

- [ ] **Step 1: Rename the inventory test (bod 16)**

V `tests/models/test_inventory.py` nahraď řádky 305–307:

```python
def test_inventory_schema_version_is_five():
    """Konstanta schematu je 5 - stara inventory se nemigruje, generuje se znovu.

    Nazev drive sliboval odmitnuti schematu 3, ktere tenhle test nikdy
    netestoval: telo jen asertuje hodnotu konstanty. Skutecne odmitnuti
    stare inventory pokryva test_old_inventory_fails_loudly a ten zustava.
    """
    assert INVENTORY_SCHEMA_VERSION == 5
```

- [ ] **Step 2: Record why the route key is safe (bod 17)**

V `migration_validator/checks/routes.py` nahraď komentář nad `deactivated`
(řádky 88–92, ten začínající „Chybejici klic 'active'") tímto — původní tři
věty zůstávají, přibývá odstavec o klíčování:

```python
        # Chybejici klic 'active' znamena zamer od parseru pred vlnou 8.
        # Snapshot i inventory maji od te vlny schema 5, takze se takovy
        # zamer nenacte - default je tu jen proto, aby jednotkovy test
        # nemusel psat klic, ktery netestuje.
        #
        # Klicovani jen dvojici (rib, prefix) je bezpecne, ne opomenuti:
        # duplicitni identita s ruznymi priznaky by umlcela i tu aktivni
        # routu, ale parser dva zaznamy pro tentyz prefix nevydava. Zmereno
        # 2026-08-04 na zive laborce konfiguraci s holym next-hopem a dvema
        # qualified-next-hopy (jeden deaktivovany): parser vydal jediny
        # zaznam. `configured` je klicovana stejne - je to sdileny dusledek,
        # ne nekonzistence mezi dvema mnozinami.
```

- [ ] **Step 3: Fix the parser comment in BOTH parsers (bod 17b)**

V `mx_parser.py` **i** `evo_parser.py` nahraď komentář na řádcích 747–752
(uvnitř `StaticRoute(...)`, nad `next_hop=`). **Musí být v obou souborech
doslova stejný**, jinak se rozejde zámek parserů:

```python
                        # Jen holý next-hop. discard, reject a next-table
                        # adresu k porovnání se subnetem rozhraní nemají,
                        # takže se na službu nenamapují.
                        #
                        # qualified-next-hop ji naopak má — nese buď adresu,
                        # nebo interface-name — a do výčtu výš nepatří.
                        # Vynechává se vědomě a odloženě, ne proto, že by
                        # adresu neměl: routa směrovaná výhradně přes něj
                        # dostane prázdný next_hop, na službu se nenamapuje
                        # a nenainstalovaná zmizí beze stopy. Zapsáno jako
                        # otevřený bod roadmapy vlny 10.
```

Poznámka k diakritice: parsery jsou psané česky **s** diakritikou, na rozdíl
od `migration_validator/`. Řiď se souborem.

- [ ] **Step 4: Verify the parser lock immediately**

Run: `diff mx_parser.py evo_parser.py | wc -l`
Expected: **146**

Kdyby vyšlo jiné číslo, komentáře se v obou souborech neshodují — sjednoť je
a opakuj. **Nepokračuj s rozejitým zámkem.**

- [ ] **Step 5: Record why the BGP label qualifier is unconditional (bod 9)**

V `migration_validator/checks/bgp.py` přidej nad třídu `BgpSessionStateCheck`
(tedy nad `@register` na řádku 44):

```python
# Popisek radku je vzdycky "BGP status (adresa)", i kdyz ma sekce jedineho
# peera a adresa je tam potreti. Podminit ho poctem peeru v sekci se
# nabizelo - zmereno, ze v laborce je redundantni ve vsech sedmi sekcich -
# ale `label` je identifikator radku i ve strojovem JSON vystupu. Podmineny
# kvalifikator by znamenal, ze pribyti druheho souseda prejmenuje i radek
# toho prvniho, takze dva behy tehoz stavu by se v diffu nesparovaly.
#
# Na sdilene podsiti (/29, /24 na NNI nebo zakaznicka podsit se dvema CPE)
# je kvalifikator nutny: bez nej by dva radky "BGP status" vedle sebe
# nerekly, ktery soused je rozbity. Fixtures tenhle tvar nemodeluji, takze
# mereni ukazuje redundanci, ne cenu jejiho odstraneni.
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: PASS, **683 passed, 0 skipped** — počet se nemění, úloha jen
přejmenovává a komentuje.

- [ ] **Step 7: Verify nothing referenced the old test name**

Run: `grep -rn "test_inventory_rejects_schema_three" . --include=*.py --include=*.md`
Expected: zásah **jen** v `docs/` (spec a starší roadmapy — ty se
nepřepisují, popisují stav v době vzniku). Zásah v `tests/` nebo
`migration_validator/` znamená, že název někdo cituje — oprav ho.

- [ ] **Step 8: Commit**

```bash
git add tests/models/test_inventory.py migration_validator/checks/routes.py \
        migration_validator/checks/bgp.py mx_parser.py evo_parser.py
git commit -m "docs: uzavreni bodu 16, 17 a 9 zapsanim oduvodneni

Bod 16: nazev testu odpovida telu. Bod 17: klicovani (rib, prefix) je
bezpecne, doplneno merenim z laborky. Bod 9: kvalifikator zustava
bezpodminecny, protoze label je identifikator i v JSON.

Komentar o qualified-next-hop se v obou parserech opravuje - nese bud
adresu, nebo interface-name, takze do vyctu tvaru bez adresy nepatri."
```

- [ ] **Step 9: Verify the parser lock over committed work**

```bash
diff mx_parser.py evo_parser.py | wc -l
git status --porcelain
```

Expected: **146** a jen `?? mx1-pop1.yml`.

Mutant se v téhle úloze **nepouští** — nemění chování, takže by nebylo co
zabít. Zámek parserů z kroku 4 a nezměněný počet testů z kroku 6 jsou její
důkazy.

---

## Task 4: Bod 12 — rozrůzněné countery a test, který je zamyká

**Files:**
- Modify: `tests/conftest.py:63-95`
- Test: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: nic z předchozích úloh.
- Produces: `tests.conftest._counters_for(peer: str) -> dict[str, int]`. Žádná
  pozdější úloha ji nevolá.

**Kontext, který implementer NUTNĚ potřebuje — změřeno:**

**Ripple je NULA, a to je nález, ne potvrzení.** Změřeno při psaní tohohle
plánu: countery se rozrůznily, mutace byla ověřeně aktivní (peery dostaly
`received` 10, 11, 15, 17, 18 a report ta čísla vytiskl), a přesto zůstalo
**676 passed, 0 failed**. Hodnoty counterů ze sdílených fixtures tedy
**nehlídá nikdo**.

Praktický důsledek: samotné rozrůznění by bylo **dekorativní**. Vedlejší
přínos, kvůli kterému bod 12 vznikl — „záměna peerů v kódu by byla na reportu
vidět" — se neuskuteční, dokud tu vlastnost nezamkne test. Krok 5 ho přidává.

**Determinismus je podmínka, ne preference.** Baseline i subject staví tatáž
funkce z téže adresy. Kdyby se čísla lišila mezi snímky, `bgp_prefix_counts`
by začal hlásit rozdíly u každé zdravé služby a fixtures by přestaly být
zdravou výchozí sadou.

**Změřené hodnoty**, které navržená funkce vydá (`received` na primární RIB):

| peer | received |
|---|---|
| `152.11.13.2` | 17 (a 6 na druhé RIB z `_SECOND_RIB_COUNTERS`) |
| `152.11.14.4` | 11 |
| `198.11.13.2` | 18 |
| `198.11.14.2` | 10 |
| `2001:abcd:11:13::b` | 15 |
| `2001:db8:11:13::b` | 10 |
| `2001:db8:11:14::b` | 11 |

Kolize napříč službami existují (`198.11.14.2` a `2001:db8:11:13::b` mají obě
10), ale **uvnitř žádné služby ne** — a to je to, na čem záleží: dva peery
téhož bloku musí jít rozlišit. Krok 5 to zamyká.

- [ ] **Step 1: Write the failing lock test**

Do `tests/test_end_to_end.py` přidej:

```python
def test_peers_of_one_service_carry_different_prefix_counts(synthetic_snapshot):
    """Dva peery jedne sluzby musi mit ruzne countery.

    Sdilene fixtures davaly kazdemu peerovi 14/14/14/3, takze zamena peeru
    v kodu by na reportu nebyla videt vubec. Zmereno pri psani planu vlny
    10: rozruzneni counteru neshodilo ani jeden z 676 testu, tedy jejich
    hodnoty nehlidal nikdo. Bez teto aserce by se fixtures mohly kdykoli
    vratit k uniformnim cislum a nic by to nevytklo.

    Zabiji mutanta: navrat `_ribs_for` k `dict(_RIB_COUNTERS)`.
    """
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    service = next(
        scope for scope in new.scopes
        if scope.id == "svc:L3VPN-CPE13-NNI:IPVPN"
    )
    peers = service.selectors.bgp_neighbors
    assert len(peers) >= 2, "sluzba uz nema dva peery, test by byl vakuovy"

    received = [
        counters["received"]
        for peer in peers
        for counters in new.facts["bgp"][peer]["ribs"].values()
    ]

    assert len(set(received)) == len(received), f"countery se opakuji: {received}"


def test_prefix_counts_match_between_baseline_and_subject(synthetic_snapshot):
    """Rozruznene countery musi byt v obou snimcich stejne.

    Kdyby se lisily mezi snimky, bgp_prefix_counts by zacal hlasit rozdil
    u kazde zdrave sluzby a fixtures by prestaly byt zdravou vychozi sadou.

    Zabiji mutanta: countery odvozene z ceho jineho nez z adresy peera
    (napr. z poradi peeru), coz by dalo jina cisla v .4 a v .5.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    shared = set(old.facts["bgp"]) & set(new.facts["bgp"])
    assert shared, "snimky nemaji spolecneho peera, test by byl vakuovy"

    for peer in sorted(shared):
        assert old.facts["bgp"][peer]["ribs"] == new.facts["bgp"][peer]["ribs"], peer
```

- [ ] **Step 2: Run the tests to verify the first one fails**

Run: `.venv/bin/python -m pytest -o addopts="" tests/test_end_to_end.py -q -k "different_prefix_counts or match_between_baseline"`

Expected: FAIL, 1 failed — `test_peers_of_one_service_carry_different_prefix_counts`
(všechny countery jsou dnes 14). Druhý projde už teď a projít má: uniformní
čísla jsou triviálně shodná mezi snímky. Nechej ho — hlídá, aby ho krok 3
nerozbil.

- [ ] **Step 3: Differentiate the counters**

V `tests/conftest.py` nahraď blok `_RIB_COUNTERS` (řádky 63–69) tímto.
`_SECOND_RIB_COUNTERS` **zůstává beze změny** — nese druhou RIB
`DUAL_RIB_PEER`a a jeho čísla se od primární RIB liší už dnes:

```python
def _counters_for(peer: str) -> dict[str, int]:
    """Countery odvozene z adresy peera, aby se peery na reportu odlisily.

    Drive nesl kazdy peer 14/14/14/3, takze zamena peeru v kodu nebyla na
    reportu videt vubec. Zmereno pri psani planu vlny 10: rozruzneni
    neshodilo ani jeden ze 676 testu, tedy hodnoty counteru nehlidal nikdo -
    proto k teto zmene patri i zamykajici test v test_end_to_end.py.

    Odvozeni z ADRESY je podminka, ne styl: baseline i subject stavi tataz
    funkce z teze adresy, takze bgp_prefix_counts porovnava shodna cisla.
    Kdyby se cisla lisila mezi snimky, kazda zdrava sluzba by zacala svitit
    oranzove.

    Invarianty, ktere skutecny Junos drzi a fixtures je drzet musi taky:
    accepted <= received, active == accepted, suppressed == received - accepted.
    """
    seed = sum(ord(character) for character in peer) % 9
    received = 10 + seed
    accepted = received - seed % 3
    return {
        "received": received,
        "accepted": accepted,
        "advertised": 2 + seed % 4,
        "active": accepted,
        "suppressed": received - accepted,
    }
```

A v `_ribs_for()` (`conftest.py:90-91`) nahraď:

```python
    primary = "inet6.0" if family == 6 else "inet.0"
    ribs = {primary: _counters_for(peer)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -o addopts="" tests/test_end_to_end.py -q -k "different_prefix_counts or match_between_baseline"`
Expected: PASS, 2 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`

Expected: PASS, **685 passed, 0 skipped** (683 + 2 nové). Ripple do
existujících testů je změřený jako **nulový**; kdyby cokoli spadlo, změřený
předpoklad neplatí — zastav a nahlas, neopravuj to potichu.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_end_to_end.py
git commit -m "test: countery ve fixtures se odvozuji z adresy peera

Kazdy peer nesl 14/14/14/3, takze zamena peeru v kodu nebyla na reportu
videt. Rozruzneni samo nic nehlida - zmereno, ze hodnoty counteru
nehlidal zadny z 676 testu - proto k nemu patri i zamykajici test."
```

- [ ] **Step 7: Run the mutant (over committed work)**

Mutant míří do `tests/conftest.py`, což je **výjimka z pravidla „mutant nesmí
mířit do testů"**: conftest je tady produkční kód úlohy a testy, které ho
asertují, jsou v jiném souboru (`test_end_to_end.py`). Pravidlo zakazuje
mutovat **tentýž** soubor, proti kterému test asertuje.

```bash
sed -i 's|    ribs = {primary: _counters_for(peer)}|    ribs = {primary: {"received": 14, "accepted": 14, "advertised": 3, "active": 14, "suppressed": 0}}  # MUTANT|' tests/conftest.py
grep -n MUTANT tests/conftest.py
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: FAIL — `test_peers_of_one_service_carry_different_prefix_counts`.
Kdyby prošel, zámek nic nedrží a bod 12 zůstal dekorativní — zastav a nahlas.

- [ ] **Step 8: Revert the mutant**

```bash
git checkout -- tests/conftest.py
.venv/bin/python -m pytest -o addopts="" -q
git status --porcelain
```

Expected: PASS, 685 passed. `git status --porcelain` jen `?? mx1-pop1.yml`.

---

## Task 5: Bod 2 — capture z laborky a přeběh všech mutantů

**Files:**
- Modify: `172.20.20.4.yml`, `172.20.20.5.yml`, `tests/fixtures/rpc/junos-evo/*`

**Interfaces:**
- Consumes: hotové úlohy 1 až 4 — tahle úloha jejich mutanty pouští znovu.
- Produces: nic.

**Kontext, který implementer NUTNĚ potřebuje:**

**Tahle úloha vyžaduje uživatele.** Laborka běží, ale služby jsou aktuálně
aktivní na `.4`. Pořadí, které uživatel popsal 2026‑08‑04: capture z `.4` →
**přemigrovat služby na `.5`** → capture z `.5`. Tu migraci dělá uživatel,
ne implementer. Heslo do laborky je `MIG_LAB_PASSWORD` v `~/.bashrc` **pod**
neinteraktivní pojistkou, takže se načte jen explicitním `eval`.

**Proč je tahle úloha poslední.** Přegenerování fixtures zneplatní každý
mutant změřený předtím. Krok 5 je proto pouští **všechny znovu**.

**Co se čte a co ne.** Kořenové `172.20.20.{4,5}.yml` jsou dnes na schematu 4
a nástroj je odmítne načíst; **žádný test je nečte**, takže jejich
přegenerování nic neshodí. `tests/fixtures/rpc/junos-evo/` naopak testy čtou —
ripple z něj se **měří**, neodhaduje.

**Uživatelova dočasná konfigurace** s qualified-next-hopy na `.4` zůstává na
krabici. Do přegenerované kořenové inventory se promítne; nevadí to, protože
ty soubory nikdo nečte. Do `tests/fixtures/` se promítnout **nesmí** —
`tests/fixtures/172.20.20.{4,5}.yml` tahle úloha **nepřegenerovává**, mění
jen kořenové soubory a RPC fixtures.

**Pořadí kroků 1 až 4 je dané provozem laborky, ne pohodlím.** Služby jsou
teď aktivní na `.4`. Kdyby se `.5` četlo hned po `.4`, parser by přečetl
krabici **bez služeb** a vydal by prázdnou inventory. Migraci mezi tím dělá
uživatel.

- [ ] **Step 1: Parse the pre-migration device (`.4`)**

CLI obou parserů je ověřené z `--help`:
`mx_parser.py [--auth {key,password}] [-u USERNAME] [-o OUTPUT] hostname`,
výchozí výstup je `<hostname>.yml`, výchozí autentizace SSH klíčem.

```bash
eval "$(grep MIG_LAB_PASSWORD ~/.bashrc)"
.venv/bin/python mx_parser.py 172.20.20.4 -o 172.20.20.4.yml
head -1 172.20.20.4.yml
```

Expected: `schema_version: 5`.

Kdyby v souboru nebyla jediná služba, běželo to proti špatné krabici nebo
se migrace už stala — **zastav a zeptej se uživatele**, nepokračuj.

- [ ] **Step 2: Ask the user to migrate the services**

Napiš uživateli přesně tohle a **počkej na odpověď**:

> Capture z `.4` je hotový (`172.20.20.4.yml`, schema 5). Můžeš teď
> přemigrovat služby na `.5`? Až budou nahoře, řekni mi to a udělám capture
> z `.5` a přegeneruju `tests/fixtures/rpc/junos-evo/`.

Bez potvrzení **nepokračuj** a nezkoušej migraci provést sám.

- [ ] **Step 3: Parse the post-migration device (`.5`)**

Po potvrzení od uživatele:

```bash
.venv/bin/python evo_parser.py 172.20.20.5 -o 172.20.20.5.yml
```

`mx_parser.py` je pro Junos (MX, `.4`), `evo_parser.py` pro Junos EVO (`.5`) —
zaměnit je znamená přečíst zařízení špatným parserem. Kdyby autentizace
klíčem selhala, přidej `--auth password -u <uzivatel>`. Parsery přepínač
`--password` **nemají**, takže si heslo vyžádají interaktivně; `eval` výš je
tam proto, aby hodnota byla v shellu k dispozici (`MIG_LAB_PASSWORD` sedí
v `~/.bashrc` **pod** neinteraktivní pojistkou, takže se sama nenačte).
Interaktivní prompt nemůžeš obsloužit ty — v tom případě požádej uživatele,
ať parser spustí sám přes `! <prikaz>`.

Ověř:

```bash
head -1 172.20.20.4.yml 172.20.20.5.yml
```

Expected: `schema_version: 5` u obou.

- [ ] **Step 4: Refresh the junos-evo RPC fixtures (post-migration)**

**Až po migraci** — fixtures mají zachytit stav `.5` **se službami**, ne
prázdnou krabici. Na to je subpříkaz `record` (`migration_validator/cli.py:258`),
který ukládá syrové RPC XML přesně pro tenhle účel:

Na rozdíl od parserů `record` přepínač `--password` **má**, takže ho lze
spustit neinteraktivně:

```bash
.venv/bin/python -m migration_validator.cli record \
    --device 172.20.20.5 --output-dir tests/fixtures/rpc/junos-evo \
    --auth password --password "$MIG_LAB_PASSWORD"
```

(Autentizace klíčem je výchozí; přepínače `--auth`/`--password` přidej jen
tehdy, když klíč selže.)

Ověř, že vznikly všechny soubory, které tam byly předtím — `arp.xml`,
`bfd.xml`, `bgp.xml`, `evpn_esi.xml`, `evpn_mac.xml`, `evpn_vpws.xml`,
`interfaces.xml`, `nd.xml`, `routes.xml`:

```bash
git status --porcelain tests/fixtures/rpc/junos-evo/
ls tests/fixtures/rpc/junos-evo/
```

Chybějící soubor znamená, že collector na `.5` selhal — **to je nález**,
zastav a nahlas, nedoplňuj ho ze starého capture.

`tests/fixtures/172.20.20.{4,5}.yml` **nech beze změny** — testy nad nimi
stojí a jejich přegenerování není součástí bodu 2. Adresář
`tests/fixtures/rpc/junos/` (MX) taky ne — bod 2 mluví jen o `junos-evo`.

- [ ] **Step 5: Measure the ripple**

Run: `.venv/bin/python -m pytest -o addopts="" -q`

Expected: **neznámý počet** — tohle je jediné místo v plánu, kde se počet
neodhaduje. Zapiš, co spadlo, a u každého testu rozhodni: **shodila ho změna
tvaru dat z laborky** (pak se upraví očekávání), nebo **odkryl skutečnou
vadu** (pak je to nález — zastav a nahlas). Nikdy neupravuj aserci tak, aby
přestala něco tvrdit.

- [ ] **Step 6: Re-run every mutant of the wave**

Fixtures se změnily, takže **všechny dosavadní důkazy jsou neplatné**. Pusť
znovu, každý zvlášť, každý s `grep -n MUTANT` a každý vrácený
`git checkout -- <soubor>` před dalším:

```bash
# Uloha 1
sed -i 's|                    else f"{peer}: v baseline patril k teto sluzbe, v subjektu uz ne",|                    else f"{peer}: v baseline byl, v subjektu neni",  # MUTANT|' migration_validator/checks/bgp.py
grep -n MUTANT migration_validator/checks/bgp.py && .venv/bin/python -m pytest -o addopts="" -q
git checkout -- migration_validator/checks/bgp.py

# Uloha 2, vrstevni mutant podle znacky
sed -i 's|        if check.details.get(SKIPPED_BECAUSE) == SKIP_DEACTIVATED|        if check.status is Status.SKIP  # MUTANT|' migration_validator/reporting/view.py
grep -n MUTANT migration_validator/reporting/view.py && .venv/bin/python -m pytest -o addopts="" -q
git checkout -- migration_validator/reporting/view.py

# Uloha 2, mutant priznaku detail
sed -i 's|    views = \[(scope, build_view(scope, detail=detail)) for scope in result.scopes\]|    views = [(scope, build_view(scope)) for scope in result.scopes]  # MUTANT|' migration_validator/reporting/text_report.py
grep -n MUTANT migration_validator/reporting/text_report.py && .venv/bin/python -m pytest -o addopts="" -q
git checkout -- migration_validator/reporting/text_report.py

# Uloha 4
sed -i 's|    ribs = {primary: _counters_for(peer)}|    ribs = {primary: {"received": 14, "accepted": 14, "advertised": 3, "active": 14, "suppressed": 0}}  # MUTANT|' tests/conftest.py
grep -n MUTANT tests/conftest.py && .venv/bin/python -m pytest -o addopts="" -q
git checkout -- tests/conftest.py
```

Expected: každý shodí tytéž testy jako ve své úloze. Kdyby některý po výměně
fixtures přestal cokoli shazovat, je to nález — zastav a nahlas.

- [ ] **Step 7: Verify the locks and commit**

```bash
diff mx_parser.py evo_parser.py | wc -l
head -1 tests/fixtures/172.20.20.4.yml tests/fixtures/172.20.20.5.yml
.venv/bin/python -m pytest -o addopts="" -q
```

Expected: **146**, `schema_version: 5` u obou fixtures, sada zelená.

```bash
git add 172.20.20.4.yml 172.20.20.5.yml tests/fixtures/rpc/junos-evo/
git commit -m "chore: resync korenovych inventory a junos-evo RPC fixtures

Korenove 172.20.20.{4,5}.yml byly na schematu 4 a nastroj je odmital
nacist. RPC fixtures junos-evo jsou z capture po migraci sluzeb na .5."
```

Pokud krok 5 vyžádal úpravy testů, přidej je do `git add` a zmiň je ve
zprávě.

---

## Task 6: Roadmapa vlny 10

**Files:**
- Create: `docs/superpowers/roadmap-2026-08-04-vlna10-hotovo.md`

**Interfaces:**
- Consumes: výsledky všech pěti předchozích úloh.
- Produces: vstup pro vlnu 11.

**Kontext, který implementer nemá:**

Roadmapa je **doklad**, ne shrnutí. Vzorem je
[`roadmap-2026-08-04-vlna9-hotovo.md`](../roadmap-2026-08-04-vlna9-hotovo.md):
sekce „Co vlna přinesla", „Jak si vyrobit důkazy", „Co vyšlo jinak, než plán
čekal", „Co zbývá" a „Pravidla do plánu další vlny".

- [ ] **Step 1: Write the roadmap**

Napiš `docs/superpowers/roadmap-2026-08-04-vlna10-hotovo.md`. Musí obsahovat:

**Co vlna přinesla** — bod 18 (sloučení podle značky, ne podle stavu), bod 19
(hláška o členství), uzavřené body 16, 17, 9, rozrůzněné countery (12) a
resync fixtures (2). U každého uzavřeného bodu **napiš, čím byl uzavřen** —
u 17 měřením z laborky, u 9 zapsaným odůvodněním.

**Jak si vyrobit důkazy** — příkazy se skutečnými očekávanými výstupy:

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""      # pocet doplnit podle skutecnosti
diff mx_parser.py evo_parser.py | wc -l       # 146
head -1 172.20.20.4.yml                       # schema_version: 5
```

**Co vyšlo jinak, než plán čekal** — povinně aspoň tyhle tři, plus cokoli, co
implementeři našli:

1. **Nulový ripple bodu 12 byl nález, ne potvrzení.** Countery ze sdílených
   fixtures nehlídal žádný z 676 testů, takže rozrůznění samo by bylo
   dekorativní — proto k němu patří zamykající test.
2. **Bod 18 nebyl důsledek vlny 9.** Bloky FAIL a nespárované se rozbalovaly
   už předtím; vlna 9 přidala jediný nový případ. Roadmapa vlny 9 to zapsala
   jako nový bod a měření to vyvrátilo.
3. **Bod 19 byl v prvním popisu přehnaný.** Autor specu tvrdil, že si blok
   odporuje sám v sobě, a doložil to řádky ARP a Ping — ty ale jdou
   z `local_ipv4`, ne z `bgp_neighbors`, a se sporem nemají nic společného.
   Měření tvrzení oslabilo a spec to zapsal, místo aby to zamlčel.

**Co zbývá** — po vlně 10 zůstává **jediný** bod:

> ### 20. Parsery zahazují qualified-next-hop
>
> Nový bod, změřený uživatelem na živé laborce 2026‑08‑04. Konfigurace
> `route 198.62.254.0/29` s holým next-hopem a dvěma qualified-next-hopy
> (jeden `inactive`) dala v inventory jediný záznam s `next_hop:
> [152.11.14.4]`. Routa směrovaná **výhradně** přes qualified-next-hop tedy
> dostane prázdný `next_hop`, nenamapuje se na žádnou službu, a pokud není
> nainstalovaná, zmizí beze stopy.
>
> Odloženo vědomě (rozhodnutí uživatele: speciální případ, který teď nestojí
> za investovaný čas). Vlna 10 opravila jen komentář v obou parserech —
> qualified-next-hop nese buď adresu, nebo `interface-name`, takže do výčtu
> tvarů bez adresy nepatří. Až se to bude dělat, obnoví se tím i bod 17:
> deaktivovat qualified-next-hop individuálně **jde**, takže jedna routa může
> nést zároveň aktivní a deaktivovaný next-hop, a klíčování `(rib, prefix)`
> se pak musí navrhnout znovu.

**Vědomě uzavřeno, znovu neotvírat** — vlastní podsekce, povinná. Bez ní
vlna 11 tytéž věci objeví znovu a bude o nich rozhodovat podruhé:

> **Statické routy se chovají stejně jako BGP a sjednocovat se nebudou.**
> `checks/routes.py` bere `ctx.baseline["routes"]` do svého sjednocení
> a `_unassigned_static_routes` počítá `assigned` jen ze subjektových scopů —
> tedy týž tvar, kvůli kterému vznikl bod 19 u peerů. Varianta „sjednotit
> i statické routy" byla uživateli 2026‑08‑04 nabídnuta a **odmítnuta**:
> širší rozsah bez podpírajícího nálezu. Bod 19 se u BGP vyřešil
> přeformulováním hlášky, ne změnou vlastnictví, takže u rout není co
> dorovnávat.
>
> **Peer zůstává v NEZAŘAZENO i v bloku služby.** Varianty „blok mlčí"
> a „NEZAŘAZENO mlčí" byly nabídnuty a odmítnuty. Obě sekce mluví dál,
> každá pravdivě o něčem jiném: blok o členství ve službě, NEZAŘAZENO
> o session, kterou si žádná služba nenárokuje.
>
> **Popisek `BGP status (adresa)` zůstává bezpodmínečný** (bod 9) a
> **klíčování `(rib, prefix)` se nemění** (bod 17) — odůvodnění je zapsané
> přímo v `checks/bgp.py` a `checks/routes.py`.

**Pravidla do plánu vlny 11** — přenes ta z vlny 9, která se uplatnila, a
přidej nová zaplacená touhle vlnou. Povinně mezi nimi:

> **Nulový ripple se v této vlně potvrdil dvakrát a pokaždé jako nález.**
> Bod 12 (hodnoty counterů) i bod 18 (vykreslený obsah bloku deaktivované
> služby) prošly beze změny počtu testů, přestože obojí měnilo pozorovatelné
> chování. Obojí znamenalo, že tu vlastnost nehlídal nikdo — a v obou
> případech musela vlna pokrytí doplnit, ne se o ně opřít.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/roadmap-2026-08-04-vlna10-hotovo.md
git commit -m "docs: roadmapa vlny 10 - tichy blok a pravdiva hlaska o peeru"
```

---

## Závěrečné whole-branch review

Po úloze 6 **před mergem**: pusť review celé větve, ne per-úlohovou. Vlna 9
tím našla rozhodnutí implementované na dvou místech a zamčené na jednom —
tvar, který per-úlohová review z principu chytit nemůže.

Review musí zvlášť prověřit:

1. **Každý docstring, který jmenuje konkrétní `Outcome`, `Status` nebo
   zabíjející test**, se ověří **spuštěním toho mutanta**. Vlna 9 jich takhle
   našla devět zastaralých, dva až v závěrečné review.
2. **Značka `SKIP_DEACTIVATED` se vyplňuje na jediném místě** — `grep -rn
   "skipped_because\|SKIP_DEACTIVATED" migration_validator/` nesmí ukázat
   druhé volání `_skip()` se značkou.
3. **Žádná aserce se nezahodila bez mutanta.** Kde se v téhle vlně aserce
   ubrala, review pustí mutant, který ten test jmenuje, a doloží, že pod ním
   pořád padá.
4. **Zámek parserů 146** a **schema 5** u inventory i snapshotu.
