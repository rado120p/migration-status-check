# Vlna 6 — sémantika závažnosti u chybějícího `active`

**Datum:** 2026‑08‑03
**Zadání:** body 3 a 4 v „Co zbývá" roadmapy
[`roadmap-2026-08-03-vlna5-hotovo.md`](../roadmap-2026-08-03-vlna5-hotovo.md)
**Výchozí stav:** `main` na `6da9d3d`, **633 testů zelených, 0 přeskočených**.

---

## Proč vlna vypadá jinak, než zadání čekalo

Zadání vedlo dvě položky. Měření před psaním tohoto specu ukázalo, že
**jedna z nich vada není**.

Roadmapa u bodu 4 tvrdí: „`Status.SKIP` má vyšší prioritu než `Status.PASS`
(`models/result.py:44-48`). Jedna routa bez klíče `active` proto stáhne celý
`static_route_status` daného scopu na SKIP a zakryje PASSy sourozenců."

Změřeno sondou nad sdílenou fixturou `tests/fixtures/172.20.20.4.yml`, ze
které se routě `inet.0 198.62.1.0/29` smazal klíč `active`:

```
svc:INTERNET-CPE13-NNI:Internet  scope.status = PASS
routy = [('inet.0 198.62.1.0/29', SKIP),
         ('inet.0 198.62.2.0/24', PASS),
         ('inet6.0 2001:aaaa::/64', PASS)]
```

Scope zůstal **PASS**. Sourozenci zakrytí nejsou. Brání tomu `engine.py:145`,
který SKIPy z hlasování `Status.worst()` vyfiltruje ještě předtím, než se
hlasuje — pořadí `_STATUS_RANK[SKIP] > _STATUS_RANK[PASS]` se tedy na scope
status vůbec nedostane. Filtr je navíc chráněný: mutant „filtr pryč" shodí
`test_healthy_scope_without_baseline_is_pass_not_skip` a
`test_service_deactivated_on_both_sides_is_pass`.

Bod 4 proto **nemá v kódu co opravovat**. Zůstává z něj jeden chybějící test
(AR‑44) a oprava textu roadmapy (AR‑45).

Bod 3 naopak vada je a jeho rozbor už existuje — udělal ho spec vlny 4 v
AR‑34c a tahle vlna ho nepřepisuje, jen provádí.

---

## AR-43 — mlčící baseline nesmí eskalovat na FAIL

### Co je špatně

`migration_validator/checks/routes.py:176`:

```python
was_active = baseline.get("active", True) if baseline else None
```

Default `True` znamená, že baseline, který o aktivitě routy **nic neříká**,
se o dvě řádky níž (`:193`) přečte jako „forwardovala" a dá `Outcome.BROKEN`,
tedy tvrdý FAIL. Bez defaultu by vyšlo `None` → `Outcome.DEGRADED` → WARN.
Ponechání defaultu je tedy to, co eskalaci **způsobuje**, ne to, co jí brání.
(Zjištění je doslova AR‑34c; tady se jen provádí.)

R‑2 zakazuje eskalovat nejednoznačnost na FAIL. Mlčící baseline je
nejednoznačnost: nevíme, jestli routa forwardovala, nebo ne.

Ověřeno, že `:176` je **jediné** baseline‑side místo v repu:

```bash
grep -rn 'get("active"' migration_validator/   # 1 zásah, routes.py:176
```

### Proč je asymetrie proti `subject` věcná

AR‑34c dal chybějícímu `active` v `subject` `Outcome.SKIP`, ne DEGRADED. Není
to nedůslednost. `subject` vždy vzniká aktuálním collectorem
(`collectors/routes.py` vždy `active` nastavuje), takže tam chybějící klíč může
znamenat jedině regresi collectoru — a na regresi měření je správná odpověď
„neměřeno", ne „zhoršeno". Baseline je proti tomu cizí, starší artefakt; jeho
mlčení je nejednoznačnost o stavu světa, ne porucha měření.

### Nové chování

Čtou se **tři** stavy baseline místo dvou, protože jinak by mlčící baseline
dostal formulaci určenou pro „baseline vůbec není":

