# Fáze 2 hotová — evpn_instance_status a MAC count přes `count` RPC, stav k 2026-08-05

**Výchozí bod:** větev `faze2-evpn-instance-a-mac-count` založená z `main` na
commitu `ddb5141` (merge-base main..HEAD, změřeno), všech osm úloh hotových.
**745 testů zelených, 1 přeskočený** (výchozí stav na `ddb5141`, změřeno ve
zvláštním worktree: **714 zelených, 1 přeskočený** — fáze tedy přidala 31
testů, mj. `test_evpn.py` pro nový collector `EvpnInstanceCollector`, commit
`96ba8dc`). Schema **snapshotu** je **7** (bylo 6); schema **inventory**
zůstává **5** — fáze 2 ho neměnila.

Zadáním byla celá fáze 2 roadmapy
[`specs/2026-08-05-evpn-a-run-management-roadmapa-design.md`](specs/2026-08-05-evpn-a-run-management-roadmapa-design.md)
(spec 2.1–2.4). Návrh je
[`specs/2026-08-05-evpn-a-run-management-roadmapa-design.md`](specs/2026-08-05-evpn-a-run-management-roadmapa-design.md),
provedení
[`plans/2026-08-05-faze2-evpn-instance-a-mac-count.md`](plans/2026-08-05-faze2-evpn-instance-a-mac-count.md).

---

## Co fáze 2 přinesla

**MAC count přešel z plné tabulky na `count` RPC (schema 6 → 7).**
`EvpnMacCollector` už nestahuje a nepočítá celou MAC tabulku — na boxu s
tisíci MAC to bylo drahé a per-interface počty z toho nešly vůbec spočítat.
Nový RPC volá `count=True` (`get_bridge_mac_table` + `get_evpn_mac_table` na
Junosu, `get_mac_vrf_mac_table` na EVO) a vrací hotové počty per `learn-vlan`
a per interface v jednotném tvaru `{"vlans": {...}, "interfaces": {...}}`.
VLAN klíč je od teď skutečné `learn-vlan` číslo — dřív byl u vlan-based
instancí `"-"` (placeholder), a per-instance/vlan klíčování tak splynulo pro
všechny domény jedné instance dohromady. Ověřeno živě na obou platformách.
Commit `e547a24`.

**`EvpnMacCountCheck` porovnává per-VLAN (union s baseline) i per-interface.**
Nálezy nesou nové labely — `BD-313 MAC count` (u instancí s pojmenovanou
doménou) nebo jen `MAC count` (vlan-based bez domény), a
`Interface ge-0/0/2.313:313 MAC count` pro interface řádky. `checks/evpn.py`
sjednocuje množinu VLAN mezi baseline a subjektem (union), takže zmizelá VLAN
dostane vlastní nález místo tichého vynechání. Commit `0e8601b`.

**Engine přejmenovává baseline interface klíče `evpn_mac` na jména
subjektu.** Interface klíč nese jméno fyzického portu (`ge-0/0/2.313`), které
se migrací mění (jiný port na EVO) — `_aligned_baseline_data` v enginu teď
tohle přejmenování dělá stejně, jako to už dělá pro jiné oblasti, takže
porovnání baseline/subjekt srovnává správné páry, ne cizí porty se stejným
indexem. Commit `1520a6a`.

**Nový collector `EvpnInstanceCollector` (schema 7, oblast `evpn_instance`).**
Junos volá `get_evpn_instance_information`, EVO
`get_mac_vrf_instance_information` (RPC ověřeno živě v laborce, ne odhadem) —
obě vrací shodný tvar `evpn-instance-information`, takže `parse()`
nepotřebuje platformní větev. Extrahuje per-instanci: local interfaces
(total/up/entries), IRB interfaces (total/up/entries vč. `l3_context`),
neighbors (total/addresses) a ESI mapu (bez `05:` — ty si box generuje sám a
nenesou status). Systémová instance `__default_evpn__`
(`EvpnInstanceCollector.SYSTEM_INSTANCES`) se přeskakuje — není služba a
v device scope by trvale hlásila FAIL bez výpovědi. Commity `682beb6`,
`96ba8dc`.

