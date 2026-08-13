# Profil a auth konfigurace - mene CLI flagu, YAML jako nositel zameru

Datum: 2026-08-13
Stav: navrh schvalen v brainstormingu

## Cil

Nastroj se ma pouzivat i mimo migrace - jako obecny validator stavu
sluzeb/zarizeni (nahrada jsnapy). Dnesni ovladani stoji na mnoha CLI
flazich. Zavadi se dva YAML soubory:

1. **Profil** (sdileny, v projektovem adresari) - "co tenhle testovaci
   beh dela": collectory, konfigurace checku, service-type filtr,
   ping count.
2. **Auth soubor** (per-user, v home adresari) - "kdo jsem": username,
   auth metoda, klic, heslo pres env promennou.

CLI flagy zustavaji vsechny funkcni; soubory je delaji volitelnymi.

## Schvalena rozhodnuti (nerelitigovat)

- Profil je **nadmnozina dnesniho --config YAML**: kazdy existujici
  config soubor (jen sekce `checks:`) zustava validni beze zmeny.
- Flag se prejmenovava na `--profile`, `--config` zustava jako skryty
  alias.
- Auth soubor je **per-user**, default `~/.config/mig-validate/auth.yml`,
  prepsatelny pres `--auth-file` (tudy se voli sdileny `ansible` ucet).
- Heslo primarne pres `password_env:` (jmeno env promenne). Plaintext
  `password:` se **honoruje jen pri opravneni 0600** - jinak nastroj
  soubor odmitne nacist (ToolError, ne warning).
- **Profil nesmi ukazovat na auth soubor.** Sdileny, git-trackovany
  soubor ridici vyber credentials je utocna plocha; vyber auth zustava
  u uzivatele (default cesta nebo `--auth-file`).
- Precedence vsude stejna: **CLI flag > hodnota ze souboru > vestavena
  default**.
- `service_types` filtr: capture stavi inventory a scopy **v plnem
  rozsahu**; filtruje se jen resolve ping cilu. Evaluate pousti checky
  jen na scopy odpovidajici filtru; sekce NESPAROVANO zustava
  **nefiltrovana** a hlavicka nese marker `filtered` + jmeno profilu.
- Deklarativni `tests:` sekce je planovane rozsireni profilu -
  samostatna poznamka `2026-08-13-deklarativni-filter-testy-poznamka.md`,
  do teto prace nepatri.

## 1. Format profilu

```yaml
profile:
  collectors: [interfaces, bgp, evpn_instance]  # klic chybi = vsechny
  service_types: [Internet, IPVPN]    # klic chybi = vsechny typy sluzeb
  ping_count: 3
checks:                               # presne dnesni sekce, beze zmeny
  interface_optics_levels:
    enabled: false
  bgp_prefix_counts:
    tolerance_percent: -10
```

- Sekce `profile` i `checks` jsou obe volitelne.
- Neznamy klic v `profile:` je chyba (ToolError s vypisem znamych
  klicu) - preklep v `service_types` nesmi tise znamenat "vsechno".
- Nezname jmeno collectoru je taky ToolError s vypisem registrovanych
  jmen (`interfaces`, `bgp`, `evpn_vpws`, `evpn_esi`, `evpn_instance`,
  `evpn_mac`, `arp`, `nd`, `routes`, `bfd`, `optics`).
- Jmeno profilu (basename souboru) se propisuje do vysledku evaluate,
  aby report rikal, pod jakym profilem vznikl.

## 2. Format auth souboru

```yaml
username: rmohyla
auth: key                       # key | password
key_file: ~/.ssh/id_rsa
password_env: MIG_PROD_PASSWORD # nastroj cte env promennou pri connectu
# password: ...                 # jen pri chmod 0600 souboru
ssh_port: 22
timeout: 30
```

Pravidla nacitani:

- Soubor neexistuje a `--auth-file` nebyl zadan -> zadna chyba, plati
  dnesni defaulty (username `ansible`, klic `~/.ssh/id_rsa`).
- `--auth-file` zadan a soubor neexistuje -> ToolError.
- `password:` pritomen a soubor ma group/world prava -> ToolError
  s navodem (`chmod 600 ...`).
- `password_env:` pritomen a promenna neni nastavena -> ToolError
  `promenna X neni nastavena` uz pri nacitani, ne az jako selhani
  autentizace o tri vrstvy hloubeji.
- `password` i `password_env` zaroven -> ToolError (jednoznacnost).
- `~` v `key_file` se expanduje.

### Proc env promenna funguje v multi-user produkci

