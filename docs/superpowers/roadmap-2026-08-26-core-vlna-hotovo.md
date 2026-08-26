# Core vlna hotová — transit/loopback checky, stav k 2026-08-26

**Výchozí bod:** větev `core-transit-loopback-checks` založená z `main`, dvanáct úloh
hotových (Úkoly 1–12). **1220 testů sebraných, 0 FAIL** (`pyats-venv/bin/python -m pytest
tests/ -q`, exit 0; jeden `s` v průběhovém výpisu = 1 skip; výchozí stav vlny byl 1089
testů). Zámek parserů drží na **8 řádcích** (`diff mx_parser.py evo_parser.py | wc -l`).
Schema **inventory 6 → 7**, **snapshot 10 → 11**.

Zadáním bylo rozdělit službu `Core` na dvě role (`transit`/`loopback`) a pokrýt tranzitní
Core rozhraní a lo0.0 protokolovými checky, které dřív chyběly úplně (IS-IS adjacency,
IS-IS interface, IS-IS overview, LDP neighbor, PIM neighbor, MPLS interface, BFD podle
rozhraní). Návrh je
[`specs/2026-08-26-core-transit-loopback-checks-design.md`](specs/2026-08-26-core-transit-loopback-checks-design.md),
provedení
[`plans/2026-08-26-core-transit-loopback-checks.md`](plans/2026-08-26-core-transit-loopback-checks.md).

---

## Co vlna přinesla

**Inventory a parser (Úkoly 1–4).** `_classify` v obou parserech (MX i EVO) vrací pro
Core rozhraní `service_subtype` — `"loopback"` pro `lo0.*`, `"transit"` pro ostatní
s family iso/mpls. Parser navíc čte `protocols pim interface <name>` do existujícího pole
`ServiceEntry.protocol` (žádné nové pole) a rozlišuje interní BGP peery: explicitní `type
internal` na neighbor/group, bez příkazu fallback na `peer-as == local-as`. Interní peeři
se přiřazují do `bgp_neighbor` záznamu **Core loopback** (lo0.0), ne do zákaznické služby
ani do NEZAŘAZENO — a **nedostávají BFD záměry** (`_assign_bfd` má na rozšíření filtru
`_assign_bgp_neighbors` explicitní gate, commit `42df918`). Inaktivní interní peeři jdou
do nového `bgp_neighbor_inactive`, ne do ztracena (oprava `80024df`). Fixtures šesti
nových RPC (obě platformy) nahrané z živé laborky, commit `b1af611`.

**Sběr (Úkoly 5–6).** Šest nových collectorů (`isis.py` — tři třídy, `ldp.py`, `pim.py`,
`mpls.py`), snapshot schema 10 → 11. Všechny sdílí namespace-agnostickou iteraci
(`{*}element`) a `_localname_text`/`_seconds_attr` helpery z `isis.py`. LDP collector
zahazuje `lo0.*` záznamy už při parsování (targeted session, ne stav tranzitního linku).
PIM collector nedostává syntetický záznam pro `pim-interface` bez `pim-neighbor`.

**Scoping (Úkol 7).** `Check.service_subtypes` (AND k `service_types`) rozlišuje Core
transit od Core loopback. `Scope.select()`: pět per-interface areas se vybírá přes
`matches_interface`; `isis_overview` (device-global) jde jen do Core loopback scopu; BFD
dostal druhou cestu výběru — na Core transitu podle rozhraní session, vedle stávající
cesty přes peer adresu.

**Checky (Úkoly 8–11).** Sedm nových checků v `checks/core_protocols.py`:
`isis_adjacency_state`, `isis_interface_info` (jeden check, role-aware Passive flag),
`ldp_neighbor_state` a `bfd_transit_state` (LDP i BFD očekávané na transitu vždy, bez
záměru), `pim_neighbor_state` (gate na `protocols pim`, ticho bez záměru — ne SKIP),
`mpls_interface_state`, `isis_overview` (overload bit, jen loopback). Starý
`bfd_session_state` dostal `applies_to()` gate proti Core transitu, aby na tranzitu
nevypisoval `WARN | bez konfigurace` za každou session bez záměrové konfigurace.

