# Vlna 2 hotová — BFD a statické routy, stav k 2026-07-30

**Výchozí bod:** větev `vlna2-bfd-a-staticke-routy` je zmergovaná do `main`.
**547 testů zelených, 1 přeskočen.** Zámek parserů drží na 146 řádcích.

Zadání, proti kterému se to dělalo, je „Vlna 2" v
[`roadmap-2026-07-29-dalsi-kroky.md`](roadmap-2026-07-29-dalsi-kroky.md).
Návrh je v [`specs/2026-07-29-bfd-a-staticke-routy-design.md`](specs/2026-07-29-bfd-a-staticke-routy-design.md)
(AR‑11 … AR‑17), plán v [`plans/2026-07-29-bfd-a-staticke-routy.md`](plans/2026-07-29-bfd-a-staticke-routy.md).
Tenhle soubor nese **jen to, co z vlny 2 zbylo na příště** — hotovou práci
popisuje dokumentace v `docs/cs/` a `docs/en/`, sem se opisovat nemá.

---

## Co vlna 2 přinesla

Dvě schopnosti, které nástroj předtím nekontroloval vůbec:

- **Statické routy** — parsery čtou `routing-options`, záměr má identitu
  `(RIB, prefix)` a next-hop je hodnota (AR‑12). `RoutesCollector` +
  `StaticRouteStatusCheck`.
- **BFD pro BGP** — parsery jdou hierarchií dědění `neighbor > group >
  protocols bgp` a přepisují **celou hodnotu**, ne položku po položce
  (AR‑13). `BfdCollector` + `BfdSessionStateCheck`.

Schema inventory i snímku je na verzi **3**.

---

## Co zbývá — v doporučeném pořadí

### 1. Guardy na `inactive` pro zbývající kontejnery (průřezová změna)

Deaktivace těchto kontejnerů dnes **vyrobí záměr**, tedy falešný FAIL:

| kontejner | co zůstane živé | proč to nešlo udělat ve vlně 2 |
|---|---|---|
| `protocols`, `bgp` | BFD **i** BGP záměr celé úrovně | tentýž XPath `protocols/bgp` čte i `_parse_bgp_neighbors()` — ošetřit to jen pro BFD by znamenalo, že se BFD a BGP na deaktivovaném `protocols bgp` neshodnou |
| `routing-instances` (kontejner, ne jednotlivá `instance`) | statiky všech VRF | totéž — kontejner čte i `_parse_routing_instances()` |
| `interfaces`, `interface`, `unit` | služba i s jejími statikami | spadá do už odloženého rozhodnutí o poli `active` (bod 2) |

Ověřeno na obou parserech: `<routing-instances inactive="inactive">` vrací
statiku `('L3VPN-TEST.inet.0', '10.9.9.0/24')` jako živou.

Je to **jedna změna napříč parsováním BGP i routing-instancí**, ne detail
BFD — proto vlastní úloha a ne dodatek. Každý guard potřebuje vlastní
pokrytí; nepokrytý guard je stejná chyba jako chybějící.

Kontext v `docs/cs/files/parsers.md`, sekce „Deaktivovaná konfigurace
nevyrábí záměr" — výčet neošetřených kontejnerů je tam už zapsaný.

### 2. Pole `active` — číst ho a přejmenovat

Dvě věci, které spolu souvisí:

- `StaticRouteStatusCheck` pole `active` **nečte** (rozhodnutí uživatele
  z vlny 2: odložit). Neaktivní routa v tabulce tedy projde jako PASS.
- `RoutingInstance.active` je stav **routing-instance**, ne rozhraní. Jméno
  mate a mimo `models/inventory.py` ho nikdo nečte. Přejmenování je změna
  viditelná ve schématu, takže potřebuje vlastní bump verze.

### 3. `unassigned` v textovém reportu

