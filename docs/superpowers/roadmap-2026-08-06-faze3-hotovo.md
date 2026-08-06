# Fáze 3 hotová — vazba L2+L3, stav k 2026-08-06

**Výchozí bod:** větev `faze3-vazba-l2-l3` založená z `main` na commitu
`9d29f87` (merge-base main..HEAD, změřeno), všech pět úloh hotových.
**747 testů zelených, 1 přeskočený** (výchozí stav na `9d29f87`, změřeno
ve zvláštním worktree přes `.venv/bin/pytest`). Po fázi 3: **776 testů
zelených, 1 přeskočený** (změřeno na `HEAD`, `.venv/bin/pytest`) — fáze
tedy přidala 29 testů.
Schema **snapshotu** zůstává **7**, schema **inventory** zůstává **5** —
fáze 3 ani jedno neměnila (vazba se počítá z faktů, které fáze 2 už
sbírala).

Zadáním byla fáze 3 roadmapy
[`specs/2026-08-05-evpn-a-run-management-roadmapa-design.md`](specs/2026-08-05-evpn-a-run-management-roadmapa-design.md)
(sekce „Fáze 3 — Vazba L2+L3 (1a, schválená varianta A)"). Provedení
[`plans/2026-08-06-faze3-vazba-l2-l3.md`](plans/2026-08-06-faze3-vazba-l2-l3.md).

---

## Co fáze 3 přinesla

**Nový modul `migration_validator/scoping/linker.py` (Task 1, commit
`6494181`).** Vazba L3 (IRB) scopu na L2 (E-LAN) scope v téže EVPN
instanci. Zdroj: `l3_context` IRB rozhraní z faktu `evpn_instance` (fáze
2.1) + shoda VLAN/unitu IRB s L2 scopem téže instance (`_carries_unit`);
`master` znamená inet.0 (Internet scope bez RI, `_context_matches`).
Párování je 1:1 — víc kandidátů (`len(candidates) != 1`) vazbu nevytvoří,
stejně jako už jednou přiřazený L3 nebo L2 scope (`linked_l3`/`linked_l2`
guard). Vazba se počítá jen nad subject snapshotem, párování
baseline↔subject zůstává na matcheru beze změny.

**Engine počítá vazby a nese je ve `ScopeResult.link` (Task 2, commit
`03aca84`).** `evaluate_snapshots` volá `link_scopes` nad subject scopy a
`_link_payloads` z toho staví oboustranný slovník `{scope_id:
{"role", "peer_scope_id", "peer_interface", "peer_instance"}}` —
`role="l3"` na IRB straně, `role="l2"` na E-LAN straně. Výsledné pořadí
scopů (`_reorder_linked`) přesune L2 scope hned za jeho spárovaný L3
scope, beze změny pořadí nesouvisejících scopů. `link` je aditivní klíč
na `ScopeResult` a v `to_dict()` — bez vazby chybí úplně, stejné pravidlo
jako u ostatních volitelných polí formátu výsledku.

**`interface_errors`/`interface_traffic` na L3 části vázané služby
odkazují na L2 (Task 3, commit `fd692e3`).** Pomocná
`_l3_link_without_transit` v `checks/ifaces.py` pozná scope, který má
vazbu s `role="l3"` a sám nemá tranzitní rozhraní. `InterfaceErrorsCheck`
pak vrátí jeden `Finding(Outcome.INFO, ...)` s popiskem
„Interface errors / traffic" a hodnotou
`mereno na L2 (<L2 rozhraní>) - viz blok nize` místo obvyklého SKIP.
`InterfaceTrafficCheck` pro tu samou službu nevrátí vůbec žádný nález —
řádek by jinak zdvojoval INFO odkaz z errors checku (INFO navíc není v
`_STATUS_ORDER`, takže se do souhrnného sloupce NALEZ nedostane).

**Renderer vypisuje spárované bloky za sebou s odkazy v hlavičce (Task 4,
commit `74a6298`).** `reporting/view.py` doplňuje do hlavičky bloku
`L3 cast: <irb> v <RI> (blok vyse)` na E-LAN straně a
`L2 cast: <iface> v <instance> (blok nize)` na L3 straně. E-LAN blok
vázané služby se v textu podepisuje jako „E-LAN (L2 cast)". V
`text_report.py` (`render`) se množina zobrazených bloků rozšiřuje o
partnera vazby, kdykoli je partner viditelný v beze-filtru výstupu.

**Oprava z finálního review (bez nového commit hashe v tomto zápisu, viz
`git log`): `filter_result` (stejný soubor) samotné scopy mazal drív, než
se k tomuto rozšiřování vůbec dostalo — `--filter`/`--status` tak partnera
smazaly úplně a odkaz „blok nize/vyse" ukazoval do prázdna i přesto, co
tenhle odstavec tvrdil. `filter_result` teď po výběru podle textu i statusu
ponechá i scope, jehož `link["peer_scope_id"]` je mezi vybranými - dvojice
tak filtr přežije spolu, pokud sedí aspoň jedna strana; nesedí-li ani L3,
ani L2, zmizí celá. Partnerovy checky se počítají i do přepočítaného
souhrnu ve `filtered`.

**End-to-end test a dokumentace (Task 5, commit `bbf0bfc`).**
`test_l2_l3_link_renders_paired_blocks` v `tests/test_end_to_end.py`
staví scény ze skutečného `172.20.20.5.yml` (irb.15 ↔ ae0.15 v
`EVPN-VLAN-AWARE-POP1`/`L3VPN-CPE14-UNI`), dosadí `evpn_instance` fakta
ručně (conftest je nesyntetizuje) a ověří celou cestu snapshot →
`evaluate_snapshots` → `render`: pořadí scopů, `ScopeResult.link`,
INFO pointer i oba textové odkazy v hlavičkách. `docs/cs/reference.md` a
`docs/en/reference.md` (zrcadlově) dostaly novou podsekci „Vazba L2+L3" /
„L2+L3 linking" v kapitole 5 (formát výsledku) s ukázkou `scopes[].link`,
bullet o aditivnosti klíče a poznámku u `interface_errors`/
`interface_traffic` v katalogu checků.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/pytest                                     # 776 passed, 1 skipped
.venv/bin/pytest tests/test_end_to_end.py -v -k l2_l3 # 1 passed
grep -n "SCHEMA_VERSION" migration_validator/models/snapshot.py   # = 7, beze zmeny
grep -n "INVENTORY_SCHEMA_VERSION" migration_validator/models/inventory.py  # = 5, beze zmeny
git log --oneline main..HEAD
```

```
bbf0bfc test+docs: end-to-end vazba L2+L3, reference k odkazum a scopes[].link
74a6298 feat: render vazby L2+L3 - odkazy v hlavickach, E-LAN (L2 cast), parove bloky
fd692e3 feat: errors/traffic u L3-extended sluzby odkazuji na L2 blok (INFO miste SKIP)
03aca84 feat: engine pocita vazby L2+L3, ScopeResult.link, L2 blok hned za L3
6494181 feat: linker - vazba L3 (IRB) scope na L2 (E-LAN) scope pres l3_context
```

---

## Vědomé odchylky a co je vědomě mimo rozsah

Přesně jak uvádí brief úlohy 5, sekce „Mimo rozsah fáze 3 (vědomě)":

- **Cross-scope baseline pro traffic** (staré `ge-0/0/4.0` pps proti
  novému `ae0.15` pps) — L2 scope je na novém boxu nová služba bez
  baselinu; případné srovnání přes vazbu je námět pro pozdější fázi.
- **Fyzické členy LAG** (`et-0/0/10` pod `ae0`) — do scope vstupuje jen
  LAG parent přes záznam Layer1, členy ne (beze změny oproti
  dosavadnímu chování).
- **`--detail` chování INFO řádků `evpn_instance_status`** — beze změny
  z fáze 2.
- **Ověření proti laborce** (capture na 172.20.20.4/5) — proběhne po
  dokončení implementace jako samostatný krok, není součástí tasků 1–5.
  **Proběhlo 2026-08-06 po merge, viz sekce níže.**

---

## Ověření proti laborce (2026-08-06, po merge `5f7ae57`)

Capture spuštěn proti oběma zařízením (uživatel `admin`, heslo
z `MIG_LAB_PASSWORD`), snapshoty mimo repo ve scratchpadu úlohy.

**MX (172.20.20.4) — vazba L2+L3 se vytvořila a renderuje přesně podle
mockupu.** Instance `EVPN-VLAN-AWARE-POP1` nese `irb.4094` s
`l3-context: MGMT`; linker spároval L3 scope `svc:irb.4094:IPVPN`
(RI `MGMT`) s L2 scopem `MGMT-VLAN` (E-LAN, `ge-0/0/6.4094`). Z
`evaluate --detail`:

```
 PASS  svc:irb.4094:IPVPN   IPVPN   irb.4094   RI: MGMT
 L2 cast: ge-0/0/6.4094 v EVPN-VLAN-AWARE-POP1 (blok nize)
...
      | Interface errors / traffic              : mereno na L2 (ge-0/0/6.4094) - viz blok nize
...
 FAIL  MGMT-VLAN   E-LAN (L2 cast)   ge-0/0/6.4094   RI: EVPN-VLAN-AWARE-POP1
 L3 cast: irb.4094 v MGMT (blok vyse)
```

L2 blok následuje bezprostředně za L3 blokem a nese měření errors/traffic
na `ge-0/0/6` i `ge-0/0/6.4094`; v L3 bloku zůstal admin/oper stav IRB.
FAIL na `EVPN neighbors: 0` je pravdivý nález laborky (protistrana na EVO
neběží), ne vada nástroje. Souhrnná tabulka ukazuje typ `E-LAN (L2 cast)`.
Ověřeno i párové chování z opravné vlny finálního review: bez `--detail`
FAIL L2 blok vytáhl svůj PASS L3 protějšek (oba odkazy vypsané) a
`evaluate --status fail` zachoval partnera přes filtr — žádný visící
odkaz. Hlavička L3 bloku ukazuje `svc:irb.4094:IPVPN`, protože `irb.4094`
nemá description — korektní fallback na scope id.

**EVO (172.20.20.5) — vazbu zatím ověřit nelze, správné chování bez dat.**
`get_mac_vrf_instance_information` vrací prázdný výsledek
(`facts["evpn_instance"] == {}`, collector `ok`), `evpn_mac` hlásí
očekávané „the l2-learning subsystem is not running" — konfigurace služeb
na EVO existuje (inventory je vidí aktivní), ale control plane v tomto
stavu laborky neběží. Žádná vazba se nevytvořila (správně — není z čeho),
report bez visících odkazů. **Ověření EVO strany (cílový scénář
`irb.15 ↔ ae0.15` v `EVPN-VLAN-AWARE-…`) čeká na migraci služeb na
PTX** — pak stačí nový capture a `evaluate --detail`.

### Doplněk: EVO strana po migraci služeb (2026-08-06 odpoledne)

Uživatel zmigroval služby na PTX a přegenerovala se obě inventory
(`mx_parser.py` / `evo_parser.py` proti živým boxům — CPE14 UNI má nově
adresaci `/29`+`/64` a peery `.4`/`::4`). Po novém capture 172.20.20.5:

- **Všechny tři vazby se vytvořily**: `irb.14 ↔ ae0.14` (Internet,
  kontext `master` → hlavička `L3 cast: irb.14 v inet.0`), cílový
  `irb.15 ↔ ae0.15` (IPVPN `L3VPN-CPE14-UNI`) i `irb.4094 ↔ ae0.4094`
  (MGMT). Bloky dvojic pod sebou, INFO odkaz `mereno na L2 (...)`,
  v L2 blocích ESI `Resolved by IFL`, MAC county per VL i per interface,
  errors/traffic na `ae0` + unitu. ARP/ND v L3 blocích nesou
  `[via ae0.15]` (learned_via z fáze 1).
- **Párové vyhodnocení `--baseline` MX-pre → EVO-post**: matcher spároval
  čisté L3 na UNI se svým IRB přes description (`ge-0/0/4.0 → irb.15`,
  `ge-0/0/5.0 → irb.14`), nové L2 scopy correctly `nova sluzba, chybi
  baseline` a přesto se renderují jako `(L2 cast)` partner bloky —
  přesně chování ze spec fáze 3.
- Pravdivé nálezy laborky v tomto stavu: `EVPN neighbors: 0` (MX strana
  EVPN po migraci už neběží), BFD `Down` na CPE14 peerech, CPE13
  `session Down`, a compare nález `local interfaces: 4, baseline 2`
  u MGMT-VLAN (instance na EVO nese všechny tři ae unity proti dvěma
  na MX) — fáze 2 sémantika rovnosti počtů.

Menší nálezy odložené v průběhu tasků (`.superpowers/sdd/2026-08-06-faze3-vazba-l2-l3/progress.md`),
všechny potvrzené jako neškodné nebo netestovatelné v praxi:

- linker nekouká na deaktivační flagy scope — záměrně (deaktivovaná
  služba zůstává viditelná, checky mají framework SKIP samostatně).
- dva L2 scopy téže instance mířící na stejný IRB — první vyhrává, druhý
  zůstává bez vazby (netestováno, shoduje se s plánem).
- `_reorder_linked` guard na chybějící peer v `results` netestován (v
  praxi nedosažitelný — peer vždy existuje, protože oba scopy pocházejí
  ze stejného `subject_scopes`).
- řazení s víc nesouvisejícími scopy testováno jen s jedním „other"
  scopem navíc.
- překlep v commit subjectu `fd692e3` („miste" místo „misto") —
  kosmetika, commit message se neopravuje zpětně.

## Co zbývá

Fáze 3 pokrývá sekci „Fáze 3 — Vazba L2+L3" roadmapy kompletně. Otevřené
body mimo fázi 3:

- **Fáze 4 — Run management** (bod 4 zadání) + **ping z baseline ARP**
  (bod 1c, schválená varianta 2) — dosud neimplementováno.
- **Fáze 5 — EX podpora** (bod 1b) — dosud neimplementováno.
- **Staré body 20–21** (qualified-next-hop, dual-homed ESI DF role) —
  vědomě odloženy už ve vlně 10, fáze 2 i 3 se jich netýkaly.
- **`docs/cs/files/checks.md` a `docs/en/files/checks.md`** — follow-up
  z fáze 2 (detailní popis `evpn_mac_count` je zastaralý, chybí sekce
  `evpn_instance_status`) zůstává otevřený; fáze 3 do těchto souborů
  taky nesahala (brief úlohy 5 scope výslovně omezil na `reference.md`
  cs/en a tento roadmap dokument) — `evpn_instance_status` i nová vazba
  L2+L3 tam obě čekají na stejný follow-up.
