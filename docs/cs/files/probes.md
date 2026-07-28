# `probes/` — aktivní testy

Soubory: `ping.py` a prázdný `__init__.py`.

Ping je **jediný aktivní test** v nástroji — proto má vlastní kategorii mimo collectory:

| | collectory | probes |
|---|---|---|
| povaha | pasivní čtení stavu | aktivní generování provozu |
| rozsah | device-scoped (jedno RPC na celé zařízení) | per cíl |
| závislost na inventory | žádná | **ano** — bez scope není znám cíl ani source adresa |
| kdy běží | v první fázi `capture` | až po bulk sběru (cíle se odvozují z ARP/ND) |

Ve fázi `evaluate` je výsledek pingu obyčejná data ve snapshotu. Díky tomu jsou checky
navzájem nezávislé a dají se pouštět v libovolném pořadí.

---

## `ping.py`

### `PingTarget`

Frozen dataclass: `scope_id`, `target`, `source`, `routing_instance`, `resolved_from`
(`arp` | `nd` | `subnet-fallback`), `family` (4 | 6), `interface` (jen pro IPv6 link-local
cíle — viz níže). `to_dict()` z ní udělá základ záznamu ve snapshotu, do kterého se pak
doplní naměřené hodnoty.

`scope_id` je důležitý: podle něj `Scope.select()` později přiřadí probe zpátky ke službě.

### `source_address(scope, family)`

Adresa dané rodiny, ze které se pinguje — rodina zdroje musí odpovídat rodině cíle, jinak ji
Junos ping odmítne:

1. **`virtual_gw_v4`/`virtual_gw_v6` má přednost** — u IRB rozhraní je správný zdroj
   virtual-gateway adresa, ne fyzická adresa boxu,
2. jinak první `local_ipv4`/`local_ipv6`,
3. jinak `None` (ping poběží bez `source`).

Prefix se odřízne (`198.11.13.1/30` → `198.11.13.1`).

### `subnet_fallback(addresses, family, owned=None)`

Když je ARP/ND tabulka pro rozhraní prázdná, zkusí se **první použitelná adresa ze
subnetu**, která není naše vlastní. `owned` jsou další adresy, které scope vlastní a které se
nesmí vrátit jako cíl — typicky virtual-gateway adresa IRB rozhraní; bez toho by fallback
vrátil VGW jako cíl, zatímco `source_address()` už VGW použila jako zdroj, a výsledkem by byl
ping sám na sebe.

Network a broadcast adresa se přeskakují jen u **IPv4** sítí širších než /31 — u p2p linek
(/31) jsou obě adresy legitimní hosty a v IPv6 je adresa se samými nulami subnet-router
anycast, ne broadcast, takže se nepřeskakuje vůbec. Sítě s prefixem rovným maximu (/32, /128)
se přeskočí úplně.

**U IPv6 navíc platí `IPV6_FALLBACK_MIN_PREFIX = 126`** — fallback se zkouší jen u sítí
`/126` a delších (point-to-point rozsahy). Střílet náhodnou adresu do `/64` nemá smysl: je to
zaručený neúspěch, který se v reportu čte jako nedostupné CPE.

Typicky: PE má `.1`, zkusí se `.2`.

### `resolve_targets(scopes, arp_entries, nd_entries=None)`

Srdce fáze „ARP/ND → ping". Pro každý scope a každou rodinu (4, 6):

- **přeskočí device scope a všechny typy služeb kromě `Internet` a `IPVPN`.** `Core`,
  `E-Line` ani `E-LAN` ping nedostanou — `lo0.0` je díky kategorizaci `Core`, takže odpadá
  automaticky;
- `routing_instance` se do pingu předá **jen u `IPVPN`** (`ping <ip> routing-instance <RI>`);
  `Internet` jede v default `inet.0`;
- **IPv4**: vezme všechny ARP adresy naučené na rozhraních scope;
- **IPv6**: vezme jen ND záznamy, které jsou **použitelné** (`_usable_nd()`: mají MAC a stav
  není `unreachable`/`incomplete`) a nejsou link-local — **pokud sama služba nemá jako
  jedinou adresu nakonfigurovanou link-local** (`_link_local_configured()`); pak se link-local
  soused ponechá jako legitimní cíl;
- **link-local cíl bez rozhraní Junos ping odmítne** — proto `PingTarget.interface` nese
  název rozhraní ND záznamu, kdykoli je cíl link-local;
- u obou rodin: neplatí typicky, ale kdyby ARP/ND vrátila naši vlastní adresu, cíl se
  vynechá (ping sám na sebe je nesmyslný výsledek);
- když ARP/ND nic nedá, použije `subnet_fallback()` a označí `resolved_from:
  "subnet-fallback"`.

V rámci jednoho scope přijdou cíle IPv4 před IPv6; napříč více scopy už pořadí neplatí — na
pořadí nic nezávisí, checky rodinu čtou z pole `family`, ne z pozice v seznamu.

Management rozhraní se sem nedostanou, protože se z nich vůbec nestane scope
(`scoping/builder.py`). Bez toho pravidla by nástroj pingoval do management sítě.

### `run_ping(device, target, count)`

Spustí `device.rpc.ping(host=..., count=..., rapid=True, [source=...],
[routing_instance=...], [interface=...])`. `rapid=True` zkracuje běh z ~5 s na ~0,3 s na cíl
(ověřeno proti laborce, že tvar odpovědi — `probe-results-summary` a jeho pole — zůstává
stejný, takže `parse_ping_result()` se nemění). `interface` se předá jen u link-local cílů.

**Neúspěch není chyba nástroje, ale výsledek měření.** Výjimka se proto odchytí a zapíše
jako záznam se 100% ztrátou a klíčem `error`. Odchytává se záměrně cokoliv: kromě
očekávaných RPC chyb vMX občas vrátí poškozené XML (`<ping-results>` bez uzavíracího tagu),
na kterém PyEZ spadne na `XMLSyntaxError` — ověřeno jako přechodné, při opakování projde.
Shodit celou capture kvůli jednomu advisory probu by bylo horší.

### `parse_ping_result(xml)`

Rozlišuje **dva druhy neúspěchu**, které Junos vrací:

| případ | co v XML | co se zapíše |
|---|---|---|
| „no response" — ping odešel, nic se nevrátilo | summary existuje, hlásí 100% loss | platný výsledek měření |
| „internal error" — ping vůbec neodešel (např. `bind: Can't assign requested address`) | summary **chybí**, je tam `ping-error-message` | `loss_percent: 100` + klíč `error` s důvodem |

Bez toho rozlišení by druhý případ vypadal jako úspěšně změřených nula paketů. Když summary
chybí, zapíše se `loss_percent: 100` — je to pravdivější než `None`, které by se v reportu
četlo jako „neměřeno".

`rtt_avg_ms` se počítá z mikrosekund (`rtt-average`) a plní se **jen když něco doopravdy
přišlo**. Důvod selhání se sbírá z `ping-failure` i `ping-error-message`
(`_failure_reason()`), duplicity se odstraní a spojí se do jednoho řetězce.

---

## Konfigurace

Počet paketů se řídí na CLI: `capture --ping-count N` (výchozí 5, konstanta `DEFAULT_COUNT`).
Volba `ping_reachability.count` v `config.yml` je konfigurace **checku** — samotné měření
proběhlo už při sběru.
