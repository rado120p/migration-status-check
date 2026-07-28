# `reporting/` — výstup

Soubory: `json_report.py`, `view.py`, `text_report.py` a prázdný `__init__.py`.

Vrstva jen **formátuje** — nic nevyhodnocuje a nic nepočítá. Souhrn i stav každého scope už
spočítal engine, takže se terminál a budoucí GUI nemohou rozejít v číslech.

`view.py` a `text_report.py` jsou záměrně oddělené: pořadí sekcí a zařazení řádku do rodiny
jde testovat porovnáním datových struktur, zatímco šířky sloupců je nutné testovat proti
řetězci s mezerami. V jednom souboru by se logika testovala přes mezery.

---

## `json_report.py`

Dvě funkce nad `RunResult.to_dict()`:

- `to_json(result, indent=2)` — řetězec s `ensure_ascii=False`, takže české znaky zůstanou
  čitelné a nerozpadnou se na `í`;
- `write_json(result, path)` — zapíše do souboru v UTF‑8, založí nadřazený adresář a přidá
  koncový newline.

JSON je **kanonický formát výsledku**. Textový report je jeho zjednodušený pohled, ne
naopak.

---

## `view.py` — data za reportem

Převádí `ScopeResult` na to, co potřebuje sazba, aniž by řešil jediný znak mezery.

### `Row`, `Section`, `ServiceView`

- **`Row`** — jeden řádek bloku: `status`, `label`, `value`, `baseline_value`, `delta`,
  `mode`.
- **`Section`** — skupina řádků jedné rodiny: `family` (`4` | `6` | `None`), `addresses`,
  `virtual_gw`, `rows`. `family=None` je pro řádky, které na rodině nezávisí (stav rozhraní,
  countery, provoz) — stojí hned pod hlavičkou bloku a nemají vlastní nadpis, protože nadpis
  sekce nese adresu a tyhle řádky žádnou nemají.
- **`ServiceView`** — vše, co blok jedné služby potřebuje: `status`, `description`,
  `service_type`, `routing_instance`, `baseline_interfaces`, `subject_interfaces`,
  `worst_message`, `sections`.

### `build_view(scope)`

Sestaví `ServiceView` ze `ScopeResult`:

