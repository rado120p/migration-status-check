# Skupiny řádků, sekce NEZARAZENO a sjednocení link-local — návrh

**Datum:** 2026-08-03
**Stav:** schváleno, neprovedeno
**Navazuje na:** [`roadmap-2026-07-31-vlna4-hotovo.md`](../roadmap-2026-07-31-vlna4-hotovo.md),
sekce „Co zbývá" — bod 2 (Report)
**Výchozí stav:** 605 testů zelených / 0 přeskočeno,
`diff mx_parser.py evo_parser.py | wc -l` = 146

## Účel

Report je jediná položka, kterou roadmapa vede jako vlastní spec už od
2026‑07‑28, a od té doby se nehnula. Obsahuje tři nálezy: F‑2 (jméno RIB se do
reportu nikdy nedostane), F‑11 (dvě kopie logiky pro link-local) a F‑14
(kosmetika v `NESPAROVANO` a `unassigned` jen v JSON).

Měření provedené při psaní tohoto specu premisu F‑2 potvrdilo a zároveň
upřesnilo — chybí nejen RIB, ale i peer. U F‑14 ukázalo, že roadmapa popisuje
jen půlku, a u AR‑5 odkrylo přímý rozpor mezi tím, co spec z 2026‑07‑28
předepisuje, a tím, co vlna 2 nasadila. Všechno tři níž.

## Rozsah

**V rozsahu:**

- mechanismus pojmenovaných skupin řádků uvnitř sekce rodiny (`view.py`,
  `text_report.py`) a jeho zavedení u BGP counterů a statických rout — F‑2
- jméno peeru v popisku `BGP status` — nález z měření při psaní tohoto specu,
  v roadmapě není
- nová sekce `NEZARAZENO` s obsahem `result.unassigned` — F‑14b
- doplnění sloupce `(TYP)` na šířku v `NESPAROVANO` — F‑14a
- jeden výskyt link-local predikátů v novém modulu `addressing.py` — F‑11

**Mimo rozsah:**

- **bod 1 roadmapy** — příznaky na hlubších úrovních konfigurace. Odloženo
  rozhodnutím uživatele z 2026‑07‑31, včetně top-level
  `<routing-options inactive>`.
- **bod 3** — resync `172.20.20.5.yml` a `tests/fixtures/rpc/junos-evo/`.
  Vlastní položka s vlastním rozpočtem na ripple.
- **body 4 a 5** — chybějící `active` v baseline eskaluje na FAIL, a priorita
  `SKIP` nad `PASS` v jednom scopu. Obojí je změna sémantiky checků, ne sazby;
  tenhle spec mění to, jak se výsledek zobrazuje, ne co znamená.
- **bod 6** — drobnosti ze závěrečného review vlny 4. Netýkají se reportu.
- **HTML report a TUI.** Uzavřeno 2026‑07‑28, neotvírá se.

## Ověřený výchozí stav

Vše ověřeno 2026‑08‑03 čtením kódu a vyrenderováním ostrého reportu ze
společných fixtures (`tests/fixtures/172.20.20.4.yml` a `.5.yml` přes
`synthetic_snapshot`), ne odhadem.

### Premisa F-2 platí a je širší, než roadmapa říká

`checks/bgp.py:152-173` vydává **jeden `Finding` na trojici (peer, RIB,
counter)**. `details["rib"]` je vyplněný (`:193`), ale popisek je jen
`f"BGP {key}-prefix-count"` (`:187`) — bez RIB **a bez peeru**.
`view.py:_row()` `details` do řádku nepřenáší vůbec; kvalifikace popisku sahá
jedině po `details["address"]` (`view.py:89`), který BGP findingy nemají.

Důsledky, obojí dnes tiché:

- peer se dvěma RIB → osm řádků, čtyři dvojice shodných popisků
- dva peery téže rodiny v jedné službě → dva řádky `BGP status` se shodným
  popiskem

Roadmapa vede F‑2 jako „podřádky se jménem RIB **u statických rout**". To je
zkratka, která se rozešla s původním zněním: F‑2 z 2026‑07‑28 mluví o BGP.
U statických rout jméno RIB v popisku **je** — vlna 2 ho tam dala.

### AR-5 a vlna 2 si odporují

