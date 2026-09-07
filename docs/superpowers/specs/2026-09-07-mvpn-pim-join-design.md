# MVPN-PIM: PIM join, role sender/receiver a subtype `mvpn` — design

Datum: 2026-09-07

## Cíl

Druhá multicastová vlna: IPVPN instance s `protocols mvpn`, kde zákaznické
rozhraní (IRB i fyzický/agregovaný tranzit) má `protocols pim interface X`
místo nebo vedle `protocols igmp interface X`. Očekávané (S,G) se čtou
z PIM join tabulky (`show pim join instance <X> extensive`) stejnou logikou
jako z IGMP membership reportu, a **obě** množiny se sloučí. Rozhraní je
v joinu buď **downstream** (receiver site) nebo **upstream** (sender site,
zdroj za servisním rozhraním).

Vše pro obě platformy (MX i EVO), fixtures z živé laborky
(MX1-POP1 receiver, MX1-POP2 sender, PTX1-POP1 receiver, 2026-09-07).

## Uzavřená rozhodnutí (brainstorm 2026-09-07, nerelitigovat)

- **Jeden subtype `mvpn`** nahrazuje `mvpn-igmp`. IPVPN + `protocols mvpn`
  v instanci + (IGMP záměr **nebo** PIM záměr na rozhraní) ⇒ `mvpn`.
  Který záměr je přítomen, říká `protocol` seznam a detection reason.
- **Role per (S,G) se odvozuje z PIM join výpisu**, ne z konfigurace:
  `upstream-interface-name == servisní rozhraní` ⇒ sender,
  servisní rozhraní v `downstream-interface/pim-interface-name`
  (nebo `pim-pseudo-downstream-interface-name`) ⇒ receiver. IGMP pár je
  vždy receiver.
- **Role instance z konfigurace** (`mvpn_site`) slouží jen k interpretaci
  prázdné tabulky: `sender-site` nebo `provider-tunnel` ⇒ sender;
  `receiver-site` nebo žádné site klíčové slovo ⇒ receiver (Junos default
  je obojí). `provider-tunnel` bez site klíčového slova dá obě role —
  přijaté riziko: takový site může hostit receiver a tabulka to nerozliší.
- **Rozhraní s oběma záměry a jen jednou množinou**: řádek protokolu bez
  párů je INFO, ne BROKEN (`bez PIM join, o streamy se hlasi IGMP` /
  `bez IGMP reportu, o streamy se hlasi PIM join`). BROKEN je jen, když
  nemá páry ani jeden protokol.
- **Sender-only instance bez joinu** (žádný vzdálený receiver) je
  DEGRADED, ne BROKEN — bez receiveru není co ověřit a nejde to odlišit
  od rozbitého receiveru jinak než konfigurací.
- **Struktura (varianta A):** jeden collector `pim_join`, jeden nový check
  `pim_join`, sdílený helper `expected_pairs()` (sjednocení IGMP + PIM
  párů s rolemi). Forwarding a c-multicast checky berou páry z helperu
  a větví per role. Žádný samostatný PIM forwarding modul, žádné slučování
  IGMP a PIM do jedné fact area.
- **Ověřeno v laborce 2026-09-07:** IGMP-řízený (S,G) se na EVO objevuje
  i v PIM join tabulce s IRB jako downstream (irb.2, 239.1.1.1) — obě
  množiny se na rozhraní s oběma záměry typicky překrývají, sjednocení je
  dedupuje. Internet/multicast join v master instanci nese downstream
  `Pseudo-GMP` a skutečné jméno v `pim-pseudo-downstream-interface-name`.
  Sender site (MX1-POP2): join upstream `irb.10`, downstream `Pseudo-MVPN`;
  multicast routa upstream `irb.10`, downstream `ge-0/0/0.0`; c-multicast
  záznam s provider tunelem, jehož sender PE je lokální loopback.
- Mimo rozsah: Internet/multicast rozhraní s globálním `protocols pim
  interface` zůstává IGMP-only (PIM join check tam sice běží přes gate
  "pim" v protokolech, ale subtype se z PIM neodvozuje). IPVPN s PIM bez
  `protocols mvpn` zůstává plain IPVPN. PIM neighbor check na MVPN IRB
  není součástí vlny. `core_multicast_forwarding` se nemění.

## 1. Inventory a parser

### Záměr PIM per rozhraní

