# Multicast vlna hotová — IGMP, multicast forwarding, MVPN c-multicast, stav k 2026-09-03

**Výchozí bod:** větev `worktree-multicast-checks`, deset úloh implementace hotových
(Úkoly 1–10) + Úkoly 5b/5c (lab capture). **1379 testů sebraných, 1 skip, 0 FAIL**
(`pyats-venv/bin/python -m pytest tests/ -q`, exit 0; výchozí stav vlny byl 1272 testů).
Schema **inventory 7 → 8**, **snapshot 11 → 12**. Návrh je
[`specs/2026-09-02-multicast-checks-design.md`](specs/2026-09-02-multicast-checks-design.md),
ledger [`../../.superpowers/sdd/2026-09-02-multicast-checks/progress.md`](../../.superpowers/sdd/2026-09-02-multicast-checks/progress.md).

Zadáním bylo pokrýt tři multicast role, které dřív neměly žádný check: **Internet/multicast**
(zákaznický IGMP receiver), **Core/loopback** (globální `inet.2` RPF statiky) a
**IPVPN/mvpn-igmp** (IRB v MVPN VRF s IGMP receiverem) — čtyři nové checky nad třemi
novými collectory.

---

## Co vlna přinesla

**Sběr (Úkol 1).** Tři nové collectory (`igmp_group`, `multicast_route`, `mvpn_instance`)
v `collectors/multicast.py`. MX nezná `get-multicast-route-information(instance="all")`
(vrací `<output>instance is not running</output>`) — collector proto na MX dělá master
volání bez argumentu plus dynamickou enumeraci VRF (`get-instance-information(brief=True)`,
`instance-type vrf`) a jedno volání na RI; junos-evo má jedno `instance="all"` volání.
`record_calls()` hook v `collect()` dává `mig-validate record` uložit MX odpovědi jako
`multicast_route.xml` + `.2.xml`, `.3.xml`… `forwarding_rate_pps` je `int | None` — EVO
často vrací `<multicast-statistics-timed-out/>` i na živé routě, absence není nula.

**Parser (Úkoly 3–4).** IGMP záměr z `protocols igmp interface X` (globálně i v RI).
Subtypy Internet `multicast` a IPVPN `mvpn-igmp` (IGMP záměr + `protocols mvpn` v
instanci). Globální `inet.2` statiky patří vždy Core lo0.0 bez ohledu na next-hop
(RPF statiky patří routeru jako celku, ne tranzitní lince, do jejíhož subnetu náhodou
padne next-hop).

**Inventory (Úkol 5).** Schema 7 → 8: `ServiceEntry.l2_interface` — IRB nese L2 access
porty domén (globálních i v jiné RI), které routuje; do reportu jde jen jako poznámka
`L2: …` v hlavičce bloku, do výběru faktů se nepromítá.

**Scoping (Úkoly 2, 6).** Snapshot schema 11 → 12 (tři nové fact areas). `Scope.select()`:
multicast tabulka jde jen do rolí, které multicast měří (Internet, IPVPN, Core loopback) —
tranzitní Core ani L2 služby tabulku nedostanou.

**Checky (Úkoly 7–10).** Čtyři nové checky v `checks/multicast.py`:
`igmp_membership_report` (množina (S,G) proti baseline), `multicast_forwarding_status`
(role-aware upstream: transit prefix pro Internet, `lsi.`/`vt-` pro mvpn-igmp),
`core_multicast_forwarding` (řízeno `inet.2` statikami, ne IGMP; ticho bez záměru),
`mvpn_cmulticast_status` (c-multicast záznam + provider tunnel, proti baseline jen sender
PE, ne celý tunnel id). Chybějící IGMP množina kaskáduje do jednoho SKIP řádku napříč
oběma IGMP-řízenými checky, ne do nezávislého hledání v tabulce.

