# Audit: všechny checky a logika všech možných výsledků (stav kódu 2026-09-03)

Vygenerováno přímo z kódu `migration_validator/checks/*.py` (ne z docstringů) jako podklad
pro opravné kolo hlášek. Každý check má tabulku `situation | Outcome | message | value`.
Řádky označené *no finding emitted* jsou větve, které v reportu nezanechají žádnou stopu.

## Kandidáti pro opravné kolo (nalezeno při extrakci, kód vs. očekávání)

Seřazeno podle toho, jak moc to mate operátora. Nic z toho zatím není opraveno.

**Hlášky, které nenesou důvod verdiktu**
- `interface_traffic` bez baseline: `<iface>: input_pps 0 pps` je BROKEN, ale hláška neříká proč (require_nonzero).
- `bfd_session_state`: `<peer>: session Down` bez očekávání; `bgp_session_state` naproti tomu píše `ocekavano Established`.
- `evpn_vpws_status` per peer: řádek `<instance>: remote peer <ip>` je BROKEN při neresolvnutém stavu, ale text je stejný jako u OK.
- `interface_optics_alarms`: alarm i warning mají stejnou hlášku `<tag> je zvednuty`, liší se jen FAIL/WARN.
- `multicast_forwarding_status` upstream: `nema upstream interface` i když upstream existuje, jen má špatný prefix (role).
- `mvpn_cmulticast_status`: `sender PE se zmenil z <was>` neuvádí nový PE.

**Verdikt, který neodpovídá situaci**
- `isis_adjacency_state`: chybějící IPv6 adresa souseda bez baseline = FAIL i na IPv4-only lince; s baseline jen WARN (nekonzistentní).
- `isis_adjacency_state`: adjacency Up, v baseline ne-Up → DEGRADED/WARN („zlepšení je změna“). Stejně `deactivation_state`: služba reaktivovaná = WARN.
- `isis_interface_info` loopback: chybějící level 2 dá dva FAILy (level 2 chybí + passive=ne) za jednu příčinu; transit s chybějícím level 2 dostane OK `bez Passive`.
- `ldp/pim_neighbor_state`: adresa souseda None bez baseline = INFO `chybí v outputu`, nikdy FAIL.
- `evpn_esi_status`: `df_role == ""` → OK `DF bez zaznamu`; `df_role None` → INFO.
- `interface_errors`: fyzické rozhraní bez error counterů ve faktech → OK `bez chyb` (nic se neměřilo).
- `interface_traffic` s baseline 0 pps a subject 0 pps → OK `v toleranci` (require_nonzero se při baseline ignoruje).
- `isis_overview`: chybějící data collectoru = OK `nenastaven`.
- `bgp_prefix_counts`: RIB nebo peer v baseline a ne v subjektu = ticho; nárůst prefixů je vždy OK.
- `static/aggregate_route_status` v device scope: `v baseline byla, v subjektu neni` i pro routu, která v baseline nebyla (jen configured).

**Sloupec ZMENA / baseline_value nekonzistentní**
- `evpn_instance_status`: řádky EVPN interface, IRB, neighbor, ESI nikdy nenesou baseline_value → vždy `bez baseline`.
- `isis_adjacency_state`: řádek soused při shodě nemá baseline_value, řádek stav ano.
- `aggregate_route_status`: hláška `baseline aktivitu neuvadi`, ale ZMENA ukáže `bylo v tabulce`.
- `bgp_session_state` deaktivovaný peer: baseline stav znám, ale nevypsán.
- `mpls_interface_state`, `bfd_transit_state`: mode both, ale baseline se k verdiktu nepoužívá.
- `mvpn_cmulticast_status`: tunnel id v baseline_value → ZMENA ukáže změnu bez WARN (záměrně se porovnává jen PE).

**Diakritika / formát**
- `MISSING = "chybí v outputu"` a `nakonfigurován` mají diakritiku, všechny ostatní hlášky ne.
- `evpn_vpws_status`: label `ESI` vs. message `esi`. `evpn_esi_status`: message `DF DF not elected yet`.
- `core_multicast_forwarding`: 4 doplňkové SKIP řádky s prázdnou value; nemá souhrn `N z M nefunguje` jako servisní varianta.
- `ping_reachability`: `mimo profil` neříká který profil; `sent == 0` → `neodpovedel (0 paketu)`.

**Ticho místo řádku** (větve bez stopy v reportu)
- `arp_present`/`nd_present` bez adresy dané rodiny; `pim_neighbor_state` bez `protocols pim`; `core_multicast_forwarding` bez inet.2 statik; `interface_traffic`/`traffic_ceased` na L3 části linku (INFO ukazatel emituje jen `interface_errors`, po jeho vypnutí zmizí); `evpn_mac_count` interface jen v baseline; `bfd_session_state` bez peerů.

## 0. Rámec společný všem checkům

**Outcome → Status** (`models/result.py::derive_status`):

| Outcome | Status | poznámka |
|---|---|---|
| OK | PASS | |
| INFO | INFO | informativní řádek, do stavu scopu se počítá jako nejnižší |
| SKIP | SKIP | |
| DEGRADED | WARN | **vždy**, i při severity critical |
| BROKEN | FAIL | při severity critical |
| BROKEN | WARN | při severity advisory |

**Stav scopu** = nejhorší status ze všech řádků kromě SKIP (pořadí INFO < PASS < SKIP < WARN < FAIL);
jen samé SKIPy → SKIP. Deaktivační SKIPy se v reportu bez `--detail` slévají do jednoho řádku
`N dalsich checku preskoceno, sluzba je deaktivovana`.

**Sloupec ZMENA** (`reporting/view.py::change_text`): prázdný bez baseline snapshotu a u state checků;
`bez baseline` když řádek compare/both checku nemá `baseline_value` (kromě SKIP); prázdný při shodě;
`bylo <baseline_value>   <delta>` při rozdílu.

**Řádky vyráběné frameworkem před spuštěním checku** (`checks/base.py::run_check`, v tomto pořadí):

| situation | Outcome | message | value |
|---|---|---|---|
| check vypnutý v settings | *no finding emitted* | | |
| `applies_to(scope)` False (service type/subtype/layer1 filtr) | *no finding emitted* | | |
| `requires_inventory` a device scope | SKIP | `check vyzaduje inventory, snapshot ji neobsahuje` | `bez inventory` |
| mode `compare` a žádná baseline | SKIP | `porovnavaci check bez baseline snapshotu` | `bez baseline` |
| scope deaktivovaný (kromě `deactivation_state`) | SKIP | `sluzba je v konfiguraci deaktivovana (<reason>)` | `<reason>` |
| selhaný collector pro oblast v `requires` | SKIP | `chybi data z collectoru '<area>': <error>` | `collector selhal` |
| výjimka v `run()` | SKIP | `check selhal: <error>` | `check selhal` |

**Config defaults** (`config.py`): `interface_traffic` tolerance_percent -60, require_nonzero True;
`interface_optics_levels` tolerance_db 2.0; `bgp_prefix_counts` tolerance_percent -10;
`evpn_mac_count` tolerance_percent -60; `traffic_ceased` enabled False, max_residual_pps 1.
Žádný jiný check nečte options.


## 1. Rozhraní, optika, deaktivace (ifaces.py, optics.py, deactivation.py)

### interface_state
title `Stav rozhrani` | label `Interface status` | mode STATE | default severity CRITICAL | service_types None, service_subtypes None (all services) | requires `("interfaces",)` | requires_inventory False | layer1 True | order 0 | config options: none.

Emits TWO rows per interface in `scope_interfaces(ctx)` (admin, then oper). Label on layer1 scope is bare (`Interface admin status` / `Interface operational status`), on service scope `qualified(...)` = `Interface admin status (<name>)` etc. Note: this check does NOT filter on transit -- lo0/irb/etc. are included when they are in the subject.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `subject["interfaces"]` empty/missing | SKIP | `pro tento scope nejsou data o rozhranich` | `bez dat` | no label -> `Interface status` |
| `interfaces` non-empty but `scope_interfaces(ctx)` returns [] (e.g. layer1 scope with only unit names, or service with transit parent and only physical names) | (no finding emitted) | - | - | returns `[]`, block gets no interface_state row at all |
| per interface, `str(data.get("admin_status","unknown")) == "up"` | OK | `<name>: admin_status up` | `Up` | subject `{"admin_status": "up"}` |
| per interface, admin_status != "up" (incl. missing -> "unknown") | BROKEN | `<name>: admin_status <state>` | `<state>.capitalize()` e.g. `Down`, `Unknown` | subject `{"admin_status": <state>}` |
| per interface, `oper_status == "up"` | OK | `<name>: oper_status up` | `Up` | subject `{"oper_status": "up"}` |
| per interface, oper_status != "up" (incl. missing -> "unknown") | BROKEN | `<name>: oper_status <state>` | `<state>.capitalize()` | subject `{"oper_status": <state>}` |

Docstring/comment notes: module docstring says "Counter-based checky bezi jen na tranzitnich rozhranich" -- interface_state is not counter-based and indeed runs on all scope interfaces; consistent. No discrepancy.

---

### interface_errors
title `Chybove countery rozhrani` | label `Interface errors` | mode STATE | default severity ADVISORY | service_types None, service_subtypes None | requires `("interfaces",)` | requires_inventory False | layer1 True | order 0 | config options: none.

Branch order in code:

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `ctx.link` set, `link["role"] == "l3"`, and no transit interface in subject | INFO | `errors/traffic se meri na L2 casti (<peer1, peer2>)` | `mereno na L2 (<peers>) - viz blok nize` (1 peer) / `mereno na L2 (<peers>) - viz bloky nize` (>1 peer) | label `Interface errors / traffic`; peers = `", ".join(p["interface"] for p in link["peers"])`; with 0 peers value is `mereno na L2 () - viz blok nize`. One row, covers both errors and traffic |
| not layer1 scope AND any `selectors.physical_interfaces` is transit (service with L1 parent) | (no finding emitted) | - | - | returns `[]` -- errors are reported in the L1 block |
| names = `scope_interfaces` filtered to transit AND physical is empty, but subject has transit interfaces (only units) | SKIP | `chybove countery nese jen fyzicke rozhrani, ve scope jsou jen unity (<transit names>)` | `jen unity` | no label -> `Interface errors` |
| names empty and no transit interfaces at all | SKIP | `neni tranzitni rozhrani (<all names or 'zadne rozhrani'>), counter check se preskakuje` | `netranzitni rozhrani` | `_no_transit_finding`, label -> `Interface errors` |
| per physical transit interface, sum of present `input_errors`/`output_errors`/`framing_errors` == 0 | OK | `<name>: bez chyb` | `bez chyb` | label `Interface errors` (layer1) or `Interface errors (<name>)`; subject = dict of the counters present |
| per physical transit interface, sum > 0 | BROKEN | `<name>: chybove countery nenulove (<detail>)` where detail = `input_errors=<n>, output_errors=<n>` (only non-zero keys, in order input_errors, output_errors, framing_errors) | `<detail>` | same label/subject |

Notes: counters absent from data are omitted (dict comprehension `if key in data`), so an interface with none of the three keys yields OK `bez chyb` with empty subject -- a "measurement" that didn't happen (contrast with the `is_physical` docstring's concern about units). One row per physical interface.

Docstring/comment discrepancies: none material. The comment "Chybove countery nese jen fyzicky port - v service scopu s L1 rodicem ho hlasi ten L1 blok" matches code.

---

### interface_traffic
title `Datovost rozhrani` | label `Interface traffic` | mode BOTH | default severity ADVISORY | service_types None, service_subtypes None | requires `("interfaces",)` | requires_inventory False | layer1 True | order 0 | config options: `tolerance_percent` (default -60, float), `require_nonzero` (default True, bool). Both read with `options[...]` (KeyError -> `check selhal` skip if missing from merged config).

Emits TWO rows per transit interface in `scope_interfaces(ctx)`: `input_pps` (label base `Interface traffic in`) then `output_pps` (`Interface traffic out`). Layer1: bare label; service: `Interface traffic in (<name>)`. Values are `int(data.get(key, 0))`.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `_l3_link_without_transit(ctx)` not None | (no finding emitted) | - | - | returns `[]`; the INFO pointer row is emitted by interface_errors only |
| no transit interface in `scope_interfaces` | SKIP | `neni tranzitni rozhrani (<all names or 'zadne rozhrani'>), counter check se preskakuje` | `netranzitni rozhrani` | label -> `Interface traffic` |
| per iface/direction, no baseline for this interface (no baseline snapshot, or interface missing in baseline) and NOT (`require_nonzero` and pps == 0) | OK | `<name>: <key> <n> pps` e.g. `xe-0/0/1: input_pps 1234 pps` | `<n> pps` | baseline_value None, delta None, details `{}`; subject `{<key>: n}` |
| per iface/direction, no baseline, `require_nonzero` True and pps == 0 | BROKEN | `<name>: <key> 0 pps` | `0 pps` | same; note message gives no reason for BROKEN |
| per iface/direction, baseline present, baseline pps == 0 (`percent_change` -> None) | OK | `<name>: <key> v toleranci <tol:.0f> %` e.g. `... v toleranci -60 %` | `<n> pps` | baseline_value `0 pps`, delta None, details `{"tolerance_percent": tol}`; require_nonzero is NOT applied when baseline exists -- 0 -> 0 is OK |
| per iface/direction, baseline present, baseline > 0, `change < tolerance` (i.e. drop steeper than -60 %) | BROKEN | `<name>: <key> kleslo o <abs(round(change))> % (<baseline> -> <subject>), prah je <tol:.0f> %` | `<n> pps` | baseline_value `<b> pps`, delta `<change:+.0f> %` e.g. `-75 %`, details `{"tolerance_percent": tol}` |
| per iface/direction, baseline present, baseline > 0, `change >= tolerance` (including any increase) | OK | `<name>: <key> v toleranci <tol:.0f> %` | `<n> pps` | baseline_value `<b> pps`, delta `<change:+.0f> %` |

