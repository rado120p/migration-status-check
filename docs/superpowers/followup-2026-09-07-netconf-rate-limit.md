# Follow-up: NETCONF rate-limit v produkci a počet loginů na capture (2026-09-07)

**Stav: poznámka k dořešení, nic z níže uvedeného není implementované.**

## Co se stalo

Produkční Junos boxy mají `system services netconf ssh rate-limit 3` — maximálně
3 pokusy o SSH spojení na port 830 za minutu, neúspěšná autentizace se počítá
také. Capture s `--parse-services` selhal, protože se přihlašoval čtyřikrát.

## Jak se loginy počítají (stav kódu k a53abdd)

Kód otevře **dvě** NETCONF session na capture s parsováním služeb:

1. `_parse_services` v `migration_validator/runs/orchestrate.py` — vlastní krátké
   spojení: detekce platformy, stažení konfigurace, zápis inventory.
2. `api.capture` v `migration_validator/api.py` — nové spojení pro collectory a pingy.

Gather facts **není** samostatný login: PyEZ `Device.open()` volá `facts_refresh()`
uvnitř už otevřené session (běží ale v obou sessionách, tedy dvakrát zbytečně).

Každá session stojí tolik pokusů, kolik jich udělá `connect()` v
`migration_validator/connection/junos.py`: pro každou cestu ke klíči, která
lokálně existuje, samostatný `Device.open()` (= nové SSH spojení), pak heslo.
Pokud box klíč odmítne a projde heslo, jedna logická session = 2 pokusy.
Dvě session × 2 pokusy = 4 > limit 3.

## Past v settings.yml (`migration_validator/auth.py`)

| Klíč ponechaný prázdný | Efekt |
|---|---|
| `ssh_key_paths:` | YAML `null` → **fallback na výchozí klíče** `~/.ssh/id_ed25519`, `~/.ssh/id_rsa` |
| `ssh_key_paths: []` | žádný klíč, zkouší se jen heslo |
| `password_env:` | YAML `null` → žádné heslo, nic se nezkouší (výchozí env proměnná neexistuje) |
| `password_env: X`, X neexportované | loader spadne hned s „proměnná X není nastavena“, na síť nesáhne |

Asymetrie mezi prvními dvěma řádky byla příčina: v produkci bylo
`ssh_key_paths:` prázdné, lokálně existoval `id_rsa`, box ho neznal.

**Řešení nasazené v produkci:** `ssh_key_paths: []` → capture = 2 pokusy.

## Co dořešit (seřazeno podle přínosu)

1. **Zdokumentovat / narovnat sémantiku prázdného `ssh_key_paths`.** Buď doplnit
   do `config/settings-template.yml` a nápovědy GUI, že `[]` klíče vypíná a prázdná
   hodnota znamená výchozí, nebo změnit loader tak, aby explicitně přítomný prázdný
   klíč znamenal „žádné klíče“ a výchozí seznam se použil jen když klíč v YAML chybí
   úplně. Druhá varianta mění chování pro každého, kdo dnes na prázdný zápis spoléhá.
2. **Jedna session na capture.** `capture_into_run` otevře `connect()` jednou a nad
   stejným `device` zavolá `generate_inventory` i `capture_device` (ten už otevřené
   zařízení přijímá). Buď `capture_into_run` obejde `api.capture`, nebo `api.capture`
   dostane variantu přijímající existující `device`. Docstring `_parse_services`
   zaznamenává rozhodnutí ze spec 2026-08-17 držet session odděleně — důvod byla
   jednoduchost `api.capture`, ne rate-limit, takže ho lze přepsat. Vedlejší zisk:
   gather facts jen jednou.
3. **Cache úspěšné autentizace per host** po dobu běhu procesu, aby druhý `connect()`
   nezkoušel znovu odmítnutý klíč. Volitelně předat `Device()` `look_for_keys=False`
   a `allow_agent=False` — paramiko pak nezkouší agent/výchozí klíče uvnitř heslové
   session (nepočítá se jako spojení, ale dělá „Failed publickey“ šum v logu boxu).
4. **Retry při odmítnutém spojení** jako možném zásahu rate-limitu (pauza, jeden
   opakovaný pokus). Až nakonec — náklady skrývá, neodstraňuje.

Cílový stav po bodech 2 + 3: jeden login na capture v ustáleném stavu.

## Ověření na boxu

```
show log messages | match "sshd.*<username>" | last 20
```

Řádky „Failed publickey“ následované „Accepted password“ pro každou session potvrzují
fallback klíč → heslo.
