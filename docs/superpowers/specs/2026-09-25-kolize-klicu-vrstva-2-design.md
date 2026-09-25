# Kolize klíčů – vrstva 2 (bump schématu 13 → 14) — design

Datum: 2026-09-25

## Cíl

Collectory `bgp`, `bfd`, `evpn_esi` a `routes` klíčují fakta tak, že se
dvě legitimní položky na jednom boxu přepíšou (poslední vyhrává), a
`pim_neighbor` nechá IPv6 souseda přepsat IPv4 souseda na stejném
rozhraní. Vrstva 2 mění tvar faktů tak, aby každý záznam nesl celou svou
identitu a nic se nepřepsalo, a na to navazuje výběr faktů pro službu,
NEZARAZENO a checky.

Podnět: ostrý běh MX → ACX 2026-09-23 (customer-a a customer-b mají peera
192.168.1.2 každý ve své VRF, baseline customer-b nesla session
customer-a). Podklady:

- `docs/superpowers/followup-2026-09-23-kolize-adres-peeru-vrstva-2.md`
- `docs/superpowers/review-2026-09-24-kolize-klicu.md` (nálezy F1–F8,
  E1–E12, ověření v laborce)
- `docs/superpowers/lab-captures/2026-09-24/` (PIM dual-stack, ESI ve dvou
  instancích, ESI se dvěma IFL)

## Uzavřená rozhodnutí (nerelitigovat)

Z 2026-09-24 (review + odpovědi uživatele):

- Rozsah bumpu: F1 bgp, F2 bfd, F3 evpn_esi, F5 pim_neighbor (jen v4),
  F8 routes (static + aggregate na stejném (rib, prefix) jde – potvrzeno).
- Mimo rozsah: F4 ASM (S,G,rpt), F6 IS-IS broadcast / L1+L2, F7 LDP
  multi-neighbor, IPv6 PIM.
- BFD `extensive` u multihop session instanci nenese – dvě multihop
  session na stejnou adresu se nerozliší, hlásí se jako nejednoznačné.
- Per-port ESI se v provozu očekává; ESI blok vypisuje jen jeden lokální
  IFL i při `num-local-intf` = 2, per-IFL stav nese jen
  `evpn-interface-status-table`.
- Žádný shim 13 → 14. Snímky s raw XML přegeneruje `mig-validate upgrade`,
  snímky bez raw záznamu jsou ztracené (uživatel přijal).
- Device režim je pryč (spec 2026-09-24), vrstva 2 řeší jen per-service
  bloky.

Z brainstormu 2026-09-25:

- **Tvar faktů: seznam záznamů s celou identitou** (jako `arp`/`nd`) pro
  `bgp`, `bfd`, `evpn_esi`, `routes`. Ne vnořené slovníky.
- **Pohled pro službu (`Scope.select`) si nechává dnešní tvar** klíčovaný
  adresou / ESI. Scope má jedno rozhraní a nejvýš jednu RI
  (`scoping/builder.py:97-101`), takže uvnitř služby je klíč jednoznačný
  z konstrukce. Checky, popisky řádků, id řádků v JSON a GUI zůstávají.
- **BGP záznam nese `local_interface`** (`<local-interface-name>`). Ukládá
  se hned, vlastnictví ho zatím nepoužívá (E10 je samostatná vyhodnocovací
  vlna, bez dalšího bumpu).
- **PIM soused bez `ip-protocol-version` = IPv4.** Zahazuje se jen
  explicitní `6`.
- **ESI stav per IFL z `evpn-interface-status-table`** (`Up`/`Down`),
  ne `Up/Forwarding` z ESI bloku. Přípona `/Forwarding` / `/Blocking` se
  ztrácí; v all-active je vždy Forwarding, ne-DF v single-active říká DF
  řádek.
- **Kolizní mašinérie vrstvy 1 zaniká.** Když obě session přežijí,
  pravidlo „náš peer v cizí instanci“ by našlo session customer-a
  v customer-b a hlásilo falešnou kolizi. Nahrazuje ji úzká značka
  nejednoznačnosti (dva záznamy projdou vlastnictvím na jednu adresu).

## 1. Tvar faktů v snímku (collectory)

`SCHEMA_VERSION = 14`. `LIST_AREAS` v `capture.py` dostane `bgp`, `bfd`,
`evpn_esi`, `routes` – selhaný collector zapíše `[]`.

