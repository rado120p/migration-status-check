# EVPN-VPWS local-switch + AC filtr (E2) a matcher (E5) — design

Datum: 2026-09-25

## Cíl

Dvě chyby, kvůli kterým služba dostane cizí nebo žádné výsledky:

- **E2 + local-switch (bump schématu 14 → 15).** Check `evpn_vpws_status`
  projde všechna AC instance, ne jen AC služby, a baseline páruje pozicí.
  V provozu jsou dvě AC v jedné evpn-vpws instanci typicky **lokálně
  přepínaný** EVPN-VPWS a u toho check dnes hlásí dva BROKEN řádky „remote
  peer chybi“. Když byla lokálně přepínaná i baseline, `unchanged_or` z nich
  udělá **UNCHANGED** – blok zezelená, a partnerské AC ani cross-connect
  nikdo nezkontroloval (false PASS).
- **E5 (jen vyhodnocení).** Matcher zahodí scope natrvalo, když je pod
  některým pravidlem nejednoznačný. K pravidlům, která by ho rozlišila
  (`routing_instance`, `subnet`, `vlan`), se už nedostane. Ve step běhu
  takový subjektový scope skončí v `excluded_services`, takže nemá checky
  a není ani v NESPAROVANO.

Podklady:

- `docs/superpowers/review-2026-09-24-kolize-klicu.md`, nálezy E2 a E5
- `docs/superpowers/lab-captures/2026-09-25/`:
  - `ptx1-pop1_evpn_vpws_local_switch_config.xml` – konfigurace
    EVPN-VPWS-LOCAL (et-0/0/8.211 local 100 remote 200, et-0/0/8.212
    local 200 remote 100)
  - `ptx1-pop1_evpn_vpws_local_switch.xml` – EVO 25.2R1.8, `show evpn
    vpws-instance`
  - `mx1-pop1_evpn_vpws_local_switch.xml` – MX 24.2R1-S2.5, totéž na
    ge-0/0/2.211/.212

## Uzavřená rozhodnutí (nerelitigovat)

Z brainstormu 2026-09-25:

- **Local-switch patří do této vlny** a řeší se sběrem explicitních polí
  (varianta a), ne odvozením z SID sousedního AC (to by porušilo „stav se
  nikdy nefabuluje“). Za cenu bumpu 14 → 15. Raw XML má dnes jen
  `test-no-inventory`, takže bump je levný.
- **Local-switch se pozná z operačních dat**, ne z konfigurace. Remote SID
  blok místo PE nese `evpn-vpws-sid-local-interface-name` / `-status`.
  Obě platformy to mají stejně (MX i EVO ověřeno v laborce). Parser
  konfigurace subtyp local-switch **nedostane**.
- **`evpn-vpws-pseudowire-status`** vypisuje jen EVO (`CCC-Up` u
  vzdáleného PW i u local-switch). MX ho nemá ani u vzdáleného PW. Řádek
  PW stavu vzniká **jen když element existuje**. Na MX žádný řádek, ani
  SKIP.
- **AC ze scopu, které ve výpisu instance chybí, je BROKEN**, ne SKIP.
- **Párování baseline AC podle (local SID, remote SID)**, ne podle pozice.
- **Matcher: nejednoznačný scope propadá dál volně (varianta A).** Pozdější
  pravidlo ho smí spárovat s jakýmkoli zbylým scopem. Stejně se dnes chová
  scope bez shody popisu. Konfidence páru je konfidence pravidla, které
  pár udělalo.
- **mapping.yml: `Selector` dostane `routing_instance`.** Nejednoznačné
  ruční pravidlo se chová jako dnes (scopy nespárované s důvodem).

## 1. Collector `evpn_vpws` (tvar faktů, schéma 15)

`collectors/evpn.py`, `EvpnVpwsCollector._interface` / `_sid`. Klíč
instance a seznam `interfaces` zůstávají. Každé AC dostane dvě pole:

```
{
  "name": "et-0/0/8.211",
  "status": "Up",
  "mode": "single-homed",
  "pseudowire_status": "CCC-Up",          # <evpn-vpws-pseudowire-status>, None když chybí (MX)
  "local_sid":  {"value": 100, "peers": [], "local_interface": None},
  "remote_sid": {"value": 200, "peers": [],
                 "local_interface": {"name": "et-0/0/8.212", "status": "Up"}},
}
```