**Nový check `evpn_instance_status` (both, critical, jen E-LAN).** Pravidla:
local interfaces > 0 a všechna `Up`; IRB interfaces `Up`, pokud nějaké
existují; EVPN neighbors > 0; ESI status začíná `Resolved` (case-insensitive)
— bez dat SKIP. S baseline navíc porovnává počty (local/IRB interfaces, neighbors)
proti subjektu — rozdíl je nález. `--detail` rozepisuje INFO řádky (`EVPN
interface`, `IRB interface`, `EVPN neighbor`) beze změny rendereru — `_block()`
je vypisuje jako běžné INFO řádky bloku služby. Commit `f82c976`, scope
filtr pro oblast `evpn_instance` `c7c35d3`.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/pytest                              # 745 passed, 1 skipped
grep -n "SCHEMA_VERSION" migration_validator/models/snapshot.py   # = 7
grep -n "INVENTORY_SCHEMA_VERSION" migration_validator/models/inventory.py  # = 5 (beze zmeny ve fazi 2)
git log --oneline main..HEAD
```

---

## Ověření proti laborce (2026-08-05)

Capture spuštěn proti oběma zařízením, uživatel `admin`, `--auth password`,
heslo z `MIG_LAB_PASSWORD` (`eval "$(grep 'export MIG_LAB_PASSWORD' ~/.bashrc)"`
— proměnná není v neinteraktivním shellu automaticky nastavená). Snapshoty
uloženy mimo repo do scratchpad adresáře úlohy.

**MX (172.20.20.4, služby zapnuté zde) — `capture` prošel bez chyby
collectoru**, `evpn_instance` i `evpn_mac` v `capture.collectors` obě
`status: "ok"`, výsledný snapshot má `schema_version: 7`.

```bash
.venv/bin/mig-validate capture --device 172.20.20.4 --username admin \
    --auth password --password "$MIG_LAB_PASSWORD" \
    --inventory 172.20.20.4.yml --phase pre --output <scratchpad>/faze2-mx-pre.json
.venv/bin/mig-validate evaluate --snapshot <scratchpad>/faze2-mx-pre.json --detail
```

E-LAN blok `MGMT-VLAN` (RI `EVPN-VLAN-AWARE-POP1`, IRB, dvě lokální
rozhraní), ukázka nových řádků z `evaluate --detail`:

```
===========================================================================
 FAIL  MGMT-VLAN   E-LAN   ge-0/0/6.4094   RI: EVPN-VLAN-AWARE-POP1
===========================================================================
 STAV | CHECK                                          : HODNOTA
 -----+------------------------------------------------+-------------------
 SKIP | EVPN ESI status                                : bez dat
 PASS | EVPN local interfaces                          : 2
 PASS | EVPN local interfaces up                       : 2/2
      | EVPN IRB interfaces                            : 1
 PASS | EVPN IRB interfaces up                         : 1/1
 FAIL | EVPN neighbors                                 : 0
 SKIP | ESI status                                     : bez dat
      | EVPN interface                                 : .local..12 Up
      | EVPN interface                                 : ge-0/0/6.4094 Up
      | IRB interface                                  : irb.4094 Up (MGMT)
 PASS | BD-4094 MAC count                              : 4
 PASS | BD-4094 Interface ge-0/0/6.4094:4094 MAC count : 4
```

FAIL na `EVPN neighbors` je pravdivý nález laborky (0 sousedů na této
instanci v okamžiku capture), ne vada nástroje. Druhý blok
(`EVPN-VLAN-AWARE-CPE13-NNI`, vlan-aware s pojmenovanou doménou `BD-313`)
ukazuje i variantu bez IRB:

```
 PASS | EVPN local interfaces                       : 2
 PASS | EVPN local interfaces up                    : 2/2
      | EVPN IRB interfaces                         : 0
 PASS | EVPN neighbors                              : 1
 PASS | BD-313 MAC count                            : 2
 PASS | BD-313 Interface ge-0/0/2.313:313 MAC count : 1
```

a vlan-based instance (`EVPN-VLAN-BASED-CPE13-NNI`, bez pojmenované domény)
label bez `BD-` prefixu:

```
 PASS | MAC count                                   : 2
 PASS | Interface ge-0/0/2.413:413 MAC count        : 1
