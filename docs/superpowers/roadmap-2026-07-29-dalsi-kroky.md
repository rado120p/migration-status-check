# Co dál — stav k 2026-07-29

**Výchozí bod:** větev `ipv6-a-report` je zmergovaná do `main` (`f27a1c6`) a
pushnutá na `origin/main`. 424 testů zelených, 1 přeskočen. Worktree i větev
uklizené.

Tenhle soubor je vstupní bod pro navazující relaci. Historie a důkazy k už
hotové práci jsou v [`roadmap-2026-07-28-ipv6-a-report.md`](roadmap-2026-07-28-ipv6-a-report.md);
sem se opisovat nemají.

**Doporučené pořadí:** vlna 1 (doladění reportu) → vlna 2 (druhý spec) → zbytek.
Důvod je v každé sekci.

---

## Než se začne — jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest                      # 424 passed, 1 skipped
```

Snímky pro ostré ověření jsou **na disku, ne v repu** (`runs/` je v `.gitignore`):

| cesta | co to je |
|---|---|
| `runs/ipv6/` | snímky, o které se opírá roadmapa z 2026-07-28 (F-1, F-9) |
| `runs/ipv6-live-2026-07-29/` | čerstvý capture z ostrého ověření po opravné vlně |

```bash
.venv/bin/python -m migration_validator.cli evaluate \
  --snapshot runs/ipv6/post.json --baseline runs/ipv6/pre.json --detail
```

Nový capture proti laborce (`172.20.20.4` = VMX/junos, `172.20.20.5` =
PTX10002-36QDD/junos-evo, oba `admin` + heslo, **ne** klíč):

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"   # jinak je promenna prazdna
.venv/bin/python -m migration_validator.cli capture \
  --device 172.20.20.4 --inventory 172.20.20.4.yml --phase pre-migration \
  --username admin --auth password --password "$MIG_LAB_PASSWORD" \
  --output runs/<nazev>/pre.json
```

---

## Vlna 1 — doladění reportu

Proč první: **špatné číslo je horší než ošklivé.** Dvě z těchto položek způsobí,
že si operátor odnese jiná čísla, než na která se dívá. A dvě testové mezery
jsou jediné místo, kde by tichá regrese prošla celou sadou.

### F-12 — `--filter` a `--status` tisknou počty za nefiltrovaný běh

`migration_validator/reporting/text_report.py:28` (`filter_result`)

`filter_result()` profiltruje `scopes`, ale `summary` nechá beze změny.

Reprodukce:

```
$ ... evaluate --status fail
  83 PASS   32 WARN   8 FAIL   9 SKIP      <- za cely beh
  (tabulka ma 4 radky)
```

Rozhodnout je potřeba **co má filtr znamenat**: je to pohled (počty zůstanou za
celý běh, ale musí to být z výpisu poznat), nebo nový výpočet (počty se
přepočítají za filtrovanou množinu)? Dnešní stav je první varianta bez toho
označení, což je nejhorší ze všech.

### F-8 — souhrn počítá checky, tabulka pod ním služby

`migration_validator/engine.py:230-241`

`summary` sčítá `check.status` napříč všemi scopy, takže `83 PASS` jsou checky.
Tabulka hned pod tím má 11 řádků, a to jsou služby. Dvě různé jednotky nad
sebou bez označení. Rozdělení podle AR-4 (jeden finding na fakt, ne na
rozhraní) ten rozdíl znásobilo.

Souvisí s F-12 — obojí je „hlavička říká něco jiného než tělo", řeší se spolu.

### F-7 + F-10 + F-15 — řádek říká něco jiného, než má

`migration_validator/reporting/view.py:83` (`_row`), `:55` (`change_text`)

Tři projevy jedné věci, proto dohromady:

- **F-7** — při chybějícím `value` sáhne `_row` po `check.message`, takže do
  sloupce hodnot padají celé věty (`et-0/0/8: bez chyb`, u SKIP i delší).
  Je to přesný opak AR-4.
- **F-10** — finding bez popisku spadne na `check.id`, takže mezi hezkými
  popisky sedí `SKIP | evpn_esi_status`. Pohltí i odloženou drobnost T9a.
- **F-15** — `change_text` nekouká na stav, takže i **SKIP** řádek dostane do
  sloupce ZMENA `bez baseline`, přestože zpráva vedle říká totéž
  (`peer neni v baseline snapshotu`). V ostrém běhu je to 14 z 18 výskytů
  té hlášky.

Po opravě F-5 (popisky nesou jméno rozhraní) jsou všechny tři vidět víc, protože
okolní řádky se zkvalitnily.

### T13a + T13b — fixtures, přes které by rozbitá větev prošla

`tests/probes/test_ping.py:278` a ND testy tamtéž

- **T13a** — pojistka proti self-pingu je testovaná jen s vlastní adresou na
  indexu 0. Ověřeno mutací: `if index > 0 or address != source` projde.
  Dokazuje „pojistka existuje", ne „platí na každý prvek".
- **T13b** — v ND testu se očekávaný cíl rovná tomu, co by vrátil
  `subnet_fallback`, takže rozbitá ND větev by prošla přes fallback.