**Úkol 12 (tenhle):**
- **Mutanty 5/5 zabity**, ověřeno spuštěním (viz tabulka níže).
- **Dokumentace cs+en** — `checks.md`, `collectors.md`, `reference.md`, `models.md`
  (`Selectors.protocols`, `Scope.select()` nové areas), plus dohledané mezery ze
  starších vln (EN `models.md` chyběla věta o aktuálním `SCHEMA_VERSION`, catalog
  citoval starý commit hash).
- **Re-capture `runs/mig01`** na živé laborce (172.20.20.4 MX, 172.20.20.5 EVO,
  `capture --run mig01 --parse-services`, auth `admin`/password) — schema
  `snapshot: 11`, `inventory: 7`. `runs/` je gitignored, snímky žijí jen v tomhle
  worktree.
- Kořenové `172.20.20.{4,5}.yml` přegenerovány stejným postupem, schema 7.

## Jak si vyrobit důkazy

```bash
# pyats-venv/ zije v hlavnim repu, ne v tomhle worktree - absolutni cesta
WT=/home/rado/Desktop/scripts/migration-status-check/.claude/worktrees/core-transit-loopback-checks
PY=/home/rado/Desktop/scripts/migration-status-check/pyats-venv/bin/python
cd "$WT"
"$PY" -m pytest tests/ -q; echo $?
  # exit 0; pyats 9.1.1 na tomhle stroji netiskne souhrnny radek,
  # pocet testu overuje --collect-only (viz nize)
"$PY" -m pytest tests/ --collect-only -q | \
  awk -F': ' '{sum+=$2} END{print sum}'   # 1220
diff mx_parser.py evo_parser.py | wc -l   # 8
head -1 172.20.20.4.yml                   # schema_version: 7
head -1 172.20.20.5.yml                   # schema_version: 7
# runs/mig01 je gitignored - existuje jen lokalne v tomhle worktree,
# po re-capture (postup v sekci "Co vlna prinesla" vys)
grep '"schema_version"' runs/mig01/snapshot_pre_172.20.20.4_all.json
grep '"schema_version"' runs/mig01/snapshot_post_172.20.20.5_all.json
  # oba: "schema_version": 11,
grep schema_version runs/mig01/inventory_172.20.20.4_all.yml
grep schema_version runs/mig01/inventory_172.20.20.5_all.yml
  # oba: schema_version: 7
"$PY" -m migration_validator.cli evaluate --run mig01
git log --oneline main..HEAD
```

---

## Mutanty (Úkol 12, krok 1)

| mutant | soubor:řádek | zabíjí |
|---|---|---|
| `Outcome.DEGRADED` → `Outcome.OK` ve větvi „Up teď / Down v baseline" | `checks/core_protocols.py::IsisAdjacencyStateCheck._rows` | `test_baseline_state_down_before_up_now_is_warn` |
| `ok = passive if loopback else not passive` → `ok = passive` | `checks/core_protocols.py::IsisInterfaceInfoCheck.run` | `test_transit_non_passive_level2_is_pass`, `test_transit_passive_level2_is_fail`, `test_full_migration_run_has_no_unexplained_fail_or_warn` |
| smazání gate `if "pim" not in ctx.scope.selectors.protocols` | `checks/core_protocols.py::PimNeighborStateCheck.run` | `test_pim_neighbor_without_intent_is_silent_not_skip`, `test_full_migration_run_has_no_unexplained_fail_or_warn` |
| smazání větve `if not entries` | `checks/core_protocols.py::BfdTransitStateCheck.run` | `test_bfd_transit_missing_session_is_fail_down` |
| smazání podmínky `if self.service_subtype == "loopback"` u `isis_overview` | `models/scope.py::Scope.select` | `test_isis_overview_goes_only_to_loopback_scope` |