**Lab capture (Úkoly 5b/5c, mimo tento úkol).** `.4` (pre) a `.5` (post) recapturovány na
schema 8/12; BFD odstraněno z obou routerů záměrně (nestabilita laborky) —
`KNOWN_LAB_ASYMMETRIES` je prázdná n-tice, `test_bfd_check_really_reads_the_session_table`
převeden na syntetická fakta.

**Úkol 11 (tenhle):**
- **Deset mutantů, 10/10 zabito**, ověřeno spuštěním (tabulka níže).
- **Dokumentace cs+en** — `checks.md`, `collectors.md`, `parsers.md`, `models.md`,
  `reference.md` a zrcadla v `docs/en/`.
- **Render verifikace `runs/mig01`** — se zásadním nálezem (viz níže): jen jeden ze tří
  požadovaných bloků se z `runs/mig01` reálně vyrenderuje.
- Finální suita zelená (1379 passed, 1 skip).

---

## Mutanty (Úkol 11, krok 1)

Protokol: aplikovat mutanta, spustit jmenovaný test, pozorovat FAIL, `git checkout --
<soubor>`, teprve pak zapsat tvrzení do docstringu. Všech 10 skutečně spuštěno v tomhle
běhu (2026-09-03), žádné převzato z paměti.

| # | mutant | soubor | zabíjí |
|---|---|---|---|
| 1 | `Outcome.DEGRADED` → `Outcome.OK` ve větvi „množina se liší" | `checks/multicast.py::IgmpMembershipReportCheck.run` | `test_igmp_report_changed_set_is_warn` |
| 2 | `_upstream_ok`: vrať `True` vždy | `checks/multicast.py::_upstream_ok` | `test_forwarding_internet_upstream_must_be_transit`, `test_forwarding_mvpn_upstream_must_be_lsi_or_vt` (bonus: `test_forwarding_missing_upstream_renders_dash`) |
| 3 | `iface in downstream` → `bool(downstream)` | `checks/multicast.py::MulticastForwardingStatusCheck._stream` | `test_forwarding_downstream_without_service_interface_fails_stream_row` |
| 4 | `upstream in vias` → `upstream.startswith(("ge-","xe-","et-","ae"))` | `checks/multicast.py::CoreMulticastForwardingCheck._stream` | `test_core_upstream_must_be_one_of_via` |
| 5 | `assign_sources`: sort podle `prefixlen` vzestupně (místo sestupně) | `checks/multicast.py::assign_sources` | `test_assign_sources_longest_prefix_wins_and_each_route_once` |
| 6 | `_tunnel_row`: `was_tunnel != tunnel` místo `was_pe != pe` | `checks/multicast.py::MvpnCmulticastStatusCheck._tunnel_row` | `test_mvpn_sender_pe_change_is_warn_but_tunnel_id_change_is_not` |
| 7 | smazání `if route.rib == "inet.2"` větve | `parsers/core.py::JunosServiceParserCore._route_matches_service` | `test_globalni_inet2_statika_patri_lo0_ne_tranzitu` (obě platformy) |
| 8 | smazání `measures_multicast` gate | `models/scope.py::Scope.select` | `test_core_loopback_gets_master_table_but_transit_does_not` |
| 9 | `interface == "local"` filtr pryč | `collectors/multicast.py::IgmpGroupCollector.parse` | `test_igmp_group_drops_local_pseudo_interface` (obě platformy) |
| 10 | `stream_rows`: `raw_pps is None` → `raw_pps == 0` | `checks/multicast.py::stream_rows` | `test_stream_rows_skip_when_rate_missing` (TypeError v `int(None)`) |

Poznámka k #10: reálně spuštěný mutant selže na `TypeError` v `int(None)`, ne na
asserci — stejný jev jako mutant BFD z minulé vlny (viz
[`roadmap-2026-08-26-core-vlna-hotovo.md`](roadmap-2026-08-26-core-vlna-hotovo.md)):
mutant podle briefu občas změří přesně to, co má, jen jinou cestou (crash místo
falešného PASS). Zapsáno v docstringu testu, ne jen tady.

Žádný mutant nepřežil — žádná díra k doplnění testem tenhle úkol nenašel.