RPC ani jejich kwargs se **nemění** – replay `call_key` sedí a
`mig-validate upgrade` přegeneruje každý běh s raw XML. Všechna nová pole
jsou v dnešních nahrávkách.

### bgp – záznam na `<bgp-peer>`

```json
{"address": "192.168.1.2", "routing_instance": "customer-a",
 "local_interface": "ae0.100", "state": "Established", "peer_as": 65001,
 "ribs": {"customer-a.inet.0": {"received": 3, "accepted": 3,
          "advertised": 5, "active": 3, "suppressed": 0}}}
```

- `address` bez portu (`strip_port`), `routing_instance` s dnešní
  normalizací (master/default/"" → `null`).
- `local_interface` z `<local-interface-name>`; chybí-li, `null`.

### bfd – záznam na `<bfd-session>`

```json
{"neighbor": "192.168.1.2", "interface": "ae0.100", "multihop": false,
 "state": "Up", "remote_state": "Up", "local_diagnostic": "None",
 "clients": ["BGP"], "detection_time": "0.900",
 "transmission_interval": "0.300", "multiplier": 3}
```

- Prázdné `<session-interface/>` → `interface: null`.
- `multihop` = `<session-type>` obsahuje „Multi hop“ (nahrávka:
  `Multi hop BFD`). Když `session-type` chybí, rozhoduje `interface is
  None`.

### evpn_esi – záznam na (instance, ESI)

```json
{"instance": "EVPN-VLAN-AWARE-POP1",
 "esi": "00:11:12:13:14:00:00:00:00:00",
 "resolved_status": "Resolved by IFL ae0.4093",
 "df_role": "10.0.0.1",
 "interfaces": {"ae0.4093": {"status": "Up", "mode": "all-active"},
                "ae0.4094": {"status": "Up", "mode": "all-active"}}}
```

- Iterace `evpn-instance` → jeho `evpn-esi` bloky. `interfaces` =
  `evpn-interface` z tabulky `evpn-interface-status-table` **téže
  instance**, jejichž `evpn-interface-esi` je rovno ESI segmentu.
- ESI začínající `05:` se dál přeskakují. ESI `00:…:00` (single-homed)
  jako segment nevzniká – single-homed IFL v žádném `evpn-esi` bloku nejsou.
- `evpn-esi-local-intf-*` se už nečtou.
- Segment bez jediného IFL v tabulce se uloží s `interfaces: {}` (stav se
  nefabuluje; žádný scope ho nevybere).

### routes – záznam na (rib, prefix, protocol)

```json
{"rib": "CUST.inet.0", "prefix": "10.1.0.0/24", "protocol": "static",
 "next_hop": ["192.168.1.2"], "via": ["ae0.100"], "active": true}
```

- Oba průchody (static, aggregate) jen připojují, `setdefault` mizí.
- Slévání víc `rt-entry` pod jedním prefixem (QNH fix) zůstává, ale jen
  v rámci jednoho protokolu.
- Docstring „prefix nemůže být zároveň static i aggregate“ se opraví.

### pim_neighbor – tvar beze změny

`{interface: {"neighbor_address": …, "uptime_seconds": …}}`. Iteruje se po
`pim-neighbor` uzlech; soused s `ip-protocol-version` = `6` se přeskočí,
chybějící verze = v4. Na jednom rozhraní vyhrává první v4 soused.

## 2. Výběr pro službu (`Scope.select`)

### BGP

Záznam patří scopu, když `address` ∈ `bgp_neighbors` ∪
`bgp_neighbors_inactive` a `routing_instance` == `Scope.bgp_instance`
(dnešní `owns_bgp_peer`, nově nad záznamem). Pohled `{address: záznam}`.

### BFD

Záznam patří scopu:

- **single-hop** (`interface` vyplněno): `neighbor` ∈ `bgp_neighbors` a
  rozhraní je scopu; nebo scope je Core transit a rozhraní je jeho
  (beze změny proti dnešku, vč. vynechání `bgp_neighbors_inactive`).
- **multihop** (`interface` null): `neighbor` ∈ `bgp_neighbors` a scope
  nemá single-hop session k témuž sousedu na svém rozhraní.

Pohled `{neighbor: záznam}`.

Protože druhá podmínka multihop závisí na ostatních záznamech, predikát
vlastnictví dostává celou množinu záznamů (nebo ji `Scope` spočítá jednou)
– NEZARAZENO musí použít tentýž výpočet, ne vlastní kopii.

### ESI