`_parse_pim_interfaces()` vedle `_parse_igmp_interfaces()`
(`parsers/core.py` ~978), stejný tvar: xpath
`./protocols/pim/interface/name/text() |
./routing-instances/instance/protocols/pim/interface/name/text()`,
výsledek do `self.pim_interfaces: set[str]` a do
`global_protocols_by_interface[name]` jako `"pim"`. Nahrazuje volání
`_parse_global_protocol_interfaces("pim")`. Instance-level `"pim"`, které
`RoutingInstance.protocols` (`child_names(./protocols/*)`) rozsévá na
všechna rozhraní instance, zůstává — gate PIM neighbor checku na Core
tranzitu se nemění.

### `mvpn_site` na instanci

`RoutingInstance` dostane `mvpn_site: list[str]` s hodnotami z
`{"sender", "receiver"}` (seřazeno), odvozeno z `./protocols/mvpn` a
`./provider-tunnel` uzlu instance:

| konfigurace | mvpn_site |
|---|---|
| bez `protocols mvpn` | `[]` |
| `mvpn sender-site`, bez tunelu | `["sender"]` |
| `mvpn receiver-site` | `["receiver"]` |
| `provider-tunnel` + `mvpn sender-site` | `["sender"]` |
| `provider-tunnel` + `mvpn receiver-site` | `["receiver", "sender"]` |
| `provider-tunnel`, mvpn bez site klíčového slova | `["receiver", "sender"]` |
| mvpn bez site klíčového slova, bez tunelu | `["receiver"]` |

Pravidlo: `sender` ⇔ `sender-site` nebo `provider-tunnel`;
`receiver` ⇔ `receiver-site` nebo (`sender-site` není nakonfigurováno).

`InterfaceService` i `ServiceEntry` dostanou `mvpn_site: list[str]`
zkopírované z instance (prázdné mimo MVPN), aby check četl roli ze scopu
(`scope.selectors`) a ne z konfigurace. `Selectors` nese `mvpn_site`
stejně jako `protocols`.

### Detekce subtype

`_ipvpn_subtype()`: instance má `"mvpn"` v `protocols` a rozhraní je
v `igmp_interfaces` nebo `pim_interfaces` ⇒ `"mvpn"`. Reason text
vyjmenuje nalezené záměry, např. `Rozhraní je pod protocols pim a
instance má protocols mvpn — MVPN site.` (u obou záměrů `protocols igmp
a pim`). Všechny výskyty `mvpn-igmp` (checky, conftest, docs, testy) se
přejmenují.

`INVENTORY_SCHEMA_VERSION` 8 → **9** (nový klíč `mvpn_site`, přejmenovaný
subtype). Staré inventory s `mvpn-igmp` se na pre straně nepáruje přes
subtype pravidlo — stejný dopad jako při zavedení subtypu 2026-09-02,
přijato.

## 2. Collector `pim_join` a snapshot

| Fact area | RPC | Klíčováno |
|---|---|---|
| `pim_join` | `get-pim-join-information(extensive=True)`; EVO navíc `instance="all"`; MX master bez argumentu + jedno volání per VRF z `get-instance-information(brief)` | jméno instance → `"source,group"` → payload |

Ekvivalentní CLI: `show pim join instance all extensive` (EVO),
`show pim join extensive` + `show pim join instance <X> extensive` (MX).

Smyčka `collect()` z `MulticastRouteCollector` (rpc per instance,
agregace selhání, `CollectorError` při jakémkoli neúspěchu) se vytáhne do
sdílené základny `_PerInstanceCollector` v `collectors/multicast.py`;
oba collectory z ní dědí. `record_calls` stejně.

Payload (jen `address-family INET`, INET6 se ignoruje; `PIM.` prefix
z `pim-instance` se stripuje, `PIM.master` → `master`):

```
pim_join:
  NGMVPN-PIM-SOURCE:
    "10.10.10.1,232.10.10.1":
      source: "10.10.10.1"            # None, když join-group nemá multicast-source-address ((*, G))
      group: "232.10.10.1"
      upstream_interface: "irb.10"     # verbatim, "Through BGP" zůstává
      upstream_neighbor: "10.10.13.1"  # verbatim, "Through MVPN" zůstává
      downstream_interfaces: ["Pseudo-MVPN"]
      uptime_seconds: 2249
```

- Klíč `route_key(source or "*", group)` — pro (*, G) je klíč `"*,group"`,
  aby zůstal JSON-safe a jednoznačný.