Notes: `tolerance` is negative by default so message `prah je -60 %` prints the signed threshold. With baseline present and subject == 0 and baseline > 0, change = -100 < -60 -> BROKEN `kleslo o 100 %`. If an operator sets a positive tolerance, `change < tolerance` would flag every non-increase.

Docstring/comment discrepancies: `_traffic_finding` docstring "Bez baseline se hodnoti jen absolutni hodnota" is accurate. No mention anywhere that `require_nonzero` is ignored when a baseline exists; the option name suggests otherwise.

---

### traffic_ceased
title `Utichnuti stareho rozhrani` | label `Interface traffic ceased` | mode COMPARE | default severity ADVISORY | service_types None, service_subtypes None | requires `("interfaces",)` | requires_inventory False | layer1 True | order 0 | config: `enabled` default False (check is off by default), `max_residual_pps` (default 1, int).

One row per transit interface in `scope_interfaces(ctx)` (not per direction; residual = max(in, out)). Label bare on layer1, `Interface traffic ceased (<name>)` on service scope.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| (framework) no baseline | SKIP | `porovnavaci check bez baseline snapshotu` | `bez baseline` | COMPARE mode |
| `_l3_link_without_transit(ctx)` not None | (no finding emitted) | - | - | returns `[]` |
| no transit interface in `scope_interfaces` | SKIP | `neni tranzitni rozhrani (<names>), counter check se preskakuje` | `netranzitni rozhrani` | label -> `Interface traffic ceased` |
| per iface, interface absent from `baseline["interfaces"]` | SKIP | `<name>: rozhrani neni v baseline snapshotu` | `bez baseline` | no baseline_value |
| per iface, baseline input_pps == 0 and output_pps == 0 | SKIP | `<name>: v baseline zadny provoz, utichnuti nelze overit` | `bez provozu v baseline` | baseline dict + subject dict set; baseline_value None (the comment above says compare check without baseline_value shows 'bez baseline' in ZMENA column -- this SKIP row still has none) |
| per iface, `residual = max(in,out) > max_residual_pps` | BROKEN | `<name>: stare rozhrani stale nese provoz (<residual> pps, prah <threshold> pps)` | `<residual> pps` | baseline_value `<max(baseline in,out)> pps`, delta None, details `{"max_residual_pps": threshold, "residual_pps": residual}` |
| per iface, residual <= threshold | OK | `<name>: provoz utichl (<residual> pps)` | `<residual> pps` | same baseline_value/details |

Docstring/comment discrepancies: class docstring "Default vypnuty - vyzaduje treti capture stareho boxu po migraci" matches config (`enabled: False`). The inline comment about `baseline_value` motivating `previous` is only honoured on the BROKEN/OK rows, not on the "bez provozu v baseline" SKIP row (which has baseline data but no baseline_value).

---

### interface_optics_levels
title `Opticke urovne` | label `Interface optical levels` | mode BOTH | default severity CRITICAL | service_types `frozenset()` (empty -> never matches any service scope; runs only on layer1 and device scopes), service_subtypes None | requires `("optics",)` | requires_inventory False | layer1 True | order 0 | config options: `tolerance_db` (default 2.0, float).

Ports iterated: `_ports(ctx)` = `[selectors.interfaces[0]] + sorted(selectors.lag_members)` (None dropped). On device scope `_port` is None and lag_members presumably empty -> no rows (no finding emitted). One row per lane per port. Label via `_optics_label(base, name, lane, port)`: parts = `[name if name != port]` + `[f"lane {lane}"]` -> e.g. `Interface optical levels (lane 0)` for the port itself, `Interface optical levels (xe-0/0/1 lane 0)` for a LAG member; bare `Interface optical levels` only if name == port and lane is None (never for level rows since lane is always passed).

`value` always = `RX <fmt(rx)> / TX <fmt(tx)>` where `_fmt` gives `<x:.2f> dBm`, `-Inf dBm`/`Inf dBm` for non-finite, `?` for None.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `_ports(ctx)` empty (device scope) | (no finding emitted) | - | - | returns `[]` |
| per port, `optics.get(name)` is None | SKIP | `<name>: rozhrani nevraci opticka data` | `bez optiky` | label always `Interface optical levels (<name>)` even when name == port |
| per lane, no baseline lane (no baseline snapshot / port or lane missing in baseline), no dark side | OK | `<name>: RX <rx> / TX <tx>` (i.e. `<name>: <value>`) | `RX x.xx dBm / TX y.yy dBm` | subject `{rx_power_dbm, tx_power_dbm}`; no baseline_value/delta/details |
| per lane, no baseline lane, rx and/or tx non-finite (`_dark_sides`) | BROKEN | `<name>: RX bez svetla (<value>)` / `<name>: TX bez svetla (...)` / `<name>: RX/TX bez svetla (...)` | `RX -Inf dBm / TX ...` | subject set |
| per lane, baseline lane present, any dark side now | BROKEN | `<name>: <RX|TX|RX/TX> bez svetla (<value>)` | value | baseline_value `RX <fmt> / TX <fmt>` of baseline lane; delta = joined deltas of the sides that were finite on both sides (e.g. `TX +0.3 dB`) or None; details `{"tolerance_db": tol}` |
| per lane, baseline present, not dark, some side with both finite values and `abs(now-before) > tolerance` | DEGRADED (-> WARN always) | `<name>: uroven se posunula o vic nez <tol:.1f> dB (<deltas>)` e.g. `... o vic nez 2.0 dB (RX -3.2 dB, TX +0.1 dB)` | value | baseline_value, delta `RX <+d.d> dB, TX <+d.d> dB`, details `{"tolerance_db": tol}` |
| per lane, baseline present, not dark, all finite deltas within tolerance (or no comparable side -- e.g. baseline was -Inf, now finite, or key None on either side) | OK | `<name>: urovne v toleranci <tol:.1f> dB` | value | baseline_value; delta joined string or None (None when no side was comparable); details |

Notes: sides where `lane.get(key)` or `baseline_lane.get(key)` is None are skipped silently; `value` for None prints `?`. A lane that recovered from -Inf in baseline to finite now yields OK `urovne v toleranci` with delta None (no message about recovery). Rows are per lane, so a 4-lane QSFP produces 4 rows per port.

Docstring/comment discrepancies: module docstring "Alarm radky se tisknou JEN zvednute; tichy port ma jeden souhrnny radek" refers to the alarms check, accurate there. Class comment "Delta pres toleranci je DEGRADED a ta zustava WARN pri jakekoli severity" matches `derive_status`. Nothing inaccurate found.

---

### interface_optics_alarms
title `Opticke alarmy` | label `Interface optical alarms` | mode STATE | default severity CRITICAL | service_types `frozenset()` (never on service scope), service_subtypes None | requires `("optics",)` | requires_inventory False | layer1 True | order 0 | config options: none.

Ports iterated as in levels check. Labels: quiet-port row `_optics_label(label, name, None, port)` -> bare `Interface optical alarms` for the port itself, `Interface optical alarms (<member>)` for LAG members; raised rows include `lane <n>`.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `_ports(ctx)` empty (device scope) | (no finding emitted) | - | - | returns `[]` |
| per port, `optics.get(name)` is None | SKIP | `<name>: rozhrani nevraci opticka data` | `bez optiky` | label always `Interface optical alarms (<name>)` |
| per port, no lane has any truthy entry in `lane["alarms"]` or `lane["warnings"]` | OK | `<name>: bez optickych alarmu` | `bez alarmu` | one summary row per port |
| per port, per lane, per truthy `alarms[tag]` | BROKEN (-> FAIL at CRITICAL) | `<name>: <tag> je zvednuty` | `<tag>` | label `Interface optical alarms (lane <n>)` or `(<member> lane <n>)`; one row per raised flag; alarms of all lanes listed before warnings? No: per lane, alarms first then warnings, lanes in data order |
| per port, per lane, per truthy `warnings[tag]` | DEGRADED (-> WARN) | `<name>: <tag> je zvednuty` | `<tag>` | same labeling; message does not distinguish alarm vs warning -- only Outcome/status does |

Notes: `lane["alarms"]`/`lane["warnings"]` accessed with `[]` -- missing keys raise KeyError -> framework `check selhal: ...` SKIP. No baseline usage (STATE).

Docstring/comment discrepancies: none.

---

### deactivation_state
title `Stav deaktivace sluzby` | label `Deaktivace` | mode BOTH | default severity CRITICAL | service_types None, service_subtypes None | requires `()` (no collector areas) | requires_inventory True (device scope -> framework SKIP `check vyzaduje inventory, snapshot ji neobsahuje` / `bez inventory`) | layer1 True | order 0 | config options: none.

Only check exempt from the framework's deactivation SKIP (`DEACTIVATION_CHECK_ID`). Inputs: `subject_off = ctx.scope.is_deactivated`; `baseline_off = ctx.baseline_scope.is_deactivated` if `baseline_scope` present else None; `reason = ctx.scope.deactivation_reason`. Outcome via `deactivation_outcome(subject_off, baseline_off)`:
- not subject_off and not baseline_off (baseline_off False or None) -> None
- subject_off and baseline_off is False -> BROKEN
- otherwise -> DEGRADED

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| service active now, baseline active or no baseline scope | (no finding emitted) | - | - | healthy service: `[]` |
| service deactivated now, `baseline_scope is None` | DEGRADED (WARN) | `sluzba je v konfiguraci deaktivovana (<reason>), baseline neni k porovnani` | `<reason>` | baseline_value None; label `Deaktivace` |
| deactivated now AND deactivated in baseline | DEGRADED (WARN) | `sluzba je deaktivovana (<reason>) stejne jako v baseline` | `<reason>` | baseline_value = `baseline_scope.deactivation_reason` |
| deactivated now, active in baseline (`baseline_off is False`) | BROKEN (FAIL at CRITICAL) | `sluzba v baseline bezela, ted je deaktivovana (<reason>) - migrace nedokoncena` | `<reason>` | baseline_value `aktivni` |
| active now, deactivated in baseline | DEGRADED (WARN) | `sluzba byla v baseline deaktivovana (<baseline reason>), ted je aktivni` | `aktivni` | baseline_value = `baseline_scope.deactivation_reason` |

Notes: `reason` is whatever `scope.deactivation_reason` carries (may be None when active -> not printed since active-now rows use the baseline reason). No `details`, `subject`, or `delta` set on any row.

Docstring/comment discrepancies:
- Module docstring: "Sam SKIP nikdy nevydava" -- true for `run()`, but the framework still emits SKIP `bez inventory` for this check on device scope (requires_inventory), so the report can show a SKIP row for `deactivation_state`.
- `deactivation_outcome` docstring row 4 ("aktivni ted, vypnuty v baselinu ... zlepseni neni varovani (R-2)") -- for the service itself the code DOES emit DEGRADED/WARN for that reactivation case, i.e. the improvement is reported as a warning for the service; the docstring only exempts routes/BGP peers, so this is consistent with code but worth noting for the audit as a deliberate WARN-on-improvement.

---

### Poznámky napříč (ifaces)

1. `_no_transit_finding` lists ALL interfaces in the subject (including e.g. `lo0.0`) and says `neni tranzitni rozhrani (...)`, used by interface_errors, interface_traffic, traffic_ceased -- each with the check's own label.
2. The INFO pointer to the L2 block (`errors/traffic se meri na L2 casti (...)`) is emitted only by interface_errors; interface_traffic and traffic_ceased return `[]` in the same situation, so if interface_errors is disabled in config the pointer disappears entirely.
3. interface_traffic's `require_nonzero` is applied only when there is no baseline for the interface; with a baseline of 0 pps and subject 0 pps the row is OK `v toleranci`.
4. interface_errors on a physical transit interface whose data contains none of the three error keys reports OK `bez chyb` with empty subject.
5. Only interface_traffic and interface_optics_levels populate `delta`; traffic_ceased and deactivation_state set `baseline_value` but never `delta`.

## 2. BGP, BFD, dosažitelnost (bgp.py, bfd.py, reachability.py)

### bgp_session_state

`BgpSessionStateCheck` – title `Stav BGP session`, label `BGP status`, mode **both**, default severity **critical**, service_types `{Internet, IPVPN}` + přes `_AppliesToCoreLoopback.applies_to` i `Core` se subtype `loopback` (jiné Core subtypy vrací False), service_subtypes None, requires `("bgp",)`, requires_inventory False, order 0, layer1 False. Config options: žádné (jen severity). `describe()` hlásí `service_types = ["Core","IPVPN","Internet"]` a `service_subtypes_by_type = {"Core": ["loopback"]}`.