Záznam patří scopu, když `instance` ∈ `routing_instances` a některé
rozhraní scopu je v `interfaces`. Pohled je zploštělý na dnešní tvar,
který check čte:

```json
{"00:11:12:13:14:00:00:00:00:00": {
   "resolved_status": "Resolved by IFL ae0.4093", "df_role": "10.0.0.1",
   "interface": "ae0.4093", "status": "Up", "mode": "all-active"}}
```

`interface`/`status`/`mode` jsou z IFL scopu. Scope má jedno rozhraní,
takže vzniká jeden stavový řádek na službu.

### Routes

Selektor `(rib, prefix)` → `(rib, prefix, route_type)`; záznam se vybere,
jen když jeho `protocol` == `route_type` selektoru (chybějící `route_type`
v selektoru = `static`, dnešní default). Pohled:

```json
{"static": {"CUST.inet.0": {"10.1.0.0/24": {...}}},
 "aggregate": {"CUST.inet.0": {"10.1.0.0/24": {...}}}}
```

Prázdné protokoly a tabulky se nevracejí (dnešní pravidlo).

### Nejednoznačnost místo kolizí

- `bgp_collisions` / `bfd_collisions` zanikají.
- `select()` vrací `bgp_ambiguous` a `bfd_ambiguous`:
  `{adresa: počet_záznamů}`, když vlastnictvím projde víc než jeden záznam
  na jednu adresu. Reálně: dvě multihop BFD session na stejnou adresu,
  nebo dva link-local BGP sousedi se stejnou adresou a různým
  `local-interface` v jedné RI.
- Taková adresa v pohledu (`bgp` / `bfd`) **není** – nic se nevybírá
  náhodně.
- Známá mezera zůstává: jediná multihop session na adresu, kterou má i
  peer jiného scopu, patří oběma scopům (`extensive` VRF nenese).

## 3. Engine a NEZARAZENO

- `_unassigned_bgp_peers`: iteruje záznamy `bgp`, predikát shodný se
  `select()`. Položka nese `peer`, `routing_instance`, `local_interface`,
  `snapshot`. Per-port `_on_port` čte pole záznamu.
- `_unassigned_bfd_sessions`: iteruje záznamy `bfd`, tentýž predikát jako
  `select()` (vč. multihop pravidla). Výjimky zůstávají (Core loopback,
  asymetrie `bgp_neighbors_inactive`). Položka nese navíc `multihop`.
- `_unassigned_static_routes`: `assigned` z `(rib, prefix, route_type)`.
  Bez toho by služba vlastnící statiku označila agregát na stejném
  prefixu za přiřazený a agregát by zmizel beze stopy.
- **Nejednoznačný záznam do NEZARAZENO nejde** – vlastníka má a je vidět
  jako SKIP řádek v jeho bloku.
- `_aligned_baseline_data`: beze změny kódu (běží nad pohledem, kde BFD a
  ESI zůstávají `{klíč: záznam s interface}`); upraví se docstring.
- `bfd_transit_state`: beze změny (čte pohled, seskupuje podle
  `interface`). Kolizní SKIP chybějící podle review F2 není potřeba –
  transit session na sdílené adrese teď přežije.
- `link_scopes`, ping, capture (kromě `LIST_AREAS`), replay: beze změny.

Renderery NEZARAZENO (`reporting/text_report.py:_unassigned_row`,
`gui/static/view.js:unassignedRow`) dostanou shodnou drobnou úpravu:

- BGP peer: podrobnost `RI <ri>`, a když položka nese `local_interface`,
  `RI <ri>   <local_interface>` – dva link-local peery jinak vypadají
  stejně.
- BFD session: místo `-` pro prázdné rozhraní vypíše `multihop`, když
  `multihop` je true.

## 4. Checky

### `checks/bgp.py`, `checks/bfd.py`

- Kolizní větve zanikají: parametry `collision` / `baseline_collided`,
  konstanty `ADDRESS_COLLISION` / `NO_BASELINE_COLLISION`, texty
  „cizí instance … collector uchoval jen jeho session“.