1. vezme `scope.identity` (adresy, VGW, popisek, RI — viz
   [models.md](models.md#resultpy--modely-výsledku));
2. pro každou rodinu v `FAMILY_ORDER = (None, 4, 6)` vybere checky s odpovídajícím `family`
   a poskládá `Section` — **prázdná rodina žádnou sekci nedostane**: služba bez IPv6 nemá mít
   prázdnou sekci IPv6;
3. `_row(check, qualify)` udělá z `CheckResult` `Row`; `qualify=True`, když má rodina víc než
   jednu adresu — pak řádky vázané na konkrétní adresu (ARP, ND, ping) nesou tu adresu
   v popisku (`ARP (198.11.13.0/29)`). U jediné adresy se popisek vypouští, protože adresa už
   je v hlavičce sekce.

### `change_text(row, has_baseline)`

Obsah sloupce `ZMENA`:

- bez baseline vůbec → `""` (sloupec se v `text_report.py` celý zahodí, viz níž),
- `row.mode == "state"` → `""` — stavové checky (`arp_present`, `nd_present`,
  `ping_reachability`, `interface_state`, ...) baseline hodnotu nemají z definice, takže by se
  jinak `bez baseline` vypsalo skoro všude a nikdo by tomu nevěnoval pozornost,
- `baseline_value is None` → `NO_BASELINE` (`"bez baseline"`),
- `baseline_value == value` → `""` (beze změny),
- jinak `f"bylo {baseline_value}   {delta}"`, nebo jen `f"bylo {baseline_value}"`, když delta
  není.

### `_worst_message(scope)`

Zpráva nejhoršího nálezu podle `_STATUS_ORDER = (FAIL, WARN, SKIP, PASS)`; u `PASS` scope
vrací `""`. Je to text, který se ukáže ve sloupci `NALEZ` souhrnné tabulky.

---

## `text_report.py` — sazba

Vrstva dostane hotová data z `view.py` a řeší jen **jak to vyrovnat do sloupců**.

### `filter_result(result, *, text=None, statuses=None)`

Vrátí **kopii** výsledku (`dataclasses.replace`) s profiltrovaným seznamem scopů:

- `text` — case-insensitive podřetězec ve `scope_id` **nebo** v `key["description"]`,
- `statuses` — množina požadovaných stavů.

**Sekce `NESPAROVANO` zůstává celá.** Je to hlavní pojistka proti přehlédnutí nezmigrované
služby a filtr ji nesmí schovat.

### `SYMBOL`

Symboly jsou textové, ne unicode, a všechny čtyři znaky dlouhé: `PASS`, `WARN`, `FAIL`,
`SKIP`. (Specifikace kreslila `✓ ⚠ ✗`; kód je nepoužívá.) `PASS` dřív byl `"OK "` — změnilo
se to spolu s přepisem reportu, aby byly všechny symboly stejně dlouhé a sloupec `STAV` se
nemusel řešit zvlášť.

### `render(result, *, detail=False)`

Skládá pět částí:

1. **Hlavička** — s baselinem `Migrace: <adresa> (<fáze>) -> <adresa> (<fáze>)`, bez něj
   `Validace: <adresa> (<fáze>)`.
2. **Souhrn** — počty PASS/WARN/FAIL/SKIP a počty spárovaných a nespárovaných služeb.
3. **Souhrnná tabulka** — jeden řádek na službu, sloupce `STAV`, `SLUZBA` (description, jinak
   scope id), `TYP`, `STARY PORT`, `NOVY PORT`, `RI`, `NALEZ` (nejhorší nález,
   `_worst_message()`; u zeleného řádku prázdný). Šířky všech sloupců **se počítají z dat**,
   ne napevno — dřív pevná šířka `SLUZBA`/`RI` přetekla na reálných jménech z laborky
   a rozjela zarovnání napravo.
4. **Blok pro každou službu, která není `PASS`** — vypíše se automaticky, bez `--detail`.
   `--detail` navíc rozbalí bloky i u služeb se stavem `PASS`.
5. **Sekce `NESPAROVANO`** — vždy, i když je všechno ostatní zelené (pak `(nic)`), a filtr se
   na ni nevztahuje.

### Blok jedné služby (`_block`)

Řádky uvnitř bloku jdou v pořadí `FAMILY_ORDER = (None, 4, 6)`:

1. řádky vázané na rozhraní, ne na adresu (stav rozhraní, chybové countery, datovost) — bez
   vlastního nadpisu sekce;
2. sekce `IPv4`, uvozená nadpisem s nakonfigurovanými adresami a případnou `VGW` adresou;
3. sekce `IPv6`, stejně.

Šířky sloupců **se počítají ze VŠECH řádků bloku najednou** (`label_width`, `value_width`,
`change_width`), ne po sekcích zvlášť — jinak by nesedla společná oddělovací čára, která se
kreslí jednou přes celý blok. **Nic se neořezává**: dlouhý název RIB nebo IPv6 adresa jsou
horší jako nic než jako useknutý text.

**Sloupec `ZMENA` se bez načtené baseline nevypisuje vůbec** — ne jen že zůstane prázdný,
celý sloupec i jeho hlavička (`ZMENA PROTI <port>`) zmizí z výstupu. Bez baseline je hlavička
hodnotového sloupce taky jiná: `POST (<port>)` s baselinem, `HODNOTA` bez něj.

Záhlaví bloku (`=====`, popisek služby, `RI: <instance>`) se do šířky rámce počítá taky —
dlouhý název služby nebo routing instance z laborky umí být delší než tabulka sloupců, takže
rámec musí obalit i tenhle řádek, ne jen tabulku.

### Příklad skutečného výstupu

Vygenerováno přímo z `render()` nad daty ve tvaru `tests/reporting/test_text_report.py`
(`_dual_stack_scope()` + `_result()`, `detail=True`):

```
Migrace: 172.20.20.4 (pre-migration) -> 172.20.20.5 (post-migration)

  1 PASS   1 WARN   0 FAIL   0 SKIP
  Sparovano 1 sluzeb, 0 nesparovana v baseline, 0 nesparovane v subject

STAV  SLUZBA             TYP      STARY PORT  NOVY PORT   RI NALEZ
WARN  INTERNET-CPE13-NNI Internet ge-0/0/2.13 et-0/0/8.13 -  pokles

==================================================================================================
 WARN  INTERNET-CPE13-NNI   Internet   ge-0/0/2.13 -> et-0/0/8.13   RI: -
==================================================================================================
 STAV | CHECK                  : POST (et-0/0/8.13)                      | ZMENA PROTI ge-0/0/2.13
 -----+------------------------+-----------------------------------------+------------------------
 PASS | Interface admin status : Up                                      |
 WARN | Interface traffic in   : 460 pps                                 | bylo 520 pps   -12 %

 -- IPv4  152.11.13.1/30 -------------------------------------------------------------------------
 PASS | ARP                    : 0c:00:ef:5e:df:01 -> 152.11.13.2        |

 -- IPv6  2001:abcd:11:13::a/127 -----------------------------------------------------------------
 PASS | ND                     : 0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b |

NESPAROVANO
  (nic)
```

Bez `--baseline` zmizí sloupec `ZMENA` úplně (hlavička hodnotového sloupce se změní na
`HODNOTA`) a hlavička bloku ukáže jen `NOVY PORT` míso `STARY -> NOVY`. Na IRB rozhraní se
za adresou v hlavičce sekce navíc objeví `VGW <adresa>` — např.
`-- IPv4  152.11.14.2/29   VGW 152.11.14.1`.

---

## Stav a známé mezery

Co v reportu dnes **není**:

- žádné barvy ani unicode symboly,
- `unassigned.bgp_peers` se v textovém výstupu **netiskne** (v JSON ano),
- `match.method` a `confidence` se v textu neobjeví — na to je podpříkaz `match`.
