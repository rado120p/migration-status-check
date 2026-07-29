# `tests/` — testy

```bash
.venv/bin/pytest          # vše prošlo kromě jednoho přeskočeného (~1,2 s)
```

**Žádný test nepotřebuje síť ani laborku.** Je to přímý zisk z toho, že checky jsou čisté
funkce a že se syrové RPC XML nahrává do fixtures. Jediná vrstva, která by reálný box
chtěla, je `connection/` — a ta je záměrně tak tenká, že se testuje přes `FakeDevice`.

Jeden test se přeskakuje trvale: `test_esi_interface_matches_a_scope[junos]` — nahrávka
z MX žádný ESI segment neobsahuje, takže není co ověřovat.

---

## Struktura

| cesta | co testuje |
|---|---|
| `conftest.py` | sdílená fixture `synthetic_snapshot` — postaví snapshot ze **skutečné inventory** |
| `test_package.py` | verze balíčku |
| `test_engine.py` | orchestrace vyhodnocení (13 testů) |
| `test_capture.py` | orchestrace sběru přes `FakeDevice` |
| `test_cli.py` | podpříkazy, návratové kódy, chybové hlášky |
| `test_end_to_end.py` | celý běh nad reálnou inventory obou zařízení |
| `models/` | `inventory`, `scope`, `snapshot`, `result` |
| `connection/` | `detect_platform`, `device_meta`, sestavení PyEZ kwargs |
| `collectors/` | per collector nad nahraným XML + **conformance testy** |
| `probes/` | resolvování cílů a parsování odpovědi pingu |
| `scoping/` | builder, matcher, mapping |
| `checks/` | per check nad ručně psanými minimálními fixtures |
| `reporting/` | textový report a filtrování |
| `fixtures/` | inventory obou zařízení + `rpc/{junos,junos-evo}/*.xml` |

---

## `fixtures/` — nahraná data

```
tests/fixtures/
├── 172.20.20.4.yml            inventory z MX
├── 172.20.20.5.yml            inventory z PTX (EVO)
└── rpc/
    ├── junos/                 interfaces, arp, bgp, evpn_vpws, evpn_esi,
    │                          evpn_mac, evpn_mac.2
    └── junos-evo/             totéž bez evpn_mac.2 (EVO má jen jedno RPC)
```

Fixtures se nahrávají příkazem `mig-validate record --device <ip> --output-dir tests/fixtures/rpc`.
Neznámý výstup z produkce se zkopíruje sem a regresní test je hotový — je to jediný
udržitelný způsob, jak collectory neshnijí.

**Fixtures z obou platforem jsou povinné.** Tím se vynutí, že se platformní rozdíly řeší
v collectoru a neprosáknou do checků — což je předpoklad, na kterém stojí cross-device
porovnání.

Soubor `evpn_mac.2.xml` existuje jen pro `junos`, protože MX potřebuje na MAC tabulku dvě
RPC. Kdyby `record` ukládalo jen první, chyběly by vlan-based instance.

---

## Conformance testy — nejdůležitější soubor

`tests/collectors/test_conformance.py` je jediný test, který **připíná obě poloviny švu
collector ↔ check proti sobě**:

1. vyrobí fakta **skutečnými collectory** z **nahraného XML z laborky** (žádné ruční
   dolepování),
2. postaví scopy ze **skutečné inventory**,
3. prožene to **skutečnými checky** přes `api.evaluate()`,
4. tvrdí, že checky nevrátily **samé `SKIP`**.

Proč je to potřeba: když collector přejmenuje klíč, check ho nenajde a vrátí `SKIP`.
**A protože `SKIP` není `FAIL`, nikde jinde se to neprojeví** — nástroj mlčky přestane
kontrolovat celou oblast. Ostatní offline testy to nechytí, protože jejich fixtures si
data staví ze stejných selektorů jako scope, takže jsou konzistentní z konstrukce.

Konkrétní tvrzení:

