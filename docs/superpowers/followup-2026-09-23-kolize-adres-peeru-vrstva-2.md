# Follow-up: kolize adres peerů – vrstva 2 (2026-09-23)

**Stav: vrstva 1 je hotová a na main (3065a8c, 9defa07). Vrstva 2 není
implementovaná. Pořadí dohodnuté 2026-09-23: nejdřív spec „raw XML + replay“
(rozhodnutí z 2026-09-09), vrstva 2 až na něm.**

## Co se stalo

Ostrý běh MX → ACX 2026-09-23: dvě IPVPN služby (customer-a, customer-b) mají
stejnou p2p podsíť, peer 192.168.1.2 v obou VRF. Collectory `bgp` a `bfd`
klíčují fakta jen adresou peera, takže na zařízení přežije jedna položka –
ta, kterou collector přečetl poslední. Baseline customer-b pak nesla session
customer-a:

```
 WARN | BGP prefixy (customer-a.inet.0) : chybi                                             | bylo v tabulce
 FAIL | BFD (192.168.1.2)               : v baseline patril k teto sluzbe, v subjektu uz ne | bylo Up
```

## Co udělala vrstva 1 (jen vyhodnocení, formát snapshotu beze změny)

- `Scope.owns_bgp_peer`: položka patří službě, jen když její `routing_instance`
  (peer-cfg-rti) je instance, ze které parser bere sousedy služby – IPVPN svou
  VRF, Internet a Core lo0.0 master (zrcadlí `_assign_bgp_neighbors`).
- `Scope.owns_bfd_session`: single-hop session musí být na rozhraní služby;
  multihop rozhraní nenese, páruje se dál jen adresou.
- NEZARAZENO používá stejné predikáty.
- `select()` vrací `bgp_collisions` / `bfd_collisions`; checky z nich dělají
  SKIP `neznamy (kolize adresy)` a u prefixů `bez baseline (kolize adresy)`.
  Kolize v baseline nikdy nedá UNCHANGED.

## Co vrstva 1 nevyřeší

- **Přepsaná session je ztracená.** V baseline MX byla zachycena jen jedna ze
  dvou session na sdílené adrese; srovnání před/po pro takovou službu vyžaduje
  vrstvu 2 a nový capture.
- **Multihop BFD** se páruje jen adresou (např. 198.11.14.4 v L3VPN-CPE14-UNI
  v laborce) – dvě VRF s multihop session na stejnou adresu se pořád slijí.

## Vrstva 2 – rozsah

1. **Collector `bgp`**: klíč (instance, adresa) místo adresy. Tvar klíče
   (složený řetězec vs. vnořený slovník per instance) je otevřený.
2. **Collector `bfd`**: klíč (adresa, rozhraní) pro single-hop. Pro multihop
   ověřit, jestli RPC odpověď (`get-bfd-session-information`, případně
   `extensive`) nese routing instance – v dnešních nahrávkách takové pole
   není.
3. **SCHEMA_VERSION 13 → 14.** Loader přijímá jen přesnou shodu, takže všechny
   dnešní snapshoty (včetně produkční baseline) by přestaly jít načíst – proto
   pořadí níže.
4. **Konzumenti klíče adresou**, které je nutné přepsat: `Scope.select`
   (+ `owns_*`, kolizní mapy pak možná zaniknou), `_unassigned_bgp_peers` /
   `_unassigned_bfd_sessions`, přejmenování rozhraní u `bfd` v
   `_aligned_baseline_data` (`_RENAMED_FIELD_DICT_AREAS`), `checks/bgp.py`,
   `checks/bfd.py`, `bfd_transit_state` v `checks/core_protocols.py`,
   popisky řádků `BGP status (adresa)` / `BFD (adresa)` (identifikátor řádku
   i v JSON výstupu), fixtures a conformance testy collectorů.
5. **Hloubková revize** (přání uživatele): projít všechny oblasti faktů, jestli
   jinde klíč nesplývá napříč instancemi/rozhraními stejně jako tady.
   Kandidáti ke kontrole: `arp`/`nd` (seznam s rozhraním), `routes` (klíč
   tabulka + prefix), `evpn_esi` (ESI), per-interface oblasti (isis, ldp,
   pim_neighbor, mpls, igmp_group), `multicast_route` / `pim_join` (per
   instance), ping probes. Tohle je seznam ke kontrole, ne závěr.

## Pořadí: nejdřív raw XML + replay

Rozhodnutí z 2026-09-09: každý capture (i baseline) uloží konfiguraci a
všechny RPC odpovědi jako XML do run adresáře a příkaz
`mig-validate upgrade <run>` z nich přegeneruje inventory i snapshot aktuální
verzí nástroje. Řetězy upgrade funkcí byly zamítnuté (odvozená pole jako
subtype nejdou bez konfigurace přepočítat). Dnes je nahrávání raw XML jen
volitelné (`--record-raw`) a konfiguraci nezahrnuje.

S tím v provozu je bump schématu kvůli vrstvě 2 bezpečný: snapshoty se
přegenerují z raw XML novým collectorem, a to včetně session, kterou dnešní
collector zahazuje (v XML je obě).

**Rozhodnuto 2026-09-23 (spec
`docs/superpowers/specs/2026-09-23-raw-retention-a-upgrade-design.md`):**
snapshoty zachycené před zavedením raw XML (včetně běhu MX → ACX
z 2026-09-23) se nepřevádějí – **žádný shim 13 → 14 ve vrstvě 2**. Po bumpu
je nová verze nenačte a `mig-validate upgrade` je vypíše jako „nelze – bez
raw záznamu“. Služby i konfigurace z MX už jsou pryč, kontroly toho běhu
jsou hotové; uživatel to přijal.