Vstupy: `peers = subject["bgp"]`, `baseline_peers = baseline["bgp"]`, `configured = scope.selectors.bgp_neighbors`, `inactive = [p in selectors.bgp_neighbors_inactive if p not in peers]`, `universe = configured ∪ bgp_neighbors_inactive ∪ peers ∪ baseline_peers`. Baseline pro řádek peera je z `baseline_peers[peer]["state"]` (default `"unknown"`), nebo None když peer v baseline není. `family = peer_family(peer)` (4/6 podle IP adresy, None když není IP).

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `universe` prázdné (žádný configured, inactive, měřený ani baseline peer) | SKIP | `sluzba nema zadne BGP peery` | `zadny peer` | label default `BGP status`, family None, jeden řádek |
| **per peer v `peers`** (sorted): `state != "Established"` (`state = str(peers[peer].get("state","unknown"))`) | BROKEN | `<peer>: stav <state>, ocekavano Established` | `<state>` | label `BGP status (<peer>)`, family, baseline_value=`<baseline_state>` nebo None, baseline=`{"state": baseline_state}` nebo None, subject=`{"state": state}` |
| per peer v `peers`: state == Established a `baseline_state is not None and baseline_state != state` | OK | `<peer>: stav se zmenil <baseline_state> -> Established` | `Established` | baseline_value=`<baseline_state>`, baseline=`{"state":...}`, subject=`{"state":"Established"}` |
| per peer v `peers`: state == Established, bez baseline nebo baseline_state == Established | OK | `<peer>: Established` | `Established` | baseline_value=None nebo `Established`; baseline dict None nebo `{"state":"Established"}` |
| **per peer v `inactive`** (deaktivovaný v konfiguraci, bez session): peer v `baseline_scope.selectors.bgp_neighbors` (aktivní v baseline) → `deactivation_outcome(True, False)` | BROKEN | `peer <peer> v baseline bezel, ted je v konfiguraci deaktivovan - migrace nedokoncena` | `deaktivovan` | label `BGP status (<peer>)`, family; baseline_value None, baseline None, subject None |
| per peer v `inactive`: peer v `baseline_scope.selectors.bgp_neighbors_inactive` (`baseline_off=True`) NEBO baseline_scope None / peer v žádném seznamu baseline (`baseline_off=None`) → `deactivation_outcome(True, True|None)` = DEGRADED | DEGRADED | `peer <peer> je v konfiguraci deaktivovan` | `deaktivovan` | baseline_value None (i když baseline_peers peera obsahuje – nečte se) |
| **per peer v `without_session = universe − peers − bgp_neighbors_inactive`**: `peer in configured` | BROKEN | `<peer>: nakonfigurovan, ale session neexistuje` | `bez session` | label `BGP status (<peer>)`, family; baseline_value = `str(baseline_peers[peer]["state"])` (default `unknown`) když peer v baseline_peers, jinak None; baseline dict/subject None |
| per peer v `without_session`: `peer not in configured` (tedy jen v baseline_peers) | BROKEN | `<peer>: v baseline patril k teto sluzbe, v subjektu uz ne` | `neni ve sluzbe` | baseline_value = baseline state (peer je vždy v baseline_peers v této větvi) |
| deaktivovaný peer, pro který PŘESTO přišla session (`peer in bgp_neighbors_inactive and peer in peers`) | – | není vlastní řádek – projde běžnou větví `peers` výše (Established/BROKEN) | | záměr dle komentáře |

Poznámky ke komentářům: 
- Docstring modulu říká „Peer patri ke sluzbe pres bgp_neighbor z inventory“; kód navíc bere peery z `baseline_peers` a měřených `peers` (univerzum) – komentář v kódu to popisuje správně, modulový docstring je zjednodušený.
- Komentář u `_AppliesToCoreLoopback` říká, že Core transit „dostal by prazdne SKIP/FAIL radky“ – v kódu je to řešeno v `applies_to`, souhlasí.
- Komentář „Radek 4 tabulky... nedostane se sem taky“ souhlasí s kódem (`inactive` filtr).
- Řádek pro deaktivovaný peer nenese `baseline_value`, přestože check `baseline_peers[peer]` k dispozici má – není to v rozporu s žádným komentářem, ale ve sloupci ZMENA bude prázdno i když baseline session znal.

---

### bgp_prefix_counts

`BgpPrefixCountsCheck` – title `Pocty BGP prefixu`, label `BGP prefixy`, mode **compare**, default severity **advisory**, service_types `{Internet, IPVPN}` + Core/loopback (stejný mixin jako výše), requires `("bgp",)`, requires_inventory False, order 0. Config option: `options("bgp_prefix_counts")["tolerance_percent"]` – default `-10` (config.py DEFAULTS), čte se přes `float(...)`; KeyError, pokud by settings klíč odstranily (DEFAULTS ho ale vždy dodá). Framework: bez baseline → SKIP `porovnavaci check bez baseline snapshotu`.

Countery `PREFIX_KEYS = ("active", "received", "accepted", "advertised")`; `suppressed` záměrně vynechán.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `subject["bgp"]` prázdný | SKIP | `zadna namerena BGP session, neni co porovnat` | `zadna session` | label default `BGP prefixy`, family None, jeden řádek |
| **per peer** (sorted): `peer not in baseline_peers` | SKIP | `<peer>: peer neni v baseline snapshotu, nelze porovnat` | `bez baseline` | label `BGP prefixy` (bez kvalifikátoru peera!), family=peer_family; ostatní pole None |
| **per peer / per RIB** v `peers[peer]["ribs"]` (sorted): `baseline_ribs.get(rib_name) is None` | SKIP | `<peer>/<rib_name>: RIB neni v baseline, nelze porovnat` | `bez baseline` | label `BGP prefixy (<rib_name>)`, family |
| **per peer / per RIB / per key** (4 řádky na RIB): `percent_change(baseline,subject)` None (baseline == 0) nebo `change >= tolerance` | OK | `<peer>/<rib_name>: <key> <subject>` | `<subject>` (str) | label `<key>-prefix-count`, group `BGP <peer> / <rib_name>`, family; baseline_value=`str(baseline)`; delta=`f"{subject-baseline:+d}"` když se liší, jinak None; baseline=`{key: baseline}`, subject=`{key: subject}`; details `{"rib": rib_name, "tolerance_percent": tolerance}` + `change_percent` (round 1) jen když baseline != 0 |
| per key: `change is not None and change < tolerance` (tj. pokles větší než |tolerance| % – tolerance je záporné číslo) | BROKEN (→ WARN při advisory) | `<peer>/<rib_name>: pokles <key> <baseline> -> <subject>, prah je <tolerance:.0f> %` | `<subject>` | stejné jako výše; message vypíše např. `prah je -10 %` |
| RIB v baseline, ale ne v subjektu | – | no finding emitted (iteruje se jen `subject_ribs`) | – | ztráta celé RIB je tichá |
| peer v baseline, ale ne v subjektu | – | no finding emitted (iteruje se jen `peers`) | – | hlásí až bgp_session_state |
| `subject[key]`/`baseline[key]` chybí | – | KeyError → framework SKIP `check selhal: ...` | `check selhal` | |

Poznámky ke komentářům:
- Modulový docstring: „Presna shoda generuje mnozstvi FAILu“ – při default severity advisory je BROKEN → WARN, nikoli FAIL; docstring je nepřesný v terminologii.
- Docstring `_prefix_finding` „Jeden radek na counter“ souhlasí.
- Komentář u PREFIX_KEYS zmiňuje „u potlacenych rout je pokles zlepseni“ – irelevantní pro kód, konzistentní.
- Nárůst prefixů (change > 0) je vždy OK bez ohledu na velikost – nikde nedokumentováno jako záměr v tomto souboru, ale kód to tak dělá.

---

### bfd_session_state

`BfdSessionStateCheck` – title `Stav BFD session`, label `BFD`, mode **both**, default severity **critical**, service_types None (všechny typy) ale `applies_to` vrací False pro jakýkoli `Core` (transit i loopback), tedy fakticky běží na ne-Core službách a device scope; service_subtypes None; requires `("bfd", "bgp")`; requires_inventory False; order 0. Config options: žádné.

Vstupy: `intent = {str(item["peer"]): item for item in scope.selectors.bfd_peers if item.get("peer")}`, `sessions = subject["bfd"]`, `baseline_sessions = baseline["bfd"]`, `bgp = subject["bgp"]`. Iteruje `sorted(intent ∪ sessions ∪ baseline_sessions)` – **jeden řádek per peer**, vždy label `BFD (<peer>)`, family=peer_family(peer). `was = str(baseline["state"])` když peer v baseline_sessions (může být literál `"None"`, pokud klíč `state` chybí), jinak None. `bgp_state = str((bgp.get(peer) or {}).get("state",""))`.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| žádný peer v intent, sessions ani baseline | – | no finding emitted (prázdný list → Status.worst prázdné = SKIP na úrovni scope, ale žádný řádek) | – | |
| session existuje, `not configured and not is_device` (service scope, peer není v bfd_peers) | DEGRADED | `<peer>: session existuje (<state>), v konfiguraci sluzby neni` | `bez konfigurace` | baseline_value=`was`, subject=session dict, baseline None |
| session existuje, (configured nebo device scope), `state == "Up"` | OK | `<peer>: session Up` | `Up` | baseline_value=`was`, baseline=baseline dict, subject=session |
| session existuje, (configured nebo device scope), `state != "Up"` (`state = str(session.get("state","unknown"))`) | BROKEN | `<peer>: session <state>` | `<state>` | totéž; message neobsahuje očekávání ani slovo o chybě |
| session None, `not configured`, `is_device` (peer jen v baseline) | BROKEN | `<peer>: session byla v baseline (<was>), v subjektu neexistuje` | `session zmizela` | baseline_value=`was`, baseline=baseline dict |
| session None, `not configured`, service scope (peer jen v baseline) | BROKEN | `<peer>: v baseline patril k teto sluzbe, v subjektu uz ne` | `neni ve sluzbe` | baseline_value=`was`, baseline=baseline dict |
| session None, configured, `bgp_state != "Established"` | SKIP | `<peer>: BFD nakonfigurovano, ale BGP je <bgp_state or 'neznamy'>` | `BGP neni Established` | baseline_value=`was`; když peer v `bgp` není nebo state chybí → `... BGP je neznamy` |
| session None, configured, bgp Established | BROKEN | `<peer>: BFD nakonfigurovano, BGP bezi, ale session neexistuje` | `bez session` | baseline_value=`was` |
| intent položka bez klíče `peer` | – | no finding emitted (odfiltrována) | – | |

Poznámky ke komentářům:
- Modulový docstring „Peer, ktery BFD nikdy nemel, radek nedostane (R-1)“ souhlasí (iteruje se jen sjednocení tří BFD zdrojů, ne BGP peery).
- Komentář u NOT_IN_SERVICE: „hlaska se v textovem vypisu neobjevi“ – tvrzení o rendereru, ne o tomto kódu; nelze zde ověřit.
- Docstring nezmiňuje, že check se přeskočí i pro Core loopback – to je jen v komentáři `applies_to`, kde je to vysvětleno; souhlasí.
- Když BFD session existuje ale BGP není Established, žádná vazba na BGP se neuplatní (větev `session is not None` jde první) – docstring o „vazbě na BGP“ platí jen pro chybějící session, což kód i komentář implicitně říkají, ale docstring to zobecňuje.

---

### arp_present

`ArpPresentCheck` – title `Existence ARP zaznamu`, label `ARP`, mode **state**, default severity **advisory**, service_types `{Internet, IPVPN}`, service_subtypes None, requires `("arp",)`, requires_inventory **True** (device scope → framework SKIP `bez inventory`), order 0. Config options: žádné.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `scope.selectors.local_ipv4` prázdné | – | no finding emitted (`_family_not_configured()` = `[]`) | – | ve výstupu není žádná stopa |
| IPv4 nakonfigurováno, `subject["arp"]` po filtru `entry.get("ip")` prázdný | BROKEN (→ WARN při advisory) | `na rozhranich sluzby neni zadny ARP zaznam` | `zadny zaznam` | label `ARP`, family 4, subject `{"count":0,"addresses":[]}` |
| **per entry** s `ip` | OK | `ARP zaznam <ip>` | `<mac or '?'> -> <ip>` nebo `<mac or '?'> -> <ip>  [via <learned_via>]` (dvě mezery) | label `ARP`, family 4, subject `{"ip","mac","learned_via"}`, details `{"address": owning_prefix(ip, prefixes)}` (prefix nebo None) |

Poznámky: modulový docstring „jeden Finding na zaznam“ souhlasí. Komentář u `_family_not_configured` přesně popisuje cenu (žádná stopa). Žádný ARP záznam se nikdy nehodnotí jako špatný (stav `incomplete` apod. se nečte).

---

### nd_present

`NdPresentCheck` – title `Existence ND zaznamu`, label `ND`, mode **state**, default severity **advisory**, service_types `{Internet, IPVPN}`, requires `("nd",)`, requires_inventory **True**, order 0. Config options: žádné. Filtr: `keep_link_local = link_local_is_configured(scope)` (má služba mezi `local_ipv6` link-local adresu); záznamy s link-local IP se zahodí, pokud není.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `scope.selectors.local_ipv6` prázdné | – | no finding emitted (`[]`) | – | |
| IPv6 nakonfigurováno, po filtru (má `ip`, a není link-local nebo link-local povoleno) žádný záznam | BROKEN (→ WARN) | `na rozhranich sluzby neni zadny pouzitelny ND zaznam` | `zadny zaznam` | label `ND`, family 6, subject `{"count":0,"addresses":[]}` |
| **per entry** po filtru | OK | `ND zaznam <ip>` | `<mac or '?'> -> <ip>` (+ `  [via <learned_via>]`) | label `ND`, family 6, subject `{"ip","mac","state","learned_via"}`, details `{"address": owning_prefix(...)}` |

Poznámky: `state` ND záznamu (např. `stale`, `incomplete`) se ukládá do subject, ale nehodnotí – vždy OK. Docstringy to netvrdí jinak.

---

### ping_reachability

`PingReachabilityCheck` – title `Dosazitelnost CPE pingem`, label `Ping`, mode **state**, default severity **advisory**, service_types `{Internet, IPVPN}`, requires `("ping",)`, requires_inventory **True**, order 0. Config options: žádné (prahy `IPV4_FALLBACK_MIN_PREFIX = 30`, `IPV6_FALLBACK_MIN_PREFIX = 126` jsou konstanty z probes/ping.py, ne z configu). Vstup `probes = subject["ping"]`, `subject["ping_skipped"]`.

