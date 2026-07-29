# Vlna 1 — hotovo (2026-07-29)

Navazuje na [`roadmap-2026-07-29-dalsi-kroky.md`](roadmap-2026-07-29-dalsi-kroky.md).
Vlna 1 (doladění reportu) je uzavřená na větvi `vlna1-doladeni-reportu`.
**449 testů zelených, 1 přeskočen** (bylo 424).

Historie a důkazy ke starší práci jsou v roadmapách z 2026-07-28 a
2026-07-29; sem se neopisují.

---

## Rozhodnutí, která padla (a nemají se otevírat)

| # | otázka | rozhodnuto |
|---|---|---|
| F-12 | je filtr pohled, nebo nový výpočet? | **nový výpočet** — počty se přepočítají za výběr |
| F-8 | jak odlišit checky od služeb? | **dva pojmenované řádky** `Sluzby:` / `Checky:` |
| F-7 | co do sloupce hodnot, když finding hodnotu nemá? | **krátká hodnota dodaná checkem**, věta zůstává v `message` |

## Co je hotové

### T13a + T13b — testy dokazují, že pojistka platí na každý prvek

`tests/probes/test_ping.py`

Oba strážní testy měly vlastní adresu na indexu 0, takže mutant
`if index > 0 or address != source` prošel — a s ním **celá sada 424 testů**.
Teď vlastní adresa obchází všechny pozice (parametrizace) a proti témuž
mutantu padá na pozicích 1 a 2.

ND test měl navíc adresu scope `/127`, takže `subnet_fallback` vracel přesně
téhož souseda, který se čekal z ND — i úplně smazaná ND větev prošla. Na `/64`
fallback mlčí (`IPV6_FALLBACK_MIN_PREFIX = 126`) a `resolved_from == "nd"` to
tvrdí přímo. Ověřeno druhým mutantem, který ND větev vyřadí.

Produkční kód se u téhle položky nezměnil.

### F-15 — SKIP řádek už nepíše `bez baseline` vedle téže věty

`migration_validator/reporting/view.py`

Ostrý capture z 2026-07-29: **18 výskytů hlášky před opravou, 4 po ní**.
Na nahraných snímcích `runs/ipv6` 19 → 12.

Podmínka je úzká záměrně: SKIP se známou dřívější hodnotou ji má dál vypsat.

### F-7 + F-10 — řádek je popisek a hodnota

`migration_validator/checks/base.py` a všechny čtyři moduly checků

Nejširší řádek ostrého výpisu měřil **277 znaků, teď 149**.

- `Check.label` je popisek sloupce `CHECK` pro řádky, které nevznikly uvnitř
  checku. `title` se na to nehodí — je to věta o checku, ne popisek sloupce.
- `run_check()` popisek doplní (`finding.label or check.label`), takže id
  checku se do reportu **nemá jak dostat**. Renderer by neměl odkud vzít nic
  lepšího, proto to dělá framework, ne sazba.
- Skipy od frameworku (chybí inventory, bez baseline, selhaný collector,
  spadlý check) jsou plnohodnotné řádky s krátkým důvodem v hodnotě.
- Fallback v rendereru zůstal jako pojistka, už ale na pomlčku místo věty.
  Hlídá ho invariant test nad skutečnými daty z laborky
  (`test_every_row_has_a_label_and_a_value_on_real_data`).

### F-8 + F-12 — hlavička říká, co počítá, a filtr přizná, co skryl

`migration_validator/engine.py`, `models/result.py`, `reporting/text_report.py`

```
  filtr: status=FAIL -- 2 z 11 sluzeb
  (pocty sluzeb a checku plati za vyber; radek Sparovano ani sekce NESPAROVANO se neprepocitavaji)

  Sluzby:  1 PASS   6 WARN  4 FAIL  0 SKIP
  Checky: 83 PASS  32 WARN  8 FAIL  9 SKIP
```

- Počty služeb počítá renderer ze `scopes`, ne engine — týž výpočet platí za
  celý běh i za filtrovaný výběr, takže do `summary` nepřibyl žádný klíč.
- Přepočítávají se jen počty checků. `scopes_matched` a `unmatched_*` ne:
  filtrování se na `NESPAROVANO` nevztahuje a přepočet jejich čísel by tiše
  smazal přesně to, co má sekce ukázat.