---

## Render verifikace `runs/mig01` — zásadní nález

**`mig-validate evaluate --run mig01` na existujících snímcích vyrenderuje jen jeden ze
tří požadovaných bloků.** Detail:

`runs/mig01` obsahuje výhradně **per-port** capture (`--port ge-0/0/2`, `ge-0/0/3` na
`.4`; `et-0/0/8`, `ae0` na `.5`), žádnou celoboxovou (`--port` vynechán / `all`). Inventory
se generuje per-port cestou (`inventory_<node>_<port>.yml`) a obsahuje jen služby, jejichž
rozhraní patří pod daný fyzický port — `lo0.0` (Core) a `irb.2` (IPVPN/mvpn-igmp) pod
žádný z migrovaných portů `ge-0/0/2`–`ge-0/0/6` nepadají, takže **v žádné z per-port
inventářů nemají scope** a `evaluate --run mig01` je nikdy neuvidí, bez ohledu na to, co
je v datech.

Ověřeno: `runs/mig01/inventory_MX1-POP1_ge_0_0_3.yml` má přesně dvě položky
(`ge-0/0/3`, `ge-0/0/3.0`). Samotná **fakta** v snímku ale `lo0.0` i `irb.2` obsahují
(collector sbírá celé zařízení bez ohledu na `--port` — filtruje se jen inventory/scope),
takže šlo ověřit renderovací logiku ofline: **scopy pro `pre`/`post` snímek přestavěny
lokálně** (žádný přístup do laborky) nad už committnutými plnými inventářemi
(`172.20.20.4.yml` / `.5.yml`, schema 8) přes `build_scopes()`, uloženo do `/tmp`, a
vyhodnoceno `evaluate --snapshot`. Tohle **není** `evaluate --run mig01` a párování
(`interface_mapping` z `run.yml`) se v tomhle náhradním běhu neuplatnilo — proto se ve
zprávě objevuje pár zdvojených/nesouvisejících párování (např. `svc:lo0.0:Core` zároveň
jako vyrenderovaný blok i v NESPAROVANO), což je artefakt obejití `run.yml`, ne chyba
checku.

**Blok 1 — Internet, z `evaluate --run mig01` (skutečný run, žádná rekonstrukce):**

```
==============================================================================================================
 WARN  MULTICAST-STREAM-A-MUX1-RECEIVER-1   Internet   ge-0/0/2.11 -> et-0/0/8.11   RI: -
==============================================================================================================
 STAV | CHECK                                      : POST (et-0/0/8.11)              | ZMENA PROTI ge-0/0/2.11
 -----+--------------------------------------------+---------------------------------+------------------------
 SKIP | BGP prefixy                                : zadna session                   |
 SKIP | BGP status                                 : zadny peer                      |
 PASS | IGMP membership report                     : (10.11.11.1, 232.1.1.1)         |
 PASS | Interface admin status (et-0/0/8.11)       : Up                              |
 PASS | Interface operational status (et-0/0/8.11) : Up                              |
 PASS | Interface traffic in (et-0/0/8.11)         : 0 pps                           |
 PASS | Interface traffic out (et-0/0/8.11)        : 6 pps                           | bylo 5 pps   +20 %
 PASS | Multicast forwarding status                : 1 S,G                           |
   -- (10.11.11.1, 232.1.1.1)
 PASS | Stream                                     : Stream se na et-0/0/8.11 posila |
 PASS | Upstream interface                         : et-0/0/0.0                      |
 PASS | Forwarding-rate                            : 6 pps                           |
 INFO | Route uptime                               : 00:04:43                        |

 -- IPv4  10.111.11.1/30 -------------------------------------------------------------------------------------
 WARN | ARP                                        : zadny zaznam                    |
 PASS | Ping                                       : 5/5  6.457 ms  10.111.11.2      |
```

Odpovídá specu: IGMP row, souhrn `1 S,G`, skupina `-- (10.11.11.1, 232.1.1.1)`. `WARN`
(chybějící ARP) je nesouvisející, reálný nález na jiném checku, ne multicast.