`oversized = _oversized_subnets_without_targets(scope, probes)`: pro každou rodinu a každý prefix v `local_ipv4`/`local_ipv6`, jehož `network.prefixlen < threshold` a do kterého nepadá žádný target probe dané rodiny (dedup podle `str(network)`).

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `not probes and subject.get("ping_skipped")` | SKIP | `ping neproveden - mimo profil` | `mimo profil` | label `Ping`, family None; jeden řádek, ostatní větve se nevyhodnotí |
| `not probes and not oversized` (ping_skipped falsy) | SKIP | `pro tento scope nejsou ve snapshotu zadne cile pingu` | `bez cile` | label `Ping`, family None |
| **per oversized subnet** (network, family, threshold) – i když probes existují pro jiné subnety | SKIP | `<network>: zadny cil - subnet vetsi nez /<threshold>, fallback by cil jen hadal` | `<network>  bez cile (subnet > /<threshold>)` (dvě mezery) | label `Ping`, family 4/6 |
| **per probe** s family 4/6, `received > 0` (`received=int(probe.get("received",0))`) | OK | `<target>: odpovedelo <received> z <sent>` | `<received>/<sent>  <target>` nebo `<received>/<sent>  <rtt_avg_ms> ms  <target>` | label `Ping`, family; subject `{"target","sent","received"}`; details `{"resolved_from": probe["resolved_from"], "address": owning_prefix(target, prefixes rodiny)}` |
| per probe s family 4/6, `received == 0` | BROKEN (→ WARN při advisory) | `<target>: neodpovedel (<sent> paketu)` | `<received>/<sent>  <target> neodpovedel` (příp. s `  <rtt> ms` uprostřed, pokud by rtt nebylo None) | subject `{"target","sent","received":0}`, details stejné |
| probes s `family not in (4,6)` (souhrnně jeden řádek) | SKIP | `probe bez rodiny nelze vyhodnotit: <target1>, <target2>` | `bez rodiny` | label `Ping`, family None |
| probes existují, ale žádný oversized a všechny mají family 4/6 | – | jen per-probe řádky | | |

Poznámky ke komentářům:
- Modulový docstring „Kazdy check vraci jeden Finding ... na cil (ping)“ – ping navíc vrací souhrnné SKIP řádky (mimo profil, bez cíle, oversized subnet, bez rodiny); docstring je neúplný.
- Komentář u `ping_skipped` říká, že se hlásí „proc“ – message obsahuje jen `mimo profil`, žádný název profilu.
- Docstring `_oversized_subnets_without_targets` „Poradi drzi poradi selektoru“ souhlasí (nejdřív všechny IPv4, pak IPv6).
- `sent == 0 and received == 0` dá message `<target>: neodpovedel (0 paketu)` – žádná speciální větev pro nevyslaný probe.
- `_family_not_configured` (bez IPv4/IPv6 prefixů) se v ping checku nepoužívá – ping bez prefixů a bez probes skončí v `bez cile`.

## 3. EVPN a routy (evpn.py, routes.py)

### Modul `checks/evpn.py`

#### evpn_vpws_status
title `Stav EVPN-VPWS`; label `EVPN VPWS status`; mode `both`; default severity `critical`; service_types `{"E-Line"}`; service_subtypes None; requires `("evpn_vpws",)`; requires_inventory False; order 0; layer1 False; config options: žádné (`ctx.options` se nečte).

Struktura: iteruje `sorted(instances)` (klíč `subject["evpn_vpws"]`), v každé instanci každé `interfaces[idx]`; baseline rozhraní je párováno **pozičně** (`baseline_interfaces[idx]`), baseline instance podle jména. `qualify = len(interfaces) > 1` → label dostane sufix ` ({iface_name})`. Pro každé rozhraní vzniknou řádky: 1× local interface status, pak blok `_sid_findings` pro `local` a pro `remote`. Uvnitř `_sid_findings`: vždy 1× INFO řádek s hodnotou SID, pak buď "bez peerů" větev, nebo per-peer 2 řádky + až 3 INFO řádky.

`<side>` je doslova `local` nebo `remote`. `<peers>` = `iface[f"{side}_sid"]["peers"] or []`. `<bp>` = baseline peer nalezený podle `ipaddr` v baseline SID (`_find_baseline_peer`), může být None.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `subject["evpn_vpws"]` prázdné/chybí (celý check) | SKIP | `pro tento scope nejsou data evpn-vpws` | `bez dat` | label None → framework doplní `EVPN VPWS status`; baseline_value None |
| per rozhraní: `status.split("/")[0].strip() == "Up"` | OK | `<instance>: stav rozhrani <status>` | `<status>` (raw, např. `Up/Forwarding`) | label `EVPN VPWS local interface status`; baseline_value = `str(baseline_iface["status"])` nebo `unknown` když klíč chybí; None když baseline rozhraní na pozici není; subject `{"interface", "status"}` |
| per rozhraní: status není Up (včetně `unknown` když klíč `status` chybí) | BROKEN | `<instance>: stav rozhrani <status>, ocekavano Up` | `<status>` | dtto |
| per rozhraní × side: vždy (hlavička SID) | INFO | `<instance>: <side> SID <value>` kde `<value>` je SID číslo nebo `?` když None | `SID <value>` / `SID ?` | label `EVPN VPWS SID <side> value`; baseline_value `SID <bval>`/`SID ?` když baseline rozhraní má klíč `<side>_sid` (i s value None), jinak None. Pozn.: `baseline_sid` je `baseline_iface.get(...)`, tj. None i když klíč chybí, ale když je klíč přítomen s hodnotou None, `baseline_sid.get` by vyhodilo AttributeError → celý check `check selhal` |
| side=`remote`, peers prázdné | BROKEN | `<instance>: remote peer chybi` | `Neznamy peer` | label `EVPN VPWS SID remote PE`; baseline_value = `str(first_baseline_peer["ipaddr"] or "?")` z prvního baseline peeru, None když baseline peery nemá |
| side=`remote`, peers prázdné (druhý řádek) | BROKEN | `<instance>: remote SID nema zadny Resolved zaznam` | `Unresolved / Chybi` | label `EVPN VPWS SID remote status`; baseline_value = `str(first_baseline_peer["status"] or "Unresolved / Chybi")` nebo None |
| side=`local`, peers prázdné | INFO | `<instance>: local strana bez multi-homing peeru` | `<mode> (multi-homing peer ve vypisu nenalezen)` kde `<mode>` = `iface["mode"] or "unknown"` | label `EVPN VPWS SID local mode`; baseline_value: když baseline rozhraní existuje, má `local_sid` a **také** bez peerů → při shodě módů celý stejný string jako value, při rozdílu jen `<baseline_mode>`; jinak None |
| per peer (peers neprázdné): `peer["status"].strip().lower() == "resolved"` | OK | `<instance>: <side> peer <ipaddr>` | `str(ipaddr or "?")` | label `EVPN VPWS SID local peer PE` (side local) / `EVPN VPWS SID remote PE` (side remote); baseline_value `str(bp["ipaddr"] or "?")` nebo None; subject = kopie peer dictu |
| per peer: status není `resolved` (včetně chybějícího) | BROKEN | `<instance>: <side> peer <ipaddr>` | dtto | dtto - message tohoto řádku **nenese důvod**, proč je BROKEN |
| per peer, řádek status, resolved | OK | `<instance>: <side> peer <ipaddr> status <status or 'chybi'>` | `str(status or "Unresolved / Chybi")` | label `EVPN VPWS SID <side> status`; baseline_value `str(bp["status"] or "Unresolved / Chybi")` nebo None |
| per peer, řádek status, ne resolved | BROKEN | dtto (např. `... status Unresolved`, nebo `... status chybi`) | dtto | dtto |
| per peer, pro každý klíč z (`mode`,`esi`,`role`) s truthy hodnotou | INFO | `<instance>: <side> peer <key> <hodnota>` - `<key>` je klíč dictu, tj. doslova `mode` / `esi` / `role` (malé písmeno) | `str(peer[key])` | label `EVPN VPWS SID <side> mode` / `... ESI` / `... role` (label používá `ESI` velkými, message `esi` malými); baseline_value `str(bp[key])` když baseline peer má truthy klíč, jinak None |
| per peer, klíč chybí/falsy | *no finding emitted* | | | |

Docstring vs. kód:
- Docstring třídy říká "baseline_value se dopocitava pozicnim parovanim rozhrani a peeru podle ipaddr" - správně; ale u řádku `EVPN VPWS SID remote PE` při chybějícím peeru se baseline hodnota bere z **prvního** baseline peeru bez ohledu na ipaddr (komentář to přiznává, docstring ne).
- Komentář u INFO řádků peer mode/ESI/role nic netvrdí, ale label a message se liší v case (`ESI` vs `esi`).

#### evpn_esi_status
title `Stav EVPN ESI`; label `EVPN ESI status`; mode `both`; default severity `critical`; service_types `{"E-LAN"}`; service_subtypes None; requires `("evpn_esi",)`; requires_inventory False; order 0; config options: žádné.

Struktura: iteruje `sorted(entries)` z `subject["evpn_esi"]` (klíč = ESI string), baseline záznam podle stejného ESI. Per ESI blok: 1 INFO hlavička, volitelně ESI Status, vždy Local interface status, vždy DF. Labely **nejsou** kvalifikovány, i když je ESI víc.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `subject["evpn_esi"]` prázdné/chybí | SKIP | `pro tento scope nejsou data evpn-esi` | `bez dat` | label None → `EVPN ESI status` |
| per ESI: hlavička vždy | INFO | `ESI <esi>` | `<esi>` | label `ESI`; baseline_value `<esi>` když baseline záznam pro ESI existuje (truthy dict), jinak None |
| per ESI: `data["resolved_status"]` truthy a `.lower().startswith("resolved")` | OK | `<esi>: <resolved_status>` | `<resolved_status>` | label `ESI Status`; baseline_value `baseline["resolved_status"]` (může být None) |
| per ESI: `resolved_status` truthy, nezačíná na "resolved" | BROKEN | `<esi>: <resolved_status>` | `<resolved_status>` | dtto |
| per ESI: `resolved_status` chybí/prázdný | *no finding emitted* (řádek ESI Status vynechán) | | | |
| per ESI: `_is_up(str(data.get("status","unknown")))` | OK | `<esi>: stav rozhrani <status>` | `<interface or '?'> <status or 'unknown'>` (`_esi_local_value`) | label `ESI Local interface status`; baseline_value `_esi_local_value(baseline)` když `baseline["status"]` truthy, jinak None; subject `{"status", "interface"}` |
| per ESI: status není Up | BROKEN | `<esi>: stav rozhrani <status>, ocekavano Up` | dtto | dtto |
| per ESI: `df_role` je None | INFO | `<esi>: DF bez zaznamu` | `-` | label `ESI DF`; baseline_value `baseline["df_role"]` (může být None); subject `{"df_role": None}` |
| per ESI: `df_role` obsahuje `"not elected"` (case-insensitive) | BROKEN | `<esi>: DF <df_role>` (např. `DF DF not elected yet`) | `<df_role>` | dtto |
| per ESI: `df_role` jiný (např. IP adresa DF) | OK | `<esi>: DF <df_role>` | `<df_role>` | dtto |
| per ESI: `df_role == ""` (prázdný string, ne None) | OK | `<esi>: DF bez zaznamu` | `-` | prázdný string není None → padne do else větve OK; message/value použijí `or` fallback. Hraniční případ - "bez zaznamu" s verdiktem OK |

Docstring vs. kód:
- Docstring: "u nezvoleneho DF vypsal 'DF DF not elected yet'" jako problém původního tvaru - nový kód při `df_role = "DF not elected yet"` stále vyrobí message `<esi>: DF DF not elected yet` (value je bez duplikace). Message zdvojení tedy trvá.
- Docstring: "ESI Status ... na rovnost s baseline se neporovnava" - kód baseline_value nastavuje, porovnání nedělá; sedí.

#### evpn_instance_status
title `Stav EVPN instance`; label `EVPN instance`; mode `both`; default severity `critical`; service_types `{"E-LAN"}`; service_subtypes None; requires `("evpn_instance",)`; requires_inventory False; order 0; config options: žádné. Čte navíc `ctx.scope.selectors.interfaces`, `ctx.scope.selectors.vlans`, `ctx.link` (role `l2` → `peer_interface` jako IRB).

