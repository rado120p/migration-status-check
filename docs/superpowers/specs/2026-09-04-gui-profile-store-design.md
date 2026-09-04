# GUI profile store and profile editor — named profiles, run picks one, form for the profile section

Date: 2026-09-04
Status: approved (brainstorm with visual companion; mockups in `.superpowers/brainstorm/861106-1788515246/content/profile-editor.html`, shared top part)

Spec 3 of 4 from the 2026-09-04 GUI feature brainstorm. Depends on spec 1
(permission seam: writes require `admin`) and spec 2 (`profile` field in
the manifest). Spec 4 adds the checks table to the editor page defined here.

## Goal

The profile YAML (`--profile` / `--config`) is chosen once at server start
and applies to every run; users cannot see or change it from the GUI.
Introduce a directory of named profiles that the GUI can create and edit,
let each run record which profile it uses, and give the `profile:` section
a form that shows defaults.

## 1. Store

- Directory `profiles/` next to `runs/`, one file per profile:
  `profiles/<name>.yml`. `<name>` matches `^[a-z0-9_-]+$` (same rule as
  run names).
- New GUI flag `--profiles-root DIR` (default `profiles`), passed to
  `create_app(..., profiles_root=...)`.
- `--profile` keeps its meaning: it is the **server default profile**,
  used by runs whose manifest has `profile: null`, and by the CLI exactly
  as today. Without the flag the server default is the built-in empty
  profile, displayed as `(default)`.
- Module `migration_validator/profiles/store.py`:
  - `ProfileStore(root)` with `list() -> list[str]`, `path(name)`,
    `load(name) -> Profile`, `save(name, document: dict)`, `delete(name)`,
    `exists(name)`.
  - `save` validates by dumping the document to YAML text, running it
    through the existing `load_profile` on a temp file in the same
    directory, then renaming over the target. A failing validation leaves
    no temp file behind and raises the loader's `ValueError` unchanged.
  - Document shape (JSON, mirrors the YAML): `{"profile": {"collectors":
    [...]|null, "service_types": [...]|null, "ping_count": int|null},
    "checks": {<check_id>: {...overrides}}}`. Null means "not set, use
    default"; the writer omits null keys and omits an empty `checks`
    section so the file stays minimal.

## 2. Resolution per run

- `migration_validator/gui/profiles.py` (or a section in `app.py` if it
  stays small): `profile_for_run(manifest) -> Profile`: if the manifest
  names a profile, load it from the store; else return the server default.
- The run evaluation, snapshot evaluation and capture routes call
  `profile_for_run` instead of loading `profile_path` directly.
- A manifest naming a profile that no longer exists → 422
  `profil '<name>' neexistuje (runs/<run>/run.yml)`. No silent fall-back.

## 3. API

All under `/api/profiles`. Writes require `admin`, reads `view`.

| Route | Purpose |
|---|---|
| `GET /api/profiles` | `{"default": <server default name or null>, "profiles": [{"name", "used_by": <int>}]}`; `used_by` counts manifests under `run_root` referencing the name |
| `GET /api/profiles/catalogue` | defaults for the form, see §4 |
| `GET /api/profiles/{name}` | the document |
| `POST /api/profiles` | `{"name", "document"}` → 201; 409 if exists; 422 on validation error with the loader message |
| `PUT /api/profiles/{name}` | replace the document → 200; 404 if missing; 422 on validation |
| `DELETE /api/profiles/{name}` | 204; 409 `profil pouziva <n> runu` while referenced |
| `POST /api/profiles/preview` | `{"document"}` → `{"yaml": "<text>"}` (spec 4 uses it; defined here because it is the store's serialiser) |

- Serialising the document to YAML is the one function
  `document_to_yaml(document) -> str` in the store module; `save` and
  `preview` both use it, so the preview is byte-identical to the file.

## 4. Catalogue

`GET /api/profiles/catalogue` returns everything the form needs to show
defaults without hardcoding:

```json
{
  "collectors": {"junos": [...], "junos-evo": [...]},
  "service_types": ["Core", "E-LAN", "E-Line", "IPVPN", "Internet"],
  "ping_count_default": 5,
  "checks": [
    {"id": "interface_traffic", "title": "...", "group": "interfaces",
     "default_severity": "advisory",
     "options": {"tolerance_percent": {"type": "number", "default": -60},
                 "require_nonzero": {"type": "boolean", "default": true}}}
  ]
}
```

- Collectors from `collectors_for(platform)` as `/api/meta` does today.
- Service types from `scoping.builder.MIGRATED_SERVICE_TYPES`, sorted.
- Ping count default from the CLI's existing fallback (5), exposed as a
  constant in `config.py` so both agree.
- Checks from `api.list_checks()` merged with `config.DEFAULTS`. `group`
  is the first entry of the check's `requires` list, or `general` when
  empty. Option types are inferred from the default value's Python type
  (bool → boolean, int/float → number). `severity` and `enabled` are not
  listed as options; every check has them.
- Order: the registry order (`order` field), same as the CLI listing.

## 5. Editor page

- The topbar `Checks` button is renamed `Profiles` and opens this page.
  The current checks view (catalogue listing) is folded into the checks
  table of spec 4.
- Top row: profile picker (all names plus `(default)` for the server
  default, read-only), `+ New profile`, `Duplicate`, `Delete`, and
  `pouziva <n> runu` on the right.
  - `(default)` is read-only; `Duplicate` on it writes the built-in empty
    profile (or the `--profile` file's document) under a new name and
    opens it. This is how a user starts from the server default.
  - `New profile` asks for a name and creates an empty document.
  - `Delete` is disabled while `used_by > 0`, with the count as tooltip.
- Profile section form:
  - Collectors: chip picker; empty = all, shown as `(vsechny)` in grey.
    The add menu lists collectors of both platforms, deduplicated.
  - Service types: chip picker from the catalogue; empty = all.
  - Ping count: number input, grey default `5 (default)` until edited.
- Spec 4's checks table follows below on the same page.
- Bottom: `<n> overrides` counter (spec 4), `Discard`, `Save profile`.
  Navigating away with unsaved changes asks for confirmation.
- The run overview shows the run's profile name under the title; clicking
  it opens the editor on that profile.
- New run form (spec 2): the Profile picker becomes active and lists the
  store plus `(default)`.

## Error handling

- Validation errors from `load_profile` are returned verbatim in 422
  `detail` and shown under the Save button; a message naming a check id
  or an option key is additionally shown next to that field when spec 4's
  table can map it.
- Store I/O errors → 500 with the OS message.

## Testing

- `tests/test_profile_store.py`: save/load round-trip; bad name rejected;
  invalid document leaves no temp file and the previous file intact;
  null keys omitted; empty checks section omitted; `document_to_yaml` is
  stable (same document → same text).
- `tests/gui/test_profile_routes.py`: list with `used_by` counts; delete
  refused while referenced; viewer 403 on POST/PUT/DELETE; catalogue lists
  every registered check exactly once with its default severity; every key
  in `config.DEFAULTS` is a registered check id; 422 on an unknown
  collector name.
- `tests/gui/test_evaluation_routes.py`: run naming a missing profile →
  422; run naming an existing profile evaluates with that profile's
  checks (e.g. a disabled check is absent from the result).

## Out of scope

- Per-user profiles and a profile permission finer than `admin`.
- Editing the settings file (credentials) from the GUI.
- Profile versioning or history.
