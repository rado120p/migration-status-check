# L1 bloky, service-relevantni EVPN vystup a opticke testy

Datum: 2026-08-12
Stav: navrh schvalen v brainstormingu

## Cil

Tri navazujici zlepseni reportu a testu:

1. **Service relevance u EVPN VLAN-AWARE**: blok sluzby tiskne jen radky,
   ktere patri te sluzbe (jeji ESI, jeji EVPN/IRB interfacy, jeji VLANy),
   ne celou routing instanci.
2. **Razeni bloku podle Layer1 portu**: report je seskupeny
   `L1 port -> jeho sluzby`, L1 testy se netisknou duplicitne v blocich
   sluzeb.
3. **Nove L1 testy**: opticke urovne a opticke alarmy z
   `show interfaces diagnostics optics`.

## Schvalena rozhodnuti (nerelitigovat)

- RI-wide agregaty (`EVPN local interfaces (up)`, `EVPN IRB interfaces
  (up)`) se z bloku sluzeb **rusi uplne**; vlastni radky sluzby (`EVPN
  interface ae0.14`, `IRB interface irb.14`) se povysuji z INFO na
  PASS/FAIL.
- L1 port dostava **vlastni renderovany blok**; radky fyzickeho portu
  mizi z bloku sluzeb (radky sub-interfacu zustavaji).
- LAG: **clenske optiky se tahnou pod ae blok** - parsuje se 802.3ad
  clenstvi do inventory (`lag_members`).
- Opticke urovne se posuzuji **deltou proti baseline** (WARN pri posunu
  > 2.0 dB); alarm/warn flagy se tisknou **jen kdyz jsou zvednute**
  (jinak jeden souhrnny radek `bez alarmu`).
- L1 blok se renderuje **jen pro porty, ktere nesou aspon jednu sluzbu**
  (fxp0, nepouzite porty a samostatne LAG cleny se netisknou).

## 1. Layout a razeni reportu

Report je seskupeny po L1 portech (prirozene razeni jmen, `ge-0/0/2`
pred `ge-0/0/10`): blok L1 portu, pak jeho sluzby v dnesnim relativnim
poradi. EVPN L3+L2 pary zustavaji sousede (L3 prvni, jako dnes) a par se
umistuje pod rodicovsky port **L2** interfacu. Sluzby bez L1 rodice
(irb-only, lo0) se tisknou az za vsemi skupinami portu.

L1 blok pouziva existujici block renderer:

```
=====================================================================
 PASS  LAYER1 ae0   EX1-POP1;ae0
=====================================================================
 STAV | CHECK                            : POST (ae0)                | ZMENA PROTI ge-0/0/5
 PASS | Interface admin status           : Up                        |
 PASS | Interface operational status     : Up                        |
 PASS | Interface errors                 : bez chyb                  |
 PASS | Interface traffic in             : 12 pps                    | 11 pps
 PASS | Interface traffic out            : 9 pps                     | 10 pps
 PASS | Interface optical levels (et-0/0/5 lane 0) : RX -5.2 dBm / TX -2.1 dBm | RX d +0.3 dB
 PASS | Interface optical alarms                   : bez alarmu                |
```

- Kvalifikator `(ae0)` v labelu je uvnitr L1 bloku redundantni a
  vypousti se; u LAG clenu zustava jmeno clena (to je pointa radku).
- Status L1 bloku = jen jeho vlastni checky; selhani sluzby se do
  verdiktu portu neroluje.
- Radek `INFO | Interface errors / traffic : mereno na L2 (...)` v L3
  bloku EVPN paru zustava.

**Baseline parovani L1 bloku:** stary port (`ge-0/0/5` na baseline
zarizeni) a novy port (`ae0`) maji jina jmena i popisy, primy match
nefunguje. Parovani se odvozuje od deti: kdyz sluzby pod post portem
`ae0` byly sparovany s baseline sluzbami, jejichz rodic byl `ge-0/0/5`,
sparuji se i L1 scopy (pri neshode deti rozhoduje vetsina). Nesparovany
L1 blok pada na `bez baseline`. Prave tohle parovani umoznuje port-level
otazku migrace: "preteklo traffic na novy port?"