Auth soubor zadne tajemstvi neobsahuje - jen jmeno promenne. Env
promenne jsou per-proces: jiny uzivatel na stejnem stroji nemuze cist
cizi `/proc/<pid>/environ` (jen vlastnik a root). Nic neni na sdilenem
disku, nic neni videt v `ps`. Slabina: automatizace (cron/CI) musi
promennou injektovat a interaktivni `export FOO=heslo` konci
v `~/.bash_history` (doporuceni do docs: `read -s`).

## 3. Semantika service_types filtru

Plati dva domaci principy: deaktivovana/nevybrana sluzba nikdy tise
nezmizi z reportu a stav se nikdy nefabuluje.

**Capture:** inventory a scopy se stavi cele, presne jako dnes. Filtr
se aplikuje jen v `resolve_targets` - scopy s typem mimo profil
nedostanou zadne ping cile. Tam zije linearni cas (serial ping
per sluzba), takze `core-only` profil na boxu s 200 sluzbami preskoci
~195 ping sekvenci, ale snapshot porad obsahuje kazdy scope se vsim
bulk-sesbiranym stavem. Preskocene scopy dostanou do snapshotu marker,
aby pozdejsi evaluate umel vyrenderovat SKIP `ping neproveden - mimo
profil` misto ticha.

**Evaluate:** checky bezi jen pro scopy s typem v profilu, analogicky
dnesnimu `--ports` prunovani. Odfiltrovane scopy vypadnou z check
smycky uplne. Dve pojistky zustavaji zamerne nefiltrovane (stejne jako
to dnes dela `filter_result` pro `--filter`): NESPAROVANO hlasi
nesparovane sluzby **jakehokoli** typu a hlavicka nese `filtered`
marker + jmeno profilu - report vznikly pod `core-only.yml` se nikdy
nesmi tvarit jako plna validace.

**Interakce collector filtru s checky:** kdyz profil vypne collector
`evpn`, EVPN checky nenajdou ve snapshotu data a vyjdou SKIP
s existujicim stylem duvodu "collector nesbiral". Zadny check tise
nePASSne na chybejicich datech.

`service_types` filtruje podle service type stringu z inventory
(`Internet`, `IPVPN`, `EVPN`, ...). Budouci "core" typ v inventory
parseru se zapoji bez zmen tady.

## 4. Dotcene soubory

**Novy modul** `migration_validator/auth.py` (nebo
`connection/credentials.py`): nacteni auth YAML, vynuceni 0600
pravidla, resolve `password_env`. Vraci maly dataclass, ktery se
merguje do existujicich `ConnectionOptions`.

**Zmenene moduly:**

- `config.py` - dataclass `Profile` obalujici dnesni `CheckConfig`
  plus novou sekci `profile:` (collectors, service_types, ping_count).
  `load_config` funguje na starych souborech: chybejici `profile:`
  klic = prazdna profile sekce.
- `cli.py` - `--profile` (alias `--config`) na `evaluate` a `capture`;
  flagy `--auth-file` a `--service-types`; merge logika
  flag > soubor > default. `_connection_options` se timhle zmensuje:
  defaulty se stehuji z argparse do merge, protoze argparse default by
  jinak vzdy "vyhral" nad hodnotou ze souboru. Jemny bod: flagy
  potrebuji `default=None`, aby slo rozlisit "uzivatel zadal" od
  "argparse doplnil".
- `capture.py` / `probes/ping.py` - `resolve_targets` dostane
  service-type filtr; preskocene scopy zapisou "mimo profil" ping-skip
  marker do snapshotu.
- `engine.py` - scope filtr pred check smyckou, `filtered` marker +
  jmeno profilu na vysledku.
- `reporting/text_report.py` - render markeru v hlavicce.

## 5. Testy

Domaci styl (fixture-driven, jedno chovani na test):

- auth: odmitnuti souboru se spatnymi pravy; resolve `password_env`;
  chyba pri nenastavene promenne; `password` + `password_env` zaroven;
  chybejici default soubor je OK, chybejici `--auth-file` je chyba.
- profil: stary `checks:`-only YAML se nacita beze zmeny; neznamy klic
  v `profile:` je ToolError.
- precedence: flag prebiji soubor, soubor prebiji default - jeden test
  na kazdou hranici vrstev.
- ping filtr: scope mimo profil nedostane cile a nese SKIP marker.
- evaluate filtr: odfiltrovany scope neni v check smycce; NESPAROVANO
  zustava netknute; hlavicka nese `filtered` + jmeno profilu.
- Existujicich 935 testu musi projit beze zmeny - to je dukaz zpetne
  kompatibility.

## Mimo rozsah

- Zadne zmeny run.yml/manifestu.
- Zadne instance-scoped RPC v collectorech (bulk RPC zustavaji
  globalni).
- Zadne zmeny formatu report bloku (to je samostatna, uz rozhodnuta
  prace na redesignu reportu).
- Deklarativni `tests:` sekce (samostatna poznamka, budouci spec).
