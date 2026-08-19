# Qualified-next-hop a agregátní routy — design

Datum: 2026-08-19. Uzavírá starý bod 20 (parsery zahazují qualified-next-hop)
a s ním i bod 17 (klíčování a příznak aktivity), a přidává parsování,
sběr a check agregátních rout. Schválený přístup: **varianta A** — jeden
sdílený model routy, per-hop záznamy, jeden collector se dvěma RPC.

Referenční měření: lab 2026-08-19 (PTX1-POP1, 25.2R1.8-EVO) — konfigurační
XML pro qualified-next-hop i aggregate viz níže; RPC tvar
`get-route-information` s `<protocol>aggregate</protocol>` potvrzen z CLI.
**Jediný nezměřený tvar je odpověď RPC pro protocol=aggregate** — ověřit na
laborce jako první krok implementace (stav se nikdy nefabuluje).

## Změřené vstupy (lab 2026-08-19)

```xml
<routing-options>
  <rib>
    <name>inet6.0</name>
    <static>
      <route>
        <name>2001:aaaa::/64</name>
        <next-hop>2001:abcd:11:14::4</next-hop>
        <qualified-next-hop inactive="inactive">
          <name>2001:db8::ffff</name>
        </qualified-next-hop>
      </route>
      <route>
        <name>2001:abcd:11:13::/64</name>
        <qualified-next-hop>
          <name>fe80::2</name>
          <interface>et-0/0/8.13</interface>
        </qualified-next-hop>
      </route>
    </static>
    <aggregate>
      <route>
        <name>2001:abcd::/32</name>
        <discard/>
      </route>
    </aggregate>
  </rib>
</routing-options>
```

Z toho plyne oprava dřívějšího invariantu: qualified-next-hop nese adresu
v `<name>` **a volitelně** `<interface>` (link-local next-hop ho vyžaduje).
`inactive` sedí přímo na uzlu `<qualified-next-hop>`. Podle měření z vlny 10
může `<name>` výjimečně nést i jméno rozhraní místo adresy.

## 1. Model záměru a parser

`StaticRoute` (parsers/core.py) se stává sdíleným modelem routy z konfigurace:

```python
@dataclass
class StaticRoute:
    rib: str
    prefix: str
    route_type: str = "static"        # "static" | "aggregate"
    next_hops: list[dict] = field(default_factory=list)
    # {"to": "2001:abcd:11:14::4", "interface": "et-0/0/8.13" | None,
    #  "qualified": bool, "active": bool}
    active: bool = True               # úroveň routy/kontejneru, jako dosud
```

Parsování per route uzel:

- každý `./next-hop/text()` → `{"to": <text>, "interface": None,
  "qualified": False, "active": True}` — holý next-hop nejde deaktivovat
  individuálně, jeho deaktivace je deaktivace routy (route-level flag),
- každý `./qualified-next-hop` → `to` z `./name/text()`, `interface`
  z `./interface/text()` (None když chybí), `qualified: True`,
  `active: not self._is_inactive(qnh_node)`. `_is_inactive` chodí po
  předcích, takže hop pod deaktivovanou routou vyjde neaktivní taky.
- `discard`, `reject`, `next-table` zůstávají neparsované — nemají adresu
  k porovnání. Komentář v obou parserech se zkrátí jen na tyhle tři tvary;
  odstavec „vědomě odloženo" o qualified-next-hop se smaže, protože přestává
  platit.

Agregáty: `_static_routes_under` projde vedle `./static` i `./aggregate`
(přímo pod `routing-options` i pod `rib`, globálně i v instancích), vydá
`route_type="aggregate"`, `next_hops=[]`, stejné odvození jména RIB
a stejné `_is_inactive`. Dítě `<discard/>` se ignoruje — u agregátu se
porovnává přítomnost, ne tvar forwardingu.

Plochý `next_hop` z modelu mizí; inventory a scope selektory nesou
`route_type` + `next_hops`. **Schema bump na 10.**

Uzavření starých bodů: bod 20 padá přirozeně (routa jen s QNH má hopy
k mapování i zobrazení). Bod 17 se řeší **bez** překlíčování — identita
zůstává `(rib, prefix)`, jeden záznam na routu, částečná deaktivace žije
uvnitř seznamu hopů.

## 2. Mapování rout na služby (`_assign_static_routes`)

Statiky drží pravidlo „stejná RIB + adresa v subnetu služby", se dvěma
změnami:

- **Precedence, ne fallback:** hop s `interface` se mapuje **jen** podle
  jména rozhraní (subnet match pro něj neběží). Link-local subnet bývá
  nakonfigurovaný na víc rozhraních (změřeno: et-0/0/8.13 ho jako subnet
  služby má), takže subnet match na `fe80::2` by routu rozstřelil na
  služby, kterých se netýká — operátor už Junosu řekl, na kterém linku hop
  žije, a to je autoritativní. Hop bez `interface` se mapuje subnetem jako
  dosud; link-local subnety se matchování účastní jako každý jiný.
- Subnet match běží přes **všechny** hopy, aktivní i neaktivní. Routa,
  jejíž jediný hop je deaktivovaný QNH, musí zůstat u své služby — stejný
  princip jako „deaktivovaná konfigurace službu nevypouští". Jinak by
  spadla do NEZARAZENO v okamžiku, kdy operátor hop deaktivuje.
- `<name>` nesoucí jméno rozhraní (ne IP) a bez `<interface>` dítěte:
  hop se mapuje tak, že se `to` považuje za jméno rozhraní. Nic víc se
  nehádá.

Jedna routa smí dál matchnout víc služeb (ECMP hopy do různých subnetů) —
beze změny, každý blok služby ukáže svůj řádek.

