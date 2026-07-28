# `connection/` — připojení k zařízení

Soubory: `junos.py` a prázdný `__init__.py`.

**Jediná vrstva v celém nástroji, která sahá na síť.** Checky ji nikdy neimportují — je to
explicitní pravidlo zapsané v docstringu `checks/base.py`.

Vrstva je záměrně tenká, protože je to jediné, co se nedá testovat bez reálného boxu.
Všechno, co jde otestovat offline, je jinde.

---

## `junos.py`

### `ConnectionOptions`

Dataclass s parametry spojení:

| pole | default |
|---|---|
| `host` | — (povinné) |
| `username` | `ansible` |
| `auth_type` | `key` (nebo `password`) |
| `key_file` | `~/.ssh/id_rsa` |
| `password` | `None` |
| `port` | `22` |
| `timeout` | `30` |

Metoda `device_kwargs()` z toho složí argumenty pro PyEZ `Device`. Validace je tady, ne až
v PyEZ:

- `auth_type="password"` bez hesla → `ValueError("auth_type 'password' vyzaduje heslo")`,
- neznámý `auth_type` → `ValueError` s výčtem povolených hodnot.

Defaulty odpovídají konvenci z existujících parserů, takže operátor nemusí přepínat návyky
mezi nástroji.

### `connect()` — context manager

```python
with connect(options) as device:
    ...
```

Otevře spojení a **přeloží PyEZ výjimky na `JunosConnectionError` s hláškou, která říká
proč**:

| PyEZ výjimka | hláška |
|---|---|
| `ConnectAuthError` | `<host>: autentizace selhala (uzivatel <user>) - ...` |
| `ConnectTimeoutError` | `<host>: timeout po <N> s - ...` |
| `ConnectRefusedError` | `<host>: spojeni odmitnuto - ...` |
| `ConnectError` (ostatní) | `<host>: pripojeni selhalo - ...` |

Rozlišení je požadavek ze specifikace: *špatný klíč* a *nedostupný box* vyžadují od
operátora jinou reakci. CLI tuhle výjimku převede na `ToolError` a vrátí **kód 2** —
snapshot v takovém případě nevznikne vůbec.

Zavření spojení je v `finally`, takže se `device.close()` provede i při výjimce uvnitř bloku.

### `detect_platform(device)`

Vrací `"junos"` nebo `"junos-evo"`:

1. `EVO` v řetězci verze (case-insensitive) → `junos-evo`,
2. jinak model začínající na `PTX10`, `ACX7`, `QFX5700`, `MX304` → `junos-evo`,
3. jinak `junos`.

Ověřeno proti laborce (VMX `24.2R1-S2.5` a PTX10002 `25.2R1.8-EVO`): rozhoduje první
pravidlo, fallback na model se neuplatní. Funkce čte `device.facts` přes `getattr`, takže
si vystačí s libovolným objektem, který má `facts` — díky tomu ji testy volají s `FakeDevice`.

Výsledek se předává do `collectors_for(platform)` a do každého `collector.collect()` —
je to jediný vstup, podle kterého se rozhoduje o platformních variantách RPC.

### `device_meta(device, address)`

Složí `DeviceMeta` z `device.facts` (`hostname`, `model`, `version`) a detekované platformy.
`uptime_seconds` zůstává `None` — pole ve schématu je, ale nikdo ho zatím neplní.

---

## Co tu záměrně není

- **Žádný retry.** Selhání spojení je tvrdá chyba pro celý běh; opakovat má smysl až na
  úrovni jednotlivých RPC, a to řeší collector (`CollectorError`), respektive ping
  (`run_ping`, který odchytává i přechodně poškozené XML z vMX).
- **Žádné parsování obsahu.** Vrací se `Device`, odpovědi RPC si tahá collector sám.
- **Žádná znalost služeb.** Vrstva neví, co je scope ani inventory.