- `downstream_interfaces` sbírá z každého `downstream-interface` jak
  `pim-interface-name`, tak `pim-pseudo-downstream-interface-name` (obě,
  pokud jsou přítomny), bez duplicit, v pořadí výpisu.
- Instance bez `join-group` nemá klíč. Collector nic nesyntetizuje.
- `uptime_seconds` z `junos:seconds` atributu `uptime` přes `_seconds_attr`.

`SCHEMA_VERSION` snapshotu 12 → **13**, `pim_join` do `FACT_AREAS`.

### Scoping

`Scope.select()`: `pim_join` podle instance, stejné pravidlo jako
`multicast_route` — scope bez RI dostane `master`, scope s RI svou
instanci, jen role, které multicast měří (Internet, IPVPN, Core/loopback).
Filtr per rozhraní dělá až helper v checku, protože rozhraní může být
upstream i downstream.

## 3. Očekávané páry a check `pim_join`

### Helper

V `checks/multicast.py`:

- `pim_pairs(facts, scope) -> list[tuple[str | None, str, frozenset[str]]]`:
  pro každý join v tabulce scopu role `receiver`, je-li servisní rozhraní
  (`scope.selectors.interfaces[0]`, konzistentně s forwarding checkem)
  v `downstream_interfaces`; role `sender`, je-li rovno
  `upstream_interface`. Join, který se rozhraní nedotýká, se ignoruje.
  Link-local skupiny (224.0.0.0/24) se zahazují stejně jako u IGMP.
- `expected_pairs(facts, scope)`: sjednocení `igmp_pairs()` (role
  `receiver`) a `pim_pairs()`; stejné (S,G) se sloučí s unií rolí.
  Seřazeno jako `igmp_pairs`. Baseline se čte s baseline scopem.

### Check `pim_join`

`id="pim_join"`, `title="PIM join na servisnim rozhrani"`, `label="PIM
join"`, `order=11` (forwarding → 12, core → 13, c-multicast → 14),
`mode=BOTH`, `Severity.CRITICAL`, `requires=("pim_join",)`,
`service_types={"Internet", "IPVPN"}`, `service_subtypes={"multicast",
"mvpn"}`. Gate: bez `"pim"` v `scope.selectors.protocols` ticho (žádné
řádky), jako PIM neighbor check.

| situace | outcome | value |
|---|---|---|
| PIM páry, množina (S,G) shodná s baseline nebo bez baseline | OK | `(S, G) [receiver], (S, G) [sender]` |
| PIM páry, množina (S,G) se liší od baseline | DEGRADED | jako výše, `baseline_value` = baseline páry |
| bez PIM párů, IGMP páry jsou | INFO | `bez PIM join, o streamy se hlasi IGMP` |
| bez PIM párů, bez IGMP párů, `mvpn_site == ["sender"]` | DEGRADED | `sender site bez vzdaleneho receiveru, neni co overit` |
| bez PIM párů, bez IGMP párů, jinak | BROKEN | `Zadny PIM join` |

Baseline se porovnává na množině (S,G), role se neporovnávají (migrací
se nemění; při rozdílu by šlo o jiný stream). IGMP páry se čtou z
`ctx.subject` volitelně (`igmp_group` není v `requires`).

### Zrcadlo v `igmp_membership_report`

Bez IGMP párů, ale s PIM páry ⇒ INFO `bez IGMP reportu, o streamy se
hlasi PIM join` místo BROKEN. `pim_join` se čte volitelně (není
v `requires`), takže check běží i nad snapshotem schema 12 beze změny
chování.

## 4. Role-aware forwarding a c-multicast

### `multicast_forwarding_status`

Páry z `expected_pairs()`. Kaskáda SKIP jen při prázdném sjednocení,
value `bez IGMP reportu ani PIM join`. Per nalezená routa:

- role `receiver` (beze změny): servisní rozhraní v downstream, upstream
  prefix pravidlo per subtype (`mvpn` přebírá seznam dosavadního
  `mvpn-igmp`: `lsi.`, `vt-`, `ge-`, `xe-`, `et-`, `ae`, `irb`).
- role `sender`: řádek `Upstream interface` OK ⇔ `upstream_interface ==
  servisní rozhraní` (jinak BROKEN `upstream <x> neni servisni rozhrani
  <iface>`); řádek `Stream` OK ⇔ downstream neprázdný, value
  `Stream odchazi na <names>` / BROKEN `S,G je v tabulce ale nema zadny
  downstream`. Bez prefix pravidla na downstream (laborka: `ge-0/0/0.0`).