Struktura: iteruje `sorted(instances)` z `subject["evpn_instance"]`; `qualify = len(instances) > 1` → labely se sufixem ` (<instance>)`. `units.active = bool(scope.selectors.interfaces)` - zapíná relevance filtr.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `subject["evpn_instance"]` prázdné/chybí | SKIP | `pro tento scope nejsou data evpn-instance` | `bez dat` | label None → `EVPN instance` |
| per instance: `neighbors.total > 0`, a NE (baseline má `neighbors` s `total` a `int(total) < int(baseline_total)`) | OK | `<instance>: EVPN neighbors: <total>` | `<total>` (str; `0` když None) | label `EVPN neighbors`; baseline_value `str(baseline["neighbors"]["total"])` když baseline `neighbors` truthy, jinak None. `_count_finding` s `ok=True, expectation="> 0", warn_below_baseline=True` |
| per instance: `total > 0` a `total < baseline_total` | DEGRADED | `<instance>: EVPN neighbors: <total>, baseline <baseline_total>` | `<total>` | dtto |
| per instance: `total` je 0/None | BROKEN | `<instance>: EVPN neighbors: 0, ocekavano > 0` | `0` | dtto; warn_below_baseline se neaplikuje (ok=False) |
| per instance × per adresa v `neighbors["addresses"]` | INFO | `<instance>: neighbor <address>` | `<address>` | label `EVPN neighbor`; baseline_value None |
| `_esi_findings`: `units.active` (scope má interface selektory) | *no finding emitted* (celý ESI blok vynechán) | | | |
| `_esi_findings`: units neaktivní, subject `esis` i baseline `esis` prázdné | SKIP | `<instance>: zadne ESI ve vypisu` | `bez dat` | label `ESI status` |
| `_esi_findings`: units neaktivní, per ESI z union(subject, baseline): v subjektu chybí | BROKEN | `<instance>: ESI <esi> v baseline bylo, ted chybi` | `chybi` | label `ESI <esi>`; baseline_value = baseline status text |
| dtto, subject status `.lower().startswith("resolved")` | OK | `<instance>: ESI <esi> <status>` | `<status>` | label `ESI <esi>`; baseline_value baseline status (může None) |
| dtto, status truthy a nezačíná na resolved | BROKEN | `<instance>: ESI <esi> <status>` | `<status>` | dtto |
| dtto, status je `""` (prázdný string, ne None) | BROKEN | `<instance>: ESI <esi> bez statusu` | `bez statusu` | `"".startswith("resolved")` False → BROKEN |
| per instance × per `local_interfaces.entries`: `units.active and name not in units.interfaces` | *no finding emitted* | | | |
| dtto, entry projde filtrem, `_is_up(status)` | OK | `<instance>: interface <name> <status>` | `<name> <status>` | label `EVPN interface`; baseline_value None (nikdy) |
| dtto, není Up | BROKEN | `<instance>: interface <name> <status>, ocekavano Up` | `<name> <status>` | dtto |
| per instance: `units.active and not qualify`, per `unit in sorted(units.interfaces - local_names)` | BROKEN | `<instance>: unit <unit> chybi v instanci` | `<unit> chybi v instanci` | label `EVPN interface`; baseline_value None |
| dtto, ale `qualify=True` (víc instancí) nebo units neaktivní | *no finding emitted* | | | |
| per instance × per `irb_interfaces.entries`: `units.active and name != units.irb` | *no finding emitted* | | | (když `units.irb` je None a units aktivní, vynechají se všechny IRB) |
| dtto, projde, Up | OK | `<instance>: IRB <name> <status>` nebo `<instance>: IRB <name> <status> (<l3_context>)` | `<name> <status>` / `<name> <status> (<l3_context>)` | label `IRB interface`; baseline_value None |
| dtto, není Up | BROKEN | `<instance>: IRB <value>, ocekavano Up` | dtto | dtto |
| per instance: `units.active and not qualify and units.irb and units.irb not in irb_names` | BROKEN | `<instance>: IRB unit <irb> chybi v instanci` | `<irb> chybi v instanci` | label `IRB interface`; baseline_value None |

Docstring vs. kód:
- Docstring `_count_finding`: "Jedinym volajicim je EVPN neighbors" - platí (jediný call site v repu je zde).
- Docstring třídy uvádí, že `qualify=True` u service scopu "dnes nenastava" - jde o tvrzení o builderu, ne o tomto kódu; kód pojistku má.
- Komentář v `_esi_findings`: "Bez selektoru zustava plny instancni kontext vcetne SKIP a 'chybi' radku" - sedí. Nicméně **řádky `EVPN interface`, `IRB interface`, `EVPN neighbor` a `ESI <esi>` nikdy nenesou `baseline_value`**, ačkoli check je `Mode.BOTH` a baseline data pro instanci má - sloupec ZMENA u nich bude vždy "bez baseline"/prázdný. Docstring třídy to nezmiňuje.
- `_count_finding` parametr `label` je zároveň použit jen jako label; message je vlastní `f"{instance}: EVPN neighbors"` - OK.

#### evpn_mac_count
title `Pocet MAC adres`; label `EVPN MAC count`; mode `both`; default severity `advisory`; service_types `{"E-LAN"}`; service_subtypes None; requires `("evpn_mac",)`; requires_inventory False; order 0; config options: `tolerance_percent` (čte `float(ctx.options("evpn_mac_count")["tolerance_percent"])`, default v `config.py` = `-60`; KeyError při chybějící opci → `check selhal`). Čte také `scope.selectors.interfaces`, `scope.selectors.vlans`, `ctx.link`.

Struktura: iteruje `sorted(instances)` z `subject["evpn_mac"]`; `many = len(instances) > 1` → label sufix ` (<instance>)`. Per instance: per VLAN z union(subject vlans, baseline vlans) seřazené číselně; pak per interface **jen ze subjektu**. `<T>` = `tolerance:.0f` (např. `-60`). `<D>` = `domain` záznamu.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `subject["evpn_mac"]` prázdné/chybí | SKIP | `pro tento scope nejsou data o MAC adresach` | `bez dat` | label None → `EVPN MAC count` |
| per VLAN: `units.active and units.vlans and vlan not in units.vlans` | *no finding emitted* | | | |
| per VLAN: v subjektu chybí, v baseline je (`_mac_compare_finding(baseline_count, 0, tol)`): `percent_change(b, 0)` = -100 < tol (když b > 0) | BROKEN | `<L>: pocet MAC klesl <b> -> 0, prah je <T> %` | `0` | `<L>` = row_label = `<D> MAC count` nebo `MAC count` (+ kvalifikace); baseline_value `<b>`; delta `-<b>`; details `{"tolerance_percent": tol, "change_percent": -100.0}`; baseline `{"mac_count": b}`, subject `{"mac_count": 0}` |
| per VLAN: v subjektu chybí, baseline count == 0 | BROKEN | `<L>: 0 naucenych MAC adres (baseline 0)` | `0` | change None → druhá větev `subject == 0`; baseline_value `0`; delta None (subject == baseline); details `{"tolerance_percent": tol}` |
| per VLAN: v subjektu je, v baseline chybí, count == 0 (`_mac_state_finding`) | BROKEN | `<L>: 0 naucenych MAC adres` | `0` | baseline_value None; subject `{"mac_count": 0}`; details {} |
| per VLAN: v subjektu je, v baseline chybí, count > 0 | OK | `<L>: <count> naucenych MAC adres` | `<count>` | baseline_value None |
| per VLAN: oba záznamy, `b > 0` a `(s-b)/b*100 < tol` | BROKEN | `<L>: pocet MAC klesl <b> -> <s>, prah je <T> %` | `<s>` | baseline_value `<b>`; delta `<s-b:+d>` nebo None při rovnosti; details `{"tolerance_percent", "change_percent": round(change,1)}` |
| per VLAN: oba záznamy, change None (b == 0) nebo change >= tol, a `s == 0` | BROKEN | `<L>: 0 naucenych MAC adres (baseline <b>)` | `0` | dtto (change_percent přítomen jen když b > 0). Pozn.: s=0, b>0 vždy dá change=-100 < tol pro tol > -100, tudíž sem reálně padne jen b == 0, s == 0 |
| per VLAN: oba záznamy, jinak | OK | `<L>: <s> MAC adres, v toleranci <T> %` | `<s>` | baseline_value `<b>`; delta `<s-b:+d>` nebo None; details dtto. Pozn.: nárůst i pokles nad prahem je OK - zpráva "v toleranci" se vypíše i při b=0, s=5 (change None) |
| per interface (jen subject): `units.active and key not in units.interfaces` | *no finding emitted* | | | |
| per interface: baseline interface záznam chybí | dtto jako `_mac_state_finding` výše (BROKEN při 0 / OK) | `<L>: 0 naucenych MAC adres` / `<L>: <count> naucenych MAC adres` | `0` / `<count>` | `<L>` = `<D> Interface <name> MAC count` nebo `Interface <name> MAC count` (+ kvalifikace); baseline_value None |
| per interface: baseline záznam je | dtto jako `_mac_compare_finding` (3 větve výše) | dtto | dtto | dtto |
| interface v baseline, ale ne v subjektu | *no finding emitted* | | | komentář to přiznává ("Per-interface se iteruje jen subject") |

Docstring vs. kód:
- Komentář u VLAN iterace: "Union se baseline: ... zmizeni by tise zahodila" - sedí; ale když je VLAN v baseline a ne v subjektu a `baseline["count"] == 0`, message zní `0 naucenych MAC adres (baseline 0)` - řádek nerozliší "VLAN zmizela" od "VLAN je, ale prázdná".
- Komentář v `_mac_compare_finding`: "Stejna trojice poli jako u BGP counteru (AR-4)" - `delta` je None při rovnosti (ne `+0`).
- `domain` se bere z `(subject_entry or baseline_entry).get("domain")` - když subject záznam domain nemá, baseline domain se nepoužije (ne dokumentováno, drobnost).

---

### Modul `checks/routes.py`

#### static_route_status
title `Stav statickych rout`; label `Staticka routa`; mode `both`; default severity `critical`; service_types None (všechny); service_subtypes None; requires `("routes",)`; requires_inventory False; order 0; config options: žádné. Čte `ctx.scope.selectors.static_routes` (filtr `route_type == "static"`, default `static`), `ctx.baseline_scope.selectors.static_routes` (baseline záměr), `ctx.subject["routes"]` a `ctx.baseline["routes"]` filtrované `_flatten(..., "static")` (klíč `protocol`, default `static`), `ctx.scope.is_device`.

Struktura: **jeden řádek per identita `(rib, prefix)`** z `sorted(configured | subject | baseline)`. `group="Staticke routy"`, `label="<rib> <prefix>"`, `family` z prefixu (4/6/None). `<was>` = `_next_hop_text(baseline)`: None když baseline záznam není, `-` když nemá next_hopy, jinak `", ".join(sorted(next_hop))`. `<now>` analogicky pro subject. `hops` = `next_hops` ze selectoru (záměr), `inactive_hops` = hopy s `active is False`-ish (`not h.get("active", True)`).

Pořadí větví v `_finding`:
1. všechny hopy deaktivované (hops neprázdné, žádný aktivní, subject None, routa jako celek není deaktivovaná)
2. ZMENA (subject aktivní v tabulce, `was` není None, `was != now`) - s anotací deaktivovaných hopů
3. `_presence_finding` (sdílené větve); pokud subject aktivní v tabulce → navíc anotace deaktivovaných hopů (`_annotate_inactive_hops`), která může přepsat outcome a připojit text k message.

Anotace `_annotate_inactive_hops` (aplikuje se jen na větve ZMENA a OK-aktivní; když `inactive_hops` prázdné, nic nemění): pro každý neaktivní hop najde baseline hop podle `(to, interface)`; `hop_baseline_off` = None (nenalezen) / True / False; `deactivation_outcome(True, hop_baseline_off)` → BROKEN když baseline hop byl aktivní, jinak DEGRADED; výsledný outcome = nejhorší z původního a všech hop outcome (rank OK<DEGRADED<BROKEN). Message: `+ "; deaktivovany next-hop: " + ", ".join(<to> via <interface> | <to>)`, pak `+ " - migrace nedokoncena"` když worst BROKEN **nebo** kterýkoli hop `hop_baseline_off is False`; jinak `+ " (stejne jako v baseline)"` když kterýkoli hop `hop_baseline_off is True`; jinak nic (hop bez baseline protějšku).

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| hops neprázdné, žádný aktivní, subject None, routa není celá deaktivovaná; baseline_hops neprázdné a všechny vypnuté (`baseline_all_hops_off=True`) | DEGRADED | `<rib> <prefix>: vsechny next-hopy jsou deaktivovane` | `deaktivovana` | baseline_value `<was>` (next-hop text baseline faktů, typicky None - routa nebyla v tabulce); baseline=baseline fakta; subject není nastaven |
| dtto, baseline_hops neprázdné a alespoň jeden aktivní (`False`) | BROKEN | `<rib> <prefix>: vsechny next-hopy jsou deaktivovane - migrace nedokoncena` | `deaktivovana` | dtto |
| dtto, baseline_hops prázdné (`None`) | DEGRADED | `<rib> <prefix>: vsechny next-hopy jsou deaktivovane` | `deaktivovana` | dtto |
| ZMENA: subject má `active: True`, `was` není None, `was != now`, bez neaktivních hopů | DEGRADED | `<rib> <prefix>: next-hop se zmenil <was> -> <now>` | `<now>` | baseline_value `<was>`; baseline, subject fakta |
| ZMENA + neaktivní hopy | DEGRADED nebo BROKEN (nejhorší) | `<rib> <prefix>: next-hop se zmenil <was> -> <now>; deaktivovany next-hop: <hop>, <hop>` + (` - migrace nedokoncena` \| ` (stejne jako v baseline)` \| nic) | `<now>` | dtto |
| `_presence_finding`: `deactivated and subject is None`, `baseline_deactivated is False` (v baseline záměru routa byla a aktivní) | BROKEN | `<rib> <prefix>: v baseline bezela, ted je v konfiguraci deaktivovana - migrace nedokoncena` | `deaktivovana` | baseline_value `<was>`; baseline fakta; subject None. Anotace hopů se **neaplikuje** (subject None) |
| dtto, `baseline_deactivated` True nebo None (baseline scope není / routa v baseline záměru nebyla) | DEGRADED | `<rib> <prefix>: routa je v konfiguraci deaktivovana` | `deaktivovana` | dtto |
| `deactivated and subject is not None` (deaktivovaná routa přesto v tabulce) | pokračuje běžnými větvemi níže (SKIP/neaktivní/OK) - žádná speciální zpráva | | | komentář: "chova se jako dosud" |
| `subject is None`, `configured and not is_device` | BROKEN | `<rib> <prefix>: nakonfigurovana, ale neni v routovaci tabulce` | `neni v tabulce` | baseline_value `<was>`; baseline fakta |
| `subject is None`, (`not configured` nebo `is_device`) - tj. v device scope nebo routa jen v baseline měření | BROKEN | `<rib> <prefix>: v baseline byla, v subjektu neni` | `chybi` | dtto. Pozn.: v device scope s `configured=True` (selectory device scopu, pokud nějaké jsou) se vypíše "v baseline byla" i když v baseline nebyla - viz níže |
| subject záznam bez klíče `active` | SKIP | `<rib> <prefix>: mereni neobsahuje aktivitu routy` | `bez dat` | baseline_value None (nepředává se!); baseline, subject fakta |
| `subject["active"]` False, `baseline["active"]` False | OK | `<rib> <prefix>: neni aktivni, stejne jako v baseline` | `neni aktivni` | baseline_value `neni aktivni` (ne `<was>`) |
| `subject["active"]` False, `baseline["active"]` True | BROKEN | `<rib> <prefix>: v baseline forwardovala, ted neni aktivni` | `neni aktivni` | baseline_value `<was>` (next-hop text) |
| `subject["active"]` False, baseline záznam je, ale bez klíče `active` | DEGRADED | `<rib> <prefix>: je v tabulce, ale neni aktivni; baseline aktivitu neuvadi` | `neni aktivni` | baseline_value `<was>` |
| `subject["active"]` False, baseline záznam není | DEGRADED | `<rib> <prefix>: je v tabulce, ale neni aktivni` | `neni aktivni` | baseline_value None |
| subject aktivní, (`was` None nebo `was == now`), bez neaktivních hopů | OK | `<rib> <prefix>: <now>` (např. `inet.0 10.0.0.0/24: 192.0.2.1`; `-` když bez next-hopů) | `<now>` (value_ok = `now or ""`) | baseline_value `<was>` |
| subject aktivní, OK + neaktivní hopy | DEGRADED nebo BROKEN (nejhorší z OK/hop outcomes; nikdy zůstane OK, protože `deactivation_outcome(True, ·)` je min. DEGRADED) | `<rib> <prefix>: <now>; deaktivovany next-hop: <hop>[, <hop>]` + (` - migrace nedokoncena` když BROKEN nebo některý hop v baseline aktivní \| ` (stejne jako v baseline)` když některý hop v baseline vypnutý \| nic) | `<now>` | baseline_value `<was>`; `replace(presence, outcome, message)` - ostatní pole zachována |

