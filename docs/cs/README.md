# Migration Validator — provozní příručka

Nástroj pro ověření stavu síťových služeb při migraci z routeru **MX (klasický Junos)**
na router s **Junos EVO** (ACX/PTX). Ověří stav před migrací, po migraci a obojí porovná
navzdory tomu, že se názvy portů mezi zařízeními liší (`ge-0/0/2.113` → `et-0/0/8.113`).

Ověřuje stav rozhraní, BGP, EVPN (E-Line i E-LAN), dosažitelnost (ARP/ND/ping),
**statické routy** a **BFD session**. Poslední dvě jsou jediné, kde se naměřený stav
porovnává i proti **konfiguračnímu záměru** — nakonfigurovaná statická routa, která se
nikdy nedostala do routovací tabulky, je jinak neviditelná.

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

Tenhle postup pokrývá **jeden pár zařízení a explicitní soubory** (`--output`,
`--snapshot`/`--baseline`) — pořád plně funguje a je nejjednodušší cesta pro jednorázové
ověření. Pro migraci, která se dělá po portech, přes víc kroků (`pre`/`post`/**`rollback`**)
nebo kde se má párování starý↔nový port pamatovat samo, existuje od fáze 4 druhá cesta —
`--run <nazev>` — popsaná v kapitole [3a](#3a-run-management---run). Obě cesty vedou na
stejný `capture`/`evaluate` a dají se i kombinovat (`--run` jen zjednodušuje účetnictví okolo).

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
| `--ssh-port` / `--timeout` | výchozí 22 / 30 s |

Platforma (`junos` vs `junos-evo`) se **detekuje automaticky** a podle ní se vyberou správná
RPC. Zadávat ji nemusíte.

`--ssh-port` je u `capture` úmyslně jiné jméno než u `record` (ten má `--port`) — v `--run`
režimu `capture` totiž `--port` používá pro síťový port (`ge-0/0/0`), takže jméno pro SSH port
muselo ustoupit, aby obě věci šly zadat současně.

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
| `--detail` | rozbalí plný blok i u služeb se stavem PASS (WARN/FAIL se rozbalují vždy) |
| `--warn-as-error` | WARN pak také vrací návratový kód 1 |

---

## 3a. Run management (`--run`)

Od fáze 4 existuje vedle ručních `--output`/`--snapshot`/`--baseline` druhá cesta: pojmenovaný
**run adresář**, který si sám pamatuje, která zařízení do migrace patří, jak se párují jejich
porty a které snímky už byly pořízeny. Hodí se pro migraci po jednotlivých portech (LAG po
LAGu, zákazník po zákazníkovi), pro víc kroků (`pre` → `post`, případně `rollback`, když se
migrace vrací) a všude tam, kde by ruční hlídání souborů `pre.json`/`post.json` po chvíli
přestalo být přehledné.

### Struktura `runs/<nazev>/`

```
runs/mig01/
├── run.yml
├── inventory_MX1-POP1_ge_0_0_0.yml
├── inventory_PTX1-POP1_et_0_0_0.yml
├── snapshot_pre_MX1-POP1_ge_0_0_0.json
├── snapshot_post_PTX1-POP1_et_0_0_0.json
└── snapshot_rollback_MX1-POP1_ge_0_0_0.json
```

Jména souborů nesou fázi (`pre`/`post`/`rollback`), **node** (jméno zařízení z `run.yml`, ne
IP) a port — normalizovaný náhradou `-` a `/` za `_` (`ge-0/0/0` → `ge_0_0_0`). Capture bez
`--port` (celoboxový režim) používá `all` místo jména portu.

### `run.yml` — hybridní manifest

Je to **jediný zdroj pravdy o migraci** a dá se naplnit dvěma způsoby, které vedou ke
stejnému souboru:

- **napsat ho ručně předem**, jako migrační plán — `mig-validate` ho pak jen čte a doplňuje
  sekci `captures`;
- **nechat ho vzniknout postupně** voláním `mig-validate capture --run ...` — první capture
  na daném zařízení založí i záznam v `devices`, `--maps-to` doplní `interface_mapping`.

Obě cesty se dají kombinovat — typicky se `devices` a `interface_mapping` sepíší předem podle
migračního plánu a `captures` pak plní samotné běhy `capture`.

```yaml
schema_version: 1

devices:
  MX1-POP1:  {host: 172.20.20.4, platform: junos,     role: old}
  PTX1-POP1: {host: 172.20.20.5, platform: junos-evo, role: new}

interface_mapping:
  # jeden zaznam = jeden migrovany stary port
  - old: {node: MX1-POP1, port: ge-0/0/0}
    new: {node: PTX1-POP1, port: et-0/0/0}
  # vic zaznamu muze sdilet stejny novy port (LAG); l2_switch popisuje
  # pripadny EX mezi EVO a CPE (faze 5, formu uz nese)
  - old: {node: MX1-POP1, port: ge-0/0/1}
    new:
      node: PTX1-POP1
      port: ae0
      l2_switch: {node: EX1-POP1, ae_port: ae0, access_port: ge-0/0/0}

captures:                          # tuhle sekci si vede aplikace sama
  - phase: pre
    device: MX1-POP1
    port: ge-0/0/0
    snapshot: snapshot_pre_MX1-POP1_ge_0_0_0.json
    taken: "2026-08-06T09:12:03Z"
```

`role` je `old` / `new` / `l2-switch`. Fáze 4 podporuje jeden box role `old` a jeden role
`new` — víc boxů a `l2_switch` (EX mezi EVO a CPE) formát manifestu už nese, ale zapojí je až
fáze 5. `interface_mapping` páruje **logické jednotky** (`ge-0/0/0`), stejně jako
`mapping.yml` výš.

### Sběr do runu (`capture --run`)

```bash
.venv/bin/mig-validate capture --run mig01 \
    --device 172.20.20.4 --phase pre --port ge-0/0/0
```

| přepínač | význam |
|---|---|
| `--run` | název run adresáře (`runs/<nazev>/`); vzájemně vylučné s `--output` |
| `--run-root` | kořen run adresářů, výchozí `runs` |
| `--phase` | `pre`, `post` nebo `rollback` — v `--run` režimu je to uzavřený výčet, ne volný text |
| `--port` | logická/fyzická jednotka, na kterou se capture omezí, např. `ge-0/0/0`; bez něj celoboxový režim (`all`) |
| `--maps-to NODE:PORT` | zapíše pár do `interface_mapping`; jen s `--port`. `NODE:PORT` je vždy **protistrana** téhle capture — u `pre`/`rollback` (role `old`) se zapíše jako `old: <tahle capture>, new: NODE:PORT`, u `post` (role `new`) obráceně. Je-li pár v `run.yml` už zapsaný, flag není potřeba |
| `--parse-services` | inventory pro `--run` vyrobí z konfigurace (samostatné krátké spojení) místo hlášky „spusť s --parse-services" |
| `--overwrite` | povolí přepis existujícího `pre` snímku ve stejném run adresáři; bez něj druhý `capture --phase pre` na stejný node/port skončí chybou. Uplatní se jen v `--run` režimu |

**Zjištění node.** `--device` je IP/hostname, které se přihlašuje; `run.yml` k němu hledá node
podle `devices[*].host`. Najde-li shodu, použije se jméno node (`MX1-POP1`) ve jménech
souborů; nenajde-li, použije se přímo hodnota `--device`. Role node podle fáze: `pre` a
`rollback` čekají zařízení s rolí `old`, `post` s rolí `new` — capture samo `devices` doplní,
pokud tam node ještě není.

**Odkud se vezme inventory:**

1. `--inventory <soubor>` — explicitně zadaný soubor jako mimo `--run` režim, má přednost
   před vším ostatním;
2. jinak s `--parse-services` — vždy `runs/<nazev>/inventory_<node>_<port>.yml` (per-port
   cesta), bez ohledu na to, jestli tam už soubor je; celoboxová cesta (`..._all.yml`) se
   v tomhle případě vůbec nezkouší;
3. jinak (bez `--parse-services`) — `runs/<nazev>/inventory_<node>_<port>.yml`, pokud
   existuje, jinak `runs/<nazev>/inventory_<node>_all.yml`, pokud existuje ten;
4. bez shody v kroku 3: chyba `inventory nenalezena - spust s --parse-services`.

**`--parse-services`** stáhne konfiguraci **samostatným krátkým spojením** (odděleně od
capture spojení, které sbírá operační stav) a vyrobí inventory na per-port cestu podle
pravidla 2 výš. Na rozdíl od dřívějšího chování existující soubor **vždy přegeneruje** — konfigurace nového
boxu se mění každou vlnou a `--parse-services` je explicitní žádost o čerstvý stav, ne jen
o doplnění chybějícího. Vypíše deltu proti předchozímu obsahu:

```
inventory pregenerovana: runs/mig01/inventory_PTX1-POP1_ae0.yml (42 sluzeb, +3 nove, -1 odebrane)
```

Poprvé (soubor ještě neexistoval) vypíše `inventory vyrobena: <cesta> (N sluzeb)` bez delty.

**Přepis `pre` snímku.** Druhé `capture --run ... --phase pre` na stejný node a port skončí
chybou `pre snimek uz existuje: <cesta>; prepis povol s --overwrite` — ochrana proti
nechtěnému přepsání baseline. `--overwrite` přepis povolí.

**Ping při `--phase post`.** Cíle pingu se odvodí z ARP/ND záznamů **`pre` snímků
spárovaných starých portů** (dohledaných přes `interface_mapping`) místo z vlastního ARP
nového zařízení — čerstvě přepojený box ARP tabulku ještě nemá naplněnou, zvlášť u větších
/24 rozsahů s mnoha hosty. U N:1 mapování (víc starých portů sdílí jeden nový LAG port, viz
`interface_mapping` výš) se cíle vezmou ze **všech** mapovaných `pre` snímků najednou —
sjednocení ARP/ND záznamů, dedup podle IP (první výskyt vyhrává, pořadí dané pořadím
mappingů v `run.yml`). Ve výsledném snapshotu má takový cíl `resolved_from: baseline-arp`
(IPv4) nebo `baseline-nd` (IPv6), na rozdíl od běžného `arp`/`nd`. Když žádný `pre` snímek
spárovaného portu neexistuje (nebo capture běží mimo `--run`), spadne se na dnešní chování —
vlastní ARP/ND nového zařízení, případně `subnet-fallback` — a na stderr se vypíše `pre
snimek nenalezen, ping cile z vlastni ARP`. Vlastní adresy a virtual-gateway se z cílů
vylučují jako dosud, ať jsou zdrojem baseline nebo vlastní tabulky.

### Vyhodnocení runu (`evaluate --run`)

```bash
.venv/bin/mig-validate evaluate --run mig01
```

| přepínač | význam |
|---|---|
| `--run` | název run adresáře; vzájemně vylučné s `--snapshot` i s `--output` |
| `--run-root` | kořen run adresářů, výchozí `runs` |
| `--ports` | čárkou oddělený seznam portů — omezí, které `post`/`rollback` capture se vyhodnotí. U `post` capture na N:1 mapovaném (LAG) portu filtruje podle **starého** portu kroku, ne podle nového LAG portu |

Než začne párovat, `evaluate --run` ověří, že **soubory ze všech záznamů `captures`
v manifestu existují** — chybí-li nějaký, skončí chybou `chybejici soubory snimku: ...` a
nevyhodnotí nic (radši žádný výsledek než výsledek nad neúplnou sadou).

**Párování je jedna evaluace na každý migrační krok** (`pre` capture sama o sobě evaluaci
netvoří, je jen zdroj baseline):

- **`post`** — na N:1 mapovaném portu (víc starých portů sdílí jeden nový LAG port,
  `interface_mapping` viz výš) `evaluate --run` vyrobí **jednu evaluaci na každý mapping**,
  tedy jeden report na migrační krok, ne jeden na celý LAG port. Baseline každého kroku je
  `pre` snímek **jeho starého portu**. Není-li capture vázaná na port, nebo pár v mapování
  chybí, spadne se na `pre` **celého starého boxu** (capture bez portu). Nenajde-li se ani ten,
  krok se vyhodnotí **bez baseline** (jen stavové checky) a na stderr jde důvod `chybi pre
  snimek stareho boxu`.
- **`rollback`** — baseline je `pre` snímek **téhož zařízení a téhož portu** (rollback se
  porovnává sám se sebou před migrací, ne s protějškem na druhé straně). Chybí-li, stejně tak
  se vyhodnotí bez baseline s důvodem `chybi puvodni pre snimek stejneho zarizeni a portu`.

Pro každou evaluaci se vytiskne záhlaví `=== <subject snapshot> vs <baseline snapshot|"bez
baseline"> ===`; `[krok STARY_NODE:STARY_PORT -> NOVY_NODE:NOVY_PORT]` se k němu připojí,
právě když evaluace vznikla z mapovaného kroku (`post` na mapovaném portu) — **nezávisle na
tom, jestli se baseline našla**. I `post` bez baseline (chybějící `pre` snímek) tak stále nese
`[krok ...]`, jen s `bez baseline` místo jména snímku. `rollback` krok nenese nikdy, protože
`rollback` evaluace se z mapování nestaví (baseline je `pre` téhož zařízení a portu, ne
protistrana). Stejné rozlišení tiskne i vlastní text report (viz
`docs/cs/files/reporting.md`). Pod záhlavím normální výstup `evaluate` (text
nebo `--format json`, `--filter`, `--status`, `--detail`, `--mapping`, `--config` fungují
stejně jako mimo `--run`). Návratový kód je **nejhorší ze všech evaluací** — jeden FAIL
v kterékoliv z nich vrátí kód 1, i když zbytek runu prošel.

**Filtr přes baseline u N:1 mapování.** Když je na jednom LAG portu potkáno víc služeb, než
kolik jich patří ke kroku (baseline starého portu), krok vyhodnotí jen ty, které se s baseline
spárovaly — zbytek (služby patřící jiným krokům na stejném LAG portu) se nevyhodnocuje a
report o nich vypíše jen souhrnný řádek:

```
  Dalsi sluzby na ae0 mimo tento krok: 7 (nesparovano s baseline ge-0/0/0)
```

### Přehled runu (`status`)

```bash
.venv/bin/mig-validate status --run mig01
```

Vypíše tabulku párů starý↔nový port (z `interface_mapping`, plus řádek na celoboxové capture
bez portu) a tři sloupce `PRE`/`POST`/`ROLLBACK` s `ano`/`-` podle toho, jestli pro danou
dvojici a fázi existuje záznam v `captures`. Slouží jako rychlá kontrola před `evaluate --run`
— vidět, co ještě chybí nasnímat, bez nutnosti procházet `run.yml` ručně.

| přepínač | význam |
|---|---|
| `--run` | název run adresáře (povinné) |
| `--run-root` | kořen run adresářů, výchozí `runs` |

---

## 4. Jak číst výstup

Skutečný výstup z laboratorního běhu (zkráceno — souhrnná tabulka má ve skutečnosti
11 řádků, zde jen výběr; 10 z těch 11 služeb není `PASS`, takže se automaticky rozbalí do
plného bloku — ukázaný je jen jeden, zbylých devět je vynecháno):

```
Migrace: 172.20.20.4 (pre-migration) -> 172.20.20.5 (post-migration)

  Sluzby:  1 PASS   6 WARN  4 FAIL  0 SKIP
  Checky: 83 PASS  32 WARN  8 FAIL  9 SKIP
  Sparovano 8 sluzeb, 2 nesparovana v baseline, 3 nesparovane v subject

STAV  SLUZBA                               TYP      STARY PORT   NOVY PORT    RI                        NALEZ
WARN  EVPN-VPWS-CPE13-NNI                  E-Line   ge-0/0/2.213 et-0/0/8.213 EVPN-VPWS-CPE13-NNI       et-0/0/8: input_pps kleslo o 100 % (9 -> 0), prah je -60 %
FAIL  INTERNET-CPE13-NNI                   Internet ge-0/0/2.13  et-0/0/8.13  -                         152.11.13.2: stav Connect, ocekavano Established
FAIL  L3VPN-CPE13-NNI                      IPVPN    ge-0/0/2.113 et-0/0/8.113 L3VPN-CPE13-NNI           198.11.13.2: stav Connect, ocekavano Established
PASS  svc:lo0.0:Core                       Core     -            lo0.0        -

======================================================================================================================
 FAIL  INTERNET-CPE13-NNI   Internet   ge-0/0/2.13 -> et-0/0/8.13   RI: -
======================================================================================================================
 STAV | CHECK                                      : POST (et-0/0/8.13)                      | ZMENA PROTI ge-0/0/2.13
 -----+--------------------------------------------+-----------------------------------------+------------------------
 PASS | Interface errors (et-0/0/8)                : bez chyb                                |
 PASS | Interface errors (et-0/0/8.13)             : bez chyb                                |
 PASS | Interface admin status (et-0/0/8)          : Up                                      |
 PASS | Interface operational status (et-0/0/8)    : Up                                      |
 PASS | Interface admin status (et-0/0/8.13)       : Up                                      |
 PASS | Interface operational status (et-0/0/8.13) : Up                                      |
 WARN | Interface traffic in (et-0/0/8)            : 0 pps                                   | bylo 9 pps   -100 %
 WARN | Interface traffic out (et-0/0/8)           : 0 pps                                   | bylo 995 pps   -100 %
 PASS | Interface traffic in (et-0/0/8.13)         : 0 pps                                   |
 PASS | Interface traffic out (et-0/0/8.13)        : 0 pps                                   |

 -- IPv4  152.11.13.1/30 ---------------------------------------------------------------------------------------------
 PASS | ARP                                        : 0c:00:ef:5e:df:01 -> 152.11.13.2        |
 FAIL | BGP status                                 : Connect                                 | bylo Established
 WARN | Ping                                       : 0/5  152.11.13.2 neodpovedel            |

 -- IPv6  2001:abcd:11:13::a/127 -------------------------------------------------------------------------------------
 FAIL | BGP status                                 : Connect                                 | bylo Idle
 PASS | ND                                         : 0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b |
 WARN | Ping                                       : 0/5  2001:abcd:11:13::b neodpovedel     |

NESPAROVANO
  baseline  clab-pop-migration-P1;et-0/0/0 (Core)  zadny kandidat na subject
  baseline  svc:lo0.0:Core                 (Core)  zadny kandidat na subject
  subject   EVPN-VLAN-AWARE-INTERNET       (E-LAN)  nova sluzba, chybi baseline
  subject   clab-pop-migration-P2;et-0/0/0 (Core)  nova sluzba, chybi baseline
  subject   svc:lo0.0:Core                 (Core)  nova sluzba, chybi baseline
```

Co je na tom podstatné:

- **Souhrnná tabulka (`STAV`/`SLUZBA`/`TYP`/`STARY PORT`/`NOVY PORT`/`RI`/`NALEZ`) má jeden
  řádek na službu a ukazuje jen nejhorší nález.** Sloupce `STARY PORT` a `NOVY PORT` jsou
  logické jednotky ze scopu, ne fyzický rodič.
- **Pod tabulkou se automaticky vypíše plný blok pro každou službu, která není `PASS`** —
  není potřeba `--detail`. `--detail` navíc rozbalí i bloky u služeb se stavem `PASS`.
- **Blok jde shora dolů: řádky vázané na rozhraní (stav, countery, provoz), pak sekce `IPv4`,
  pak `IPv6`.** Hlavička sekce nese nakonfigurované adresy a případnou `VGW` adresu u IRB.
- **Rodina, kterou služba nemá nakonfigurovanou, se v bloku neobjeví vůbec** — ani sekcí, ani
  řádkem. Čistě IPv4 služba tedy o IPv6 nemá v reportu ani zmínku. Cena toho rozhodnutí: takový
  check je v reportu k nerozeznání od checku, který prošel.
- **Každý řádek vázaný na rozhraní nese jméno rozhraní v závorce** (`Interface admin status
  (et-0/0/8.13)`). Scope drží fyzické i logické rozhraní, takže bez toho by v bloku stály dvojice
  řádků se stejným popiskem, jinými hodnotami a protichůdnými sloupci `ZMENA`.
- **Sloupec `ZMENA`** ukazuje `bylo <hodnota>` a případně deltu (`-100 %`, `+3`); u checků bez
  baseline (`arp_present`, `ping_reachability`, `interface_state`, ...) zůstává prázdný.
  **Bez načtené baseline se sloupec `ZMENA` nevypisuje vůbec** (viz `--detail` bez `--baseline`).
- Šířky všech sloupců **se počítají z obsahu** — dlouhý název služby, routing instance nebo
  IPv6 adresa se nikdy neořízne. Platí to i pro popisek v sekci `NESPAROVANO`.
- **Souhrn má dva pojmenované řádky, protože počítá dvě různé jednotky.** `Sluzby:` sedí na
  počet řádků tabulky pod ním, `Checky:` je součet přes všechna měření. Rozdíl je velký
  (11 služeb, 132 checků) a dokud nebyl označený, odnesl si operátor číslo, na které se
  nedíval.
- **Filtry (`--filter`, `--status`) přepočítají souhrn za zobrazenou množinu.** Výpis nad
  počty řekne, který filtr běžel a kolik z kolika služeb je vidět
  (`filtr: status=FAIL -- 2 z 11 sluzeb`). Řádek `Sparovano` a sekce `NESPAROVANO` se
  **nepřepočítávají** — a výpis to říká hned pod tím řádkem s filtrem. Ve strojovém výstupu
  je totéž pod klíčem `filtered`.
- **Sekce `NESPAROVANO` se vypisuje vždy**, i když je všechno ostatní zelené, a **filtry se
  na ni nevztahují.** Je to hlavní pojistka proti přehlédnuté službě:
  - `baseline` = služba byla na starém boxu a na novém není → podezření na zapomenutou migraci,
  - `subject` = na novém je něco navíc → nová nebo restrukturalizovaná služba.
- Stavy jsou `PASS` / `WARN` / `FAIL` / `SKIP`. `SKIP` znamená **„nezměřeno"**, ne „v pořádku" —
  chybějící data nikdy nedají `PASS`.

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
| `chyba: snapshot ma schema_version N` | snapshot z jiné verze nástroje; **přesběrejte ho**. Data ze starého snímku dopočítat nejde — snímek verze 2 neobsahuje oblasti `routes` ani `bfd`, takže by nové checky mlčely. Týká se to i uložených běhů `runs/ipv6/` a `runs/ipv6-live-2026-07-29/` |
| `ValueError: ... schema_version 2` při `capture` | inventory YAML je ze starší verze parseru; **vygenerujte ji znovu** (`mx_parser.py` / `evo_parser.py`). Inventory verze 2 nemá pole `static_route` ani `bfd`, takže by služba vypadala, že žádný záměr nemá |
| Služba má `FAIL … neni v tabulce` u statické routy | routa **je** v konfiguraci, ale v routovací tabulce chybí — typicky proto, že se její next-hop stal nedosažitelným (deaktivované rozhraní). Je to nález, ne chyba nástroje |
| `SKIP … BGP neni Established` u BFD | BFD je nakonfigurované, ale BGP peer ještě nenaběhl. BFD bez BGP naběhnout nemůže, takže se stav nehlásí jako chyba |
| Všechno je `SKIP` | typicky selhaly collectory — podívejte se do `capture.collectors` ve snapshotu |
| Služba chybí ve výpisu | není to migrovaný typ služby (`Internet`, `IPVPN`, `E-Line`, `E-LAN`, `Core`), je to management rozhraní, nebo je v `ignore` v `mapping.yml` |
| Hodně `ambiguous` v `NESPAROVANO` | duplicitní `description` na zařízení — dopárujte přes `mapping.yml` |
| `interface_traffic` hlásí `input_pps 0 pps` / `output_pps 0 pps` na čerstvě migrované službě | očekávatelné, pokud přes port ještě nic nejede; check je `advisory`, tedy WARN, ne FAIL |

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
  (`input_pps 0 pps`, `zadny kandidat na subject`). Je to kvůli terminálům, kde na diakritiku
  není spoleh. Tahle dokumentace je proti tomu psaná normální češtinou; ukázky výstupu a
  citované hlášky jsou vždy doslovné, aby se daly grepovat.