**Blok 2 — Core lo0.0, z ofline rekonstrukce (NENÍ z `evaluate --run mig01`):**

```
=============================================================================================
 FAIL  svc:lo0.0:Core   Core   - -> lo0.0   RI: -
=============================================================================================
 STAV | CHECK                                : POST (lo0.0)                   | ZMENA PROTI -
 -----+--------------------------------------+--------------------------------+--------------
 SKIP | BGP prefixy                          : bez baseline                   |
 PASS | Multicast forwarding status          : Existuje S,G pro 10.11.11.1/32 | bez baseline
 ...
 PASS | IS-IS overload bit                   : nenastaven                     |
   -- (10.11.11.1, 232.1.1.1)
 PASS | Upstream interface                   : et-0/0/0.0                     | bez baseline
 PASS | Downstream interfaces                : et-0/0/8.11                    | bez baseline
 PASS | Forwarding rate packets              : 6 pps                          | bez baseline
 INFO | Route uptime                         : 00:04:43                       | bez baseline

 -- IPv4  150.0.0.12/32 ---------------------------------------------------------------------
 ...
   -- Agregatni routy
 PASS | inet.0 10.1.0.0/23                   : v tabulce                      | bez baseline
 PASS | inet.0 150.0.0.0/24                  : v tabulce                      | bez baseline
   -- Staticke routy
 PASS | inet.2 10.11.11.1/32                 : 10.1.1.2                       | bez baseline
```

Odpovídá specu: `Existuje S,G pro 10.11.11.1/32`, `-- Staticke routy`, `inet.2
10.11.11.1/32`. `FAIL` v hlavičce bloku je z BGP session (`150.0.0.2: stav Connect`) —
nesouvisí s multicastem.

**Blok 3 — IPVPN irb.2, z ofline rekonstrukce (NENÍ z `evaluate --run mig01`):**

```
============================================================================================================
 WARN  MUX1 receivers POP1   IPVPN   irb.2 -> irb.2   RI: MULTICAST-STREAM-B-MUX1-RECEIVER
 L2: et-0/0/8.12
============================================================================================================
 STAV | CHECK                                : POST (irb.2)                              | ZMENA PROTI irb.2
 -----+--------------------------------------+-------------------------------------------+------------------
 SKIP | BGP prefixy                          : zadna session                             |
 SKIP | BGP status                           : zadny peer                                |
 PASS | IGMP membership report               : (10.12.12.1, 239.1.1.1)                   |
 ...
 PASS | Multicast forwarding status          : 1 S,G                                     |
 SKIP | Ping                                 : bez cile                                  |
   -- (10.12.12.1, 239.1.1.1)
 PASS | Stream                               : Stream se na irb.2 posila                 |
 PASS | Upstream interface                   : lsi.515                                   |
 PASS | Forwarding-rate                      : 6 pps                                     |
 INFO | Route uptime                         : 00:02:55                                  |
 PASS | C-Multicast status                   : 10.12.12.1/32:239.1.1.1/32                | bez baseline
 PASS | Provider tunnel                      : RSVP-TE P2MP:150.0.0.13, 24209,150.0.0.13 |

 -- IPv4  10.12.11.2/30 ------------------------------------------------------------------------------------
 WARN | ARP                                  : zadny zaznam                              |
```

Odpovídá specu: `L2:` poznámka v hlavičce (`et-0/0/8.12` — IRB routuje L2 access port
mimo EVPN link), `C-Multicast status`, `Provider tunnel`. Upstream je `lsi.515` (ne
`vt-`) — v souladu s dřívějším nálezem z Úkolu 1, že laborka má v MVPN RI jen `lsi.*`
upstream.

### Co to znamená