Obojí je jeden přeuspořádaný fixture. **Zavést mutaci, ověřit že test padne,
teprve pak opravit** — jinak se nic nedokázalo.

### Ještě v téhle vlně, pokud zbude prostor

T1, T2a, T7a, T7b — drobné mezery v pokrytí fixtures, dávkově jedním commitem.

---

## Vlna 2 — druhý spec: BFD a statické routy

Jediná **chybějící schopnost**, ne doladění. Tool dnes BFD ani statické routy
nekontroluje vůbec.

**Tvar je rozhodnutý** (2026-07-28) a nemá se relitigovat: nejdřív **parsovat
z konfigurace do inventáře** a namapovat na služby stejně, jako se dnes mapuje
`bgp_neighbor`, a **teprve pak** sbírat přes RPC.

Tři otevřené otázky, zapsané v
[`specs/2026-07-28-ipv6-a-report-design.md`](specs/2026-07-28-ipv6-a-report-design.md)
v sekci „Otevřené otázky pro navazující spec":

1. **Nemapované statické routy zmizí tiše.** Konfigurace obsahuje statiku
   v `mgmt_junos` (`next-hop 10.0.0.2`), která padne do `10.0.0.15/24` na
   `fxp0.0`; ta je ale management a scope se z ní nikdy nestane.
   `RunResult.unassigned` má na přesně tenhle tvar už `bgp_peers` — přibude
   `static_routes`.
2. **Statická konfigurace není mezi rodinami symetrická.** IPv4 je pod
   `routing-options static`, IPv6 pod `routing-options rib <jmeno>.inet6.0 static`
   (uvnitř VRF stejně). Naivní `//static/route` najde obojí, ale ztratí
   příslušnost k RIB — a právě tu report zobrazuje.
3. **Co znamená změněná routa** oproti chybějící, a jestli je BFD down na
   službě bez nakonfigurovaného BFD stav SKIP, nebo PASS.

Začít brainstormem nad těmito třemi, ne psaním kódu. Otázka 3 je stejná třída
jako R-1/R-2 z minulé vlny — mění, co se má vlastně napsat.

---

## Vlna 3 — až po druhém specu, a proč

### F-2 — název RIB se do reportu nikdy nedostane

`migration_validator/reporting/view.py`, `reporting/text_report.py:_block`

AR-5 předepisuje název RIB na odsazeném podřádku pod `BGP status`. Mechanismus
podřádků v rendereru **neexistuje** a `_row()` `check.details["rib"]` zahazuje.
Peer se dvěma RIB se vykreslí jako dvojice řádků s týmiž popisky, název RIB
nikde. To maří smysl AR-7: operátor vidí pokles, ale ne **ve které** RIB.

**Proč až sem:** potřebuje nový mechanismus podřádků v rendereru, což je větší
zásah — a dává smysl ho navrhnout zároveň s tím, jak se budou zobrazovat routy
z druhého specu. Na dnešní topologii laborky navíc nesepne (každý servisní peer
má jednu RIB), i když nahrané fixtures obsahují peery s 11 RIB.

### F-11 — dvě kopie logiky pro link-local

`migration_validator/probes/ping.py` a `checks/reachability.py`

Už jednou to způsobilo chybu: Task 13b opravoval docstring, který zasel
nepravdivé tvrzení v dokumentaci, zatímco kopie v `ping.py` docstring nemá
vůbec. Rozcházení je pořád na místě.

### F-14 — kosmetika v NESPAROVANO

Sloupec s typem služby není odsazený, sloupec s důvodem je rozházený.

---

## Co je uzavřené a nemá se otevírat

Rozhodnutí z 2026-07-29, implementovaná a zdokumentovaná:

- **R-1 = varianta (c)** — rodina bez konfigurace se v bloku neobjeví vůbec,
  ani sekcí, ani řádkem. Cena je zapsaná v kódu: chybějící check je
  k nerozeznání od toho, který prošel, a nezůstane po něm stopa ani ve
  strojovém výstupu. Přijato vědomě.
- **R-2 = PASS** — BGP relace, která se během migrace zlepšila, není WARN.
- **T9b** — counter `suppressed` vypadl z reportu, damping se v tomhle
  nasazení nepoužívá. Collector ho sbírá dál.

Dál **T3, T4, T5, T6, T10, T12, T2b, T2c** — zahozené s odůvodněním
v roadmapě z 2026-07-28.

**Pozor:** `docs/superpowers/specs/` a `plans/` jsou datované návrhové
artefakty a záměrně se nepřepisovaly. Spec proto u R-2 pořád popisuje původní
chování (WARN). Není to přehlédnutí — platný stav popisuje `docs/cs`, `docs/en`
a roadmapy.

### Zaznamenaný předpoklad

`_usable_nd` bere doslovný řetězec `"incomplete"` jako reálný stav, který Junos
vrací. V nahraných fixtures ani v živém captureu z 2026-07-29 se nevyskytl
(jsou tam `unreachable`, `delay`, `reachable`, `stale`). Ověřit se to bez
zařízení v tom stavu nedá; kdyby předpoklad neplatil, nic se nerozbije.