## 2. EVPN service-relevance filtr

Vsechno check-side v `EvpnInstanceStatusCheck` (`checks/evpn.py`).
Identita sluzby se odvozuje z toho, co check uz ma: L2 unity ze
selektoru scope (`ae0.14`), IRB unit z L3<->L2 linku (`irb.14`), VLANy
z `customer_vlan` / bridge-domain selektoru.

| radek | zmena |
|---|---|
| `EVPN local interfaces` + `... up` | rusi se |
| `EVPN IRB interfaces` + `... up` | rusi se |
| `EVPN interface` | jen vlastni unity sluzby; INFO -> PASS/FAIL (Up/Down); `.local..64` se netiskne |
| `IRB interface` | jen linkovany irb unit; INFO -> PASS/FAIL |
| `EVPN neighbors` (pocet) | zustava (remote PE jsou service-relevantni) |
| `EVPN neighbor` (adresy) | presouva se primo pod radek `EVPN neighbors` (cista zmena poradi emise, view zachovava poradi checku) |
| `ESI {esi}` (listing) | jen ESI, jejichz status jmenuje IFL sluzby (`Resolved by IFL ae0.14`); ostatni vcetne unresolved se netisknou - vlastni ESI zdravi sluzby uz posuzuje DF radek (`evpn_esi_status`), jehoz data jsou interface-filtrovana dnes |
| `VL-x MAC count` | jen VLANy sluzby |
| `VL-x Interface y MAC count` | jen unit sluzby (a jeho VLAN) |

Pojistka: scope bez interface selektoru filtr vypina a tiskne vse jako
dnes.

Dodatek (2026-08-12, review Tasku 2): kdyz je filtr aktivni a vlastni
unit sluzby v local_interfaces instance vubec NENI (IFL se do mac-vrf
nedostal - realny selhany stav migrace), blok by jinak nevypsal zadny
`EVPN interface` radek a chyba by byla neviditelna. Proto se pro
nenalezeny vlastni unit emituje BROKEN radek `{unit} chybi v instanci`;
totez pro linkovany IRB unit. Plain single-VLAN E-LAN je filtrem fakticky nedotcen (vse v jeho
instanci je jeho).

## 3. Layer1 jako plnohodnotny scope

- **Builder** (`scoping/builder.py`): pro kazdy Layer1 zaznam, jehoz
  port je rodicem aspon jedne sluzby, vznika scope `l1:{port}`,
  `service_type=Layer1`, selektory = port + `lag_members`. Dosavadni
  role Layer1 zaznamu (tagovani `physical_interfaces` na service
  scopech) zustava - matching a L2/L3 linkovani se nemeni.
- **Gating checku** (`checks/ifaces.py`): `InterfaceStateCheck`,
  `InterfaceErrorsCheck`, `InterfaceTrafficCheck` deli odpovednost podle
  typu scope - v Layer1 scopu emituji jen radky fyzickeho portu, v
  service scopu jen radky sub-interfacu (deduplikace). Ostatni checky
  (BGP, ARP, ping, EVPN...) uz gatuji pres `service_types`, Layer1 scope
  prirozene preskoci.
- **Plumbing**: identity payload (`engine._identity`) dostava rodicovsky
  port (renderer dnes rodice bloku vubec nezna); post-match reorder v
  enginu roste o seskupovaci pruchod z kap. 1.

## 4. Opticky kolektor a checky

**Krok nula, pred kodem:** overit skutecne RPC jmeno pro
`show interfaces diagnostics optics` na lab PTX/EVO pres
`| display xml rpc` a nahrat surove XML (domaci pravidlo po minulem
hadani RPC; fixture pro test kolektoru ho stejne potrebuje).

**Kolektor** (`collectors/optics.py`, nova fact area `optics`, obe
platformy): per fyzicky interface seznam lanes.

- ACX/EVO hlasi vzdy `optics-diagnostics-lane-values`; MX ma i
  bez-lane moduly (`optics-diagnostics`) - ty se ukladaji jako jedna
  lane s `lane: null`.