Docstring vs. kód:
- Docstring modulu: "Check cte jen zaznamy s `protocol == "static"` (fakta) / `route_type == "static"` (zamer)" - sedí.
- Docstring `_presence_finding`: "Vraci vzdy Finding (ne None)" - sedí. "`label_prefix` ... se do popisku radku nepromita" - sedí (parametr nepoužit).
- Komentář v `_presence_finding` u `subject is None`: "Sem se v device scope dostane jen routa, ktera byla v baseline a v subjektu neni." - Nepřesné: v device scope sem přijde i identita z `configured` (pokud device scope má static_routes selektory) nebo pouze z baseline; message `v baseline byla, v subjektu neni` se vypíše i pro `configured and is_device` bez baseline záznamu. Zároveň v service scopu identita pouze z baseline měření (nekonfigurovaná) dostane tutéž zprávu - to sedí.
- Komentář u `deactivated and subject is None`: "Radek 4 tabulky (aktivni ted, vypnuta v baselinu) se sem nedostane" - sedí, `deactivation_outcome` je volán vždy se `True`.
- Docstring `_annotate_inactive_hops`: "vyhrava nejhorsi (BROKEN > DEGRADED > puvodni vysledek)" - sedí. Říká "jedna souhrnna pripominka" - kód připojí buď ` - migrace nedokoncena`, nebo ` (stejne jako v baseline)`, nebo nic; když hopy mají smíšený baseline stav (jeden nově vypnutý, jeden vypnutý už dřív), vyhraje `- migrace nedokoncena` a informace o "stejne jako v baseline" se ztratí - záměr, ale docstring to nepopisuje.
- Řádek SKIP (`mereni neobsahuje aktivitu routy`) nepředává `baseline_value`, na rozdíl od ostatních větví - komentář to nezmiňuje.
- Docstring modulu říká identita je (RIB, prefix) a "zmena next-hopu se cte jako zmenena routa - jeden radek se sloupcem ZMENA" - sedí (větev ZMENA, DEGRADED).

#### aggregate_route_status
title `Stav agregatnich rout`; label `Agregatni routa`; mode `both`; default severity `critical`; service_types None; service_subtypes None; requires `("routes",)`; requires_inventory False; order 0; config options: žádné. Čte selectory `static_routes` s `route_type == "aggregate"` (subject i baseline scope), `_flatten(..., "aggregate")` fakta subject i baseline, `scope.is_device`.

Struktura: jeden řádek per `(rib, prefix)` z `sorted(configured | subject | baseline)`, vše přes `_presence_finding` s `group="Agregatni routy"`, `value_ok="v tabulce"`, `baseline_value=_presence_text(baseline)` = None (bez baseline záznamu) / `v tabulce` (baseline `active` truthy nebo chybí) / `neni aktivni`. Žádná ZMENA větev, žádná anotace next-hopů. `<bp>` níže = tato presence hodnota.

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| `deactivated and subject is None`, `baseline_deactivated is False` | BROKEN | `<rib> <prefix>: v baseline bezela, ted je v konfiguraci deaktivovana - migrace nedokoncena` | `deaktivovana` | baseline_value `<bp>`; baseline fakta |
| dtto, `baseline_deactivated` True/None | DEGRADED | `<rib> <prefix>: routa je v konfiguraci deaktivovana` | `deaktivovana` | dtto |
| `subject is None`, `configured and not is_device` | BROKEN | `<rib> <prefix>: nakonfigurovana, ale neni v routovaci tabulce` | `neni v tabulce` | baseline_value `<bp>` |
| `subject is None`, jinak | BROKEN | `<rib> <prefix>: v baseline byla, v subjektu neni` | `chybi` | dtto (stejná výhrada k device scope jako u statik) |
| subject bez klíče `active` | SKIP | `<rib> <prefix>: mereni neobsahuje aktivitu routy` | `bez dat` | baseline_value None |
| subject `active` False, baseline `active` False | OK | `<rib> <prefix>: neni aktivni, stejne jako v baseline` | `neni aktivni` | baseline_value `neni aktivni` |
| subject `active` False, baseline `active` True | BROKEN | `<rib> <prefix>: v baseline forwardovala, ted neni aktivni` | `neni aktivni` | baseline_value `<bp>` = `v tabulce` |
| subject `active` False, baseline záznam bez `active` | DEGRADED | `<rib> <prefix>: je v tabulce, ale neni aktivni; baseline aktivitu neuvadi` | `neni aktivni` | baseline_value `<bp>` = `v tabulce` (protože `_presence_text` defaultuje `active` na True) |
| subject `active` False, baseline záznam není | DEGRADED | `<rib> <prefix>: je v tabulce, ale neni aktivni` | `neni aktivni` | baseline_value None |
| subject `active` True | OK | `<rib> <prefix>: v tabulce` | `v tabulce` | baseline_value `<bp>` (`v tabulce` / `neni aktivni` / None) |

Docstring vs. kód:
- Docstring třídy: "Sdili se StaticRouteStatusCheck sjednoceni tri zdroju i vetve chybi/deaktivovana" - sedí.
- Větev "baseline aktivitu neuvadi" (DEGRADED) má `baseline_value = "v tabulce"` (z `_presence_text`, default `active=True`), takže sloupec ZMENA ukáže `bylo v tabulce`, zatímco message tvrdí, že baseline aktivitu neuvádí - drobný rozpor mezi message a baseline_value.
- Docstring `_presence_text`: "None = v baseline zaznam neni (view.py pak resi 'bez baseline' sam)" - sedí.
- Vzhledem k `_flatten(..., "aggregate")` s defaultem `protocol="static"` pro staré snapshoty: agregáty ze snapshotu před schématem 10 se do subject/baseline agregátního checku nedostanou (jsou počítány jako static) - komentář u `_flatten` mluví jen o statikách, což je konzistentní, ale znamená to, že u starého baseline snapshotu dostane každý konfigurovaný agregát v baselinu `baseline_value=None`.

## 4. Core protokoly a multicast (core_protocols.py, multicast.py)

Společné poznámky (framework, `checks/base.py` + `models/result.py`):

- Status z Outcome: OK→PASS, INFO→INFO, SKIP→SKIP, DEGRADED→WARN (vždy), BROKEN→FAIL při severity critical / WARN při advisory. Všechny checky níže kromě `isis_overview` (advisory) mají default CRITICAL, takže BROKEN = FAIL.
- Framework (`run_check`) přidá SKIP řádky mimo check: "check vyzaduje inventory, snapshot ji neobsahuje"/`bez inventory` (device scope + requires_inventory), "porovnavaci check bez baseline snapshotu"/`bez baseline` (jen Mode.COMPARE – žádný check zde ho nemá), "sluzba je v konfiguraci deaktivovana (<reason>)"/`<reason>`, "chybi data z collectoru '<area>': <err>"/`collector selhal`, "check selhal: <error>"/`check selhal`. `label` = `finding.label or check.label`.
- Žádný check v těchto dvou modulech nečte `ctx.options(...)` / `ctx.config` – **žádné tolerance ani config options**. Žádný check nenastavuje `delta`, `baseline` (dict), `family` ani `details`; `subject` nastavuje jen `bfd_transit_state`.
- `MISSING = "chybí v outputu"` (s diakritikou, na rozdíl od ostatních zpráv bez diakritiky).
- `qualified(label, iface)` → `"<label> (<iface>)"`.
- `_scope_transit_interfaces(ctx)` = rozhraní ze **selektorů scopu** (ne z faktů), filtrované `is_transit` (fyzická část jména začíná `ge`/`xe`/`et`/`ae`), seřazená. Rozhraní chybějící v selektorech tedy řádek nedostane vůbec; rozhraní chybějící ve faktech dostane BROKEN "chybí v outputu".
- `format_uptime(s)`: `0s` pro None/0, jinak `<h>h <m>m` / `<m>m <s>s` / `<s>s`.

---

### core_protocols.py

#### isis_adjacency_state
title `Stav IS-IS adjacency` · label `IS-IS adjacency state` · mode BOTH · default severity CRITICAL · service_types {Core} · subtypes {transit} · requires (`isis_adjacency`,) · requires_inventory True · order 0 · config options: žádné.

Emituje **per transit rozhraní** ze selektorů: 1 řádek (chybí) nebo 4 řádky (soused, stav, IPv4, IPv6). `has_baseline` = `ctx.baseline is not None`; `was` = záznam rozhraní v baseline (může být None i když baseline je).

| situation | Outcome | message | value | baseline_value/delta/details notes |
|---|---|---|---|---|
| rozhraní není v `subject["isis_adjacency"]` (`adj is None`) | BROKEN | `<iface>: rozhrani neni v IS-IS adjacency vypisu` | `chybí v outputu` | label `IS-IS adjacency state (<iface>)`; baseline_value = `str(was["state"])` pokud existuje a není None, jinak None. Další řádky se pro to rozhraní neemitují (`continue`). |
| řádek "soused": has_baseline AND was existuje AND `adj["system_name"] != was["system_name"]` | DEGRADED | `<iface>: IS-IS soused <system>, v baseline <was_system>` (obě přes str(), tj. může být doslova `None`) | `str(system)` nebo `chybí v outputu` pokud system je None | label `IS-IS neighbor name (<iface>)`; baseline_value `str(was_system)` nebo None |
| řádek "soused": has_baseline AND was existuje AND system shodný | OK | `<iface>: IS-IS soused <system>` | `str(system)` / `chybí v outputu` | label `IS-IS neighbor name (<iface>)`; baseline_value **není** vyplněno (None) ani při baseline |
| řádek "soused": bez baseline, nebo baseline bez záznamu rozhraní | INFO | `<iface>: IS-IS soused <system>` (system může být doslova `None` v message) | `str(system)` / `chybí v outputu` | label `IS-IS neighbor name (<iface>)`; baseline_value None |
| řádek "stav": `state = str(adj.get("state","unknown")) != "Up"` | BROKEN | `<iface>: adjacency <state>` | `<state>` (syrový, např. `Down`, `Initializing`, `unknown`) | label `IS-IS adjacency state (<iface>)`; baseline_value `str(was["state"])` nebo None – vyplněno i bez porovnání |
| řádek "stav": state == "Up" AND has_baseline AND was_state není None AND was_state != "Up" | DEGRADED | `<iface>: adjacency Up` | `Up` | baseline_value `<was_state>`; komentář: „zlepšení je pořád změna" |
| řádek "stav": state == "Up", jinak | OK | `<iface>: adjacency Up` | `Up` | baseline_value `<was_state>` nebo None |
| řádek IPv4 (`ip_address`) / IPv6 (`ipv6_address`) – po jednom řádku každý: has_baseline AND was existuje AND `value != was_value` | DEGRADED | `<iface>: IS-IS neighbor IPv4 address <value or 'chybi'>` resp. `<iface>: IS-IS neighbor IPv6 address <value or 'chybi'>` | `str(value)` pokud truthy, jinak `chybí v outputu` | label `IS-IS neighbor IPv4 address (<iface>)` / `... IPv6 ...`; baseline_value `str(was_value)` pokud truthy, jinak None. Pozn.: DEGRADED i když nová hodnota chybí (None) a baseline ji měla – chybějící adresa při baseline je WARN, ne FAIL. |
| řádek IPv4/IPv6: ne-změna (bez baseline / bez was / shodné) AND value is not None | OK | `<iface>: IS-IS neighbor IPv4 address <value>` (resp. IPv6) | `str(value)` (pozn.: prázdný string "" by dal message `... chybi` a value `chybí v outputu`, ale Outcome OK – hraniční) | baseline_value `str(was_value)` pokud truthy |
| řádek IPv4/IPv6: ne-změna AND value is None | BROKEN | `<iface>: IS-IS neighbor IPv4 address chybi` (resp. IPv6) | `chybí v outputu` | baseline_value None (bez baseline) nebo `str(was_value)` jen pokud was existuje a hodnota shodně None → None. Pozn.: chybějící IPv6 adresa na IPv4-only lince je FAIL. |

