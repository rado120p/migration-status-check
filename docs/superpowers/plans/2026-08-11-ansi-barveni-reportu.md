# ANSI barveni statusu v textovem reportu - implementacni plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Status tokeny (PASS/WARN/FAIL/SKIP/INFO) v CLI reportu se barvi ANSI sekvencemi; zapnuti ridi autodetekce TTY + `NO_COLOR` + prepinace `--color`/`--no-color`.

**Architecture:** Barveni zije v `migration_validator/reporting/text_report.py` v miste sazby - sirky sloupcu se dal pocitaji z cisteho textu, barva se pridava az po vypoctu paddingu. `render()` dostane keyword `color=False`, o zapnuti rozhoduje nova funkce `use_color()`, CLI ji vola z prikazu `evaluate`.

**Tech Stack:** Python 3.13, cista stdlib (zadna nova zavislost), pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-ansi-barveni-reportu-design.md`

## Global Constraints

- Zadna nova zavislost (zadny rich/colorama).
- Barvi se VYHRADNE status tokeny: sloupec STAV v souhrnne tabulce, sloupec
  STAV v blocich vcetne hlavicky bloku, nazvy stavu v radcich
  `Sluzby:`/`Checky:` (jen slovo, cislo ne). Nic jineho.
- Barvy: PASS `\x1b[32m`, WARN `\x1b[33m`, FAIL `\x1b[31m`, SKIP `\x1b[2m`
  (dim), INFO `\x1b[36m`; reset `\x1b[0m` za kazdym tokenem.
- Default `render(color=False)` - vsechny stavajici testy musi projit beze
  zmeny.
- Sirky sloupcu se NIKDE nesmi pocitat z obarveneho textu.
- Komentare a docstringy cesky bez diakritiky, stejne jako zbytek kodu.

---

### Task 1: Barveni tokenu v text_report.py

**Files:**
- Modify: `migration_validator/reporting/text_report.py`
- Test: `tests/reporting/test_text_report.py`

**Interfaces:**
- Consumes: `Status`, `SYMBOL`, `COUNT_NAMES` (uz existuji v `text_report.py`).
- Produces: `render(result, *, detail=False, color=False) -> str`;
  interni helpery `_colorize(status: Status, text: str, color: bool) -> str`
  a `_status_cell(status: Status, width: int, *, color: bool) -> str`.
  Task 3 spoleha na signaturu `render(..., color=...)`.

- [ ] **Step 1: Napsat padajici testy**

Do `tests/reporting/test_text_report.py` (fixture `_legacy_result()` uz
v souboru je, `re` uz je importovane; helper `_strip_ansi` pridej k
ostatnim helperum nahoru):

```python
def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_color_wraps_status_tokens():
    output = render(_legacy_result(), color=True)
    assert "\x1b[32mPASS\x1b[0m" in output
    assert "\x1b[33mWARN\x1b[0m" in output
    assert "\x1b[31mFAIL\x1b[0m" in output


def test_color_paints_names_in_counts_lines():
    # Radky Sluzby:/Checky: barvi nazev stavu, cislo pred nim ne.
    output = render(_legacy_result(), color=True)
    assert "1 \x1b[31mFAIL\x1b[0m" in output
    assert "\x1b[31m1" not in output


def test_color_off_emits_no_ansi():
    assert "\x1b[" not in render(_legacy_result())


def test_stripped_color_output_equals_plain_output():
    # Hlida, ze barveni nerozbiji zarovnani ani sirky ramecku: po
    # odstraneni ANSI sekvenci musi byt vystup znak po znaku stejny.
    result = _legacy_result()
    assert _strip_ansi(render(result, color=True)) == render(result)
```

- [ ] **Step 2: Overit, ze testy padaji**

Run: `pytest tests/reporting/test_text_report.py -k color -v`
Expected: FAIL - `render() got an unexpected keyword argument 'color'`
(u `test_color_off_emits_no_ansi` PASS uz ted, to nevadi).

- [ ] **Step 3: Implementace v text_report.py**

Pod konstantu `SYMBOL` pridat:

```python
# Zakladni 8barevna paleta schvalne - funguje na tmavem i svetlem pozadi
# a nevyzaduje detekci schopnosti terminalu.
_ANSI = {
    Status.PASS: "\x1b[32m",
    Status.WARN: "\x1b[33m",
    Status.FAIL: "\x1b[31m",
    Status.SKIP: "\x1b[2m",
    Status.INFO: "\x1b[36m",
}
_RESET = "\x1b[0m"


def _colorize(status: Status, text: str, color: bool) -> str:
    """Obali text ANSI barvou statusu. Prazdny token se neobaluje."""
    if not color or not text:
        return text
    return f"{_ANSI[status]}{text}{_RESET}"


def _status_cell(status: Status, width: int, *, color: bool) -> str:
    """Status token doplneny mezerami na sirku sloupce.

    Padding se pocita z cisteho textu PRED obarvenim - escape sekvence
    maji nenulovy len(), takze format spec `:<w` by rozjel zarovnani.
    """
    plain = SYMBOL[status].strip()
    return _colorize(status, plain, color) + " " * (width - len(plain))
```

Zmeny v `_block()` - signatura `def _block(view: ServiceView, has_baseline: bool, color: bool) -> list[str]:`.
Vnitrni `line()` prebira uz hotovou bunku misto formatovani:

```python
    def line(status_cell: str, label: str, value: str, change: str) -> str:
        text = f" {status_cell} | {label:<{label_width}} : {value:<{value_width}}"
        if not has_baseline:
            return text.rstrip()
        return f"{text} | {change}".rstrip()
```

Volani hlavickoveho radku tabulky: `line(f"{'STAV':<4}", label_title, value_title, change_title)`.
Volani datovych radku (obe mista - `section.rows` i `group.rows`):

```python
                line(_status_cell(row.status, 4, color=color), row.label, row.value, changes[id(row)])
```

Hlavicka bloku - sirka ramecku se musi dal pocitat z cisteho textu, takze
plain varianta zustava pro `len()` a obarvena jde do vystupu:

```python
    header_rest = (
        f"  {view.description}   "
        f"{view.service_type}   {ports}   RI: {instance}"
    )
    # Pro vypocet sirky ramecku cisty text; obarvena varianta ma delsi
    # len() o escape sekvence a prerostla by "=" caru.
    header_line = f" {SYMBOL[view.status].strip():<4}{header_rest}"
    header_out = f" {_status_cell(view.status, 4, color=color)}{header_rest}"
```

`width` se pocita dal z `len(header_line)`; do `lines` se vklada
`header_out` (`lines = ["=" * width, header_out]`).

Zmeny v `_counts_lines()` - signatura
`def _counts_lines(services: dict[str, int], checks: dict[str, int], color: bool) -> list[str]:`
a vnitrni `line()`:

```python
    def line(title: str, counts: dict[str, int]) -> str:
        return f"  {title:<7} " + "  ".join(
            f"{counts[key]:>{widths[key]}} {_colorize(Status(name), name, color)}"
            for key, name in COUNT_NAMES
        )
```

Zmeny v `render()`:

- signatura: `def render(result: RunResult, *, detail: bool = False, color: bool = False) -> str:`
- volani souhrnu: `lines.extend(_counts_lines(services, summary, color))`
- souhrnna tabulka: v `rows` nahradit prvni polozku tuple
  `SYMBOL[view.status]` -> `view.status` a ve vypisu radku nahradit
  `f"{status:<{status_w}} "` -> `f"{_status_cell(status, status_w, color=color)} "`
  (hlavicka `{'STAV':<{status_w}}` zustava beze zmeny)
- volani bloku: `lines.extend(_block(view, has_baseline, color))`

- [ ] **Step 4: Overit, ze testy prochazeji**

Run: `pytest tests/reporting/ -v`
Expected: vsechny PASS (nove i stavajici - default `color=False` nesmi
zmenit ani znak vystupu).

- [ ] **Step 5: Cela sada**

Run: `pytest`
Expected: vse PASS (851+ testu, 1 skip).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/reporting/text_report.py tests/reporting/test_text_report.py
git commit -m "feat: ANSI barveni status tokenu v textovem reportu"
```

---

### Task 2: Rozhodnuti use_color()

**Files:**
- Modify: `migration_validator/reporting/text_report.py`
- Test: `tests/reporting/test_text_report.py`

**Interfaces:**
- Consumes: nic z Task 1 (nezavisla funkce v temze modulu).
- Produces: `use_color(*, force_on: bool = False, force_off: bool = False, stream=None) -> bool`
  v `migration_validator/reporting/text_report.py`. Task 3 ji importuje
  z `text_report` a vola s `force_on=args.color, force_off=args.no_color`.

- [ ] **Step 1: Napsat padajici testy**

Do `tests/reporting/test_text_report.py` (import `use_color` pridej
k existujicimu importu z `text_report`; `io` pridej nahoru):

```python
import io


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_use_color_defaults_to_isatty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert use_color(stream=_Tty()) is True
    assert use_color(stream=io.StringIO()) is False


def test_use_color_respects_no_color_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(stream=_Tty()) is False


def test_use_color_empty_no_color_counts_as_unset(monkeypatch):
    # Konvence no-color.org: vypina jen NEPRAZDNA hodnota.
    monkeypatch.setenv("NO_COLOR", "")
    assert use_color(stream=_Tty()) is True


def test_use_color_force_on_beats_pipe_and_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(force_on=True, stream=io.StringIO()) is True


def test_use_color_force_off_beats_everything(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert use_color(force_on=True, force_off=True, stream=_Tty()) is False
```

- [ ] **Step 2: Overit, ze testy padaji**

Run: `pytest tests/reporting/test_text_report.py -k use_color -v`
Expected: FAIL - ImportError (`use_color` neexistuje).

- [ ] **Step 3: Implementace**

Do `migration_validator/reporting/text_report.py` pridat importy `os`
a `sys` a funkci:

```python
def use_color(
    *, force_on: bool = False, force_off: bool = False, stream=None
) -> bool:
    """Rozhodne, jestli report barvit.

    Poradi: --no-color > --color > autodetekce. Vypnuti vyhrava, aby se
    barvy daly vzdy zakazat i ve skriptu, ktery je jinde vynucuje.
    Autodetekce: stdout je TTY a NO_COLOR neni nastavena na neprazdnou
    hodnotu (konvence no-color.org - prazdna hodnota se cte jako
    nenastavena).
    """
    if force_off:
        return False
    if force_on:
        return True
    if stream is None:
        stream = sys.stdout
    return stream.isatty() and not os.environ.get("NO_COLOR")
```

- [ ] **Step 4: Overit, ze testy prochazeji**

Run: `pytest tests/reporting/test_text_report.py -v`
Expected: vsechny PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/reporting/text_report.py tests/reporting/test_text_report.py
git commit -m "feat: use_color - autodetekce TTY, NO_COLOR a rucni prebiti"
```

---

### Task 3: CLI prepinace --color/--no-color

**Files:**
- Modify: `migration_validator/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `render(result, *, detail, color)` (Task 1) a
  `use_color(force_on=..., force_off=...)` (Task 2), obe z
  `migration_validator.reporting.text_report`.
- Produces: `mig-validate evaluate --color | --no-color` (vzajemne
  vylucne argparse flagy, dest `color` / `no_color`).

- [ ] **Step 1: Napsat padajici testy**

Do `tests/test_cli.py` (helper `_write(tmp_path, name, address, interface, ...)`
uz v souboru je a vyrabi snapshot soubor; vraci cestu):

```python
def test_evaluate_color_flag_forces_ansi(tmp_path, capsys):
    path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    code = main(["evaluate", "--snapshot", str(path), "--color"])
    assert code == EXIT_OK
    assert "\x1b[32mPASS\x1b[0m" in capsys.readouterr().out


def test_evaluate_defaults_to_plain_when_not_a_tty(tmp_path, capsys):
    # capsys nahrazuje stdout ne-TTY objektem - autodetekce musi
    # barvy vypnout bez jakehokoli prepinace.
    path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    code = main(["evaluate", "--snapshot", str(path)])
    assert code == EXIT_OK
    assert "\x1b[" not in capsys.readouterr().out


def test_evaluate_color_and_no_color_are_exclusive(tmp_path, capsys):
    path = _write(tmp_path, "s.json", "172.20.20.5", "et-0/0/1")
    with pytest.raises(SystemExit):
        main(["evaluate", "--snapshot", str(path), "--color", "--no-color"])
```

- [ ] **Step 2: Overit, ze testy padaji**

Run: `pytest tests/test_cli.py -k color -v`
Expected: `test_evaluate_color_flag_forces_ansi` FAIL (argparse:
unrecognized arguments `--color`); exclusivity test FAIL ze stejneho
duvodu (SystemExit sice nastane, ale az po opravach musi nastat kvuli
vylucnosti - po implementaci overit hlasku `not allowed with`);
`test_evaluate_defaults_to_plain_when_not_a_tty` PASS uz ted.

- [ ] **Step 3: Implementace v cli.py**

Import rozsirit:

```python
from migration_validator.reporting.text_report import filter_result, render, use_color
```

V `build_parser()` k subparseru `evaluate` (za `--detail`):

```python
    color = evaluate.add_mutually_exclusive_group()
    color.add_argument(
        "--color",
        action="store_true",
        help="vynuti barvy i mimo terminal (napr. do 'less -R')",
    )
    color.add_argument(
        "--no-color",
        action="store_true",
        help="vypne barvy (autodetekce: barvi se jen na TTY bez NO_COLOR)",
    )
```

V `_cmd_evaluate()` nahradit radek `print(render(shown, detail=args.detail), end="")`:

```python
        color = use_color(force_on=args.color, force_off=args.no_color)
        print(render(shown, detail=args.detail, color=color), end="")
```

V `_evaluate_run()` totez - `color` spocitat jednou pred smyckou
(hned vedle `statuses = _parse_statuses(args.status)`):

```python
    color = use_color(force_on=args.color, force_off=args.no_color)
```

a ve smycce nahradit `print(render(shown, detail=args.detail), end="")`:

```python
            print(render(shown, detail=args.detail, color=color), end="")
```

- [ ] **Step 4: Overit, ze testy prochazeji**

Run: `pytest tests/test_cli.py -v`
Expected: vsechny PASS.

- [ ] **Step 5: Cela sada**

Run: `pytest`
Expected: vse PASS.

- [ ] **Step 6: Rucni overeni v terminalu**

Run: `mig-validate evaluate --snapshot <libovolny snapshot z runs/> | head -20`
a `mig-validate evaluate --snapshot <tentyz> --color | head -20`
Expected: bez prepinace pres rouru zadne escape sekvence; s `--color`
obarvene tokeny (v terminalu videt barevne PASS/WARN/FAIL).

- [ ] **Step 7: Commit**

```bash
git add migration_validator/cli.py tests/test_cli.py
git commit -m "feat: evaluate --color/--no-color, autodetekce TTY v CLI"
```
