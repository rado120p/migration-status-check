# Vlna 8 — deaktivované podprvky zůstávají v záměru a hlásí SKIP

**Výchozí bod:** `main` na commitu `50703c8` (merge vlny 7), 640 testů
zelených, 0 přeskočených, zámek parserů 146 řádků, inventory i snapshot
schema 4.

**Zadáním je bod 1** v „Co zbývá" roadmapy vlny 7
([`../roadmap-2026-08-03-vlna7-hotovo.md`](../roadmap-2026-08-03-vlna7-hotovo.md)),
a to **v plném rozsahu včetně top-level `<routing-options inactive>`** —
rozhodnutí uživatele z 2026‑08‑04. Body 2, 5, 9 a 12 zůstávají otevřené a
tahle vlna se jich netýká.

Sémantika se v téhle vlně **neotvírá**. Rozhodnutí uživatele z 2026‑08‑03
zní: deaktivovaný prvek **zůstane v záměru a hlásí SKIP**, symetricky s
existujícím chováním deaktivované služby. Tiché vypouštění je vada, ne
rozhodnutí. Spec tohle rozhodnutí provádí, nerozporuje.

---

## Měření provedená před psaním tohoto specu

Roadmapa jmenuje čtyři spouštěče. Všechny čtyři byly změřeny průchodem
syntetického konfiguračního snippetu parserem, ne odvozeny z kódu.

| spouštěč | co parser dnes udělá |
|---|---|
| `<route inactive>` | routa zmizí ze záměru beze stopy |
| `<neighbor inactive>` | peer zmizí ze seznamu beze stopy |
| `<routing-options inactive>` (top-level) | zmizí celý podstrom rout naráz |
| `<bfd-liveness-detection inactive>` | peer zdědí BFD hodnotu ze skupiny |

Doslovný výstup prvních tří:

```
-- routy, routing-options aktivni:
[StaticRoute(rib='inet.0', prefix='10.0.0.0/8', next_hop=['1.1.1.1'])]
-- routy, top-level routing-options inactive:
[]
-- bgp sousede: ['192.0.2.1', '192.0.2.3']   # 192.0.2.2 je deaktivovany
```

### Nález 1 — čtvrtý spouštěč je korektní chování, ne vada

Změřeno: neighbor s deaktivovaným `bfd-liveness-detection` pod skupinou,
která BFD má, dá `{'minimum_interval': 1000, ..., 'source': 'group'}`.

Vypadá to jako tiché podstrčení špatné hodnoty, ale **není to vada.** Junosí
`inactive` znamená „příkaz se neuplatní", tedy jako by tam nebyl; skupinová
hodnota se pak na souseda skutečně vztahuje a na krabici BFD s
`minimum-interval 1000` opravdu poběží. `_bfd_node` vracející `None` je
správný tvar a fallback `or inherited` je správné chování.

**`_bfd_node` (`mx_parser.py:776`) se proto v téhle vlně nemění.** Žádný test
tenhle případ dnes netvrdí ani jedním směrem; rozhoduje sémantika Junosu.

Zapisuje se to sem proto, že to byl nález *tohohle* speccování, ne roadmapy —
a tudíž nedostal automaticky skepsi, kterou by dostal bod roadmapy. Pravidlo
„změř bod dřív, než pro něj napíšeš práci" (vlna 6) platí i na body, které si
autor specu vyrobil sám.

### Nález 2 — top-level případ není druhý mechanismus, je týž

`_is_inactive` (`mx_parser.py:406-420`) chodí po předcích. Změřeno přímo na
listu pod deaktivovaným top-level `routing-options`:

```
_is_inactive(route_node) pod deaktivovanym routing-options: True
_is_inactive(static_node): True
```

Deaktivace na *libovolné* úrovni nad listem je tedy na listu vidět. Plný
rozsah, který si uživatel vybral, proto **nestojí skoro nic navíc** — není
to druhý mechanismus, je to týž predikát zavolaný o patro níž.

### Nález 3 — kontejnerové guardy jsou dnes čirá redundance

Změřeno zkusmým zásahem, který byl potom vrácen (strom po vrácení čistý,
640 passed, zámek 146):

| zkusmý zásah | výsledek |
|---|---|
| zrušit **jen** čtyři kontejnerové guardy (639, 650, 664, 719) | **640 passed, 0 ripple** |
| zrušit navíc listový guard na `route_node` (725) | **12 failed, 628 passed** |
| zrušit navíc oba guardy na `neighbor_node` (603, 831) | **18 failed, 622 passed** |

První řádek je nález, ne potvrzení: **kontejnerové guardy dnes nic nedělají.**
Chození po předcích znamená, že cokoliv chytí ony, chytí i listový guard o
patro níž — jsou úplně zastíněné. Je to týž tvar, jaký vlna 4 našla u tří
redundantních guardů v `_is_inactive`, a přenáší se z něj i závěr: guard,
který je zastíněný, se maže bez náhrady a nic se tím nezmění.