| test | co hlídá |
|---|---|
| `test_collector_keys_match_contract` | klíče `facts` odpovídají jménům collectorů |
| `test_evpn_instances_are_keyed_by_routing_instance` | `evpn_vpws` a `evpn_mac` klíčované názvem instance, ne rozhraním |
| `test_esi_interface_matches_a_scope` | `evpn_esi.interface` je název, který scope opravdu matchne |
| `test_interfaces_reach_their_scopes` | aspoň jeden scope vidí aspoň jedno rozhraní |
| `test_checks_produce_real_verdicts_not_all_skip` | nad reálnými daty padne aspoň jeden ne-`SKIP` verdikt |
| `test_specific_check_sees_data` | per oblast a platformu — aby selhání ukázalo, **který** šev se rozpojil |

---

## Vzorce, které se v testech opakují

**`FakeDevice` / `FakeRpc`** (`test_capture.py`, `collectors/test_base.py`,
`connection/test_junos.py`) — objekt s `facts` a `rpc`, kde `__getattr__` vrací připravené
XML. Umí i cíleně selhat (`FakeDevice(failing=("get_bgp_neighbor_information",))`), takže se
dá testovat chování při selhaném collectoru bez jakéhokoliv mockovacího frameworku.

**`synthetic_snapshot`** (`conftest.py`) — postaví snapshot z reálné inventory tak, že
fakta odvodí ze selektorů scopů. Používají ho end-to-end testy: kontrolují orchestraci
a párování, ne parsování.

**Deterministický čas** — všude se předává `now=NOW`, aby byl výsledek porovnatelný.

---

## Co která oblast hlídá (výběr)

| test | pojistka proti |
|---|---|
| `test_capture.py::test_every_area_is_registered_for_both_platforms` | collector zapomenutý v `all.py` by tiše vypustil celou oblast |
| `test_capture.py::test_failed_arp_collector_leaves_ping_empty` | bez ARP nesmí vzniknout falešný probe (a `all()` na prázdném seznamu netvrdí nic — test to explicitně ošetřuje) |
| `test_capture.py::test_record_raw_writes_every_rpc_of_multi_rpc_collector` | fixture jen z prvního RPC by byla tiše neúplná |
| `test_engine.py::test_healthy_scope_without_baseline_is_pass_not_skip` | zdravá služba nesmí svítit `SKIP` jen kvůli compare-only checku |
| `test_engine.py::test_failed_collector_produces_skip_not_pass` | chybějící data nikdy nedají PASS |
| `test_engine.py::test_unmatched_subject_scope_is_still_state_validated` | nová služba se pořád zkontroluje, jen se nemá s čím porovnat |
| `test_end_to_end.py::test_management_interfaces_never_appear` | `fxp0` ani `mgmt` se nesmí objevit ve výstupu |
| `test_end_to_end.py::test_render_after_filter_still_shows_unmatched_section` | filtr nesmí schovat `NESPAROVANO` |
| `test_end_to_end.py::test_evpn_checks_produce_real_verdicts_on_real_data` | EVPN fact-schéma nesmí tiše sklouznout do samých `SKIP` |
| `scoping/test_matcher.py::test_ambiguity_never_guesses` | radši nespárováno než tichý špatný match |
| `scoping/test_matcher.py::test_ambiguous_under_one_key_is_not_paired_under_a_sibling_key` | scope nesmí skončit zároveň v `pairs` i v `unmatched` |
| `checks/test_base.py::test_missing_data_never_passes` | nadřazené pravidlo celého nástroje |
| `models/test_result.py::test_degraded_is_warn_even_when_critical` | „částečný úspěch = WARN" platí i při `critical` |
| `cli` testy s kódy 0/1/2 | *nástroj selhal* ≠ *test selhal* |

Poznámka k `collectors/test_base.py`: registruje si vlastní demo collectory do **stejného
modulového registru**, takže testy jinde porovnávají registr operátorem `>=`, ne `==`.
