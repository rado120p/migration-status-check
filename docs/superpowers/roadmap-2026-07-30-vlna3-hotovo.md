# Vlna 3 hotová — deaktivovaná konfigurace, stav k 2026-07-30

**Výchozí bod:** větev `vlna3-deaktivovana-konfigurace`, úloha 12 (poslední).
**588 testů zelených, 1 přeskočen.** Zámek parserů drží na 146 řádcích.
`git stash@{0}` (inventory schema 2 z 2026-07-30 13:55) je pořád ve stashi —
nedropoval se.

Zadání je „Vlna 3" v [`roadmap-2026-07-29-vlna2-hotovo.md`](roadmap-2026-07-29-vlna2-hotovo.md),
konkrétně body 1, 2, 4 a 5 odtud. Hotovou práci popisuje dokumentace v
`docs/cs/` a `docs/en/` (sekce „Deaktivovaná konfigurace nevyrábí záměr" v
`files/parsers.md`) — sem se opisovat nemá.

---

## Co vlna 3 přinesla

- **AR-19** — `_is_inactive()` dědí z předků dolů, ne jen z uzlu samotného.
  Deaktivovaný kontejner (`routing-instances`, `protocols`/`bgp` přes
  ancestor walk z `neighbor`) teď zabírá stejně jako deaktivace jednotlivé
  položky pod ním.
- **AR-20 / AR-21** — inventory schema 4: `ServiceEntry` má
  `routing_instance_active` a `interface_active` místo jednoho pole `active`;
  `Scope` je nese dál (snapshot schema taky 4). Služba se při deaktivaci
  z inventory **nevypouští** — jen se označí. Validator ji SKIPne s důvodem
  `interface deactivated` / `routing instance deactivated`, místo aby počítal
  FAIL/WARN, jako by běžela.
- **AR-29 (tahle úloha)** — regenerace `172.20.20.{4,5}.yml` a fixture proti
  laborce, přenahrání `tests/fixtures/rpc/junos-evo/interfaces.xml`.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # 588 passed, 1 skipped
diff mx_parser.py evo_parser.py | wc -l    # 146
git stash list                             # stash@{0} porad tam
```

**Laborka:** `172.20.20.4` (vMX, platforma `junos`), `172.20.20.5`
(PTX10002-36QDD, platforma `junos-evo`), uživatel `admin`, autentizace
**heslem, ne klíčem**. `MIG_LAB_PASSWORD` je v `~/.bashrc` pod stráží na
neinteraktivní shell — načíst explicitně (`eval "$(grep -h
MIG_LAB_PASSWORD ~/.bashrc)"`).

Laborka teď modeluje stav **po migraci**: šest ze sedmi adres na
deaktivovaných rozhraních koliduje se živými, takže ji zpátky zapínat nejde
(ověřeno v úloze 12).

---

## Co v úloze 12 kroku 7 vyšlo jinak, než plán čekal

Plán (task-12-brief.md) předpovídal kaskádu testovacích oprav podle tabulky
v kroku 7. Skutečnost byla užší — jen dva testy v `tests/test_end_to_end.py`
spadly a jeden parametr conformance testu se odstranil, ne kvůli
kolabujícím počtům služeb, ale ze dvou konkrétních důvodů, které se lišily
od predikce:

1. **`ge-0/0/3` na `.4` (CPE24) je taky deaktivované.** Plán předpokládal
   deaktivaci jen na `ge-0/0/2`, `ge-0/0/4`, `ge-0/0/5` — proto 12 výskytů
   `interface_active: false` na `.4`, ne predikovaných ~10. Ověřeno přímo
   z RPC `get-configuration`: `<interface inactive="inactive"><name>ge-0/0/3</name>`,
   včetně `routing_instance_active: false` (RI `EVPN-VPWS-CPE24-UNI` je taky
   deaktivovaná). Na `.5` je `ae0.224` (stejná služba) naopak plně aktivní —
   asymetrie mezi stranami je reálný stav laborky, ne chyba parseru.
2. **Na `.5` přibyly `ae0.4094` a `irb.4094` (MGMT plumbing), ne 2 nové
   výskyty predikované jen z `et-0/0/10`.** Tahle MGMT VLAN infrastruktura
   v laborce dřív neexistovala vůbec (stará fixture ji nemá). `irb.4094` má
   navíc `description: null` — legitimní služba bez popisku, na kterou
   narazil test `test_service_without_ipv6_has_no_ipv6_section` (viz níž).
