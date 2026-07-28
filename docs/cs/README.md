# Migration Validator — provozní příručka

Nástroj pro ověření stavu síťových služeb při migraci z routeru **MX (klasický Junos)**
na router s **Junos EVO** (ACX/PTX). Ověří stav před migrací, po migraci a obojí porovná
navzdory tomu, že se názvy portů mezi zařízeními liší (`ge-0/0/2.113` → `et-0/0/8.113`).

- Architektura a to, jak spolu soubory souvisí: [architecture.md](architecture.md)
- Popis každého souboru: [index.md](index.md)
- Referenční tabulky (katalog checků, `config.yml`, `mapping.yml`, JSON schémata): [reference.md](reference.md)
- English version: [../en/README.md](../en/README.md)

---

## 1. Instalace

Vyžaduje Python ≥ 3.11.

```bash
cd /home/rado/Desktop/scripts/migration-status-check
python3 -m venv .venv
.venv/bin/pip install -e .
```

Instalace vytvoří příkaz `mig-validate` (`.venv/bin/mig-validate`). Závislosti se instalují
automaticky: `junos-eznc` (PyEZ), `PyYAML`, `lxml`.

Testy (nepotřebují síť ani laborku):

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

---

## 2. Základní princip: capture → evaluate

Nástroj má **dvě oddělené operace** a je důležité je nezaměňovat:

| operace | sahá na síť | co dělá |
|---|---|---|
| `capture` | **ano** | připojí se k zařízení, sesbírá operační stav a zmrazí ho do JSON snapshotu |
| `evaluate` | **ne** | čistá funkce nad hotovými snapshoty — vyhodnotí je a vypíše výsledek |

Z toho plyne provozní důsledek: **snapshot ze starého zařízení musí vzniknout dřív, než
přepojíte kabel.** Po migraci už ten stav nikdo nezjistí. Vyhodnocení se naproti tomu dá
opakovat kolikrát chcete — nezatěžuje produkci a vrací pokaždé identický výsledek.

---

## 3. Celý migrační postup

| krok | akce | příkaz |
|---|---|---|
| 1 | připravit inventory obou zařízení | `mx_parser.py` / `evo_parser.py` (viz níže) |
| 2 | sběr na **starém** zařízení, před migrací | `mig-validate capture --phase pre-migration` |
| 3 | validace starého zařízení (bez baseline) | `mig-validate evaluate --snapshot pre.json` |
| 4 | **vlastní migrace** — přepojení kabelů | — |
| 5 | sběr na **novém** zařízení, po migraci | `mig-validate capture --phase post-migration` |
| 6 | validace nového + porovnání proti starému | `mig-validate evaluate --snapshot post.json --baseline pre.json` |

### Krok 1 — inventory ze zařízení

Inventory je YAML se seznamem rozhraní a služeb, které na nich běží. Vyrábějí ho dva
samostatné parsery **v kořeni repozitáře** — každý pro jinou platformu:

| zařízení | parser |
|---|---|
| MX (klasický Junos) | `mx_parser.py` |
| ACX / PTX (Junos OS Evolved) | `evo_parser.py` |

```bash
.venv/bin/python mx_parser.py  172.20.20.4 -o 172.20.20.4.yml
.venv/bin/python evo_parser.py 172.20.20.5 -o 172.20.20.5.yml
```

Bez `-o` se soubor jmenuje `<hostname>.yml`. Přihlášení je stejné jako u validatoru
(`--auth key|password`, `-u/--username`, `-k/--key-file`), výchozí je SSH klíč a uživatel
`ansible`. Podrobnosti: [files/parsers.md](files/parsers.md).

**Bez inventory nástroj funguje taky**, ale všechno je pak v jednom „device" scope: místo
per-službu dostanete souhrn za celé zařízení a ping se nespustí vůbec (není znám cíl ani
source adresa).

### Krok 2 a 5 — sběr (`capture`)

```bash
.venv/bin/mig-validate capture \
    --device 172.20.20.4 \
    --inventory 172.20.20.4.yml \
    --phase pre-migration \
    --output runs/mig01/pre/172.20.20.4.json
```