- `local_interface` se čte v `_sid` z `evpn-vpws-sid-local-interface-name`
  a `-status` přímo pod `evpn-vpws-sid-local` / `evpn-vpws-sid-remote`.
  Když chybí jméno, je `None`. Pole nese oba SID bloky kvůli jednotnému
  tvaru, v nahrávkách je vyplněné jen u remote.
- Stav partnera bez textu → `"unknown"` (jako `status` rozhraní).
- `peers` beze změny. U vzdáleného PW je `local_interface` `None`.

## 2. Výběr pro službu (`Scope.select`) – filtr AC (E2)

`models/scope.py` (dnes ř. ~325): instance se dál vybírá podle
`selectors.routing_instances`, ale `interfaces` se zúží na AC, pro která
platí `selectors.matches_interface(ac["name"])`. Stejný výběr prochází
baseline scope, takže v praxi má každá strana jedno AC.

Instance, ve které po filtru nezbylo žádné AC, se do výběru **dostane
s prázdným seznamem**. Check z ní pozná „AC ve výpisu chybí“. Rozlišit to
musí od stavu „instance ve faktech vůbec není“, který zůstává SKIP
„bez dat“.

## 3. Check `evpn_vpws_status`

`checks/evpn.py`, `EvpnVpwsStatusCheck`.

### Párování s baseline

Uvnitř instance (jméno RI migraci přežije, páruje se dál jménem):

1. AC se páruje s baseline AC se stejnou dvojicí
   `(local_sid.value, remote_sid.value)`. Dvojice je jednoznačná
   i u zrcadlených local-switch SID (100/200 vs 200/100).
2. Když se dvojice nenajde a obě strany mají právě jedno AC, spárují se
   ta dvě. Změněné SID pak ukáže ZMENA na řádku SID value.
3. Jinak AC baseline nemá.

Poziční `idx` a jeho komentář zmizí. `_find_baseline_peer` (páruje PE
podle ipaddr uvnitř SID) zůstává, jen docstring přestane mluvit
o pozičním párování SID.

### Řádky pro jedno AC

Pořadí a popisky existujících řádků se nemění, přibývají dva nové:

| Řádek | Kdy | Outcome |
|---|---|---|
| `EVPN VPWS local interface status` | vždy | beze změny |
| `EVPN VPWS pseudowire status` (nový) | jen když `pseudowire_status` není `None` | `CCC-Up` → OK, jinak BROKEN přes `unchanged_or` (same = baseline má stejnou hodnotu) |
| `EVPN VPWS SID local value` / `mode` / `peer PE…` | vždy | beze změny |
| `EVPN VPWS SID remote value` | vždy | beze změny |
| `EVPN VPWS SID remote local switch` (nový) | `remote_sid.local_interface` není `None` | stav partnera `Up` → OK, jinak BROKEN přes `unchanged_or` |
| `EVPN VPWS SID remote PE` / `status` | `local_interface` je `None` | beze změny (vč. „remote peer chybi“) |

Řádek local switch:

- `value` = `"<jméno partnera> <stav>"`, např. `et-0/0/8.212 Up`.
- `baseline_value` = totéž z baseline AC, když baseline byla také
  local-switch.
- `same` pro `unchanged_or` porovnává **jen stav**, ne jméno. Partnerský
  IFL se migrací přejmenuje (ge-0/0/2.212 → et-0/0/8.212).

Řádek PW stavu:

- `baseline_value` je `None`, když baseline element nemá (MX). Při
  migraci MX → EVO tedy vždy „bez baseline“, nikdy UNCHANGED.

### Změna tvaru mezi baseline a subjektem

Step běh přesune jen jedno AC local-switch páru. Na novém boxu je z něj
vzdálený PW, SID zůstávají, takže se AC spárují.

- Subjekt dostane řádky PE/status. Jejich `baseline_value` je
  `local switch (<partner> <stav>)` a `same` je False, takže nikdy
  UNCHANGED.
- Opačný směr (baseline vzdálený PW, subjekt local-switch): řádek local
  switch má `baseline_value` = `<ipaddr PE> <status>` prvního baseline
  peeru a `same` False.
- Dnešní větev „remote peer chybi“ počítá `same = baseline_iface is not
  None and not baseline_peers`. Baseline local-switch (bez peerů) by tam
  dala UNCHANGED, proto `same` navíc vyžaduje, aby baseline AC nebylo
  local-switch.

### AC chybí ve výpisu

Instance ve výběru je, ale seznam AC je po filtru prázdný. Vznikne jeden
řádek BROKEN `EVPN VPWS local interface status`:

- text: `<instance>: AC <iface scopu> ve vypisu instance chybi`
- `value` = `Chybi`
- přes `unchanged_or`: same = baseline scope měl stejnou instanci taky
  bez AC

### Kvalifikace popisků

`many` (kvalifikace popisku jménem rozhraní) se počítá z AC po filtru,
v praxi tedy odpadá.

## 4. Matcher (E5)

`scoping/matcher.py`, `match_scopes`.

1. **Nejednoznačnost scope z poolu nevyřadí.** Po každém pravidle se
   z `remaining_*` odeberou jen spárované scopy. Množina `dropped` zůstává
   **uvnitř jednoho pravidla** jako dnešní pojistka pro pravidla s více
   klíči na scope (subnet, vlan). Scope nejednoznačný pod jedním klíčem
   pravidla se pod jiným klíčem téhož pravidla nespáruje.
2. **Seznam nespárovaných se staví až na konci.** Když je scope pod
   klíčem nejednoznačný, zapamatuje se důvod a scopy **druhé strany**,
   se kterými pod tím klíčem soupeřil (u subjektového scope `b_hits`,
   u baseline scope `s_hits`). Pamatuje se jen první nejednoznačnost,
   tedy ta z nejsilnějšího pravidla. Scope, který nespárovalo žádné
   pravidlo, dostane:
   - svůj důvod nejednoznačnosti, jen když **aspoň jeden** z těch scopů
     druhé strany taky zůstal nespárovaný. Nejednoznačnost pak trvá.
   - jinak `REASON_NO_CANDIDATE` (baseline) / `REASON_NEW_SERVICE`
     (subject). Nejednoznačnost vyřešilo pozdější pravidlo, které
     protějšek spárovalo s jiným scopem, takže tento scope protějšek
     nemá.

   Příklad ze step běhu: baseline (starý port) má „CPE“ ve VRF-a, nový
   box „CPE“ ve VRF-a (tento krok) a „CPE“ ve VRF-b (dřívější vlna).
   Popis je 1×2 nejednoznačný, `routing_instance` spáruje VRF-a ↔ VRF-a.
   VRF-b soupeřil jen s baseline VRF-a, která je spárovaná, takže dostane
   `nova sluzba` a step běh ho dál vyloučí (§5). Důvod „ambiguous“ by ho
   vytáhl do NESPAROVANO a jmenoval by kandidáta, který už má pár.
3. Modulový docstring („při nejednoznačnosti se nikdy nehádá“) platí dál.
   Pár vzniká jen z jednoznačné shody 1:1 pod nějakým pravidlem.

Reprodukce z review: popis „CPE“, VRF customer-a a customer-b, obě
192.168.1.1/30 na obou boxech. Dnes 0 párů, nově 2 páry metodou
`routing_instance+service_type`.

Local-switch pár (dvě AC ve stejné RI): `routing_instance` je
nejednoznačné, rozliší je až `vlan+service_type` (low).

### mapping.yml

`scoping/mapping.py`, `Selector`:

- Nové pole `routing_instance` znamená, že
  `routing_instance in scope.selectors.routing_instances`.
- Platí pro `mappings` i `ignore`.
- `from_dict` ho čte a prázdný selektor je i s ním dál chyba.
- `_apply_manual` beze změny.

## 5. Engine – step běh (E5)

`engine.py` (~ř. 696–707): do `excluded_services` jde nespárovaný
subjektový service scope jen tehdy, když jeho důvod je
`REASON_NEW_SERVICE` (a dál platí `not partner_matched`). Scope
nespárovaný kvůli nejednoznačnosti poběží checky jako nespárovaný
a objeví se v NESPAROVANO. Může totiž patřit tomuto kroku.

## 6. Bump, testy, akceptace

### Bump

`SCHEMA_VERSION = 15` v `models/snapshot.py`, komentář jmenuje
`evpn_vpws` (`pseudowire_status`, `local_interface`). Loader zůstává
exact-match. Běhy s raw XML → `mig-validate upgrade`, běhy bez raw jsou
ztracené (přijato už u 14).

### Testy

- **Collector:**
  - tři lab záznamy z 2026-09-25 jako fixtures
  - PTX RPC: obě AC s `local_interface` na partnera, `pseudowire_status`
    `CCC-Up`
  - MX RPC: totéž s `pseudowire_status` `None`
  - existující fixtures `tests/fixtures/rpc/{junos,junos-evo}/evpn_vpws.xml`:
    `local_interface` `None`, EVO `CCC-Up`, MX `None`
