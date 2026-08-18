# Ping opravy z produkce — design

Datum: 2026-08-18. Vychází z produkčního testování: tři nálezy na ping
testech. Vše se týká odvozování cílů a renderu v jednom toku, proto
jeden spec.

## Kontext a vztah k LAG specu

Spec 2026-08-17 (LAG migrace po krocích) předepsal pro ping cíle
sjednocení ARP/ND ze všech pre snímků starých portů namapovaných na
nový port + povinný dedup. **Obojí už je implementováno**:
`cli.py:510-527` sbírá pre snímky přes `manifest.mapped_olds()` (N:1)
a `capture.py:_merged_baseline_entries()` sjednocuje ARP/ND s dedupem
podle IP (první výskyt vyhrává, pořadí určuje pořadí mappingů).
Tento spec ho proto neřeší znovu — jen na něj navazuje: vlastní
odvozování cílů (`probes/ping.py`) a render (`checks/reachability.py`)
od teď vlastní tento spec. Deferred minor z fáze 4 „dedup baseline
ping kandidátů" je tímto uzavřen jako hotový.

## Rozhodnutí (uzavřeno v brainstormu)

- Cílová adresa se vypisuje i u úspěšného pingu, ne jen u selhání.
- Explicitní source adresa se ruší **všude** (všechny tiery) — router
  volí egress adresu per cíl; produkční testy ukázaly, že je to
  spolehlivější než první adresa z `source_address()`.
- Pole `source` ze snapshot záznamů `probes.ping` **zmizí** (žádné
  mrtvé `null`).
- `.local` záznamy (remote-PE v EVPN VLAN-AWARE) se z ping cílů
  vylučují **tiše a ve všech tierech** (živý ARP/ND i baseline).
  Ve faktech a v ARP/ND checkách zůstávají viditelné beze změny —
  jen se nepingují.
- Žádný report řádek o počtu vynechaných `.local` adres — je to
  normální EVPN chování, ne nález.

## 1. Cílová adresa u úspěšného řádku

`checks/reachability.py`, `_ping_findings`. Dnes:

- OK: `value = "5/5  2.1 ms"` — cíl chybí,
- BROKEN: `value = "0/5  10.1.1.1 neodpovedel"` — cíl je.

Nově OK řádek nese cíl na konci: `value = "5/5  2.1 ms  10.1.1.1"`
(bez RTT `"5/5  10.1.1.1"`). BROKEN větev beze změny. `message` i
`subject` cíl nesly už dřív, mění se jen sloupec HODNOTA.

## 2. Zrušení explicitní source adresy

`probes/ping.py`:

- `source_address()` se maže. `PingTarget` ztrácí pole `source`,
  `to_dict()` klíč nevydává, `run_ping` `source` do RPC nepředává.
- Self-ping guardy přestávají porovnávat proti `source` a všude
  používají existující `own_addresses` (všechny local + VGW adresy
  scope):
  - arp/nd tier: `address != source` → `ip_address(address) not in
    own_addresses`. Přísnější než dnes — druhá vlastní adresa dnes
    guardem projde.
  - baseline tier: redundantní podmínka `!= source` odpadá,
    `own_addresses` už tam je.
  - `subnet_fallback`: `owned` dostane všechny vlastní adresy
    (local + VGW), aby nikdy nevrátil vlastní IP — dnes to jistil
    ještě `fallback != source`.
- Docstringy o párování rodiny source↔cíl a o VGW-as-source self-pingu
  se přepíšou — ta historie s odchodem source neplatí.

**Snapshot schema: bez bumpu, zůstává 9.** `Snapshot.from_dict` bere
jen přesnou shodu verze, bump by zneplatnil všechny existující snímky
rozběhnutých migrací. Pole `source` nikdo nečte (reachability check
ho ignoruje), takže staré snímky s klíčem i nové bez něj se evaluují
stejně — smíšený pár pre(9, se source) × post(9, bez source) funguje.

## 3. Vyloučení `.local` (remote-PE) záznamů

Kolektory už oddělují `irb.612 [.local..9]` na `interface="irb.612"`
a `learned_via=".local..9"` (`collectors/arp.py:split_learned_via`,
ND ukládá stejně). Nový helper v `probes/ping.py`:

```python
def _is_remote_learned(entry) -> bool:
    return (entry.get("learned_via") or "").startswith(".local")
```

Prefix, ne substring — `ae0.14` v `learned_via` projít musí.
Aplikuje se na třech místech `resolve_targets`:

- živý ARP tier (IPv4),
- živý ND tier (IPv6),
- `_baseline_addresses` — baseline páruje jen podle subnetu, bez
  filtru by remote-PE hosty ze starého boxu vrátil při cutoveru.

Fakta (`facts["arp"]`, `facts["nd"]`) a checky `arp_present` /
`nd_present` se nemění — záznamy zůstávají v reportu vidět.

Rozhodnutí doplněno po produkční otázce: pokud jsou pro scope a rodinu
jediným ARP/ND důkazem `.local` záznamy, subnet-fallback se nespouští —
fabrikovaný cíl by generoval falešný FAIL, i když hosti demonstrativně
žijí za vzdáleným PE. Prázdné tabulky (žádný důkaz) fallback pouštějí
dál beze změny. Potlačení je per rodina — `.local` důkaz v ND (IPv6)
nesmí zastavit IPv4 fallback a naopak.

## Testy (TDD)

- OK řádek pingu nese cíl; s RTT i bez RTT; BROKEN beze změny.
- `resolve_targets` nevrací `source` (pole neexistuje) a `run_ping`
  RPC kwargs `source` neobsahují.
- Self-ping guard drží bez source: druhá vlastní adresa scope se
  nevrátí jako cíl ani z ARP/ND, ani z baseline, ani ze
  subnet-fallbacku.
- `.local` vyloučení: živý ARP, živý ND i baseline tier; prefix match
  (`learned_via="ae0.14"` projde, `".local..9"` ne; chybějící/None
  `learned_via` projde).
- Stávající fixtures s `source` hodnotami a starým tvarem OK řádku se
  upraví vědomě (per test), ne mechanickým sed.

## Mimo rozsah

- Report řádek o vynechaných `.local` adresách (zamítnuto).
- Per-target výběr source podle subnetu (zamítnuto — reimplementace
  toho, co dělá router).
- Union/dedup baseline snímků — už implementováno, viz Kontext.