Všech 5/5 potvrzeno `pytest tests/ -x -q` → FAIL, pak revert → suita zpátky zelená.
Docstringy testů nesou přesný popis mutanta a datum ověření (projektová konvence:
tvrzení o mutantovi se ověřuje spuštěním, ne z paměti).

**Poznámka k mutantu BFD:** doslovné smazání `if not entries: ... continue` by nechalo
`sorted(None)` spadnout na `TypeError` (crash celého běhu, ne měřitelný FAIL jednoho
testu). Spuštěná varianta byla `entries = by_interface.get(name) or []` — sémanticky
totéž (žádný speciální BROKEN řádek pro chybějící session), ale bez pádu interpretu.
Tenhle rozdíl je zaznamenaný v docstringu testu i tady, aby nezůstal jen v paměti.

---

## Co vyšlo jinak, než plán čekal

### 1. Referenční dokumentace nesla dluh ze čtyř předchozích vln, ne jen z týhle

`docs/cs/reference.md` § „Formát snapshotu" tvrdil `schema_version: 7` jako aktuální —
zbytek historie (bumpy 7→8, 8→9, 9→10) nikdy nebyl zapsán, takže tabulka verzí končila
u bumpu, který svého času proběhl při vlně EVPN instance. Oprava: aktuální hodnota na 11,
nový řádek `10 → 11` s popisem tyhle vlny, a **výslovně přiznaná mezera** mezi 7 a 10 —
žádné vymýšlení historie, kterou nikdo nezapsal. Stejný vzorec (dohledaná, ne vymyšlená
mezera) platí i pro `Selectors` v `models.md`, kde chyběly `bgp_neighbors_inactive` a
`lag_members` v souhrnném výčtu — doplněny, ale bez fabrikace důvodu jejich vzniku.

### 2. Anglická `models.md` byla o jednu větu chudší než česká už před touhle vlnou

CS verze `Snapshot` sekce nesla „Aktuální `SCHEMA_VERSION = 10`" — EN verze tu větu
neměla vůbec, takže bump na 11 v ní nešlo prostě najít-a-nahradit. Doplněno jako nová
věta na stejném místě, ne jako mechanický zrcadlový překlad chybějícího textu.

### 3. Mutant na BFD musel ustoupit sémantice, ne doslovnému znění brief

Brief řekl „smaž větev `if not entries`" — doslovná realizace by shodila `sorted(None)`
na výjimku dřív, než by check stihl vyprodukovat měřitelný nález. Skutečně spuštěná
varianta (`or []`) zachovává úmysl mutanta (žádný BROKEN řádek pro chybějící session) bez
zbytečného pádu interpretu, a přesně tenhle rozdíl je zapsaný v docstringu testu i
v tabulce výš — ne mlčky nahrazen bez poznámky.

### 4. Auth na laborce vyžadoval jiný účet, než README defaultně předpokládá

Krok 1 README (`mx_parser.py`/`evo_parser.py` bez přepínačů) čeká SSH klíč a uživatele
`ansible` — na tomhle stroji žádný klíč neexistuje (`~/.ssh/id_rsa` chybí) a `ansible`
s heslem z `MIG_LAB_PASSWORD` autentizaci odmítl. Fungující kombinace byla `--auth
password -u admin`. Sdílený `~/.config/mig-validate/auth.yml` navíc míří na
`MIG_PROD_PASSWORD`, ne na laborku — pro tenhle běh vznikl samostatný `--auth-file`
s `password_env: MIG_LAB_PASSWORD`, aby default auth soubor běh nezablokoval.

---

## Vědomě uzavřeno, znovu neotvírat