- **Parser:** konfigurace EVPN-VPWS-LOCAL → obě IFL jako E-Line ve stejné
  RI (ověření, že inventory dá dva scopy).
- **Scope.select:** instance se dvěma AC → každý scope vidí jen své AC.
  Scope, jehož AC ve výpisu není, dostane instanci s prázdným seznamem.
- **Check:**
  - local-switch MX → EVO, oba Up → OK, řádek local switch, žádné řádky
    PE/status, PW řádek bez baseline
  - partner Down → BROKEN. UNCHANGED jen když baseline partner byl taky
    Down.
  - případ z review: .101 Down → scope .100 zůstane čistý
  - baseline AC v jiném pořadí → párování podle SID
  - jedno AC na každé straně, změněné SID → spárováno, ZMENA na SID value
  - step: baseline local-switch → subjekt vzdálený PW, i opačně → nikdy
    UNCHANGED
  - baseline local-switch + subjekt bez peerů a bez partnera → BROKEN,
    ne UNCHANGED
  - AC ve výpisu chybí → BROKEN
  - EVO `pseudowire_status` jiný než `CCC-Up` → BROKEN
  - MX → žádný PW řádek
- **Matcher:**
  - reprodukce „CPE“ → 2 páry přes `routing_instance`
  - scope nejednoznačný pod všemi pravidly → nespárovaný s důvodem
    nejednoznačnosti
  - nejednoznačnost vyřešená pozdějším pravidlem (příklad VRF-a/VRF-b
    z §4): nespárovaný scope VRF-b dostane `nova sluzba`, ne
    „ambiguous“
  - pojistka více klíčů v jednom pravidle drží
  - existující testy důvodů nespárování se upraví tam, kde se důvod mění
- **Engine:** step běh, scope nejednoznačný až do konce → v NESPAROVANO
  a má checky, ne v `excluded_services`. Scope s `nova sluzba` dál
  vyloučen, včetně scope VRF-b z příkladu v §4.
- **mapping.yml:** `routing_instance` v `mappings` i `ignore`.
- **Mutanti** jmenovaní v docstrings dotčených testů se po přegenerování
  fixtures pustí znovu.

### Dokumentace

`docs/cs` popisuje tvary faktů a řádky checků. Schéma 15 (`evpn_vpws`:
`pseudowire_status`, `local_interface`), dva nové řádky checku, filtr AC
ve `Scope.select`, propad nejednoznačnosti v matcheru a `routing_instance`
v mapping.yml se promítnou do `docs/cs/files/{collectors,models,checks}.md`,
`docs/cs/reference.md` a `docs/cs/architecture.md`, kde je to relevantní.
Při vrstvě 2 to finální review muselo dohánět (833a04b).

### Akceptace v laborce

Běh `runs/test-no-inventory` (raw z 2026-09-25) už obsahuje všechny
případy. MX1-POP1 pre a PTX1-POP1 post mají:

- EVPN-VPWS-LOCAL: ge-0/0/2.211/.212 → et-0/0/8.211/.212, local-switch
  na obou stranách
- vzdálené PW: EVPN-VPWS-CPE13-NNI ge-0/0/2.213 → et-0/0/8.213 a
  EVPN-VPWS-CPE23-UNI ge-0/0/3.0 → ae0.224

Kroky:

1. `mig-validate upgrade` na kopii `test-no-inventory` a diff reportu
   před/po. Očekávané rozdíly:
   - EVPN-VPWS-LOCAL (.211 i .212): místo „remote peer chybi“ řádek local
     switch OK a PW řádek `CCC-Up`
   - vzdálené PW (.213, ae0.224): přibude jen PW řádek `CCC-Up`. Řádek
     „AC … ve vypisu instance chybi“ se **nesmí** objevit. To je kontrola,
     že filtr `matches_interface` sedí na skutečná jména IFL (`ae0.224`,
     `ge-0/0/3.0`).
   - Jakýkoli jiný rozdíl je nález.
2. Pak skutečný `mig-validate upgrade test-no-inventory` (uživatel).
3. Po merge restart lab GUI.

## Mimo rozsah

- Subtyp local-switch v parseru konfigurace, čtení `vpws-service-id`
  z konfigurace.
- Změna chování nejednoznačného ručního pravidla v `_apply_manual`.
- Ostatní nálezy review: E3, E4, E6–E12.