| stav baseline | outcome | status | zpráva |
|---|---|---|---|
| baseline chybí (`None`) | `DEGRADED` | WARN | `{rib} {prefix}: je v tabulce, ale neni aktivni` |
| baseline je, `active` nemá | `DEGRADED` | WARN | `{rib} {prefix}: je v tabulce, ale neni aktivni; baseline aktivitu neuvadi` |
| baseline říká `True` | `BROKEN` | FAIL | `{rib} {prefix}: v baseline forwardovala, ted neni aktivni` |
| baseline říká `False` | `OK` | PASS | `{rib} {prefix}: neni aktivni, stejne jako v baseline` |

Poslední řádek se **nemění** — je to AR‑25 a `None` do něj nespadne, protože
podmínka na `:178` porovnává identitou (`was_active is False`).

`value` zůstává `NOT_ACTIVE` a `baseline_value` zůstává `was`
(`_next_hop_text(baseline)`) ve všech větvích, kde jsou dnes. Sloupec
`baseline_value` už dnes mezi větvemi mění význam (v OK větvi nese
`NOT_ACTIVE`, v ostatních next‑hop) — tahle vlna ten šev **neotvírá**, protože
by to byla jiná změna než ta zadaná.

Zpráva pro mlčící baseline je bez diakritiky, protože `checks/routes.py` ji
dnes nenese a pravidlo zní „do souboru, který diakritiku nemá, ji nezanášej".

### Rozlišení proti alternativám

- **„Nechat default, jen přidat test"** — test by hlídal, že se `True` doplní,
  tedy zabetonoval by chování, které je špatné. (Zapsáno už v AR‑34c.)
- **„Dát mlčícímu baseline SKIP, symetricky k `subject`"** — SKIP znamená
  „neměřeno". Tady ale změřeno je: routa v tabulce **není aktivní**, což je
  fakt o subjektu, ne o baseline. Zahodit ho jako neměřený by ztratil nález.
  Nejednoznačné je jen to, jestli je to změna proti baseline — a přesně na to
  je WARN.
- **„Jedna společná zpráva pro oba nejednoznačné stavy"** — není to lež (věta
  o baseline nic netvrdí), ale operátor z řádku nepozná, který z těch dvou
  důvodů nastal. Rozhodnuto pro tři zprávy.

### Pokrytí, které tahle změna dostane

**Změřeno 2026‑08‑03:** s odstraněným defaultem projde
`.venv/bin/python -m pytest -o addopts=""` **633 passed** — tedy ripple je
nulový. To není dobrá zpráva, je to nález: dnešní chování „mlčící baseline ⇒
BROKEN" **nedrží ani jeden test**. Nové testy proto nejsou doplněk k opravě,
jsou jediná pojistka celé změny a bez nich by byla nekrytá stejně, jako je
nekryté chování dnešní.

Vyžadují se dva nové testy v `tests/checks/test_routes.py`, oba ověřené
mutantem:

1. **mlčící baseline dává WARN** — mutant: vrátit default `True` na `:176`.
   Test musí padnout.
2. **mlčící baseline má vlastní zprávu** — mutant: sloučit obě DEGRADED větve
   do jedné zprávy. Test musí padnout.

Existující test na `was_active is False` (AR‑25) se **nesmí upravovat**; kdyby
ho změna shodila, je to signál, že se rozbila větev, která se měnit neměla.

---

## AR-44 — chybí test na scénář, který roadmapa vedla jako vadu

Mutant `engine.py:145` (filtr SKIPů pryč) je dnes zabitý — ale oběma testy
přes **jiný** scénář: compare‑only check bez baseline a služba deaktivovaná na
obou stranách. Ani jeden netvrdí to, co bod 4 popisoval a co jsem naměřil:
**routa bez `active` nezakryje PASSy sourozenců.**

Přidá se jeden test v `tests/test_engine.py`, který to tvrdí přímo: scope se
statickými routami, z nichž jedné chybí `active`, má status `PASS`, a mezi
jeho checky je zároveň aspoň jeden SKIP (jinak by test mohl projít i tehdy,
kdyby se SKIP vůbec nevyrobil).

