# Deklarativni "filter" testy - planovane rozsireni (poznamka, ne spec)

Datum: 2026-08-13
Stav: zamer schvalen v brainstormingu, design bude samostatny spec

## Motivace

Nastroj ma nahradit jsnapy testy i mimo migracni praci - jako obecny
validator stavu sluzeb/zarizeni. Psani novych checku dnes vyzaduje
Python (trida + registrace); jsnapy umoznuje testy psat deklarativne
i uzivatelum, kteri nekoduji. Drahou polovinu prace uz mame: parsery
normalizuji XML do dictu a run masinerie resi snapshot/baseline
parovani. Chybi jediny dil - genericky check, jehoz chovani urcuje
YAML misto Pythonu.

## Zamysleny tvar (ilustrace, ne finalni format)

```yaml
tests:
  ibgp-peers-established:
    area: bgp                  # facts ktereho collectoru cist
    rows: peers                # seznam uvnitr facts
    filter:                    # vyber radku, napr. "jen interni peery"
      - field: peer_group
        matches: "INTERNAL.*"
    assert:
      - field: state
        equals: Established
    severity: fail
    mode: both                 # single snapshot i baseline compare
    compare:
      key: peer_address        # identita radku napric snapshoty
      no_missing: true         # zadny baseline peer nesmi zmizet
```

Implementacne: jedna trida `DeclarativeCheck` implementujici stavajici
`Check` interface, instancovana per YAML zaznam a registrovana vedle
python checku. Tim zdedi vse existujici: `mig-validate checks` ji
vypise, config ji umi vypnout/prepnout severity, report ji renderuje
jako kazdy jiny check. Engine se nemeni.

## Kde je skutecna prace (pro budouci spec)

1. **Dokumentace schematu facts.** Uzivatel pisici `field: peer_group`
   musi vedet, ze pole existuje a jak se jmenuje. Bez dokumentovaneho
   schematu (nebo prikazu typu `mig-validate facts --area bgp`, ktery
   vypise jmena poli z realneho snapshotu) uzivatel hada. Je to prace
   na docs/tooling, ne na enginu - ale rozhoduje o "pouzitelne pro
   non-codery" vs "pouzitelne jen pro autora".
2. **Mala sada operatoru.** jsnapy jich ma ~30, pouziva se ~6. Start:
   `equals / not-equals / matches / in / gte / lte` pro asserty,
   `no_missing / no_decrease / delta_percent` pro compare. Zadny
   expression language.
3. **Vazba na scopy.** Python checky bezi per-service-scope;
   deklarativni testy jsou prirozene device-global (jako iBGP
   priklad). Start device-global - to pokryva jsnapy use case;
   per-scope vazba az kdyz se ukaze realna potreba.

## Vztah k profilum (spec 2026-08-13-profil-a-auth-konfigurace)

Profil soubor se navrhuje s vedomim, ze sekce `tests:` casem pribude.
Cilovy stav: jeden YAML popisuje celou validaci - ktere collectory,
ktere vestavene checky, ktere vlastni filter testy.