Obsah a formát všech tří bloků **odpovídá specu** tam, kde je vidět — checky samy fungují
správně na reálných lab datech. Ale **`runs/mig01` v aktuálním stavu neumí vyrenderovat
bloky 2 a 3** přes zdokumentovaný postup `evaluate --run mig01` — chybí jim scope, protože
žádná celoboxová capture nebyla nikdy pořízena. To je mezera v **datech runu**, ne
v checku, a check se kvůli ní neupravoval.

**Uzavření vyžaduje laborku** (mimo rozsah tohoto úkolu — „no lab access"): buď
celoboxová capture (`mig-validate capture --run mig01 --parse-services --phase pre`
/ `--phase post` bez `--port`) pro `.4` a `.5`, nebo rozšíření `interface_mapping`
v `run.yml` o `lo0.0`/`irb.2` jako vlastní kroky s `--port`. Otevřený bod, viz „Co
zbývá" níže.

---

## Jak si vyrobit důkazy

```bash
WT=/home/rado/Desktop/scripts/migration-status-check/.claude/worktrees/multicast-checks
PY=/home/rado/Desktop/scripts/migration-status-check/pyats-venv/bin/python
cd "$WT"
PYTHONPATH=. "$PY" -m pytest tests/ -q; echo $?         # 0
PYTHONPATH=. "$PY" -m pytest tests/ --color=no           # "1379 passed, 1 skipped"
head -1 172.20.20.4.yml                                   # schema_version: 8
grep '"schema_version"' runs/mig01/snapshot_post_PTX1-POP1_et_0_0_8.json  # 12
PYTHONPATH=. "$PY" -m migration_validator.cli evaluate --run mig01
git log --oneline main..HEAD
```

---

## Vědomě uzavřeno, znovu neotvírat

> **Chybějící IGMP množina kaskáduje do SKIP, ne do nezávislého hledání v tabulce.**
> `multicast_forwarding_status` a `mvpn_cmulticast_status` bez `igmp_membership_report`
> dat vrátí jeden SKIP řádek. Alternativa (počítat rovnou z multicast tabulky bez ohledu
> na IGMP) byla zvážena a odmítnuta v návrhu — bez záměru nemá check s čím porovnávat, co
> tam „mělo" být.
>
> **MX nemá `instance="all"`.** Ověřeno probe (2026-09-02): MX vrací `<output>instance
> is not running</output>`. Dynamická enumerace VRF (`get-instance-information`) místo
> hardcodovaných jmen — collector nemá inventory a jména RI hardcodovat nesmí.
>
> **`forwarding_rate_pps` absence není nula.** EVO často vrací
> `<multicast-statistics-timed-out/>` i na živé routě — SKIP, ne BROKEN s fabrikovanou
> nulou.
>
> **BFD odstraněno z obou routerů záměrně** (uživatel 2026-09-03, nestabilita laborky).
> `KNOWN_LAB_ASYMMETRIES` zůstává prázdná n-tice; `test_bfd_check_really_reads_the_
> session_table` běží na syntetických faktech (konfigurovaný peer + `Up` session
> postavené ve tvaru `conftest.py::_facts_for`), ne na lab datech — mutant kill ověřen
> proti syntetickým datům, ne proti mizející lab session.
>
> **`inet.2` statiky uvnitř VRF se nepokrývají.** Jen globální `inet.2` → Core lo0.0.
> Rozhodnutí specu, ne implementátorská mezera.

---

## Co zbývá

### Z návrhu (`specs/2026-09-02-multicast-checks-design.md`, „Odloženo")

- **`igmp snooping membership` check** na přístupovém L2 portu
  (`get-igmp-snooping-membership-information`) — duplikuje pohled z IRB, odloženo záměrně.
- **Port checky (stav, chyby, optika) pro access porty v globální bridge-domain/vlan** —
  obecná mezera klasifikace, ne specifikum multicastu.
- **NEZAŘAZENO pro multicast routy bez vlastnící služby** — routa v multicast tabulce bez
  odpovídajícího servisního scopu dnes nikde nefiguruje jako NEZAŘAZENO.
- **`inet.2` statiky uvnitř VRF** — jen globální `inet.2` → Core lo0.0 je v scope.

### Render verifikace `runs/mig01` (tenhle úkol, viz sekce výš)

`runs/mig01` chybí celoboxová capture — bloky Core lo0.0 a IPVPN irb.2 nejdou vyrenderovat
přes `evaluate --run mig01`, protože per-port inventory je nikdy nedá do scope. Vyžaduje
laborku, mimo rozsah tohoto úkolu.

### Drobnosti odložené z Úkolů 1–10 (ledger)

- `_fixture_paths` glob (`name.xml` + `name.N.xml`) oslabuje mutační sílu na počty
  v `rpc_names` — zvážit cílený invariantní test, pokud finální review bude chtít.
- Žádná `detection_reason` věta pro čistý IPVPN+igmp bez `mvpn` (Úkol 3) — subtyp zůstává
  `None`, jen bez vysvětlující věty v poli `detection_reason`.
- `NO_REPORT_SKIP` (Úkol 7) nevyužito — konstanta existuje, ale žádný check ji zatím
  nečte.
- Neošetřený `int()` na rate (Úkol 7) — vstup se dnes vždy validuje dřív, ale bez
  explicitní ochrany.
- `_upstream_ok` (Úkol 8) se vyhodnocuje dvakrát v `_stream()` — funkčně neškodné,
  kosmetický duplikát výpočtu.
- Baseline `assign_sources` (Úkol 9) používá prefixy ze subject scope, ne baseline scope
  — stabilní identifikátor napříč migrací, ale bez komentáře v kódu proč.
- Summary/SKIP řádky u `core_multicast_forwarding` (Úkol 9) mají `group=None` — nejasné
  při víc než jedné `inet.2` statice na stejném lo0.0 scope.
- Zmizelý baseline c-multicast záznam (Úkol 10) dá `OK`, ne `DEGRADED` — asymetrie proti
  chování IGMP checku, kde zmizelá skupina je `DEGRADED`.
- `_cmulticast_entry` (Úkol 10) nezachytává `TypeError` na neočekávaně tvarovaném vstupu.

### Přenesené ze starších vln (stále otevřené)

- **Bod 21** — dual-homed ESI `df_role` se neasertuje (`checks/evpn.py`), jen se
  formátuje do zprávy. Tahle vlna se EVPN logiky nedotkla.
- **Fáze 5** — EX mezi EVO a CPE, formát manifestu (`role: l2-switch`) existuje, zapojení
  do `capture`/`evaluate` odložené.
- **iBGP BFD na loopbacku** — vědomě odložené rozhodnutí z vlny 2026-08-26.
- **MGMT `irb.4094`/`ae0.4094` drift** (poznámka Úkolu 5c): mezi `.4` a `.5` capture přešly
  z inactive na active — reálný drift v laborce, nevyšetřeno, nesouvisí s multicastem.

---

## Pravidla do další vlny

**Mutant „podle briefu" se občas změří jinou cestou, než popisuje zadání — a ta cesta patří
do zápisu.** Mutant #10 (`stream_rows`, `raw_pps is None` → `raw_pps == 0`) reálně spadl na
`TypeError`, ne na chybnou asserci — stejný vzorec jako BFD mutant ve vlně 2026-08-26.
Recept se opakuje: „tvrzení o mutantovi se ověřuje spuštěním, a spuštěná varianta (i když
se liší od doslovného popisu) se zapisuje."

**Per-port `runs/mig01` capture nestačí na ověření checků vázaných na role bez vlastního
migračního portu.** Core loopback a MVPN VRF IRB nejsou svázané s jedním fyzickým portem
migrace — dokud `runs/mig01` obsahuje jen per-port snímky, tyhle dvě role (a jejich checky)
nejdou nikdy ověřit přes zdokumentovaný `evaluate --run` postup. Kdo příště potřebuje
end-to-end render verifikaci mimolokálních rolí (Core lo0.0, cokoliv na device-wide
scope), musí buď použít celoboxovou capture, nebo si předem naplánovat `interface_mapping`
krok, který danou roli pokryje.