```

**EVO (172.20.20.5, žádné L2 služby v tomto stavu laborky) —
`evpn_instance` bez chyby, `evpn_mac` s očekávanou chybou collectoru.**
`capture` na EVO vypsal:

```
varovani: collector 'evpn_mac' selhal - collector 'evpn_mac': RPC selhalo -
get_mac_vrf_mac_table: RpcError: RpcError(severity: error, bad_element: None,
message: the l2-learning subsystem is not running)
```

`capture.collectors.evpn_mac.status == "error"`, `evpn_instance.status ==
"ok"`. Tohle je **správné chování, ne vada** — EVO v tomto stavu laborky
nemá zapnuté L2 služby, takže l2-learning subsystém neběží; `evpn_instance`
RPC (`get_mac_vrf_instance_information`) na EVO ale odpoví i bez L2 —
ověřeno přímo v uloženém snapshotu, `facts["evpn_instance"] == {}` (prázdný
slovník, žádná instance). `evaluate` nad EVO snapshotem korektně
vrací `SKIP | EVPN MAC count : collector selhal` a `SKIP | EVPN instance :
bez dat` pro všechny scopy (služby jsou v tomto stavu laborky pořád na MX,
takže EVO scope pro ně nemá EVPN data vůbec — to platí i pro `evpn_esi`,
stejné chování jako dřív).

---

## Poznámka o rozbitých starých snapshotech

Snapshoty se `schema_version: 6` (`runs/mig01/pre.json`,
`runs/mig01/post.json`) nástroj s verzí 7 tvrdě odmítne
(`SnapshotVersionError`), ne pokusem o migraci dat. Je potřeba je znovu
nasnímat: `pre` = služby na MX (172.20.20.4), `post` = po migraci na EVO
(172.20.20.5). Totéž je zapsáno do `docs/cs/reference.md` a
`docs/en/reference.md` (kapitola 4, historie verzí schématu).

---

## Dokumentace

`docs/cs/reference.md` a `docs/en/reference.md` (zrcadlově):

- katalog checků: nový řádek `evpn_instance_status`, upravený popis
  `evpn_mac_count` (počty z `count` výpisu per VLAN a per interface),
- historie verzí schématu doplněna o bumpy 4→5, 5→6, 6→7 (dosud chyběly —
  tabulka se zastavila na 3→4, `schema_version` v textu i v JSON příkladu
  ukazoval zastaralou 4; opraveno na 7),
- poznámka o odmítnutí `runs/mig01/*.json` (schema 6) nástrojem verze 7,
- JSON příklad snapshotu: přidána oblast `evpn_instance`, `evpn_mac`
  aktualizován na nový tvar `{"vlans": {...}, "interfaces": {...}}`,
- přehled RPC: nový řádek `evpn_instance`, `evpn_mac` řádek doplněn o
  argument `count=True`.

**Vědomě mimo rozsah:** `docs/cs/files/checks.md` popisuje `evpn_mac_count`
dosavadním (před-count) chováním — VLAN klíč `"-"` u vlan-based, žádná
zmínka o per-interface počtech ani o `evpn_instance_status`. Brief úlohy 8
scope explicitně omezil na `reference.md` (cs/en) a tento roadmap dokument;
`files/checks.md` potřebuje samostatný follow-up, ale není součástí této fáze.

---

## Vědomé odchylky od dosavadního chování (zdůvodněno v kódu)

- **vlan-based MAC klíč `"-"` → skutečné `learn-vlan`** — ověřeno na obou
  platformách v laborce; dřív všechny domény jedné vlan-based instance
  splývaly pod jeden placeholder klíč.
- **Každý nový collector přeskakuje svou vlastní systémovou instanci** —
  `EvpnMacCollector` přeskakuje `default-switch`
  (`SYSTEM_INSTANCES = {"default-switch"}`), `EvpnInstanceCollector`
  přeskakuje `__default_evpn__` (`SYSTEM_INSTANCES = {"__default_evpn__"}`).
  Ne obě jména v obou collectorech — každé jméno se objevuje jen ve výpisu
  vlastního RPC. Bez přeskočení by v device scope trvale hlásily nesmyslný
  nález.

---

## Co zbývá

Fáze 2 pokrývá spec 2.1–2.4 kompletně (viz self-review v
`.superpowers/sdd/2026-08-05-faze2-evpn-instance-a-mac-count/task-8-brief.md`).
Otevřené body mimo fázi 2:

- **`docs/cs/files/checks.md` a `docs/en/files/checks.md`** — detailní
  popis `evpn_mac_count` je zastaralý (viz výše), chybí sekce
  `evpn_instance_status`.
- **Fáze 3–5** roadmapy
  `specs/2026-08-05-evpn-a-run-management-roadmapa-design.md` — dosud
  neimplementovány.
- **Staré body 20–21** (qualified-next-hop, dual-homed ESI DF role) —
  vědomě odloženy už ve vlně 10, fáze 2 se jich netýkala.