Agregáty, bez hopů, se mapují jen podle RIB:

- VRF rib (`<instance>.inet.0` / `.inet6.0`, nebo rib pod instancí) →
  všechny služby té routing instance,
- globální rib (`inet.0` / `inet6.0`) → služba se
  `service_type == "Core"` a `interface == "lo0.0"` (obě lab inventory ji
  detekují), **ne** tranzitní rozhraní,
- bez shody → nezařazeno, sekce NEZARAZENO je pojistka, konzistentně
  s BGP/BFD sirotky.

## 3. Collector a snapshot

`RoutesCollector` volá `get_route_information` **dvakrát** —
`protocol=static` jako dosud, pak `protocol=aggregate` — a sloučí do téže
struktury `tables`; každý záznam dostane `"protocol": "static" | "aggregate"`.

- Per-entry pojistka na `protocol-name` (collectors/routes.py:78) se
  zobecní: každý průchod přijme jen svůj protokol, takže nasazení s jiným
  filtrem nemůže křížově kontaminovat.
- Discard agregát má `<to>` i `<via>` prázdné — `next_hop: []`, `via: []`
  je očekávaný uložený tvar, ne vada; `active` z `active-tag` stejně jako
  u statik. Tvar rt-entry pro protocol=aggregate ověřit na laborce (viz
  úvod).
- Slučování: druhý průchod jen **přidává chybějící prefixy**; prefix nemůže
  být v jedné RIB zároveň static a aggregate, a last-write-wins by jeden
  z nich tiše schoval. Důvod zapsat komentářem u merge.
- Snapshot **schema 10** (nový klíč `protocol` v route záznamu + změny
  záměru z bodů 1–2).

## 4. Checky a report

- `static_route_status` drží identitu `(rib, prefix)`, ale iteruje jen
  **statické** záznamy záměru i měření (`protocol == "static"`; chybějící
  klíč = static kvůli starým baseline snímkům). Hodnota řádku zůstává
  z **měření** (`_next_hop_text` nad `<to>` z collectoru) a diff dál
  porovnává baseline vs. subjekt — to se nemění. Záměrové `next_hops`
  slouží mapování (bod 2) a nové anotaci deaktivace: routa se směsí
  aktivních a deaktivovaných hopů dostane deaktivované hopy poznamenané
  na řádku (`to`, s `via <interface>` kde je) přes `deactivation_outcome`
  — deaktivované v obou → tichý informační výstup; nově deaktivované
  vs. baseline → eskalace „migrace nedokoncena" jako u route-level flagu.
  Větev „nakonfigurovana, ale neni v tabulce" nově nastane i pro routu,
  jejíž hopy jsou **všechny** deaktivované a v tabulce nic není — ta se
  ale chytí do route-level deaktivační větve jen tehdy, když je
  deaktivovaná sama routa; deaktivace všech hopů jednotlivě se posoudí
  stejně: žádný aktivní hop = záměr neforwardovat, takže absence
  v tabulce je informační stav (přes `deactivation_outcome`), ne BROKEN.
- Nový check `aggregate_route_status`, skupina „Agregatni routy": stejné
  sjednocení tří zdrojů (konfigurace / subjekt / baseline), stejné větve
  chybí/deaktivována přes sdílené helpery z checks/routes.py, ale hodnota
  je jen přítomnost/aktivita — žádný next-hop text. Severity CRITICAL jako
  statiky: zmizelý zákaznický agregát po migraci je signál výpadku.

## 5. Hrany a kompatibilita

- **Staré baseline (pře-schema-10):** záměr bez `next_hops`/`route_type`
  a měření bez `protocol` se čtou jako čisté statiky — oba checky berou
  chybějící klíč jako „static", takže starý baseline proti novému subjektu
  degraduje elegantně (bez hop-level anotace, bez agregátních řádků
  z baseline), nepadá. Plná věrnost = přesnímat; `runs/mig01` je na to
  flagnutý beztak.
- **Fixtures:** conftest hardcoduje `active: True` — testy deaktivovaného
  hopu/routy musí ručně upravit i `facts["routes"]`.
- **Docs:** `docs/cs+en/files/parsers.md`, `checks.md`, `probes.md`
  (routes proba nově střílí dvě RPC); en strana se drží v synchronu.

## 6. Testy a ověření

1. Prvním krokem implementace je ověření odpovědi
   `get_route_information protocol=aggregate` na laborce (rt-entry pole
   discard agregátu). Konfigurační XML výše se stane fixture parseru
   doslovně.
2. TDD po vzoru vln: parser (mix holý+QNH, routa jen s QNH, neaktivní QNH,
   QNH s interface, aggregate pod `rib` i pod instancí), mapování
   (precedence interface nad link-local subnetem, fan-out VRF agregátu,
   globální agregát → lo0.0 Core, bez shody → NEZARAZENO), collector
   (dvouprůchodový merge, protokolová pojistka), checky (smíšená aktivita
   hopů, agregát chybí/deaktivován/OK, tolerance starého baseline).
3. Mutanty se přeběhnou tam, kde docstringy tvrdí kill (standardní
   pravidlo — tvrzení o mutantovi zastarává).

## Rozhodnutí (uzavřeno v brainstormu 2026-08-19)

- QNH: **per-hop záznamy** s vlastním `active`, ne zploštění; identita
  checku `(rib, prefix)` se nemění.
- Agregáty jsou VRF/instance-specifické (zákazník/interní) nebo globální
  (Core); globální se váže **jen na lo0.0 Core službu**, ne na tranzit.
- Hop s `interface` se mapuje výhradně podle rozhraní (precedence, ne
  fallback) — link-local subnety jsou na víc rozhraních.
- Junosí `generate` routy jsou mimo rozsah (YAGNI).