| přepínač | význam |
|---|---|
| `--device` | IP nebo hostname zařízení (povinné) |
| `--output` | kam zapsat snapshot (povinné) |
| `--inventory` | YAML z parseru; bez něj se sbírá v device režimu |
| `--phase` | volný text, ukládá se do snapshotu a tiskne v reportu (`pre-migration`, `post-migration`) |
| `--collectors` | čárkou oddělený podseznam oblastí (`interfaces,bgp`) — pro ladění |
| `--ping-count` | počet ICMP paketů na cíl, výchozí 5 |
| `--record-raw DIR` | vedle sběru uloží i syrové RPC XML do `DIR/<platforma>/` |
| `--username` | výchozí `ansible` |
| `--auth key\|password` | výchozí `key` |
| `--key-file` | výchozí `~/.ssh/id_rsa` |
| `--password` | jen pro `--auth password` |
| `--port` / `--timeout` | výchozí 22 / 30 s |

Platforma (`junos` vs `junos-evo`) se **detekuje automaticky** a podle ní se vyberou správná
RPC. Zadávat ji nemusíte.

Selhání jednoho collectoru sběr nezruší — zapíše se do snapshotu, na stderr se vypíše
varování a checky, které tu oblast potřebují, později dostanou `SKIP`, nikdy `PASS`.
Selhání **připojení** naopak sběr ukončí, snapshot nevznikne a návratový kód je 2.

### Krok 3 — validace bez baseline

```bash
.venv/bin/mig-validate evaluate --snapshot runs/mig01/pre/172.20.20.4.json
```

Proběhnou jen **stavové** checky (rozhraní jsou up, BGP je Established, teče provoz, ...).
Porovnávací checky vrátí `SKIP` s důvodem `porovnavaci check bez baseline snapshotu`.

### Krok 6 — validace s porovnáním

```bash
.venv/bin/mig-validate evaluate \
    --snapshot runs/mig01/post/172.20.20.5.json \
    --baseline runs/mig01/pre/172.20.20.4.json \
    --mapping  mapping.yml \
    --format json --output runs/mig01/post.result.json
```

| přepínač | význam |
|---|---|
| `--snapshot` | vyhodnocovaný snapshot (povinné) |
| `--baseline` | snapshot, proti kterému se porovnává |
| `--mapping` | `mapping.yml` s ručním párováním a ignorem |
| `--config` | `config.yml` s tolerancemi a severity |
| `--format text\|json` | výchozí `text` |
| `--output` | soubor s výstupem (u `--format text` se do souboru zapíše **JSON**) |
| `--filter` | podřetězec v description nebo scope id |
| `--status` | čárkou oddělený seznam: `pass,warn,fail,skip` |
| `--detail` | vypíše všechny checky každé služby, ne jen nejhorší nález |
| `--warn-as-error` | WARN pak také vrací návratový kód 1 |

---

## 4. Jak číst výstup

Skutečný výstup z laboratorního běhu:

```
Migrace: 172.20.20.4 (pre-migration) -> 172.20.20.5 (post-migration)

  68 PASS   15 WARN   1 FAIL   7 SKIP
  Sparovano 8 sluzeb, 2 nesparovana v baseline, 3 nesparovane v subject

SLUZBA                           TYP        STAV  DETAIL
EVPN-VPWS-CPE13-NNI              E-Line     WARN  et-0/0/8.213: provoz netece (in 0 pps, out 0 pps)
INTERNET-CPE13-NNI               Internet   WARN  152.11.13.2: stav se zmenil Idle -> Established
EVPN-VLAN-AWARE-INTERNET         Internet   FAIL  152.11.14.4: stav Active, ocekavano Established
clab-pop-migration-P2;et-0/0/0   Core       OK

NESPAROVANO
  baseline  clab-pop-migration-P1;et-0/0/0   (Core)  zadny kandidat na subject
  subject   EVPN-VLAN-AWARE-INTERNET         (E-LAN)  nova sluzba, chybi baseline
```

Co je na tom podstatné:

- **Řádek na službu ukazuje jen nejhorší nález.** Zbytek checků zobrazí `--detail`.
- **`OK` neznamená automaticky „vše ověřeno".** Může znamenat i to, že proběhla jen část
  checků. Právě proto existuje `--detail` — ukáže, co konkrétně se kontrolovalo.