- Dve MX varianty RX power (`laser-rx-optical-power-dbm` vs
  `rx-signal-avg-optical-power-dbm` u koherentnich modulu) se **slucuji
  pri parsovani** do jednoho pole `rx_power_dbm` - downstream rozdil
  nikdy nevidi.
- Alarm/warn flagy se parsuji case-insensitive (`off`/`Off`) na
  booleany; teplotni flagy (ACX je ma, MX ne) se parsuji, kdyz jsou.
- Port bez optiky v odpovedi v aree proste neni.

**Checky** (`checks/optics.py`, `service_types={"Layer1"}`):

- `Interface optical levels` - radek per port+lane, hodnota
  `RX -5.2 dBm / TX -2.1 dBm`. S baseline (pres child-derived parovani)
  WARN pri posunu RX nebo TX > 2.0 dB (pevna konstanta, zadny config
  knob). Bez baseline informativni (`bez baseline`). Port bez optickych
  dat: jeden SKIP radek.
- `Interface optical alarms` - tichy default: jeden `PASS ... bez alarmu` radek
  per port. Jen zvednute flagy se tisknou jako dalsi radky s Junos
  nazvy (`laser-rx-power-low-alarm`...): alarm -> FAIL, warn -> WARN.

Snapshot schema 7 -> 8 (`from_dict` stare snapshoty tvrde odmita,
`runs/mig01` je nutne presnimat - uz tak planovane).

## 5. Inventory a config parser

`ServiceEntry` dostava `lag_members: list[str]` (default prazdny -
stare inventory YAML se nacitaji dal, cistne aditivni pole). Parser
konfigurace cte 802.3ad clenstvi z `gigether-options`/`ether-options`
clenskych portu a reverzni mapovani pripina na Layer1 zaznam bundlu
(`ae0` -> `[et-0/0/5, ...]`). Clenske porty nenesou zadne sluzby, takze
pravidlo "jen porty se sluzbami" je automaticky drzi mimo samostatne
bloky - objevi se jen jako opticke radky pod ae blokem.

## 6. Testovani

TDD. Nove unit pokryti:

- builder emituje L1 scopy jen pro porty s detmi;
- razeni v enginu (seskupeni po portech + preziti L3/L2 sousednosti);
- EVPN filtr - test per trida ruseneho/drzeneho radku (vcetne dropu
  `.local..64`, povyseni INFO -> PASS/FAIL a pojistky bez selektoru);
- MAC-count filtr per VLAN/unit;
- opticky kolektor proti nahranemu lab XML (`rpc_fixture` skipuje,
  dokud XML neni - dorazi po nahravce);
- opticke checky na syntetickych faktech vcetne slouceneho RX a
  bez-lane MX modulu;
- parsovani LAG clenstvi.

Synteticky snapshot v `tests/conftest.py` dostava areu `optics` a
`COLLECTOR_NAMES` nove jmeno (docstring varuje: zapomenuti meni SKIP na
FAIL). End-to-end invarianty rostou o "kazdy blok sluzby ma pred sebou
blok sveho L1 rodice". Dle stale poznamky: mutanty dotcene
regenerovanymi fixtures se pousteji znovu a mutant-kill docstringy se
overuji spustenim mutanta.

## Zamitnute alternativy

- **Filtr v `Scope.select`** misto check-side: kolektor pocita
  `total/up` pred selekci, select-side filtr by nechal pocty
  nekonzistentni se zaznamy a menil by obsah JSON snapshotu.
- **L1 hlavicka syntetizovana jen v rendereru**: neumi spoustet checky,
  takze by nemohla vlastnit verdikty optiky ani baseline srovnani.
- **Optika jen na fyzickych portech (ae bez optiky)**: zamitnuto
  uzivatelem ve prospech tazeni clenskych optik pod ae blok.
- **Render vsech Layer1 portu**: sum u portu irelevantnich pro migraci.
- **Posuzovani urovni proti prahum modulu z XML**: duplikuje on-box
  flagy.
