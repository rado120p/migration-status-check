# ANSI barveni statusu v textovem reportu

Datum: 2026-08-11
Stav: navrh schvalen v brainstormingu

## Cil

Status tokeny v CLI reportu se barvi, aby oko naslo FAIL/WARN bez cteni
celeho radku. Vyhradne CLI zalezitost - zadna priprava pro GUI, barvy jsou
vlastnost textove sazby. Pripadne budouci graficke rozhrani si barvy odvodi
ze `Status` enumu v datove vrstve, ktera zustava nedotcena.

## Rozsah barveni (varianta "jen status tokeny")

Barvi se VYHRADNE status tokeny, nic jineho:

- sloupec STAV v souhrnne tabulce sluzeb,
- sloupec STAV v blocich sluzeb vcetne tokenu v hlavicce bloku,
- nazvy stavu v radcich `Sluzby:` / `Checky:` (jen slovo, cislo ne).

Dodatek (2026-08-11, po nasazeni na runs/mig01): INFO drive mapovalo v
SYMBOL na prazdny retezec (hodnota bez verdiktu). S barvenim se rozhodnuti
revidovalo - 47 INFO radku bez tokenu splyvalo s okolim a nemelo se cim
obarvit. SYMBOL nyni mapuje INFO na "INFO" a token se tiskne (a barvi)
v STAV sloupcich stejne jako ostatni stavy.

Cele radky se nebarvi - u sirokych tabulek to rusi a zhorsuje citelnost
sloupcu ZMENA a NALEZ. Sekce NESPAROVANO a NEZARAZENO zadne status tokeny
nemaji, takze zustavaji bez barev.

## Barvy

| Status | Barva | ANSI kod |
|--------|-------|----------|
| PASS | zelena | `\x1b[32m` |
| WARN | zluta | `\x1b[33m` |
| FAIL | cervena | `\x1b[31m` |
| SKIP | potlacena (dim) | `\x1b[2m` |
| INFO | azurova (cyan) | `\x1b[36m` |

Reset `\x1b[0m` za kazdym tokenem. Zakladni 8barevna paleta schvalne -
funguje na tmavem i svetlem pozadi a nevyzaduje detekci schopnosti
terminalu.

## Architektura

**Pristup:** barveni uvnitr `text_report.py` v miste sazby. Status token se
obarvi v okamziku skladani radku; sirky sloupcu se dal pocitaji z cisteho
textu. Zadna nova zavislost.

Zamitnute alternativy:

- post-processing (regex pres hotovy text): `PASS` se muze vyskytnout
  v popisu sluzby nebo hodnote checku a obarvil by se omylem;
- knihovna rich/colorama: zavislost kvuli peti escape sekvencim.

### Rozhrani

`render(result, *, detail=False, color=False)` - novy keyword parametr,
default `False`. Vsechna dnesni volani i testy zustavaji beze zmeny
chovani (cisty text).

### Rozhodnuti o zapnuti

Funkce `use_color(force_on, force_off)` v reporting vrstve:

1. `--no-color` -> False,
2. `--color` -> True,
3. jinak `sys.stdout.isatty()` a zaroven prazdna/nenastavena promenna
   `NO_COLOR` (konvence https://no-color.org).

CLI prida prepinace `--color` / `--no-color` k prikazum, ktere tisknou
report (obe mista volani `render` v `cli.py`), a vysledek `use_color`
preda do `render(color=...)`.

`--color` je k tomu, aby sly barvy vynutit do roury
(`mig-validate ... | less -R`).

### Mechanika zarovnani

Barva se aplikuje az PO vypoctu sirek. Pomocna funkce obarvi token a
doplni padding podle delky cisteho textu:

```
colored + " " * (width - len(plain))
```

Sirky sloupcu se nikde nepocitaji z obarveneho textu, takze zarovnani
sloupcu ZMENA/NALEZ sedi stejne jako dnes.

## Testy

Stavajici testy projdou beze zmeny (default `color=False`).

Nove testy:

1. s `color=True` obsahuje vystup ocekavane sekvence kolem tokenu
   (napr. `\x1b[31mFAIL\x1b[0m`),
2. po odstraneni ANSI sekvenci je barevny vystup ZNAK PO ZNAKU identicky
   s nebarevnym - to hlida, ze barveni nerozbiji zarovnani,
3. rozhodovaci tabulka `use_color`: flagy x `NO_COLOR` x isatty.

## Mimo rozsah

- Barveni jinych casti reportu nez status tokenu.
- Detekce poctu barev terminalu (256/truecolor).
- Jakakoli priprava pro GUI/HTML/TUI (uzavrene rozhodnuti: zadne
  HTML/TUI).