- **Filtry (`--filter`, `--status`) zúží jen tabulku služeb.** Souhrnné počty nahoře zůstávají
  za celý běh — u `--status fail` tedy uvidíte jeden řádek, ale souhrn pořád hlásí všech
  68 PASS. Je to záměr: filtr je pohled, ne nový výpočet.
- **Sekce `NESPAROVANO` se vypisuje vždy**, i když je všechno ostatní zelené, a **filtry se
  na ni nevztahují.** Je to hlavní pojistka proti přehlédnuté službě:
  - `baseline` = služba byla na starém boxu a na novém není → podezření na zapomenutou migraci,
  - `subject` = na novém je něco navíc → nová nebo restrukturalizovaná služba.
- Stavy jsou `OK` / `WARN` / `FAIL` / `SKIP`. `SKIP` znamená **„nezměřeno"**, ne „v pořádku" —
  chybějící data nikdy nedají PASS.

### Návratové kódy

| kód | význam |
|---|---|
| `0` | žádný FAIL |
| `1` | aspoň jeden FAIL (nebo WARN při `--warn-as-error`) |
| `2` | **chyba nástroje** — nepřipojil se, chybí snapshot, rozbitý JSON, jiná `schema_version` |

Rozdíl mezi 1 a 2 je záměrný: *test selhal* a *nástroj selhal* jsou dvě různé věci.

---

## 5. Ladění párování služeb

Párování mezi starým a novým zařízením je nejcitlivější část celého nástroje, protože stojí
na kvalitě `description` na zařízeních. Nástroj **nikdy nehádá**: když na některé úrovni
vyjde víc kandidátů, službu nespáruje a pošle ji do `NESPAROVANO` s důvodem
`ambiguous: N kandidatu (...)`.

Ladit se to dá bez spouštění celé validace a bez sahání na síť:

```bash
.venv/bin/mig-validate match \
    --baseline runs/mig01/pre/172.20.20.4.json \
    --subject  runs/mig01/post/172.20.20.5.json \
    --mapping  mapping.yml
```

```
SPAROVANO (8)
  high     description+service_type+service_subtype      svc:EVPN-VPWS-CPE13-NNI:E-Line
           -> svc:EVPN-VPWS-CPE13-NNI:E-Line
  medium   routing_instance+service_type                 svc:L3VPN-CPE14-UNI:IPVPN
           -> svc:et-0/0/10.0:IPVPN
...
```

Smyčka je: spustit `match` → doplnit chybějící pár do `mapping.yml` → spustit znovu.
Trvá to sekundy. Popis pravidel a formátu `mapping.yml`: [reference.md](reference.md).

---

## 6. Konfigurace

Obojí je nepovinné; bez nich platí výchozí hodnoty ze `migration_validator/config.py`.

**`config.yml`** — tolerance a severity checků, předává se přes `--config`:

```yaml
checks:
  interface_traffic:
    tolerance_percent: -60      # pokles provozu, který ještě projde
    require_nonzero: true
  bgp_prefix_counts:
    tolerance_percent: -10
    severity: advisory
  ping_reachability:
    count: 5
  traffic_ceased:
    enabled: false              # výchozí; vyžaduje třetí capture starého boxu
```

**`mapping.yml`** — ruční párování a ignorování, předává se přes `--mapping`:

```yaml
mappings:
  - baseline: {description: EVPN-VLAN-AWARE-INTERNET, service_type: Internet}
    subject:  {description: EVPN-VLAN-AWARE-INTERNET, service_type: E-LAN}
    note: "sluzba restrukturalizovana na EVPN vlan-aware s IRB gateway"
  - baseline: {interface: "ge-0/0/4.0"}
    subject:  {interface: "et-0/0/10.0"}

ignore:
  - {description: "EVPN-VLAN-AWARE-L3VPN"}
  - {interface: "ge-0/0/7.0"}
```

Selektor `{interface: ...}` cílí na **logickou jednotku** (`ge-0/0/2.113`), ne na fyzický
port. Management rozhraní (`fxp`, `em`, `me`, `vme`, `bme`, `re0:mgmt-*`, `re1:mgmt-*`) se
sem psát nemusí — jsou vyloučená už na úrovni stavby scopů a nikdy se nepingují.