`unassigned.static_routes` a `unassigned.bfd_sessions` jdou dnes **jen do
JSON** — `reporting/text_report.py` vypisuje `result.unmatched`, ne
`result.unassigned`. Bezpečnostní argument specifikace („routa, kterou
parser nepřečetl, tiše nezmizí") tedy platí jen pro konzumenta JSONu.
Preexistující — `bgp_peers` na tom byly stejně. Patří k **F‑14**.

### 4. Regenerovat inventory — laborka má novou službu, kterou schema 3 nezná

Při mergi vlny 2 (2026‑07‑30) byly v pracovní kopii `main` necommitnuté
`172.20.20.4.yml` a `172.20.20.5.yml` ze **schema 2**, vyrobené téhož dne
v 13:55 proti laborce, která už měla novou službu **`EVPN-VPWS-CPE24-UNI`**
(`ae0.224` na `.5`, `ge-0/0/3` na `.4`). Commitnutá schema‑3 inventory je
z běhu z 2026‑07‑29 21:53, takže tu službu **neobsahuje**.

Ty soubory jsou ve stashi:

```bash
git stash list   # "inventory schema 2 z 2026-07-30 13:55 - … EVPN-VPWS-CPE24-UNI …"
git stash show -p stash@{0}
```

Udělat se má **regenerace proti současné laborce novým parserem**, ne
oživení stashe — schema 2 je po vlně 2 zastaralý formát. Pozor, že se tím
mění i `tests/fixtures/172.20.20.{4,5}.yml`, takže to může pohnout
očekáváními v testech (počty služeb, conformance) — je to samostatný kus
práce, ne dodatek.

### 5. Přenahrát `tests/fixtures/rpc/junos-evo/interfaces.xml`

Fixture předchází přestavbě laborky a nemá `irb.15` / `ae0.15`, takže
conformance test službu CPE14 nevidí. Ta cesta **je** ověřená proti živému
zařízení, ale ne v CI.

### 6. Conformance test — dvě slabiny švu collector→check

Obě preexistující, obě odhalené až mutačním testováním ve vlně 2:

- **`pytest.skip` na chybějící fixture skryje přejmenovanou oblast.**
  Přejmenování `RoutesCollector.name` shodí celý modul do skipu —
  `527 passed / 19 skipped, nula failů`. „Prošlo" tam znamená „neproběhlo".
  Doporučení: asertovat, že `{collector.name for collector in COLLECTORS}`
  odpovídá jménům fixture, které na disku skutečně jsou.
- **MX šev pro `bfd_session_state` je nepokrytý.** Parametr
  `("junos", "bfd_session_state")` chybí záměrně — ta fixture je zámyslem
  prázdný výpis (BGP `Idle` na obou peerech), takže check správně vrací
  všechno SKIP. Důsledek ale je, že na MX by přejmenovaný klíč byl
  neviditelný.

Související zjištění, které stojí za zapamatování: **`requires` nechrání
proti checku, který čte klíč, jaký žádný collector nevydává.** Hlídá
výhradně `ctx.failed_collectors` (`checks/base.py:135`). `requires =
("route",)` místo `("routes",)` projde celou sadou.

### 7. Tři stále nediskriminující testy

- `tests/models/…` `test_mapping_list_rejects_scalars` — `match="mapping"`
  sedí i na jméno `tmp_path` adresáře. (Dnes diskriminuje, ale z nesprávného
  důvodu — je to náhoda, ne návrh.)
- `test_device_scope_reports_state_without_intent` v testech routes **i** bfd
  — `device_scope()` má vždy prázdné selektory, takže `configured` je `False`
  bez ohledu na větvení.

---

## Dvě pravidla do plánu vlny 3

Vlna 2 našla **osm** testů, které procházely proti špatné implementaci (sedm
za deset úloh, osmý až ve scoped re-review). Vzorec je systémový, ne
nahodilý, a v obou případech pochází z předepsaného testovacího kódu v plánu.

1. **Každý předepsaný test musí říct, kterou špatnou implementaci zabíjí.**
   Test, který popisuje správné chování, aniž by ho odlišil od nesprávného,
   se čte jako pokrytí a není jím.
2. **U parametrizovaného testu se mutant pouští na každou větev
   parametrizace zvlášť.** Jeden mutant v `mx_parser.py` neříká nic o
   `evo_parser.py`. Zámek 146 řádků je pro test-only změnu nutná, ne
   dostatečná podmínka.

A jedno pravidlo k měření, které se ve vlně 2 vyplatilo dvakrát:
**vždy grepem potvrdit, že mutant dopadl tam, kam měl.** Neaplikovaný mutant
vypadá identicky jako nediskriminující test. Konkrétně: `str.replace(…, 1)`
zasáhl byte-identický řádek v jiné funkci; `requires = ("route",)` nedělá
nic; a přejmenování jména collectoru shodí modul do skipu.

---

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest              # 547 passed, 1 skipped
diff mx_parser.py evo_parser.py | wc -l # musí být 146
```

Captureje pro ostré ověření jsou **na disku, ne v repu** (`runs/` je
v `.gitignore`). Co ve `runs/bfd-static-2026-07-29/` **zbylo**:

| cesta | co to je |
|---|---|
| `cfg/172.20.20.{4,5}.{raw,inherit}.xml` | konfigurace, ze kterých se dá parser ověřit offline (u `.5` pozor na stáří, viz níž) |
| `cfg-pre-group-bfd/` | tatáž konfigurace **před** tím, než se do skupiny `CPE14` přidalo BFD |
| `rpc/172.20.20.{4,5}.{bfd,bfd_detail,route_static,route_static_all}.xml` | surové RPC odpovědi, ze kterých vznikly fixture pro testy |

**Snímky `pre.json` a `post.json` už neexistují** — ležely ve worktree větve
a zmizely s ním (`runs/` není trackované, takže je merge nezachoval). Nové se
udělají proti laborce:

```bash
.venv/bin/python -m migration_validator.cli capture --help
```

Nikdy `.venv/bin/mig-validate` ve worktree — shebang dá `.venv/bin` na
`sys.path[0]` a obejde `pythonpath`, takže se natáhne balík z nadřazeného
repa. Vždy `python -m migration_validator.cli`.

Pozor u `.5`: capture nese `commit-localtime="2026-07-29 11:43:50 UTC"`, tedy
stav **před** přestavbou CPE14 na `ae0.15` / `irb.15`, kdežto commitnutá
inventory je z živého běhu **po** ní. Offline reparse `.5` proto nedá
`ae0.15` a `irb.15` má `description: null` — je to rozdíl v laborce, ne
v parseru. U `.4` je offline reparse byte-identický s commitnutým YAML.

**Laborka:** `172.20.20.4` (vMX, platforma `junos`), `172.20.20.5`
(PTX10002‑36QDD, platforma `junos-evo`), uživatel `admin`, autentizace
**heslem, ne klíčem**.

Poznámka ke stavu laborky: všech 9 BGP session na `.5` je od 2026‑07‑29
`Established` (dřív `Idle`), takže větev `SKIP / BGP neni Established` v ní
už není dosažitelná. Kód je správný, jen v téhle topologii neprocvičený —
v commitnutých fixture ji vidět je.
