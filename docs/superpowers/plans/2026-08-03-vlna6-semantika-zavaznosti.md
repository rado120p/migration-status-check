# Vlna 6 — sémantika závažnosti u chybějícího `active` (implementační plán)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Baseline, který o aktivitě statické routy mlčí, přestane eskalovat na FAIL a začne dávat WARN podle R‑2; scénář, kvůli kterému roadmapa vedla bod 4, dostane test, který ho tvrdí.

**Architecture:** Jedna větev v `checks/routes.py` se rozpadne ze dvou stavů na tři (baseline chybí / baseline mlčí / baseline mluví), engine se nemění vůbec a `_STATUS_RANK` se nepřerovnává. Zbytek vlny jsou testy a oprava dvou roadmap.

**Tech Stack:** Python 3, pytest, `.venv` v kořeni repa.

**Spec:** [`../specs/2026-08-03-vlna6-semantika-zavaznosti-design.md`](../specs/2026-08-03-vlna6-semantika-zavaznosti-design.md)

**Výchozí stav:** `main` na `576b0eb`, **633 testů zelených, 0 přeskočených.**

## Global Constraints

- **Testy se pouští takhle:** `.venv/bin/python -m pytest -o addopts=""` z kořene `/home/rado/Desktop/scripts/migration-status-check`. Bez `-o addopts=""` se přidává coverage a výstup se hůř čte.
- **Mutant se pouští nad tím stavem repa, ve kterém poběží doopravdy** — tedy až **po** commitu úlohy, ne nad rozpracovaným stromem. Po změření se strom vrátí (`git checkout -- <soubor>`) a čistota se ověří `git status --short`.
- **Měření má přednost před zadáním.** Když měření nesedí s tímhle plánem nebo se specem, platí měření a zapíše se do reportu úlohy — neupravuje se test, aby vyšel podle plánu.
- **Nulový ripple po změně chování není potvrzení, je nález.** Když změna sémantiky neshodí ani jeden test, znamená to, že měněné chování nikdo nedržel.
- **Test na text zprávy porovnává CELOU zprávu, ne podřetězec.** Obě nové DEGRADED zprávy sdílejí prefix `{rib} {prefix}: je v tabulce, ale neni aktivni`, takže `in` by je nerozlišil.
- **Diakritika:** do souboru, který ji nenese, se nezanáší. Změřeno 2026‑08‑03: nese ji 7 z 94 `.py` souborů. `tests/checks/test_routes.py` mezi ně **patří** (řádek 353, „Zabíjí"), `migration_validator/checks/routes.py` a `tests/test_engine.py` **ne**. Nové zprávy v produkčním kódu i nové docstringy v `test_engine.py` proto bez diakritiky. Dokumentace v `docs/` diakritiku má a nechává si ji.
- **Každý test jmenuje v docstringu mutanta, kterého zabíjí**, a to tvrzení musí být ověřené spuštěním, ne přečtením.
- **Rozvržení `.superpowers/sdd/`:** briefy a `progress.md` téhle vlny patří do **datovaného podadresáře** `.superpowers/sdd/2026-08-03-vlna6-semantika-zavaznosti/`. V kořeni `.superpowers/sdd/` leží naplocho `task-1-brief.md` … `task-18-brief.md` a `progress.md` ještě z první vlny (červenec) — zakládat briefy tam by cizí soubory přepsalo.

---

## File Structure

| soubor | co se s ním děje |
|---|---|
| `migration_validator/checks/routes.py` | Modify: `:176` a větev zpráv na `:193-198` — jediná změna chování v celé vlně |
| `tests/checks/test_routes.py` | Modify: dva nové testy za `test_route_without_active_key_is_skipped` |
| `tests/test_engine.py` | Modify: jeden nový test + import `Path` a konstanta `DEVICE_4` |
| `docs/superpowers/roadmap-2026-08-03-vlna5-hotovo.md` | Modify: bod 4 přepsán na změřenou pravdu |
| `docs/superpowers/roadmap-2026-07-31-vlna4-hotovo.md` | Modify: k bodu 5 přibývá odkaz na opravu |
| `docs/superpowers/roadmap-2026-08-03-vlna6-hotovo.md` | Create: uzavírací roadmapa vlny |

`engine.py` a `models/result.py` se **nemění**. Kdyby se do diffu dostaly, je to chyba.

---

### Task 1: AR-43 — mlčící baseline dává WARN místo FAIL

**Files:**
- Modify: `migration_validator/checks/routes.py:176` a `:191-198`
- Test: `tests/checks/test_routes.py` (nové testy za `test_route_without_active_key_is_skipped`, dnes končí kolem řádku 360)

**Interfaces:**
- Consumes: nic z dřívějších úloh (první úloha vlny).
- Produces: nic, co by pozdější úlohy volaly. Mění se jen `Finding.outcome` a `Finding.message` uvnitř `StaticRouteStatusCheck._finding` — signatura metody zůstává `_finding(self, identity, configured, subject, baseline, is_device) -> Finding`.

**Kontext, který implementer nemá odkud vědět:** `baseline` v `_finding` je slovník **jedné routy** z baseline snapshotu (nebo `None`, když baseline neexistuje), ne celý snapshot. Existující pomocník `_installed_without_active()` v testech (dnes kolem řádku 343) vyrábí přesně tvar „routa bez klíče `active`" a **použije se znovu**, teď na straně baseline — nezakládej druhý.

- [ ] **Step 1: Napiš první padající test**

Vlož do `tests/checks/test_routes.py` hned za `test_route_without_active_key_is_skipped`:

```python
def test_inactive_route_with_silent_baseline_is_degraded():
    """Baseline, ktery o aktivite mlci, je nejednoznacnost - podle R-2 se
    nejednoznacnost na FAIL neeskaluje.

    Zabiji mutanta: navrat defaultu `baseline.get("active", True)`. S nim se
    mlceni precte jako "forwardovala" a check vyda BROKEN, tedy tvrdy FAIL
    za neco, co neni dolozene.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _installed_without_active())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "neni aktivni"
```

- [ ] **Step 2: Napiš druhý padající test**

Vlož hned za test z kroku 1:

```python
def test_silent_baseline_has_its_own_message():
    """Tri stavy baseline musi dat tri zpravy, ne dve.

    Zabiji mutanta: slouceni obou DEGRADED vetvi do jedne zpravy. Pak by
    mlcici baseline dostal formulaci urcenou pro "baseline vubec neni" a
    operator by z radku nepoznal, ktery z tech dvou duvodu nastal.

    Porovnava se CELA zprava: obe vetve sdileji prefix "je v tabulce, ale
    neni aktivni", takze assert na podretezec by je nerozlisil.
    """
    silent = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _installed_without_active())
    )
    missing = StaticRouteStatusCheck().run(_ctx(_installed_inactive()))

    assert silent[0].message == (
        "inet.0 198.62.1.0/29: je v tabulce, ale neni aktivni; "
        "baseline aktivitu neuvadi"
    )
    assert missing[0].message == "inet.0 198.62.1.0/29: je v tabulce, ale neni aktivni"
```

- [ ] **Step 3: Spusť oba a ověř, že padají**

```bash
.venv/bin/python -m pytest -o addopts="" \
  tests/checks/test_routes.py::test_inactive_route_with_silent_baseline_is_degraded \
  tests/checks/test_routes.py::test_silent_baseline_has_its_own_message -v
```

Očekávané: **2 failed.** První na `assert Outcome.BROKEN is Outcome.DEGRADED`, druhý na neshodě zpráv (dostane `"inet.0 198.62.1.0/29: v baseline forwardovala, ted neni aktivni"`).

Kdyby některý prošel už teď, **zastav se a zapiš to** — znamenalo by to, že chování je jiné, než tenhle plán i spec tvrdí, a platí měření.

- [ ] **Step 4: Proveď změnu v `checks/routes.py`**

Nahraď řádek `:176`:

```python
            was_active = baseline.get("active", True) if baseline else None
```

za:

```python
            was_active = baseline.get("active") if baseline else None
```

A nahraď blok `:191-198` (komentář `# Bez baseline neni z ceho poznat...` plus přiřazení `outcome` a `message`):

```python
            # Bez baseline neni z ceho poznat, ze neaktivni byla i predtim -
            # podle R-2 se nejednoznacnost na FAIL neeskaluje.
            outcome = Outcome.BROKEN if was_active else Outcome.DEGRADED
            message = (
                f"{rib} {prefix}: v baseline forwardovala, ted neni aktivni"
                if was_active
                else f"{rib} {prefix}: je v tabulce, ale neni aktivni"
            )
```

za:

```python
            # Bez baseline neni z ceho poznat, ze neaktivni byla i predtim -
            # podle R-2 se nejednoznacnost na FAIL neeskaluje. Baseline, ktery
            # klic "active" nema, je tataz nejednoznacnost, jen z jineho
            # duvodu: neni to porucha mereni jako u `subject` vyse (ten vzdy
            # vyrabi aktualni collector), ale starsi artefakt, ktery o stavu
            # sveta mlci. Proto DEGRADED, ale s vlastni zpravou - jinak by
            # operator nepoznal, ktery z tech dvou duvodu nastal.
            if was_active:
                outcome = Outcome.BROKEN
                message = f"{rib} {prefix}: v baseline forwardovala, ted neni aktivni"
            elif baseline is not None:
                outcome = Outcome.DEGRADED
                message = (
                    f"{rib} {prefix}: je v tabulce, ale neni aktivni; "
                    "baseline aktivitu neuvadi"
                )
            else:
                outcome = Outcome.DEGRADED
                message = f"{rib} {prefix}: je v tabulce, ale neni aktivni"
```

Větev `if was_active is False:` o pár řádků výš (AR‑25, shoda na neaktivitě = OK) se **nemění** — `None` do ní nespadne, protože porovnává identitou.

Zároveň smaž z komentáře nad `:163` poslední dvě věty, které tuhle otázku vedly jako otevřenou:

```python
        # Na `baseline` niz se default nemeni - je to mimo rozsah teto vlny,
        # ne proto, ze by byl spravny. Zustava otevrena otazka pro dalsi vlnu:
        # ma chybejici "active" v baseline davat DEGRADED misto BROKEN, podle
        # R-2?
```

Otázka je zodpovězená; ponechaný text by byl doc drift.

- [ ] **Step 5: Spusť oba testy a ověř, že prochází**

```bash
.venv/bin/python -m pytest -o addopts="" \
  tests/checks/test_routes.py::test_inactive_route_with_silent_baseline_is_degraded \
  tests/checks/test_routes.py::test_silent_baseline_has_its_own_message -v
```

Očekávané: **2 passed.**

- [ ] **Step 6: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávané: **635 passed, 0 skipped** (633 + 2 nové).

Plán **nepředpovídá**, že se něco jiného rozbije — měření před psaním specu ukázalo nulový ripple. Kdyby přesto něco padlo, je to nález: zapiš který test a proč, a **neupravuj ho, aby vyšel**, dokud není jasné, jestli je vada v něm, nebo ve změně.

Zvlášť pozor na `test_inactive_route_that_was_inactive_before_passes` (AR‑25) — ten se shodit **nesmí**. Kdyby padl, rozbila se větev, která se měnit neměla.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/checks/routes.py tests/checks/test_routes.py
git commit -m "fix: mlcici baseline u statickych rout dava WARN misto FAIL (AR-43)"
```

- [ ] **Step 8: Ověř oba mutanty nad zacommitovaným stavem**

Mutant A — návrat defaultu:

```bash
sed -i 's|baseline.get("active") if baseline else None|baseline.get("active", True) if baseline else None|' \
  migration_validator/checks/routes.py
.venv/bin/python -m pytest -o addopts="" -q
git checkout -- migration_validator/checks/routes.py
```

Očekávané: padne **`test_inactive_route_with_silent_baseline_is_degraded`** (a nejspíš i `test_silent_baseline_has_its_own_message`, protože zpráva se změní taky).

Mutant B — sloučení obou DEGRADED větví do jedné zprávy: ručně nahraď v `elif baseline is not None:` větvi zprávu za `f"{rib} {prefix}: je v tabulce, ale neni aktivni"` (tedy tutéž jako v `else`), pusť sadu a vrať strom.

Očekávané: padne **`test_silent_baseline_has_its_own_message`** a **`test_inactive_route_with_silent_baseline_is_degraded` projde** — to je smysl toho, že testy stojí každý na jiné straně.

Do reportu úlohy zapiš, které testy který mutant shodil, **změřeno, ne odhadnuto**. Nakonec ověř `git status --short`, že je strom čistý.

---

### Task 2: AR-44 — test, že SKIP routa nezakryje PASSy sourozenců

**Files:**
- Modify: `tests/test_engine.py` (import `Path`, konstanta `DEVICE_4`, nový test na konci souboru)

**Interfaces:**
- Consumes: nic z úlohy 1. Tenhle test je na chování, které úloha 1 nemění — dá se psát nezávisle a musí projít i bez ní.
- Produces: nic.

**Kontext, který implementer nemá odkud vědět:** `tests/conftest.py` poskytuje fixture `synthetic_snapshot(inventory_path, address, phase)`, která ze skutečné inventory postaví snapshot se zdravými fakty — včetně statických rout se `"active": True`. `tests/test_engine.py` ji dnes nepoužívá (má vlastní ruční `_snapshot`), ale je dostupná, protože `conftest.py` leží v `tests/`. Vzor použití je v `tests/test_end_to_end.py:28`.

Scope id a prefixy níž **nejsou vymyšlené** — jsou změřené sondou nad `tests/fixtures/172.20.20.4.yml` z 2026‑08‑03.

- [ ] **Step 1: Přidej import a konstantu**

Do hlavičky `tests/test_engine.py` (dnes začíná `import pytest`) přidej:

```python
from pathlib import Path
```

a pod konstantu `NOW`:

```python
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEVICE_4 = str(FIXTURES / "172.20.20.4.yml")
```

- [ ] **Step 2: Napiš test na konec souboru**

```python
def test_route_without_active_key_does_not_mask_healthy_siblings(synthetic_snapshot):
    """SKIP na jedne route nesmi stahnout cely scope a zakryt PASSy sourozencu.

    Roadmapa vlny 5 vedla tenhle stav jako vadu (bod 4), protoze
    _STATUS_RANK ma SKIP nad PASS. Merenim se ukazalo, ze vada neexistuje:
    engine.py:145 SKIPy z hlasovani Status.worst() vyfiltruje driv, nez se
    hlasuje. Chybel jen test, ktery to tvrdi.

    Zabiji mutanta: vypusteni `if result.status is not Status.SKIP` z
    engine.py:145. Sourozenci ho zabijeji taky, ale oba pres jiny scenar -
    compare-only check bez baseline a sluzba deaktivovana na obou stranach.
    Pres static_route_status nechodi ani jeden.
    """
    subject = synthetic_snapshot(DEVICE_4, "172.20.20.4", "post-migration")
    del subject.facts["routes"]["inet.0"]["198.62.1.0/29"]["active"]

    result = api.evaluate(subject, now=NOW)

    scope = next(
        s for s in result.scopes if s.scope_id == "svc:INTERNET-CPE13-NNI:Internet"
    )
    statuses = [c.status for c in scope.checks if c.id == "static_route_status"]

    # Bez tehle dvojice by test prosel i tehdy, kdyby se SKIP vubec nevyrobil
    # nebo kdyby scope nemel zadneho zdraveho sourozence - tedy kdyby merit
    # nebylo co.
    assert statuses.count(Status.SKIP) == 1
    assert Status.PASS in statuses

    assert scope.status is Status.PASS
```

- [ ] **Step 3: Spusť test a ověř, že prochází**

```bash
.venv/bin/python -m pytest -o addopts="" \
  tests/test_engine.py::test_route_without_active_key_does_not_mask_healthy_siblings -v
```

Očekávané: **PASS.** Tenhle test **záměrně neprochází cyklem červená→zelená** — netvrdí nové chování, připíná existující. Kdyby padl, je to skutečný nález a znamená, že měření před specem bylo špatně.

- [ ] **Step 4: Spusť celou sadu**

```bash
.venv/bin/python -m pytest -o addopts="" -q
```

Očekávané: **636 passed, 0 skipped.**

- [ ] **Step 5: Commit**

```bash
git add tests/test_engine.py
git commit -m "test: SKIP routa nezakryva PASSy sourozencu ve scope statusu (AR-44)"
```

- [ ] **Step 6: Ověř mutanta nad zacommitovaným stavem**

```bash
sed -i 's|reported = \[result.status for result in results if result.status is not Status.SKIP\]|reported = [result.status for result in results]|' \
  migration_validator/engine.py
.venv/bin/python -m pytest -o addopts="" -q
git checkout -- migration_validator/engine.py
git status --short
```

Očekávané: padne **`test_route_without_active_key_does_not_mask_healthy_siblings`** spolu s `test_healthy_scope_without_baseline_is_pass_not_skip` a `test_service_deactivated_on_both_sides_is_pass`.

Kdyby nový test **nepadl**, měří něco jiného než šev, kvůli kterému vzniká — v tom případě ho oprav a mutanta pusť znovu. Do reportu zapiš změřený seznam padlých testů.

---

### Task 3: AR-45 — oprava obou roadmap

**Files:**
- Modify: `docs/superpowers/roadmap-2026-08-03-vlna5-hotovo.md:201-205`
- Modify: `docs/superpowers/roadmap-2026-07-31-vlna4-hotovo.md:197-201`

**Interfaces:**
- Consumes: čísla a jména testů z reportů úloh 1 a 2. Jestli se od tohohle plánu liší, platí report.
- Produces: nic.

Tahle úloha nemá testy — mění jen dokumentaci. Ověření je čtením, ne během.

- [ ] **Step 1: Přepiš bod 4 v roadmapě vlny 5**

Nahraď v `docs/superpowers/roadmap-2026-08-03-vlna5-hotovo.md` celý blok pod nadpisem `### 4. Priorita SKIP nad PASS` (dnes řádky 201‑205) tímhle:

```markdown
### 4. Priorita SKIP nad PASS — změřeno, vada neexistuje

`Status.SKIP` má v `_STATUS_RANK` (`models/result.py:44-48`) vyšší prioritu
než `Status.PASS`, ale na status scopu se to nedostane: `engine.py:145`
SKIPy z hlasování `Status.worst()` vyfiltruje ještě předtím, než se hlasuje.

Změřeno 2026‑08‑03 sondou nad `tests/fixtures/172.20.20.4.yml`, ze které se
routě `inet.0 198.62.1.0/29` smazal klíč `active`:

    svc:INTERNET-CPE13-NNI:Internet  scope.status = PASS
    routy = [('inet.0 198.62.1.0/29', SKIP),
             ('inet.0 198.62.2.0/24', PASS),
             ('inet6.0 2001:aaaa::/64', PASS)]

Sourozenci zakrytí nejsou. Filtr je navíc chráněný — mutant „filtr pryč"
shodí `test_healthy_scope_without_baseline_is_pass_not_skip` i
`test_service_deactivated_on_both_sides_is_pass`.

Zbyl z toho jeden chybějící test: ani jeden z těch dvou nechodí přes
`static_route_status`, takže scénář, který tenhle bod popisoval, netvrdil
nikdo. Doplnila ho vlna 6 jako AR‑44. **Pořadí v `_STATUS_RANK` se nemění** —
přerovnat ho a zahodit filtr by byl refaktor beze změny chování, který
přepisuje kód připnutý dvěma testy a záměrným komentářem.
```

- [ ] **Step 2: Doplň opravu k bodu 5 v roadmapě vlny 4**

V `docs/superpowers/roadmap-2026-07-31-vlna4-hotovo.md` přidej na konec bloku pod nadpisem `### 5. Vedlejší důsledek AR-34c, který spec neprobral` (dnes končí kolem řádku 201 větou „Obhajitelné („nešlo doměřit"), ale je to nové chování, které spec nezvažoval.") nový odstavec:

```markdown
**Opraveno 2026‑08‑03:** tvrzení neplatí. `engine.py:145` SKIPy z hlasování
odfiltruje dřív, než se hlasuje, takže scope zůstane PASS a sourozenci zakrytí
nejsou. Změřeno ve vlně 6 — viz bod 4 v
[`roadmap-2026-08-03-vlna5-hotovo.md`](roadmap-2026-08-03-vlna5-hotovo.md).
Text výše se nemaže: je to zápis toho, co si vlna 4 tehdy myslela.
```

- [ ] **Step 3: Zkontroluj, že bod 3 v roadmapě vlny 5 zůstal beze změny**

Bod 3 (`### 3. Baseline bez `active` dnes eskaluje na FAIL`) se v téhle úloze **nesahá** — vyřídí ho uzavírací roadmapa v úloze 4. Kdyby ho tahle úloha odškrtla, tvrdila by hotovo dřív, než proběhlo ostré ověření.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/roadmap-2026-08-03-vlna5-hotovo.md \
        docs/superpowers/roadmap-2026-07-31-vlna4-hotovo.md
git commit -m "docs: opravit bod 4 v roadmapach vln 4 a 5 na zmerenou pravdu (AR-45)"
```

---

### Task 4: Ostré ověření a uzavírací roadmapa vlny 6

**Files:**
- Create: `docs/superpowers/roadmap-2026-08-03-vlna6-hotovo.md`

**Interfaces:**
- Consumes: reporty úloh 1‑3, zejména změřené seznamy testů shozených mutanty.
- Produces: uzavírací dokument vlny, ze kterého vychází zadání vlny 7.

- [ ] **Step 1: Přeměř celou sadu a zámek parserů**

```bash
.venv/bin/python -m pytest -o addopts="" -q
diff mx_parser.py evo_parser.py | wc -l
```

Očekávané: **636 passed, 0 skipped** a **146**. Čísla, která vyjdou, se zapisují **změřená** — když se liší, platí měření a rozdíl se vysvětlí.

- [ ] **Step 2: Podívej se na změněné chování očima, ne jen testy**

Vyrob si dočasně report ze společných fixtures postupem z
`.superpowers/sdd/2026-08-03-vlna5-report-skupiny-a-nezarazeno/task-8-brief.md`,
krok 1. Pokud ten soubor neexistuje (viz poznámka o rozvržení `.superpowers/sdd/`
v Global Constraints), postup je: dočasný test, který zavolá
`migration_validator.reporting.text_report.render` nad dvojicí snapshotů z
`synthetic_snapshot(DEVICE_4, ...)` a `synthetic_snapshot(DEVICE_5, ...)`,
vytiskne výstup a po přečtení se **smaže**.

Co hledat: **nic** — sdílené fixtures mají všechny routy `"active": True`, takže
větev, kterou vlna změnila, se na nich nevyskytne. Zapiš to jako změřený fakt,
ne jako mezeru: znamená to, že AR‑43 je ověřené výhradně jednotkovými testy a
ostrý report ho potvrdit nemůže. To je stejný tvar jako bod 8 roadmapy vlny 5.

- [ ] **Step 3: Napiš uzavírací roadmapu**

Založ `docs/superpowers/roadmap-2026-08-03-vlna6-hotovo.md` podle vzoru
`roadmap-2026-08-03-vlna5-hotovo.md`. Musí obsahovat:

- **Co vlna přinesla** — AR‑43, AR‑44, AR‑45, každé jednou větou.
- **Jak si vyrobit důkazy** — příkazy z kroku 1 se změřenými čísly.
- **Co vyšlo jinak, než plán čekal** — všechny odchylky z reportů úloh 1‑3. Když žádná nebyla, napiš to výslovně; „žádná odchylka" je taky měření.
- **Co zbývá** — body 1, 2, 5, 7, 8 a 9 z roadmapy vlny 5, přenesené beze změny. Body 3 a 4 se **odškrtnou** s odkazem na AR‑43 a AR‑44. Bod 6 je už uzavřený a nepřenáší se.
- **Pravidla do plánu vlny 7** — tři přenesená z vlny 5 plus čtvrté z tohohle plánu („nulový ripple po změně chování není potvrzení, je nález"), pokud se během vlny osvědčilo. Jestli se neuplatnilo, napiš to.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/roadmap-2026-08-03-vlna6-hotovo.md
git commit -m "docs: roadmapa vlny 6 - semantika zavaznosti hotova"
```

---

## Poznámka k dokončení větve

Vlna běží na větvi `vlna6-semantika-zavaznosti` (založ ji z `main` na `576b0eb`
před úlohou 1). Po úloze 4 se merge řeší přes skill
`superpowers:finishing-a-development-branch`, ne ručně.