- obě role: řádek `Stream` OK, splní-li podmínku kterákoli role; řádek
  `Upstream interface` se hodnotí podle role, jejíž Stream podmínka
  prošla (při obou prošlých receiver). Neprošla-li žádná, Stream je
  BROKEN s receiver textem a `Upstream interface` se hodnotí receiver
  prefix pravidlem nezávisle — stejně jako u čistě receiver páru
  (upřesněno 2026-09-07 při dokumentaci: řádky jsou nezávislé, ne
  „obě BROKEN").
- `Forwarding-rate` a `Route uptime` řádky beze změny.

### `mvpn_cmulticast_status`

Páry z `expected_pairs()`, `service_subtypes={"mvpn"}`,
`requires=("mvpn_instance",)`; `igmp_group` a `pim_join` volitelně.
Kaskáda SKIP jen při prázdném sjednocení. Řádky jsou role-nezávislé:
c-multicast záznam existuje, provider tunnel má sender PE, sender PE se
porovnává proti baseline (u sender site je to lokální loopback — stejné
pravidlo).

## 5. Testy, fixtures, dokumentace

### Fixtures (z laborky 2026-09-07, už uložené)

| soubor | obsah |
|---|---|
| `tests/fixtures/rpc/junos/pim_join.xml` | MX1-POP1 receiver: `Through BGP` upstream, downstream `irb.10`, `pim-interface-state (assert winner)` |
| `tests/fixtures/rpc/junos/pim_join.2.xml` | MX1-POP2 sender: upstream `irb.10`, downstream `Pseudo-MVPN` |
| `tests/fixtures/rpc/junos-evo/pim_join.xml` | PTX `instance all`: master (`Pseudo-GMP` + pseudo downstream `et-0/0/8.11`), `NGMVPN-IGMP-RECEIVER` (irb.2), `NGMVPN-PIM-RECEIVER` (irb.10), prázdné INET6 rodiny |
| `tests/fixtures/rpc/junos/multicast_route.6.xml` | MX1-POP2 sender routa: upstream `irb.10`, downstream `ge-0/0/0.0` |
| `tests/fixtures/rpc/junos/mvpn_instance.2.xml` | MX1-POP2: c-multicast se sender PE 150.0.0.13 (lokální) |
| `tests/fixtures/rpc/junos-evo/multicast_route.2.xml` | PTX se streamy v master a obou MVPN instancích |

Syntetické: join-group bez `multicast-source-address` ((*, G)); RI
konfigurace pro čtyři kombinace `mvpn_site` (obě platformy).

### Testy (podle stávajících souborů)

- `tests/parsers/test_pim_intent.py`: RI-scoped PIM záměr na obou
  parserech; `mvpn_site` pro řádky tabulky v sekci 1; subtype `mvpn` pro
  PIM-only, IGMP-only, obojí; IPVPN s PIM bez mvpn zůstává `None`.
- `tests/collectors/test_multicast.py`: parse tří pim_join fixtures
  (klíče, role-relevantní pole, pseudo downstream jména), (*, G)
  syntetika, MX `record_calls` per VRF, částečné selhání ⇒
  `CollectorError`; sdílená základna nezmění chování
  `multicast_route` (stávající testy zelené).
- `tests/checks/test_multicast.py`: `expected_pairs` sjednocení a unie
  rolí; každý řádek tabulky outcomes `pim_join`; INFO zrcadlo v IGMP;
  sender forwarding řádky (OK/BROKEN upstream, OK/BROKEN downstream);
  případ obou rolí; c-multicast na senderu; kaskáda jen při prázdném
  sjednocení; gate bez "pim".
- `tests/models/test_scope_multicast.py`: `pim_join` scoped per instance.
- conftest: syntéza multicast rolí přejmenovaná na `mvpn`; celá sada
  zelená.

### Dokumentace

`docs/cs/reference.md`, `docs/cs/files/checks.md`, `docs/cs/files/parsers.md`
a anglické zrcadlo: přejmenování subtype, nový check `pim_join`, nová
fact area `pim_join`, `mvpn_site`. Schema bumpy v docstringách modelů.

### Ověření v laborce

Po implementaci `record` + state run proti PTX1-POP1 (receiver irb.2
a irb.10) a MX1-POP2 (sender irb.10); přečíst vykreslené bloky: PIM join
řádek s rolí, forwarding řádky per role, c-multicast na senderu.