Praktický důsledek pro plán: **jejich odstranění nesmí být samostatná úloha
s vlastním „testy zelené" kritériem** — to kritérium by splnilo i to, kdyby
implementer neudělal nic.

---

## Návrh

### 1. Parsery: jeden vzor, ne osm úprav

Precedent už v souboru je — `mx_parser.py:476` zapisuje
`active=not self._is_inactive(node)` místo aby uzel vypustil.

**Zrušit kontejnerové `continue`** (čísla řádků platí ke commitu `50703c8` a
jsou orientační, adresuje se vzorem):

| řádek | uzel |
|---|---|
| 639 | top-level `routing-options` |
| 650 | `instance` |
| 664 | `routing-options` uvnitř instance |
| 719 | `static` |

**Zapsat příznak na listu:**

- `route_node` (725) → `StaticRoute(..., active=not self._is_inactive(route_node))`
- `neighbor_node` v `_parse_bgp_neighbors` (603) → soused se zařadí do
  paralelního seznamu neaktivních místo vypuštění

**Nesahat na `_parse_bfd` (831) ani na `_bfd_node` (776).** BFD zůstává
v téhle vlně netknuté, obojí ze stejného důvodu: BFD je vlastnost **relace**,
a deaktivovaný soused žádnou relaci nemá, takže jeho BFD záměr nemá co
popisovat. `service.bfd` tedy pro deaktivovaného souseda zůstává prázdné,
přesně jak dnes tvrdí `tests/parsers/test_inactive.py:208`. SKIP za takového
peera vydá `checks/bgp.py` z `bgp_neighbors_inactive`, ne BFD check.

Zásah musí být **byte-identický v obou parserech**. Zámek
`diff mx_parser.py evo_parser.py | wc -l` **zůstává na 146** a je to
akceptační kritérium vlny, ne kontrola na konci.

Vědomé rozhodnutí u řádku 650, i s důsledkem nahlas: kontejnerový `continue`
na `instance` se ruší také, i když deaktivovanou VRF už pokrývá
`routing_instance_active` na scopu. **Na reportu to není vidět** — taková
služba se zkratuje na `checks/base.py` přes `Scope.is_deactivated` a
per‑řádkový SKIP se nikdy nevykreslí. Je to tedy změna *obsahu inventory*,
ne výstupu, a nemá paritu se zbylými třemi místy. Ruší se přesto, protože po
nálezu 3 je ten guard prokazatelně zastíněný a ponechat jediný zastíněný
guard z původních čtyř by byla výjimka bez užitku.

### 2. Nosič příznaku: tři tvary dat, ne jeden nosič

- **Statiky** — `StaticRoute` dostane pole `active: bool = True`.
  `_assign_static_routes` (`mx_parser.py:1528`) dělá `asdict(route)`, takže
  do inventory se pole propíše samo. `Selectors.static_routes` i
  `ServiceEntry.static_route` jsou už `list[dict]`; žádný ripple.
- **BGP peeři** — `bgp_neighbor` / `Selectors.bgp_neighbors` je `list[str]`
  a slouží jako **selektor**: členství se testuje na čtyřech místech
  (`engine.py:169`, `engine.py:223`, `scope.py:160`, `scope.py:200`).
  Převod na mapping by rozbil všechna čtyři pro nulový zisk. Proto
  **paralelní seznam** `bgp_neighbor_inactive` (inventory) /
  `Selectors.bgp_neighbors_inactive` (scope), a k němu
  `RoutingInstance.bgp_neighbors_inactive` a
  `default_bgp_neighbors_inactive` v parseru.
- **BFD** — beze změny.

Ten rozdíl v nosičích není nedůslednost; kopíruje rozdíl, který je v kódu už
zdůvodněný (`models/scope.py:66`: session se vybírají přes `bgp_neighbors`,
záměr leží v `bfd_peers`, a slévat je by znamenalo držet je v synchronu).

**Deaktivovaný peer zůstává zařazený.** `engine.py:169` a `engine.py:223`
sjednocují oba seznamy, takže když pro deaktivovaného peera přesto přijde
živá session, patří ke své službě a dostane nález — nespadne do
`NEZARAZENO`.

**`protocol` a `detection_reason` zůstávají řízené jen aktivními sousedy.**
`_assign_bgp_neighbors` na `if not matched_neighbors: continue` nesahá jinak
než že za ním přiřadí i neaktivní seznam. Rozšířit detekci služby o
deaktivované peery by měnilo, které služby se vůbec rozpoznají — to je jiná
vlna.

### 3. Schema 4 → 5