Test musí být ověřený mutantem `engine.py:145` a **musí ho zabít**. Když ho
nezabije, znamená to, že měří něco jiného než šev, kvůli kterému vzniká.

Tenhle test je jediná práce, kterou z bodu 4 zbylo. Není to redundance vůči
dvěma existujícím: ty připínají tentýž filtr z jiných stran a žádná z nich
nechodí přes `static_route_status`.

---

## AR-45 — oprava bodu 4 v roadmapě vlny 5

Bod 4 v `docs/superpowers/roadmap-2026-08-03-vlna5-hotovo.md` (řádky 201‑205)
tvrdí chování, které neexistuje. Přepíše se na změřenou pravdu a **přestane
být otevřenou položkou** — včetně příkazu, který měření vyrobil, aby se dalo
zopakovat.

Tvrzení, které tam má zůstat: pořadí `SKIP` nad `PASS` v `_STATUS_RANK`
existuje, ale na scope status se nedostane, protože `engine.py:145` SKIPy
odfiltruje dřív. **Pořadí se nemění.** Přerovnat rank a zahodit filtr by byl
refaktor beze změny chování, který přepisuje kód připnutý dvěma testy a
záměrným komentářem — a zadání o něj nežádá.

Roadmapa vlny 4 (`roadmap-2026-07-31-vlna4-hotovo.md`, bod 5) nese totéž
chybné tvrzení jako první výskyt. Opraví se odkazem na tenhle spec, ne
přepsáním historie — je to zápis toho, co si tehdy vlna 4 myslela.

---

## Co tahle vlna nedělá

- **Nepřerovnává `_STATUS_RANK`** ani neruší filtr v `engine.py:145` (viz
  AR‑45).
- **Nesahá na `baseline_value`** jako sloupec ani na jeho měnící se význam
  mezi větvemi.
- **Nesahá na `tests/conftest.py`** — bod 7 roadmapy (IPv6 peeři dostávají RIB
  `inet.0`) zůstává otevřený a je to samostatná vlna, protože mění vstupy
  napříč celou sadou a vynutí přeměření všech mutantů.
- **Nesahá na body 1, 2, 5, 8 a 9** roadmapy vlny 5.

---

## Pravidla do plánu

Přebírají se všechna tři z vlny 5, beze změny:

1. **Mutant se pouští nad tím stavem repa, ve kterém poběží doopravdy** —
   tedy až po commitu, ne nad rozpracovaným stromem.
2. **Měření má přednost před zadáním** — platí i pro čísla a tvrzení v tomhle
   specu. Tahle vlna sama vznikla tím, že se změřilo zadání a jedna z jeho
   dvou položek neobstála.
3. **Test, který hledá řetězec kdekoliv ve výstupu, neměří sekci — měří
   výstup.** U AR‑43 to platí přímo: obě nové DEGRADED zprávy sdílejí prefix
   `{rib} {prefix}: je v tabulce, ale neni aktivni`, takže assert typu
   `"je v tabulce, ale neni aktivni" in message` projde u obou větví a
   nerozliší je. Testy musí porovnávat **celou** zprávu.

Přidává se čtvrté, zaplacené nulovým ripplem u AR‑43:

4. **Nulový ripple po změně chování není potvrzení, je nález.** Když změna
   sémantiky neshodí ani jeden test, znamená to, že měněné chování nikdo
   nedržel — ne že je změna bezpečná. Zapsat to a doplnit pokrytí, ne to
   přejít jako zelenou sadu.

### Poznámka k rozvržení `.superpowers/sdd/`

`ls -d .superpowers/sdd/*/` nevrací nic — briefy `task-1-brief.md` až
`task-18-brief.md` a `progress.md` leží **naplocho** v kořeni, ještě z první
vlny (červenec). Roadmapa vlny 5 přitom cituje cestu
`.superpowers/sdd/2026-08-03-vlna5-report-skupiny-a-nezarazeno/task-8-brief.md`,
tedy datovaný podadresář, který neexistuje. Plán vlny 6 musí své briefy
zakládat v datovaném podadresáři, jinak `task-1-brief.md` přepíše cizí soubor
a `progress.md` taky. Vyřešit **dřív**, než se založí první brief.
