# `reporting/` — výstup

Soubory: `json_report.py`, `text_report.py` a prázdný `__init__.py`.

Vrstva jen **formátuje** — nic nevyhodnocuje a nic nepočítá. Souhrn i stav každého scope už
spočítal engine, takže se terminál a budoucí GUI nemohou rozejít v číslech.

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

## `text_report.py`

### `filter_result(result, *, text=None, statuses=None)`

Vrátí **kopii** výsledku (`dataclasses.replace`) s profiltrovaným seznamem scopů:

- `text` — case-insensitive podřetězec ve `scope_id` **nebo** v `key["description"]`,
- `statuses` — množina požadovaných stavů.

**Sekce `unmatched` zůstává celá.** Je to hlavní pojistka proti přehlédnutí nezmigrované
služby a filtr ji nesmí schovat. Ověřuje to
`tests/test_end_to_end.py::test_render_after_filter_still_shows_unmatched_section`: i když
filtr smaže všechny scopy, sekce se pořád vykreslí.

### `render(result, *, detail=False)`

Skládá čtyři části:

1. **Hlavička** — s baselinem `Migrace: <adresa> (<fáze>) -> <adresa> (<fáze>)`, bez něj
   `Validace: <adresa> (<fáze>)`.
2. **Souhrn** — počty PASS/WARN/FAIL/SKIP a počty spárovaných a nespárovaných služeb.
3. **Tabulka služeb** — sloupce `SLUZBA` (description, jinak scope id), `TYP`, `STAV`,
   `DETAIL`. Detail je **jen nejhorší nález** (`_worst_message()`), u zeleného řádku prázdný.
4. **Sekce `NESPAROVANO`** — vždy, i když je všechno ostatní zelené (pak `(nic)`).

Symboly jsou textové, ne unicode: `OK `, `WARN`, `FAIL`, `SKIP`. (Specifikace kreslila
`✓ ⚠ ✗`; kód je nepoužívá.)

### `--detail`

`_detail_lines()` vypíše **všechny** checky služby, seřazené `FAIL → WARN → SKIP → PASS`,
pak podle id a labelu. Každý řádek nese status, id checku, severity a zprávu.

Existuje z konkrétního důvodu: souhrnný řádek ukazuje jen nejhorší nález, takže bez detailu
nejde poznat, co dalšího se kontrolovalo — a hlavně jestli `OK` znamená „ověřeno", nebo
„check se vůbec nespustil".

Příklad skutečného výstupu:

```
L3VPN-CPE13-NNI                  IPVPN      WARN  198.11.13.2: stav se zmenil Idle -> Established
    WARN  bgp_session_state      critical  198.11.13.2: stav se zmenil Idle -> Established
    WARN  interface_traffic      advisory  et-0/0/8.113: provoz netece (in 0 pps, out 0 pps)
    OK    arp_present            advisory  nalezeno 1 ARP zaznamu: 198.11.13.2
    OK    bgp_prefix_counts      advisory  198.11.13.2: pocty prefixu v toleranci -10 %
    OK    interface_state        critical  et-0/0/8.113: up/up
    OK    ping_reachability      advisory  vsech 1 cilu odpovedelo
```

---

## Stav a známé mezery

Reporting je zatím nejjednodušší vrstva nástroje a `--detail` byl první krok k jeho
zhutnění. Co v něm dnes **není**:

- žádné barvy ani unicode symboly,
- `unassigned.bgp_peers` se v textovém výstupu **netiskne** (v JSON ano),
- `match.method` a `confidence` se v textu neobjeví — na to je podpříkaz `match`,
- šířky sloupců jsou pevné a delší description se ořízne (`{:<32.32}`).