- `RunResult.filtered` nese, že už nejde o celý běh — i ve strojovém výstupu,
  protože `evaluate --format json --status fail` zapisuje právě ten
  profiltrovaný výsledek. Nefiltrovaný běh vrací původní objekt a klíč v JSON
  nemá.
- Návratový kód dál čte **nefiltrovaný** výsledek (`cli.py`): filtr je pohled
  na výpis, ne změna verdiktu.

### Navíc: `bez baseline` u řádků, které baseline měly

`checks/evpn.py`, `checks/ifaces.py`

Našlo se při ostrém ověření F-15. `evpn_vpws_status`, `evpn_esi_status`
a `traffic_ceased` si baseline dohledají a vozí ji v poli `baseline`, ale
nikdy ji nerozložily na `baseline_value` — takže sloupec `ZMENA` hlásil
`bez baseline` i tam, kde baseline byla. U `traffic_ceased` to nemohla být
pravda vůbec: je to compare check, bez baseline neběží.

Při té příležitosti dostal `evpn_mac_count` `baseline_value` i `delta`, takže
sloupec `ZMENA` u něj není prázdný, přestože check obě čísla zná.

### Dokumentace

`docs/cs` i `docs/en` přepsané ve stejných commitech jako kód — README,
`files/reporting.md`, `files/top-level.md` a `reference.md` (klíč `filtered`
a nový význam `summary` po filtru). Ukázky výstupu byly generované z fixtures
a rozešly se už dřív; teď jsou znovu vygenerované.

`docs/superpowers/specs/` a `plans/` se **záměrně nesahaly** — jsou to datované
návrhové artefakty. Platný stav popisuje `docs/cs`, `docs/en` a roadmapy.

---

## T1, T2a, T7a, T7b — nemají dohledatelnou definici

Roadmapa z 2026-07-28 je vede jako „drobné mezery v pokrytí fixtures, dávkově
jedním commitem". Ledger, ze kterého ta čísla pocházejí, v repu **není** —
v `docs/` ani v git historii se nikde neříká, o které mezery šlo.

Místo hádání proběhla mutační sonda do tří oblastí, které ta čísla pojmenovávají
(Task 1 = rozdělení rodin v parserech, Task 2 = rodina BGP souseda, Task 7 =
checky ARP/ND). **Všechny tři mutanty sada chytila:**

| mutant | padlo |
|---|---|
| `peer_family()` vrací vždy `4` | 3 testy v `tests/checks/test_bgp.py` |
| `nd_present` značkuje `family=4` | `test_nd_finding_shows_mac_and_family` |
| `mx_parser` bere pro IPv6 selektor `inet` místo `inet6` | 2 testy v `tests/parsers/test_family_split.py` |

Takže v pojmenovaných oblastech díra není. Kdyby si někdo na původní znění těch
čtyř položek vzpomněl, patří sem; jinak je to uzavřené.

---

## Follow-up (nové, drobné)

### Dva idiomy popisku vedle sebe

`checks/evpn.py`

Report má v sloupci `CHECK` dva různé tvary:

```
 PASS | EVPN-VPWS-CPE13-NNI          : Up  SID 1000 -> 2000
 PASS | Interface errors (et-0/0/8)  : bez chyb
```

První je **identita** (jméno instance / ESI), druhý **check + kvalifikátor**.
Není to F-10 (popisek existuje, není to id), proto se to ve vlně 1 vědomě
nesahalo: sjednocení na `EVPN ESI status (<esi>)` rozšíří sloupec o 18 znaků
u každého ESI řádku, což je vlastní rozhodnutí. Patří k F-14 (kosmetika).

---

## Co zůstává z původní roadmapy

Beze změny: **vlna 2** (druhý spec — BFD a statické routy, začít brainstormem
nad třemi otevřenými otázkami) a **vlna 3** (F-2 podřádky s názvem RIB, F-11
dvě kopie logiky pro link-local, F-14 kosmetika v `NESPAROVANO`).

Uzavřená rozhodnutí (R-1, R-2, T9b) a zaznamenaný předpoklad u `_usable_nd`
platí dál — viz roadmapa z 2026-07-29.