Úplný přehled: [reference.md](reference.md).

---

## 7. Ostatní příkazy

**Seznam checků** — jediný zdroj pravdy, žádný ručně udržovaný seznam:

```bash
.venv/bin/mig-validate checks              # tabulka
.venv/bin/mig-validate checks --format json
```

**Nahrání syrového RPC XML** pro testovací fixtures:

```bash
.venv/bin/mig-validate record --device 172.20.20.4 --output-dir tests/fixtures/rpc
```

Uloží odpovědi všech collectorů do `tests/fixtures/rpc/<platforma>/<oblast>.xml`. Collector
s více RPC (na MX `evpn_mac`) uloží každé zvlášť — druhé jako `evpn_mac.2.xml`. Je to jediný
udržitelný způsob, jak collectory nezastarají: neznámý výstup z produkce se zkopíruje do
fixtures a regresní test je hotový.

Totéž se dá udělat mimochodem při běžném sběru: `capture --record-raw DIR`. Rozdíl je, že
`record` u každého RPC vypíše, jestli uspělo, zatímco `--record-raw` je tichý best-effort.

---

## 8. Kam se ukládají výsledky

Doporučená konvence (`runs/` je v `.gitignore`):

```
runs/2026-07-24_MX1-POP1_migration/
├── pre/172.20.20.4.snapshot.json
├── post/172.20.20.5.snapshot.json
├── pre-validation.result.json
└── post-validation.result.json
```

Cesty nejsou nikde zadrátované — `--output` je vždy explicitní.

---

## 9. Řešení potíží

| projev | příčina a co s tím |
|---|---|
| `chyba: ...: autentizace selhala` + kód 2 | špatný uživatel/klíč. Ověřte `--username`, `--key-file`, případně `--auth password --password ...` |
| `chyba: ...: timeout po 30 s` + kód 2 | zařízení nedostupné nebo nemá povolený NETCONF na portu 22 |
| `varovani: collector 'X' selhal` na stderr | RPC selhalo, sběr pokračoval. Snapshot vznikl, ale checky nad oblastí `X` budou `SKIP` |
| `chyba: snapshot ma schema_version N` | snapshot z jiné verze nástroje; přesběrejte ho |
| Všechno je `SKIP` | typicky selhaly collectory — podívejte se do `capture.collectors` ve snapshotu |
| Služba chybí ve výpisu | není to migrovaný typ služby (`Internet`, `IPVPN`, `E-Line`, `E-LAN`, `Core`), je to management rozhraní, nebo je v `ignore` v `mapping.yml` |
| Hodně `ambiguous` v `NESPAROVANO` | duplicitní `description` na zařízení — dopárujte přes `mapping.yml` |
| `interface_traffic` hlásí `provoz netece` na čerstvě migrované službě | očekávatelné, pokud přes port ještě nic nejede; check je `advisory`, tedy WARN, ne FAIL |

Pozor na jeden nezvyklý detail: při `--format text` a zadaném `--output` jde na terminál
text, ale **do souboru se zapisuje JSON** (a to nefiltrovaný, celý výsledek).

---

## 10. Známé limity

- **Inventory je vstup, ne výstup.** Kvalita párování je omezená kvalitou popisků na
  zařízeních; `mapping.yml` je únikový ventil, ne náhrada disciplíny.
- **Porovnání datovosti je best-effort.** Mezi pre- a post-snapshotem uběhne reálný čas
  (přepojení kabelu) a provoz se může legitimně lišit. Proto `advisory` a tolerance −60 %.
- **Ping je best-effort.** CPE může být vypnuté nebo blokovat ICMP. Proto `advisory`.
- **Tolerance jsou úvodní odhady** a mají se doladit podle provozu — proto jsou konfigurovatelné.
- **Sběr je snímek jednoho okamžiku.** Nástroj nedělá kontinuální monitoring.
- **Za běhu je nástroj česky bez diakritiky** — nápověda CLI, logy i hlášky ve výsledku
  (`provoz netece`, `zadny kandidat na subject`). Je to kvůli terminálům, kde na diakritiku
  není spoleh. Tahle dokumentace je proti tomu psaná normální češtinou; ukázky výstupu a
  citované hlášky jsou vždy doslovné, aby se daly grepovat.