Docstring/komentář vs. kód: module docstring říká „Absence rozhraní ve výpisu je měření, ne díra: FAIL 'chybí v outputu'" – platí. Komentář o `str(None)` platí. Pozn.: řádek "soused" při shodě a baseline nenese `baseline_value`, zatímco řádek "stav" ho nese vždy – nekonzistence, ne rozpor s docstringem.

#### isis_interface_info
title `IS-IS konfigurace rozhrani` · label `IS-IS interface` · mode STATE · default severity CRITICAL · service_types {Core} · subtypes {transit, loopback} · requires (`isis_interface`,) · requires_inventory True · order 0 · config options: žádné. Nepracuje s baseline.

Role-aware: `loopback = ctx.scope.service_subtype == "loopback"`. Pro loopback bere **všechna** rozhraní ze selektorů (sorted, bez is_transit filtru); pro transit jen transit rozhraní. **Per rozhraní**: 1 řádek (chybí) nebo 2–3 řádky (level 2, [level 1], passive).

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| rozhraní není v `subject["isis_interface"]` | BROKEN | `<iface>: rozhrani neni v IS-IS interface vypisu` | `chybí v outputu` | label `IS-IS interface (<iface>)`; další řádky se neemitují |
| `"2" in entry["levels"]` | OK | `<iface>: IS-IS level 2 nakonfigurovan` | `nakonfigurován` | label `IS-IS level 2 (<iface>)` |
| `"2" not in levels` | BROKEN | `<iface>: IS-IS level 2 chybi` | `chybí v outputu` | label `IS-IS level 2 (<iface>)` |
| `"1" in levels` | BROKEN | `<iface>: IS-IS level 1 nema na Core rozhrani co delat` | `nakonfigurován` | label `IS-IS level 1 (<iface>)`; platí pro transit i loopback |
| `"1" not in levels` | – | no finding emitted (řádek level 1 vůbec není) | | |
| passive = `bool(levels.get("2",{}).get("passive"))`; loopback AND passive, nebo transit AND not passive | OK | `<iface>: level 2 passive=ano` (loopback) / `<iface>: level 2 passive=ne` (transit) | `Passive` / `bez Passive` | label `IS-IS level 2 passive (<iface>)` |
| loopback AND not passive, nebo transit AND passive | BROKEN | `<iface>: level 2 passive=ne` (loopback) / `<iface>: level 2 passive=ano` (transit) | `bez Passive` / `Passive` | Pozn.: když level 2 chybí, passive=False → loopback dostane druhý BROKEN (passive=ne) za stejnou příčinu; transit dostane OK „bez Passive" i když level 2 chybí. |

Docstring vs. kód: bez rozporu. Pozn.: title/label check nezmiňují, že rozhraní ze selektorů bez is_transit filtru u loopbacku zahrnou i případná ne-lo0 rozhraní scopu.

#### ldp_neighbor_state
title `Stav LDP souseda` · label `LDP neighbor status` · mode BOTH · default severity CRITICAL · service_types {Core} · subtypes {transit} · requires (`ldp_neighbor`,) · requires_inventory True · order 0 · config options: žádné. Bez gate na záměr (LDP se očekává vždy).

Sdílená kostra `_neighbor_findings(area="ldp_neighbor", status_label="LDP neighbor status", address_label="LDP neighbor address")`. **Per transit rozhraní** ze selektorů: 1 řádek (chybí) nebo 2 řádky (session, adresa).

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| rozhraní není v `subject["ldp_neighbor"]` | BROKEN | `<iface>: soused ve vypisu neni` | `Down` | label `LDP neighbor status (<iface>)`; baseline_value: None bez baseline záznamu; jinak `Up` pokud `was["uptime_seconds"]` truthy, else `Down`. Adresní řádek se neemituje. |
| `seconds = entry["uptime_seconds"]`; `seconds and seconds > 0` | OK | `<iface>: session bezi` | `Up for <format_uptime(seconds)>` např. `Up for 3h 12m` | label `LDP neighbor status (<iface>)`; baseline_value None (stav se s baseline neporovnává) |
| seconds None / 0 / záporné | BROKEN | `<iface>: session nebezi` | `Down` | baseline_value None |
| adresní řádek: has_baseline AND was existuje AND `entry["neighbor_address"] != was["neighbor_address"]` | DEGRADED | `<iface>: adresa souseda <address>` (může být doslova `None`) | `str(address)` nebo `chybí v outputu` | label `LDP neighbor address (<iface>)`; baseline_value `str(was_address)` nebo None |
| adresní řádek: jinak (bez baseline, bez was, nebo shoda) | INFO | `<iface>: adresa souseda <address>` | `str(address)` / `chybí v outputu` | baseline_value `str(was_address)` pokud was a hodnota není None (u shody = stejná hodnota), jinak None. Pozn.: address None bez baseline → INFO s value `chybí v outputu`, nikdy BROKEN. |

Docstring vs. kód: komentář „baseline_value se odvozuje ze stejného pravidla (uptime_seconds > 0)" – kód používá truthiness `was.get("uptime_seconds")`, tj. záporná hodnota by dala `Up`, zatímco subject pravidlo `seconds > 0` by dalo Down. Okrajové.

#### pim_neighbor_state
title `Stav PIM souseda` · label `PIM neighbor status` · mode BOTH · default severity CRITICAL · service_types {Core} · subtypes {transit} · requires (`pim_neighbor`,) · requires_inventory True · order 0 · config options: žádné.

Gate: `"pim" not in ctx.scope.selectors.protocols` → `return []` (ticho, ne SKIP). Jinak identické řádky jako LDP s labely `PIM neighbor status (<iface>)` / `PIM neighbor address (<iface>)`, area `pim_neighbor`.

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| `"pim"` není v `scope.selectors.protocols` | – | no finding emitted (celý check ticho) | | Pozn.: gate je na celý scope, nikoli per rozhraní pod `protocols pim`. |
| rozhraní není v `subject["pim_neighbor"]` | BROKEN | `<iface>: soused ve vypisu neni` | `Down` | label `PIM neighbor status (<iface>)`; baseline_value `Up`/`Down`/None jako u LDP |
| uptime_seconds > 0 | OK | `<iface>: session bezi` | `Up for <uptime>` | label `PIM neighbor status (<iface>)` |
| uptime_seconds None/0 | BROKEN | `<iface>: session nebezi` | `Down` | |
| adresa změněna proti baseline záznamu | DEGRADED | `<iface>: adresa souseda <address>` | `str(address)` / `chybí v outputu` | label `PIM neighbor address (<iface>)`; baseline_value `str(was_address)`/None |
| adresa jinak | INFO | `<iface>: adresa souseda <address>` | `str(address)` / `chybí v outputu` | |

Docstring vs. kód: bez rozporu.

#### mpls_interface_state
title `Stav MPLS rozhrani` · label `MPLS interface status` · mode BOTH · default severity CRITICAL · service_types {Core} · subtypes {transit} · requires (`mpls_interface`,) · requires_inventory True · order 0 · config options: žádné.

**Per transit rozhraní** ze selektorů, vždy přesně 1 řádek. Baseline se nepoužívá k rozhodnutí o Outcome, jen se propisuje do `baseline_value`.

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| rozhraní není v `subject["mpls_interface"]` | BROKEN | `<iface>: rozhrani neni pod protocols mpls` | `chybí v outputu` | label `MPLS interface status (<iface>)`; baseline_value `str(was["state"])` pokud není None, jinak None |
| `state = str(entry.get("state","unknown")) == "Up"` | OK | `<iface>: MPLS Up` | `Up` | baseline_value jako výše |
| state != "Up" | BROKEN | `<iface>: MPLS <state>` (syrový, např. `Down`, `unknown`) | `Down` (vždy, i pro `unknown`) | baseline_value jako výše. Pozn.: message nese syrový stav, value ho normalizuje na `Down`. |

Docstring vs. kód: mode BOTH, ale kód proti baseline nic neporovnává (žádný DEGRADED) – baseline slouží jen jako sloupec.

#### isis_overview
title `IS-IS overview routeru` · label `IS-IS overload bit` · mode STATE · default severity **ADVISORY** (BROKEN by bylo WARN, ale check BROKEN nikdy nevrací) · service_types {Core} · subtypes {loopback} · requires (`isis_overview`,) · requires_inventory True · order 0 · config options: žádné.

Vždy přesně 1 řádek (per scope, ne per rozhraní), label z checku (`IS-IS overload bit`). Pozn.: chybějící area `isis_overview` ve faktech (prázdný dict) → `overload=False` → OK „neni nastaveny" – absence dat se tu neodlišuje od měřeného „nenastaven".

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| `bool(overview.get("overload_enabled"))` True | DEGRADED (→ WARN) | `overload bit je nastaveny - router se vyhyba tranzitnimu provozu` | `nastaven` | baseline_value None |
| False / klíč chybí / area chybí | OK | `overload bit neni nastaveny` | `nenastaven` | |

Docstring vs. kód: bez docstringu; title „overview routeru" – kód čte jediné pole `overload_enabled`.

#### bfd_transit_state
title `Stav BFD na tranzitnim rozhrani` · label `BFD` · mode BOTH · default severity CRITICAL · service_types {Core} · subtypes {transit} · requires (`bfd`,) · requires_inventory True · order 0 · config options: žádné.

`subject["bfd"]` je dict `peer -> data`; seskupí se podle `str(data.get("interface",""))`. **Per transit rozhraní** ze selektorů: 1 řádek (žádná session) nebo **1 řádek per (peer, data) session** (`sorted(entries)` – řadí podle peer adresy jako string). Baseline se nečte vůbec.

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| pro rozhraní žádná BFD session (`by_interface.get(name)` prázdné/None) | BROKEN | `<iface>: zadna BFD session` | `Down` | label `BFD (<iface>)`; baseline_value None |
| session, `state = str(data.get("state","unknown")) == "Up"` | OK | `<iface>: BFD session s <peer> Up` | `Up` | label `BFD (<iface>)`; `subject=data` (celý session dict) |
| session, state != "Up" | BROKEN | `<iface>: BFD session s <peer> <state>` (syrový, např. `AdminDown`, `Down`, `unknown`) | `<state>` syrový | label `BFD (<iface>)`; `subject=data` |

Docstring vs. kód: mode BOTH, ale baseline se nikde nečte (žádné baseline_value ani DEGRADED). Komentář odkazuje na „bfd.py:113" jako sesterský check – dle paměti byla BFD z laborky záměrně pryč; odkaz na řádek může být zastaralý (neověřeno tady).

---

### multicast.py

Společné helpery:
- `sg_label(source, group)` → `(<source or '*'>, <group>)`; `pairs_text` = join `, `.
- `igmp_pairs(facts, scope)`: množina `(entry["source"], str(entry["group"]))` přes **všechna** rozhraní `scope.selectors.interfaces` z `facts["igmp_group"][iface]`, jen záznamy s truthy `group`; sorted podle `(source or "", group)`. Baseline se čte s `ctx.baseline_scope or ctx.scope`.
- `multicast_table(facts)`: sloučí všechny instance z `facts["multicast_route"]` do jednoho dictu `"<S>,<G>" -> route`.
- `routes_for(table, source, group)`: pro SSM `(S,G)` přesný klíč `route_key(S,G)` = `"S,G"`; pro ASM `(*,G)` všechny routy, jejichž klíč po čárce == group (sorted).
- `format_uptime_hms(s)`: `-` pro None; `HH:MM:SS`, s dny `<d>d HH:MM:SS`.
- `stream_rows(sg, route, rate_label)`: vždy 2 řádky (rate + uptime) – viz tabulky; nikdy neporovnává baseline.
- `NO_REPORT = "Receiver neposila zadny IGMP membership report"`, `NO_REPORT_SKIP = "bez IGMP reportu"`, `RATE_UNAVAILABLE = "statistiky nedostupne"`.

#### igmp_membership_report
title `IGMP membership report receiveru` · label `IGMP membership report` · mode BOTH · default severity CRITICAL · service_types {Internet, IPVPN} · subtypes {multicast, mvpn-igmp} · requires (`igmp_group`,) · requires_inventory True · **order 10** · config options: žádné.

Vždy přesně 1 řádek per scope (agregát přes všechna rozhraní scopu, ne per rozhraní). `was` = baseline pairs (None bez baseline; prázdný list = baseline bez skupin). `was_value = pairs_text(was) if was else None` → baseline bez skupin dává baseline_value None a nikdy nezpůsobí DEGRADED.

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| `now` prázdné (žádný IGMP záznam s group na rozhraních scopu) | BROKEN | `receiver neposila zadny IGMP membership report` | `Receiver neposila zadny IGMP membership report` | baseline_value `pairs_text(was)` pokud baseline měla skupiny, jinak None |
| `was` neprázdné AND `set(was) != set(now)` | DEGRADED | `IGMP skupiny se zmenily proti baseline: <pairs_text(now)>` např. `IGMP skupiny se zmenily proti baseline: (*, 239.1.1.1), (10.0.0.1, 232.1.1.1)` | `<pairs_text(now)>` | baseline_value `<pairs_text(was)>` |
| jinak (bez baseline, baseline prázdná, nebo shoda množin) | OK | `IGMP membership report: <pairs_text(now)>` | `<pairs_text(now)>` | baseline_value `<pairs_text(was)>` nebo None |