3. **`test_scoping/test_builder.py` a `test_capture.py` žádné globální
   počty služeb neasertují** — jen jednotlivé scopy z ručně sestavené
   inventory nebo `len(scopes) == 1` na syntetickém vstupu. Tabulka v plánu
   („počty služeb vzrostou, oprav čísla") na nich nesedí, protože takové
   asserty v repu neexistují; nebyl to regenerací nic rozbito.
4. **`test_specific_check_sees_data[junos-static_route_status]` po
   regeneraci pořád PASSuje** — ale ne poctivě. Plán čekal FAIL a doporučoval
   parametr odstranit, protože jediný způsob, jak dřív procházel, byl
   manufakturovaný FAIL `neni v tabulce` na `L3VPN-CPE13-NNI` (statika na
   deaktivovaném `ge-0/0/2.113`). Po regeneraci `ge-0/0/2.113` z inventory
   zmizelo (deaktivovaná jednotka na zařízení neexistuje), ale **stejný
   mechanismus se objevil jinde**: nová služba `irb.4094`/MGMT je aktivní
   (`interface_active: true`), má reálnou statiku `MGMT.inet.0
   10.40.95.0/24`, ale `tests/fixtures/rpc/junos/routes.xml` tuhle tabulku
   nemá (nebyl přenahraný — brief žádal přenahrát jen `junos-evo`
   `interfaces.xml`). Check tedy vydá FAIL "neni v tabulce" ze stejného
   důvodu jako předtím, jen na jiné službě. Parametr byl **přesto
   odstraněn** (viz níž) — ponechání by jen skrylo, že se falešná pozitivní
   pokrytí přesunula, ne zmizela. Skutečný seam drží
   `test_static_route_check_really_reads_the_routing_table` na `junos-evo`,
   ověřeno že pořád vyžaduje a dostává reálný PASS z přečtené routovací
   tabulky.

## Testy, jejichž očekávání se změnilo, a proč

| test | kategorie | důvod |
|---|---|---|
| `tests/test_end_to_end.py::test_service_without_ipv6_has_no_ipv6_section` | (a) laborka teď má legitimní službu (`irb.4094`) s `description: null` | test pomocník `_block_of` volaný s `scope.identity["description"]` přímo spadl na `TypeError`, protože `None` `not in` řetězec. Produkční kód (`reporting/view.py:151`) tenhle případ už řeší (`identity.get("description") or scope.scope_id`) — to je důkaz, že jde o mezeru v testovacím pomocníkovi, ne o regresi kódu. Oprava: volací místo v testu použije stejný fallback. |
| `tests/test_end_to_end.py::test_full_migration_run_is_green` → přejmenováno na `test_full_migration_run_has_no_unexplained_fail_or_warn` | (a) laborka po regeneraci nese reálné, různé stavy deaktivace mezi `.4` a `.5` | `deactivation_state` (AR-22/AR-23) teď správně hlásí FAIL na `MGMT-VLAN`/`irb.4094` (baseline aktivní → subject deaktivováno) a WARN na CPE13/CPE14/CPE24 službách (baseline deaktivováno → subject aktivní) — to je přesně navržené chování, ne regrese. Test už neasertuje `fail == 0`/`warn == 0` (fixture pár už nemodeluje čistou migraci beze změn), ale že **žádný jiný check** než `deactivation_state` nevrátí FAIL/WARN, a že směr nálezů `deactivation_state` odpovídá AR-23 matici (`migrace nedokoncena` pro FAIL, `ted je aktivni` pro WARN). |
| `tests/collectors/test_conformance.py::test_specific_check_sees_data[junos-static_route_status]` | (a), ale s upřesněním — viz sekci výš | parametr odstraněn podle plánu; komentář u parametrizace vysvětluje, že důvod odstranění se přesunul z `L3VPN-CPE13-NNI` na `irb.4094`/MGMT, ne že zmizel |

Žádný z nálezů nebyl (b) — kód se v úloze 12 neměnil, jen inventory,
fixtures a testovací očekávání.

---

## Co zbývá

### 1. Příznaky na hlubších úrovních konfigurace

Deaktivovaná jednotlivá `route`, `bfd-liveness-detection` nebo `neighbor` se
dál vypouští ze záměru **beze stopy** — bez vlastního příznaku, jaký mají
`routing_instance_active`/`interface_active` na úrovni celé služby. Vědomě
odloženo, mimo rozsah (viz spec).

### 2. MX-specifické parsování BFD zůstává nepokryté na živých datech

Laborka na `172.20.20.4` nemá **ani jednu** BFD session — ověřeno
2026-07-30 na fixture i na surovém RPC capturu z `record`. Šev
collector→check je pokrytý (vlna 2, úloha 2), samotné parsování MX BFD
odpovědi na reálném XML ne. Pokryje se, až v laborce nějaká MX BFD session
bude.

### 3. Report

- **F-2** — podřádky se jménem RIB u statických rout.
- **F-11** — dvě kopie logiky pro link-local adresy.
- **F-14** — `unassigned.static_routes`/`.bfd_sessions` jde jen do JSON,
  textový report je nevypisuje (preexistující, zděděno z vlny 2 bodu 3).

Vlastní spec, mimo rozsah téhle vlny.

### 4. Dokumentační dluh mimo rozsah úlohy 12

`docs/cs/files/models.md:56` a `docs/en/files/models.md:56` pořád píšou
`INVENTORY_SCHEMA_VERSION = 3` a popisují jediné pole `active` — nebyly
v seznamu souborů k opravě pro tuhle úlohu (ten byl `reference.md` a
`files/parsers.md`, oba jazyky), a oprava by otevřela širší audit celého
`models.md` (pole `active` → `routing_instance_active`/`interface_active`
na víc místech, ne jen řádek 56). Zaznamenáno, ne opraveno — příští drobná
úloha na dokumentaci.