AR‑5 (`2026-07-28-ipv6-a-report-design.md:140`) říká doslova: *„Jméno RIB
nejde do sloupce s popiskem, ale na odsazený podřádek pod řádkem `BGP status`;
podřádek je volný text bez sloupců."*

Vlna 2 pak u statických rout nasadila pravý opak — kvalifikátor v popisku:

```
 PASS | Staticka routa (L3VPN-CPE13-NNI.inet6.0 2001:eeee::/64) : 2001:db8:11:13::b
```

Nebylo to přehlédnutí: plán vlny 2 to zapsal výslovně (*„Jméno RIB jde do
kvalifikátoru popisku… F‑2 se v této vlně nedělá"*). Zůstaly tím ale dva
idiomy pro tentýž fakt a odůvodnění odkladu F‑2 (*„dává smysl ho navrhnout
zároveň s tím, jak se budou zobrazovat routy z druhého specu"*) mezitím
vypršelo — druhý spec proběhl a rozhodl se jinak.

**AR‑5 se tímto specem ruší v části o podřádcích** (viz AR‑36). Zbytek AR‑5 —
šířky se počítají z celého bloku a neořezává se — platí dál a AR‑38 ho
rozšiřuje.

### Šířka: kvalifikátor u BGP by blok nerozšířil

Nejdelší jméno RIB ve fixtures je `L3VPN-CPE13-NNI.inet6.0` (23 znaků).
Kvalifikátor `BGP active-prefix-count (198.11.13.2 L3VPN-CPE13-NNI.inet6.0)`
by měl 60 znaků proti dnešnímu nejširšímu popisku téhož bloku
`Staticka routa (L3VPN-CPE13-NNI.inet6.0 2001:eeee::/64)` = 54.

Měření je zapsané proto, aby bylo vidět, že skupiny **nebyly zvoleny kvůli
šířce** — kvalifikátor by se do bloku vešel s přehledem. Zvoleny byly kvůli
čitelnosti u peeru s víc RIB, kde se identita opakuje na každém řádku. To je
rozhodnutí uživatele z 2026‑08‑03.

### Statické routy dnes splývají s BGP

Uvnitř sekce rodiny jde jeden souvislý sloupec bez jakéhokoliv předělu:

```
 -- IPv4  152.11.13.1/30 -------------------------------------------
 PASS | ARP                                   : ? -> 152.11.13.2
 PASS | BFD (152.11.13.2)                     : Up
 PASS | BGP active-prefix-count               : 14
 PASS | BGP status                            : Established
 PASS | Ping                                  : 5/5
 PASS | Staticka routa (inet.0 198.62.1.0/29) : 152.11.13.2
```

Uživatel to 2026‑08‑03 pojmenoval: statické routy vypadají, jako by patřily
pod BGP checky, a nepatří — jsou vlastní doména. Zavedení skupin ten problém
samo o sobě **zhoršuje**, protože nadpis skupinu otevře a nic ji nezavírá;
řádek statické routy hned za posledním nadpisem RIB by se četl jako součást
té RIB. Proto AR‑37.

### F-14 jsou dva nálezy, ne jeden

Roadmapa vlny 3 i 4 jmenuje jen `unassigned`. Původní F‑14 z 2026‑07‑28
(*„sloupec s typem služby v NESPAROVANO není odsazený, sloupec s důvodem je
rozházený"*) je pořád živý — vidět v ostrém výpisu:

```
NESPAROVANO
  baseline  clab-pop-migration-P1;et-0/0/0 (Core)  zadny kandidat na subject
  subject   svc:et-0/0/10.0:Internet       (Internet)  nova sluzba, chybi baseline
```

`text_report.py:310` doplňuje na šířku jen popisek, `(TYP)` ne.

### `unassigned` je jen v JSON a je subject-only

`engine.py:303-307` plní tři seznamy, `models/result.py:215` je pošle do
`to_dict`. V `reporting/` se řetězec `unassigned` **nevyskytuje ani jednou** —
textový report je nevypisuje, a to ani `bgp_peers`, které roadmapa vynechává.

Dvě omezení, která musí spec pojmenovat, aby prázdná sekce nelhala:

- všechny tři nesou napevno `"snapshot": "subject"` (`engine.py:176, 207,
  231`) — baseline strana se nesbírá vůbec
- všechny tři vracejí `[]`, kdykoliv je mezi scopy `is_device`
  (`engine.py:170, 199, 224`)

### F-11: obě kopie jsou logicky totožné

`probes/ping.py:112-126` a `checks/reachability.py:42-47, 24-40` nesou tutéž
dvojici predikátů. Rozdíl je jediný: kopie v checku má docstring, kopie
v probe ne — a právě to už jednou způsobilo chybu (Task 13b opravoval
docstring, který zasel nepravdivé tvrzení, zatímco druhá kopie docstring nemá).

Mezi `checks/` a `probes/` dnes **nevede žádný import ani jedním směrem**.
Existující sdílení uvnitř vrstvy (`checks/bfd.py` → `checks/bgp.py`,
`checks/bgp.py` → `checks/ifaces.py`) precedent pro import napříč vrstvami
nedává.

## Přejímací požadavky

### AR-36 — skupina je vlastnost měření, ne sazby

`Finding` a `CheckResult` dostanou volitelné pole `group: str | None`. Řádky
se stejnou skupinou uvnitř jedné sekce rodiny se vypíší pod společným
nadpisem.

**Proč na modelu a ne v rendereru:** jen check ví, že counter patří k dvojici
(peer, RIB) — renderer by to musel odvozovat z `details` větvením podle
`check.id`, a tím by si natáhl znalost jednotlivých checků. `label`, `value`
a `family` už na `Finding` jsou z téhož důvodu (viz jeho docstring:
*„rozklad na popisek a hodnotu musí udělat check"*).

Checky, které dnes identitu cpou do popisku, ji přesunou do `group`:

| check | dnes `label` | nově `group` | nově `label` |
|---|---|---|---|
| `bgp_prefix_counts` | `BGP active-prefix-count` | `BGP 198.11.13.2 / L3VPN-CPE13-NNI.inet.0` | `active-prefix-count` |
| `bgp_status` | `BGP status` | — | `BGP status (198.11.13.2)` |
| `static_route_status` | `Staticka routa (inet.0 198.62.1.0/29)` | `Staticke routy` | `inet.0 198.62.1.0/29` |

`bgp_status` skupinu **nedostane** schválně: je jeden na peera, RIB se ho
netýká, takže by nadpis stál nad jediným řádkem. Peer jde do kvalifikátoru,
protože bez něj se dva peery téže rodiny nedají rozlišit — to je vada, kterou
odkrylo měření k tomuto specu, a řeší se tady, protože jinde by pro ni nebyl
důvod.

Souhrnné SKIPy celého checku skupinu nemají. `Staticka routa : interface
deactivated` se neváže ke konkrétní routě, takže by pod nadpisem `Staticke
routy` tvrdil víc, než ví. Totéž platí pro `BGP prefixy : bez baseline`
(`checks/bgp.py:142`) a `BGP prefixy (rib) : bez baseline` (`:160`) — ten
druhý si jméno RIB v popisku nechává, protože do skupiny s naměřenými
countery téže RIB nepatří: neříká „counter je takový", ale „RIB tu nemá
protějšek".

`group` se propíše do `to_dict`, tedy i do JSON reportu.

### AR-37 — každou skupinu zavírá další skupina

Řádky bez skupiny stojí **nahoře, před prvním nadpisem**. Jakmile nadpis
skupiny padne, všechny další řádky sekce patří do nějaké skupiny — statické
routy proto dostanou nadpis `Staticke routy`, i když by šly nechat bez něj.

**Proč:** nadpis skupinu otevírá a nic ji nezavírá. Bez tohoto pravidla by
řádek bez skupiny za poslední skupinou vypadal, že do ní patří — přesně ta
záměna BGP a statických rout, kvůli které se skupiny zaváděly. Pravidlo je
tvarem totožné s tím, co dnes dělá sekce rodiny `None` (`view.py:15-18`):
řádky, které na rodině nezávisí, stojí nad první hlavičkou sekce a vlastní
hlavičku nemají.

Cílový tvar:

```
 -- IPv4  198.11.13.1/30 -------------------------------------------
 PASS | ARP                                  : ? -> 198.11.13.2
 PASS | Ping                                 : 5/5
 PASS | BFD (198.11.13.2)                    : Up
 PASS | BGP status (198.11.13.2)             : Established
   -- BGP 198.11.13.2 / L3VPN-CPE13-NNI.inet.0
 PASS | active-prefix-count                  : 14
 PASS | received-prefix-count                : 14
   -- BGP 198.11.13.2 / bgp.l3vpn.0
 PASS | active-prefix-count                  : 9
   -- Staticke routy
 PASS | L3VPN-CPE13-NNI.inet.0 172.26.1.0/29 : 198.11.13.2
```

Pořadí skupin je pořadím **prvního výskytu**, ne abecední — drží pořadí,
ve kterém řádky vydal check. `FAMILY_ORDER` se nemění.

`Section` se rozpadne na `rows` (bez skupiny) a `groups: list[Group]`, kde
`Group` je `(title, rows)`.

### AR-38 — nadpis skupiny vstupuje do šířky bloku vlastní délkou

Šířky sloupců i rámec bloku se počítají ze **všech** řádků všech sekcí
i skupin, a nadpisy skupin se do šířky bloku započítávají stejně jako
hlavička bloku a nadpisy sekcí.

**Proč to spec jmenuje předem:** komentář `text_report.py:140-143` říká, že
tenhle tvar chyby už byl nalezen třikrát — u hlavičky bloku, u souhrnné
tabulky a u nadpisu sekce, pokaždé na skutečných datech z laborky. Nadpis
skupiny je čtvrtý výskyt téhož a nemá se na něj čekat.

Na rozdíl od nadpisu sekce se nadpis skupiny **nedoplňuje pomlčkami** na
šířku bloku. Je to `   -- {title}` a nic dál; sekce rodiny si čáru přes celou
šířku nechává, aby zůstal vidět rozdíl mezi dvěma úrovněmi nadpisů.

### AR-39 — sekce NEZARAZENO

Za `NESPAROVANO` přibude sekce se třemi podskupinami z `result.unassigned`.
Vypisuje se **vždy**, i prázdná (`(nic)`, stejně jako `NESPAROVANO`).

```
NEZARAZENO (jen subject)
  BGP peer        10.9.9.9                RI MGMT
  Staticka routa  inet.0 10.0.0.0/8       -> 172.20.20.1
  BFD session     10.9.9.9                et-0/0/2   Up
```

**Proč vždy a proč nefiltrovaně:** je to pojistka proti mezerám v parsování —
routa, kterou parser neumí přečíst, se do selektorů nedostane, ale v tabulce
je vidět (viz docstring `_unassigned_static_routes`). Pojistka, kterou je
nutné si vyžádat přepínačem, chytí míň. `--filter` ani `--status` se na ni
nevztahují, se stejným odůvodněním, jaké už nese docstring `filter_result`
u `NESPAROVANO`: filtrování pojistky by tiše smazalo přesně to, co má ukázat.
Objekty bez služby navíc žádný status nemají, takže `--status fail` by je
schoval vždycky.

**Proč vlastní sekce a ne řádky v `NESPAROVANO`:** `NESPAROVANO` je o službě,
která nemá protějšek, tahle o objektu, který nemá službu. `NESPAROVANO` má
navíc sloupec baseline/subject, který tady nedává smysl.

Nadpis nese `(jen subject)`, protože `engine.py` plní všechny tři seznamy jen
ze subjectu. Spec zapisuje i to, že **při `is_device` scopu je sekce vždy
prázdná** — bez toho by prázdná sekce tvrdila „nic nezařazeného není",
zatímco ve skutečnosti se nesbíralo.

### AR-40 — `(TYP)` v NESPAROVANO se doplňuje na šířku

Původní F‑14. Sloupec s typem služby se doplní na šířku nejdelšího, aby
sloupec s důvodem stál v jedné linii. Šířka z obsahu, neořezává se — AR‑5.

### AR-41 — link-local predikáty mají jeden výskyt

Nový modul `migration_validator/addressing.py` s `is_link_local(address)`
a `link_local_is_configured(scope)`. Docstring z `checks/reachability.py` jde
s nimi. `checks/reachability.py` i `probes/ping.py` importují odtud.

**Proč nový modul a ne jeden ze dvou dnešních:** mezi `checks/` a `probes/`
dnes nevede žádný import. Vlastnictví v checku by znamenalo, že capture závisí
na evaluate — obráceně, než tečou data. Vlastnictví v probe by znamenalo, že
`checks/` importuje z `probes/`. Jsou to čisté predikáty nad adresou
a `Scope`, bez sémantiky capture i evaluate, takže obě vrstvy mají sahat dolů —
stejně jako dnes obě sahají do `models/`.

F‑11 je jediná položka téhle vlny, která se rendereru nedotkne. Roadmapa ji
vede pod „Report", ale je to zařazení, ne téma.

### AR-42 — ripple se vyjmenuje měřením, ne odhadem

Změna popisků u tří checků rozhýbe testy. `grep` na dotčené popisky dnes
vrací 220 řádků napříč `tests/`, ale to je horní odhad, ne seznam.

Plán **nesmí** vyjmenovávat konkrétní padlé testy z odhadu. Postup je: změnit
kód, pustit sadu, zapsat skutečný seznam. Totéž platí pro každého mutanta,
kterého plán předepíše — pouští se nad tím stavem repa, ve kterém poběží
doopravdy (pravidlo vlny 4, nález 2).

## Přejímací kritéria implementačního plánu

- **Testy skupin nesmí jít přes ostrý report.** `view.py` a `text_report.py`
  jsou oddělené schválně (viz docstring `view.py`): pořadí a zařazení řádků se
  testuje porovnáním datových struktur, šířky sloupců proti řetězci
  s mezerami. Skupiny mají obě stránky a testují se na obou.
- **Test na AR‑38 musí měřit doplnění, ne jen přítomnost.** Fixture musí mít
  nadpis skupiny **delší než celá tabulka sloupců** — jinak by test prošel
  i tehdy, kdyby se nadpis do šířky nezapočítával. To je čtvrtý výskyt téhož
  tvaru; test má být takový, aby padl na mutantu, který nadpisy ze
  `max()` vyhodí.
- **Test na AR‑39 nemůže vzniknout ze `synthetic_snapshot`.** Ostrý běh nad
  `tests/fixtures/172.20.20.4.yml`/`.5.yml` dnes vrací
  `{"bgp_peers": [], "static_routes": [], "bfd_sessions": []}` — všechny tři
  prázdné. Test potřebuje ručně sestavený `RunResult`. Kdyby to plán neřekl,
  implementer uvidí prázdnou sekci a bude hledat chybu v kódu.
- **Test na AR‑37 musí mít v jedné sekci řádek bez skupiny i řádek se
  skupinou**, a asertovat pořadí. Fixture, kde jsou všechny řádky ve
  skupinách, pravidlo neměří.
- **Test na AR‑36 musí mít peera se dvěma RIB.** S jedinou RIB projde i kód,
  který skupinu nesestaví z `details["rib"]`, ale zahodí ji.
- **Ostré ověření na konci.** Report se vyrenderuje z dnešního páru fixtures
  a přečte se očima, ne jen testem. Tři z nejostřejších chyb na větvi vlny 1
  našlo právě tohle, ne testy.

## Zapsané předpoklady

### AR-5 se ruší jen v části o podřádcích

Mechanismus podřádků, který AR‑5 předepisuje, se nikdy nepostavil a tímto
specem se stavět nebude. Ruší se **jen ta věta**. Zbytek AR‑5 — šířky
z celého bloku, neořezává se — platí a AR‑38 ho rozšiřuje o nadpisy skupin.

Zápis je tvarově týž, jaký repo používá jinde: starší rozhodnutí se nemaže,
označí se za překonané a řekne se čím.

### JSON report začne vydávat `group`

`CheckResult.to_dict` přibude klíč. Je to přírůstek, ne přejmenování — dnešní
konzumenti JSON reportu nic neztratí. Snapshot schema (`schema_version`) se
nemění; report a snapshot jsou dva různé formáty.

### Skupiny se zavádějí jen u BGP counterů a statických rout

Ostatní checky `group` nedostanou, i když by některé mohly (ARP a ND
u služby s víc adresami, countery rozhraní na fyzickém vs. logickém).
Mechanismus je obecný, ale zavádí se tam, kde dnes vzniká skutečná záměna.
Rozšíření je levné a udělá se, až bude pro co — ne dopředu.

### Nadpis skupiny se počítá do šířky, ale ne do sloupců

Nadpis je volný text, ne řádek tabulky. Do `label_width` nevstupuje, do šířky
bloku ano. Kdyby vstupoval do `label_width`, dlouhý nadpis skupiny by roztáhl
sloupec s popisky u všech řádků bloku — přesně to, co u hlavičky bloku už
jednou nastalo a co komentář na `text_report.py:132-134` popisuje.