> **PIM na Core transitu je gate na záměr, ne univerzální očekávání.** LDP a BFD jsou na
> tranzitním Core rozhraní očekávané vždy (chybí-li, FAIL); PIM je očekávaný **jen** tam,
> kde je rozhraní pod `protocols pim` — bez záměru check mlčí (žádné řádky), ne SKIP.
> Varianta „PIM vždy očekávaný jako LDP/BFD" byla zvážena a odmítnuta (brainstorm
> 2026-08-26): služba bez PIM není méně zdravá.
>
> **`isis_interface_info` zůstává jeden check pro obě role**, ne dva oddělené checky.
> Role-aware větvení (`ok = passive if loopback else not passive`) je uvnitř jednoho
> `run()`, protože obě role měří totéž pole (`level 2 passive`) s opačným očekáváním —
> rozdělení na dva checky by duplikovalo kostru bez přidané hodnoty.
>
> **Starý `bfd_session_state` se na Core transitu nemaže, jen se odstřihává gate.**
> Varianta „přesunout celou BFD logiku do jednoho checku" byla zvážena a odmítnuta —
> zákaznická (záměrová) a tranzitní (rozhraní-based) BFD logika jsou dvě různé věci
> a slití by znamenalo držet je v synchronu bez potřeby.

---

## Co zbývá

### Bod 21 — Dual-homed ESI a neasertovaná DF role

Přenesené z [`roadmap-2026-08-04-vlna10-hotovo.md`](roadmap-2026-08-04-vlna10-hotovo.md),
otevřené od vlny 10 (resync fixtures 2026-08-04). Dual-homed ESI
`00:11:12:13:14:00:00:00:00:00` (rozhraní `ae0.14`, DF `150.0.0.12`) je v
`tests/fixtures/rpc/junos-evo/evpn_esi.xml`, ale `df_role` se nikde neasertuje — jen se
formátuje do zprávy (`checks/evpn.py:48,149,155`). Core vlna se ESI/EVPN logiky nedotkla,
bod zůstává otevřený beze změny.

### Fáze 5 — EX mezi EVO a CPE

Formát manifestu (`run.yml`, role `l2-switch`) tenhle případ nese od fáze 4
(`plans/2026-08-06-faze4-run-management.md`), ale zapojení do `capture`/`evaluate` je
odložené. Core vlna se `--run` manifestu nedotkla nad rámec toho, co už fáze 4 dala —
zůstává otevřené.

---

## Pravidla do další vlny

**Referenční dokumentace driftuje tiše přes víc vln, dokud ji někdo systematicky
nezkontroluje.** Tahle vlna našla ve `reference.md` a `models.md` (EN) mezery, které
nevznikly touhle vlnou — přišly z minimálně dvou předchozích (EVPN instance, agregátní
routy). Žádný test nehlídá, že `reference.md`/`models.md` odpovídají `SCHEMA_VERSION`
v kódu. Doporučení pro budoucí vlnu: buď test, který porovná zdokumentované číslo
s konstantou v kódu (regex nad markdown, ne ideální, ale chytí právě tenhle drift), nebo
pravidelná (ne jen „když se to hodí") revize referenční dokumentace.

**Mutant podle briefu se občas musí upravit, aby vůbec něco změřil — a ta úprava patří
do zápisu, ne jen do hlavy implementujícího.** BFD mutant `if not entries` v doslovném
znění crashuje dřív, než změří cokoliv. Recept z minulých vln platí beze změny („tvrzení
o mutantovi se ověřuje spuštěním"), jen se rozšiřuje o „a spuštěná varianta se zapisuje,
pokud se liší od zadání."

**Auth na živé laborce se nedá odvodit z README defaultu bez ověření.** Default (`ansible`
+ SSH klíč) je pro tenhle stroj nedosažitelný (žádný klíč, jiný účet). Kdo bude laborku
příště potřebovat, ověří kombinaci uživatel/auth typ dřív, než na ni staví celý capture
běh — ne až po prvním selhání.