Docstring vs. kód: bez rozporu. Komentář „Baseline bez skupin = není s čím porovnat" odpovídá `if was` truthiness.

#### multicast_forwarding_status
title `Multicast forwarding na servisnim rozhrani` · label `Multicast forwarding status` · mode STATE · default severity CRITICAL · service_types {Internet, IPVPN} · subtypes {multicast, mvpn-igmp} · requires (`igmp_group`, `multicast_route`) · requires_inventory True · **order 11** · config options: žádné. Baseline se nepoužívá.

Struktura výstupu: 1 SKIP řádek (kaskáda) **nebo** [1 souhrnný řádek] + per IGMP pár: buď 1 řádek „není v tabulce", nebo **per matching routu** 4 řádky (stream, upstream, rate, uptime). Pro ASM `(*, G)` může jeden IGMP pár dát N rout (každá se vlastním `sg` s reálným zdrojem z klíče tabulky). `iface = ctx.scope.selectors.interfaces[0]` – **pouze první rozhraní scopu** se používá jako očekávaný downstream (IGMP páry se ale sbírají ze všech rozhraní scopu). Role-aware: `subtype == "mvpn-igmp"` → upstream musí začínat `lsi.`/`vt-`; jinak (`multicast`, nebo None) `ge-`/`xe-`/`et-`/`ae`.

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| IGMP páry prázdné | SKIP | `bez IGMP reportu neni co hledat v multicast tabulce` | `bez IGMP reportu` | label z checku (`Multicast forwarding status`), jediný řádek; group None |
| souhrn: `failed > 0` (počítá **IGMP páry**, ne routy; pár je failed, pokud není v tabulce nebo kterákoli jeho routa má BROKEN řádek – včetně BROKEN rate 0 pps, ale **ne** SKIP rate) | BROKEN | `<failed> z <total> S,G nefunguje` | `<failed>/<total> S,G nefunguje` | label `Multicast forwarding status`; první řádek výstupu; group None |
| souhrn: failed == 0 | OK | `<total> S,G funguje` | `<total> S,G` | |
| pár bez routy (`routes_for` prázdné) | BROKEN | `<sg>: S,G neni v multicast tabulce` – sg = `(<source or '*'>, <group>)` z IGMP | `S,G neni v multicast tabulce` | label `Stream`, group `<sg>`; failed += 1 |
| per routa: `iface in route["downstream_interfaces"]` | OK | `<sg>: stream se na <iface> posila` | `Stream se na <iface> posila` | label `Stream`, group `<sg>` (sg se skutečným zdrojem z klíče routy) |
| per routa: iface není v downstream | BROKEN | `<sg>: stream se na <iface> neposila` | `S,G je v tabulce ale stream se na <iface> neposila` | label `Stream` |
| per routa: `_upstream_ok(subtype, upstream)` True (upstream truthy a startswith prefixy dle subtypu) | OK | `<sg>: upstream <upstream>` | `<upstream>` | label `Upstream interface`, group `<sg>` |
| per routa: upstream None/"" nebo špatný prefix | BROKEN | `<sg>: upstream <upstream or '-'> - S,G je v tabulce ale nema upstream interface` | `<upstream>` nebo `-` | Pozn.: message říká „nemá upstream interface" i když upstream existuje, jen má nesprávný prefix (např. `lsi.0` u Internet/multicast, nebo `ae0.0` u mvpn-igmp). |
| per routa (stream_rows): `forwarding_rate_pps is None` | SKIP | `<sg>: forwarding statistiky nejsou ve vypisu (multicast-statistics-timed-out)` | `statistiky nedostupne` | label `Forwarding-rate`, group `<sg>`; SKIP nezvyšuje failed |
| per routa: `int(pps) > 0` | OK | `<sg>: forwarding rate <pps> pps` | `<pps> pps` | label `Forwarding-rate` |
| per routa: pps == 0 (nebo záporné) | BROKEN | `<sg>: forwarding rate <pps> pps` | `<pps> pps` | label `Forwarding-rate`; pár failed |
| per routa: uptime (vždy) | INFO | `<sg>: route uptime <format_uptime_hms(uptime_seconds)>` např. `... route uptime 1d 02:03:04` nebo `... route uptime -` | `<hms>` / `-` | label `Route uptime`, group `<sg>` |

Docstring vs. kód: module docstring „Upstream, downstream, rate a uptime se proti baseline neporovnávají nikdy" – platí. Komentář `_upstream_ok` message „nemá upstream interface" nesedí na případ špatného prefixu (viz výše). Label `Forwarding-rate` (s pomlčkou) zde vs. `Forwarding rate packets` u Core – záměrně různé.

#### core_multicast_forwarding
title `Multicast forwarding pro inet.2 statiky` · label `Multicast forwarding status` · mode BOTH · default severity CRITICAL · service_types {Core} · subtypes {loopback} · requires (`multicast_route`, `routes`) · requires_inventory True · **order 12** · config options: žádné.

Gate: `_inet2_prefixes(scope)` = prefixy ze `scope.selectors.static_routes` s `rib == "inet.2"` a `route_type` (default `static`) == `static`; prázdné → `return []`. Subject i baseline tabulka se přiřazují k **subject** prefixům (`assign_sources`: nejdelší pokrývající prefix vyhrává, každá routa jednou, ne-IPv4/neparsovatelné se přeskočí). `inet2 = subject["routes"]["inet.2"]` → `measured = inet2.get(prefix)`, `vias = list(measured["via"])` nebo None pokud routa chybí.

Výstup **per prefix**: buď 1 BROKEN + 4 SKIP řádky (bez streamu), nebo 1 OK/DEGRADED souhrn + **per routu** 4 řádky (upstream, downstream, rate, uptime). `CORE_SKIP_LABELS = ("S,G", "Forwarding rate packets", "Upstream interface", "Downstream interfaces")`.

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| žádný inet.2 static prefix v selektorech | – | no finding emitted (celý check ticho, ne SKIP) | | |
| prefix bez přiřazené routy (`streams` prázdné) | BROKEN | `neexistuje S,G se zdrojem v <prefix>` | `Neexistuje S,G pro <prefix>` | label `Multicast forwarding status`, group None; baseline_value `_labels_of(was)` = `(S, G), (S2, G2)` pokud baseline měla streamy pro prefix, jinak None |
| prefix bez routy – 4 doplňkové řádky, po jednom pro každý label v CORE_SKIP_LABELS | SKIP | `<prefix>: bez streamu` | `` (prázdný string) | labels `S,G`, `Forwarding rate packets`, `Upstream interface`, `Downstream interfaces`; group `<prefix>` |
| prefix s routami; `was` neprázdné AND množina klíčů `"S,G"` se liší | DEGRADED | `existuje S,G se zdrojem v <prefix>: <labels> (mnozina se lisi od baseline)` | `Existuje S,G pro <prefix>` | label `Multicast forwarding status`, group None; baseline_value `<labels of was>` |
| prefix s routami; bez baseline / baseline bez streamů pro prefix / shodná množina | OK | `existuje S,G se zdrojem v <prefix>: <labels>` např. `existuje S,G se zdrojem v 10.1.0.0/16: (10.1.0.5, 239.1.1.1)` | `Existuje S,G pro <prefix>` | baseline_value `<labels of was>` nebo None |
| per routa, upstream: inet.2 routa pro prefix není v `subject["routes"]["inet.2"]` (`vias is None`) | SKIP | `<sg>: inet.2 routa neni v tabulce, upstream nelze overit` | `routa neni v tabulce` | label `Upstream interface`, group `<sg>`; FAIL nese `static_route_status` |
| per routa, upstream: `upstream` truthy AND `upstream in vias` | OK | `<sg>: upstream <upstream>` | `<upstream>` | label `Upstream interface` |
| per routa, upstream: upstream None nebo není mezi via | BROKEN | `<sg>: upstream <upstream or '-'> neni mezi via inet.2 routy (<via1, via2> or '-')` | `<upstream>` / `-` | label `Upstream interface` |
| per routa, downstream: `downstream_interfaces` neprázdné | OK | `<sg>: downstream <iface1, iface2>` | `<iface1, iface2>` | label `Downstream interfaces`, group `<sg>` |
| per routa, downstream prázdné | BROKEN | `<sg>: downstream zadne` | `Zadne downstream interfacy` | label `Downstream interfaces` |
| per routa (stream_rows): rate None | SKIP | `<sg>: forwarding statistiky nejsou ve vypisu (multicast-statistics-timed-out)` | `statistiky nedostupne` | label `Forwarding rate packets` |
| per routa: pps > 0 | OK | `<sg>: forwarding rate <pps> pps` | `<pps> pps` | label `Forwarding rate packets` |
| per routa: pps <= 0 | BROKEN | `<sg>: forwarding rate <pps> pps` | `<pps> pps` | label `Forwarding rate packets` |
| per routa: uptime | INFO | `<sg>: route uptime <hms or '-'>` | `<hms>` / `-` | label `Route uptime`, group `<sg>` |

Docstring/komentáře vs. kód: 
- Žádný souhrnný řádek „N z M nefunguje" (na rozdíl od `multicast_forwarding_status`) – per prefix řádek existuje/neexistuje slouží jako souhrn.
- `assign_sources` docstring „Ne-IPv4 prefixy a zdroje se přeskočí" – kód přeskočí jen **neparsovatelné** prefixy/zdroje a pak vyžaduje shodu `version`; IPv6 prefix se validně naparsuje a zůstane v `assigned` s prázdným seznamem → dostal by BROKEN „neexistuje S,G" + 4 SKIP, ne přeskočení.
- Komentář k baseline: „prefixy jsou stabilní identifikátor napříč migrací" – baseline se dělí podle subject prefixů; baseline_scope se tu záměrně nepoužívá (dokumentováno).

#### mvpn_cmulticast_status
title `MVPN c-multicast a provider tunnel` · label `C-Multicast status` · mode BOTH · default severity CRITICAL · service_types {IPVPN} · subtypes {mvpn-igmp} · requires (`igmp_group`, `mvpn_instance`) · requires_inventory True · **order 13** · config options: žádné.

`instance = scope.selectors.routing_instances[0]` nebo None. `entries = subject["mvpn_instance"][instance]["c_multicast"]`; `_cmulticast_entry` vrátí **první** záznam (v pořadí výpisu), jehož `group_prefix` pokrývá group a (pro SSM) `source_prefix` pokrývá source; neparsovatelné group/source → None (→ BROKEN „chybí c-multicast záznam"); záznamy s chybějícím/neparsovatelným prefixem se přeskočí. **Per IGMP pár**: 1 řádek (chybí) nebo 2 řádky (c-multicast, provider tunnel).

| situation | Outcome | message | value | notes |
|---|---|---|---|---|
| IGMP páry prázdné | SKIP | `bez IGMP reportu neni co hledat v MVPN` | `bez IGMP reportu` | label `C-Multicast status`; jediný řádek |
| `subject["mvpn_instance"].get(instance)` je None (instance chybí ve výpisu, nebo scope nemá routing_instances → `instance None` → message `instance None neni v mvpn vypisu`) | BROKEN | `instance <instance> neni v mvpn vypisu` | `instance neni v mvpn vypisu` | label `C-Multicast status`; jediný řádek |
| pár bez pokrývajícího c-multicast záznamu (nebo group/source neparsovatelné) | BROKEN | `<sg>: chybi c-multicast zaznam` | `chybi c-multicast zaznam` | label `C-Multicast status`, group `<sg>`; tunnel řádek se neemituje |
| pár s záznamem | OK (vždy) | `<sg>: c-multicast <source_prefix>:<group_prefix>` např. `(10.0.0.1, 232.1.1.1): c-multicast 10.0.0.1/32:232.1.1.1/32` | `<source_prefix>:<group_prefix>` | label `C-Multicast status`, group `<sg>`; baseline se u tohoto řádku neporovnává |
| tunnel řádek: `entry["sender_pe"]` falsy | BROKEN | `<sg>: provider tunnel <tunnel_id or '-'> - bez provider tunelu` | `<provider_tunnel_id>` nebo `-` | label `Provider tunnel`, group `<sg>`; baseline_value `was["provider_tunnel_id"]` (může být None) – **ne** sender PE |
| tunnel řádek: pe truthy AND has_baseline AND baseline záznam nalezen AND `was_pe` truthy AND `was_pe != pe` | DEGRADED | `<sg>: provider tunnel <tunnel> - sender PE se zmenil z <was_pe>` | `<tunnel>` | baseline_value `<was_tunnel>`; pozn.: message nezmiňuje nový PE, value je tunnel id (které se podle komentáře může měnit bez změny služby) |
| tunnel řádek: pe truthy, jinak (bez baseline, was bez sender_pe, nebo shodný PE) | OK | `<sg>: provider tunnel <tunnel>` | `<tunnel>` | baseline_value `<was_tunnel>` nebo None – změna tunnel id při stejném PE = OK, ale sloupce value/baseline_value se liší |

Docstring/komentáře vs. kód:
- Module docstring: „porovnává se jen množina (S,G) a sender PE tunelu" – platí; navíc se ale do `baseline_value` propisuje `provider_tunnel_id`, který se záměrně neporovnává, takže report ukáže „změnu" sloupce bez WARN.
- `_cmulticast_entry` docstring „záznam, jehož S/32:G/32 pokrývá" – kód pracuje s libovolnou délkou prefixu (`ip_network(..., strict=False)`), ne jen /32; a bere první shodu, ne nejspecifičtější.
- Value řádku „chybí instance" neobsahuje jméno instance (jen message).