- Nahrazuje je větev nejednoznačnosti ze `*_ambiguous`:

  ```
   SKIP | BFD (198.11.14.4) : neznamy (nejednoznacne: 2 session) | ...
  ```

  - message: proč (např. „multihop session na stejnou adresu je 2×, RPC
    nenese VRF“ / „2 BGP session na stejnou adresu v instanci X“).
  - Nejednoznačná adresa v baseline: stavový řádek (BGP status, BFD) nese
    jako baseline hodnotu `neznamy (nejednoznacne: {n} session)` – baseline
    stav neznala, „bez baseline“ by tvrdilo, že tam nic nebylo. Řádek
    prefixů, který nemá s čím porovnat, má hodnotu `bez baseline
    (nejednoznacne)`. Nikdy UNCHANGED ani RECOVERED (pojistka proti
    falešnému PASS jako ve vrstvě 1). (Upřesněno 2026-09-25 podle
    implementace a Global Constraints plánu.)
  - Nejednoznačná adresa sama řádek nezakládá – doplní ho jen tam, kde by
    ho služba měla i tak (BFD záměr, BGP soused v `bgp_neighbors`).

### `evpn_esi_status`

- „ESI Local interface status“ bere `status` z per-IFL tabulky (`Up` /
  `Down`), `subject` nese `interface` a `mode`.
- Řádky `ESI`, `ESI Status`, DF beze změny. `_is_up` bere `Up` i
  `Up/Forwarding` už dnes.

### `checks/routes.py`

- `_flatten(routes, protocol)` → čte `routes[protocol]`.
- Fallback „chybějící `protocol` = static (před schématem 10)“ zaniká.
- Popisky řádků a texty beze změny.

### `core_multicast_forwarding`

Čte `routes["static"]["inet.2"]`.

### Ostatní

Transit BFD, PIM checky a vše ostatní beze změny.

## 5. Bump, upgrade, testy, akceptace

### Bump

`SCHEMA_VERSION = 14`, komentář pod 13 vyjmenuje pět oblastí. Loader
zůstává exact-match. Běhy s raw XML → `mig-validate upgrade`; bez raw →
upgrade je vypíše „nelze – bez raw záznamu“.

### Testy

- **Collectory:** conformance testy nad MX/EVO fixtures na nové tvary.
  Tři lab záznamy z 2026-09-24 jako fixtures:
  - PIM dual-stack: et-0/0/1.0 = `10.1.0.4` / 87773 s.
  - ESI ve dvou instancích: dva záznamy, každý se svým IFL.
  - ESI se dvěma IFL v jedné instanci: `interfaces` má oba.
- **Syntetický end-to-end MX → ACX:** customer-a a customer-b, peer
  192.168.1.2 každý ve své VRF, jeden Down. Každá služba vidí jen svou
  session; žádný WARN „BGP prefixy (customer-a.inet.0) chybi“, žádný FAIL
  „BFD … patril k teto sluzbe“.
- **Syntetické scénáře:**
  - static + aggregate na stejném (rib, prefix): každý check vidí svůj
    záznam, NEZARAZENO agregát nespolkne.
  - dvě multihop session na jednu adresu → SKIP nejednoznačné; totéž
    v baseline → baseline hodnota `neznamy (nejednoznacne: 2 session)`,
    ne UNCHANGED.
  - dva link-local BGP sousedi se stejnou adresou → nejednoznačné.
  - ESI sdílené dvěma instancemi, jeden IFL Down → služba toho IFL FAIL
    (dnes PASS).
  - multihop záznam + single-hop záznam na stejnou adresu: scope se
    single-hop na svém rozhraní bere single-hop, ne nejednoznačnost.
- **Conftest builder** pro záznamy bgp/bfd/evpn_esi/routes (device fakta),
  aby testy tvar neopakovaly. Pohledy per scope v testech checků zůstávají.
- **Kolizní testy vrstvy 1** se přepíší na nejednoznačnost nebo zruší.
- Mutanti jmenovaní v docstrings dotčených testů se po přegenerování
  fixtures pustí znovu (paměť: mutanti nad konzistentní sadou fixtures).

### Akceptace v laborce

1. `mig-validate upgrade` na běhy s raw XML (`runs/…/raw`).
2. Diff reportu před/po. Očekávané rozdíly: PIM řádek et-0/0/1.0 (v4
   místo v6) a hodnota ESI stavu (`Up` místo `Up/Forwarding`). Jakýkoli
   jiný rozdíl je nález.
3. Po merge restart lab GUI.

## Mimo rozsah

- IS-IS L1+L2, ASM (S,G,rpt), IPv6 PIM, LDP multi-neighbor.
- Vyhodnocení link-local BGP (E10) – pole se ukládá, vlastnictví ho
  nepoužívá.
- Vyhodnocovací vlny E2 (VPWS AC filtr), E5 (matcher) a další – následují
  po této vlně.