Inventory vyrobená starým parserem nový klíč nemá. `.get()` by dosadil
default a **každý deaktivovaný prvek by se tiše přečetl jako aktivní** —
přesně ta vada, kterou vlna opravuje, o patro výš. Hlasitý pád na
`schema_version 4` je smysl bumpu, ne jeho cena.

Cena je editace dvou souborů `tests/fixtures/172.20.20.{4,5}.yml`: řádek
`schema_version` a doplnění `bgp_neighbor_inactive: []` a `active: true` u
každé statiky. Mechanické, laborku nepotřebuje.

Kořenové `172.20.20.{4,5}.yml` **žádný test nečte** — ověřeno grepem, všech
šest testovacích odkazů míří do `tests/fixtures/`. Zůstanou zastaralé pod
bodem 2 („Resync"), který tahle vlna neotevírá.

**Snapshot schema se zvedá na 5 také.** První znění tohohle specu tvrdilo, že
se nemění (odůvodněné tím, že vlna sahá na záměr, ne na měření) — a bylo to
špatně. Ověřeno: `Snapshot` scopy **vnořuje**
(`models/snapshot.py:108` serializuje `[scope.to_dict() ...]`,
`:126` je rekonstruuje), a `Selectors.to_dict` nový klíč
`bgp_neighbors_inactive` nese. Starý snímek by ho tedy četl jako prázdný —
týž tichý default, kterým se o odstavec výš zdůvodňuje bump inventory.
Argument platí beze zbytku i tady, takže `SCHEMA_VERSION` v
`models/snapshot.py:17` jde ze 4 na 5.

Snímky pod `runs/` tím zastarají. Je to v pořádku: `runs/` je v
`.gitignore`, jsou to historické artefakty konkrétních běhů a nástroj na ně
spadne hlasitě, ne tiše.

### 4. Checky: SKIP na řádku, ne na scopu

Mechanika je v kódu a jen se na téhle úrovni nepoužívá: `Finding` nese
`outcome`, a `engine.py:145` SKIPy odfiltruje ještě před hlasováním
`Status.worst()`. Řádek s deaktivovanou routou tedy bude SKIP, zatímco
sousední routa téže služby zůstane PASS.

SKIP vydává **vlastnící check**, ne `deactivation.py`:

- **`checks/routes.py`** — `run` už iteruje přes sjednocení
  `configured | subject | baseline`, takže deaktivovaná routa se do iterace
  dostane sama, jakmile ji parser přestane vypouštět. `_finding` dostane
  příznak a pro `active: False` vrátí `Outcome.SKIP` s hodnotou
  `deaktivovana` místo dnešní větve „nakonfigurovaná, ale není v routovací
  tabulce" (ta by jinak dala tvrdý FAIL — routa v tabulce logicky není).
  Když deaktivovaná routa v tabulce **přesto je**, SKIP se nevydá a nález se
  chová jako dosud; je to rozpor konfigurace se stavem a má být vidět.
- **`checks/bgp.py`** — `BgpSessionStatusCheck.run` dnes iteruje přes
  **naměřené** peery (`ctx.subject["bgp"]`), ne přes nakonfigurované. Doplní
  se nálezy pro peery z `bgp_neighbors_inactive`, které session nemají:
  `Outcome.SKIP`, hodnota `deaktivovan`. Zároveň se opraví předčasný návrat
  `if not peers:` — služba, jejíž jediný peer je deaktivovaný, dnes dostane
  „sluzba nema zadne BGP peery"; nově dostane per‑peer SKIP.
- **`checks/deactivation.py`** — **beze změny**, zůstává na granularitě
  služby.

Zobecňovat příznak na scope by bylo špatně: to je právě ten mechanismus,
který táhne celou službu do SKIPu.

**Vědomé omezení rozsahu:** vlna nemění, co se stane s **aktivním**
nakonfigurovaným peerem bez session — ten je v reportu dál neviditelný,
protože `checks/bgp.py` iteruje přes měření. Sjednotit ho se statikami (které
iterují přes sjednocení tří zdrojů) je samostatná práce a patří do „Co
zbývá" roadmapy vlny 8, ne sem.

---

## Testovací strategie

Čtyři pravidla, která vlny 5–7 zaplatily, se sem promítají takhle:

1. **Test nad datovou strukturou neměří, co se vykreslí.** Ke každému
   tvrzení o SKIPu patří i test nad **vyrenderovaným reportem**, a hledá se
   **uvnitř sekce**, ne `"x" in output` — dvě sekce sdílejí řetězce.
2. **Mutant nemíří do `tests/conftest.py`.** Když test tvrdí něco o datech z
   generátoru fixtures, mutant patří do produkčního kódu, který ta data
   zpracovává.
3. **Každý mutant se před zapsáním do plánu pustí.** „Je to zjevně ten
   správný" je odhad; mutant, který svůj test nezabíjí, je horší než mutant
   chybějící.
4. **Mutant se neadresuje číslem řádku, když ho táž úloha posouvá**, a
   `git diff --stat` po každém `sed` je povinný krok, ne ozdoba.

Navíc, protože zásah do parserů je test-only neověřitelný z opačné strany:
**tvrzení o produkčním mutantovi v diffu, který ten produkční soubor
neobsahuje, musí změřit někdo mimo review té úlohy** (pravidlo z vln 6 a 7).

### Existující testy, které stojí na starém kontraktu

Devět testů (× dvě platformy = osmnáct případů) dnes tvrdí „deaktivované se
vypustí". Vlna ten kontrakt mění, takže se musí změnit i ony — a plán je
musí jmenovat, aby se nezměnily jen tak, aby prošly.

**Šest testů o statikách — skutečně padnou** (změřeno, viz nález 3, druhý
řádek tabulky):

| soubor | test |
|---|---|
| `test_inactive.py` | `test_deactivated_routing_instances_container_drops_static_routes` |
| `test_inactive.py` | `test_inactive_rib_drops_only_its_own_routes` |
| `test_static_routes.py` | `test_deactivated_static_stanza_yields_no_route` |
| `test_static_routes.py` | `test_deactivated_rib_yields_no_route` |
| `test_static_routes.py` | `test_deactivated_global_routing_options_yields_no_route` |
| `test_static_routes.py` | `test_deactivated_instance_routing_options_yields_no_route` |

Přepíšou se z „routa v záměru není" na „routa v záměru **je** a nese
`active: False`" — a přejmenují, protože `..._yields_no_route` by po změně
lhal.

**Tři testy o sousedech — nepadnou, a právě proto jsou nebezpečné:**
`test_deactivated_bgp_container_drops_neighbors_and_bfd`,
`test_inactive_bgp_group_drops_its_neighbors`,
`test_deactivated_top_level_protocols_drop_neighbors_and_bfd`.

V měření nálezu 3 sice padly, ale **to měření mělo jiný tvar než tenhle
návrh**: zkusmý zásah neaktivní sousedy sypal do hlavního seznamu, kdežto
návrh je posílá do paralelního `bgp_neighbor_inactive`. Pod návrhem tedy
`service.bgp_neighbor == []` dál platí a testy zůstanou zelené — jen už
netvrdí, co si jejich název myslí, protože soused nezmizel, jen se
přestěhoval. **Tohle je předpověď, ne měření**; první krok příslušné úlohy
v plánu je ji ověřit, a když nevyjde, platí pravidlo „měření má přednost
před zadáním".

Ať vyjde jakkoliv, ty tři testy se musí **rozšířit** o aserci, že soused
skutečně přistál v `bgp_neighbor_inactive`. Bez toho nové chování nehlídá
nikdo a zůstal by po nich test, jehož název slibuje víc než jeho aserce —
přesně vada, kterou zaplatila vlna 7.

### Nové testy

Nové testy pokryjí nejméně:

- všechny tři živé spouštěče (`route`, `neighbor`, top-level kontejner) na
  úrovni parseru — u obou platforem, protože zámek drží symetrii
- deaktivovaná routa dá SKIP a **sourozenec téže služby zůstane PASS** (to
  je vlastnost, kterou název „per‑row SKIP" slibuje, a musí ji hlídat mutant
  mířený přesně na ni)
- deaktivovaný peer bez session dá SKIP; deaktivovaný peer **se** session
  dá normální nález a nespadne do `NEZARAZENO`
- `_bfd_node` chování se **nemění** — test, který zafixuje dědění ze skupiny
  přes deaktivovaný override, aby to příští vlna neopravovala jako vadu
  (dnes to netvrdí nikdo ani jedním směrem, viz nález 1)

## Akceptační kritéria

- `diff mx_parser.py evo_parser.py | wc -l` = **146**
- celá sada zelená, **0 přeskočených**
- `schema_version: 5` konzistentně v obou souborech pod `tests/fixtures/`,
  v `INVENTORY_SCHEMA_VERSION` (`mx_parser.py:2215`, `evo_parser.py` tamtéž)
  i v `SCHEMA_VERSION` (`models/snapshot.py:17`)
- žádný test nezůstane s názvem tvaru `..._drops_...` / `..._yields_no_...`,
  který po změně kontraktu neplatí
- `grep -nP '[^\x00-\x7F]'` nad soubory pod `migration_validator/` a `tests/`
  prázdný (ASCII-only je tvrdá podmínka a dokazuje se bajtovým scanem).
  **Netýká se parserů:** `mx_parser.py` a `evo_parser.py` jsou komentované
  česky s diakritikou — změřeno, 172 řádků s ne‑ASCII znaky. Nové komentáře
  v parserech se tedy píšou **s diakritikou**, aby zůstaly konzistentní se
  souborem; ASCII-only scan se na ně nepouští.
