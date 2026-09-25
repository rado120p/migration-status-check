# User roles, login and host inventory — design

Date: 2026-09-25

## Goal

The GUI moves from a single-user tool on 127.0.0.1 to a shared server that
colleagues open over HTTPS. Two things are needed for that:

1. **Accounts and roles.** Users are created from the CLI, log in through a
   login page, and get `viewer`, `operator` or `admin` permissions. The
   permission seam in `migration_validator/gui/authz.py` already exists;
   today every request is an anonymous admin.
2. **Host inventory.** New runs and bulk groups pick nodes from an
   Ansible-style inventory file instead of typing node name and IP by hand.
   An admin-controlled hostname filter narrows what the search offers.
   Manual entry stays available.

## Closed decisions (do not relitigate)

From the brainstorm on 2026-09-25:

- **Deployment:** shared server behind HTTPS. Session cookies are always
  `Secure`.
- **User management is CLI only.** No user admin screen, no self-service
  password change in the GUI. "Create users" for the admin role means shell
  access to the server.
- **Password hash:** PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte random
  salt, stdlib only (user's choice over Argon2id / scrypt).
- **Sessions:** server-side, in memory. A GUI restart logs everyone out
  (accepted).
- **Platform is always picked by the user.** The inventory supplies node and
  host only; no platform inference.
- **Filter syntax:** shell glob (`fnmatch`), whole node name,
  case-insensitive. `MX-*` means "starts with MX-".
- **Filter scope:** the filter narrows the inventory search only. Existing
  runs, groups and manual entry are unaffected.
- **`config/settings.yml` leaves git.** Only `config/settings-template.yml`
  stays tracked.
- **Permission matrix** below is confirmed as proposed, including the rows
  the original request did not list (capture, mapping, groups, profile edit).

## Permission matrix

| Action | viewer | operator | admin |
|---|---|---|---|
| View runs, groups, profiles, checks; export evaluation JSON | ✓ | ✓ | ✓ |
| Search inventory; view hostname filter | ✓ | ✓ | ✓ |
| Create run | | ✓ | ✓ |
| Start capture (pre/post) on a run | | ✓ | ✓ |
| Edit run mapping | | ✓ | ✓ |
| Groups: create, add devices, group capture | | ✓ | ✓ |
| Archive, upgrade a run or group | | ✓ | ✓ |
| Create, edit, preview, delete profile | | ✓ | ✓ |
| Change hostname filter | | | ✓ |
| Manage users (CLI) | | | shell access |

Evaluation JSON export is client-side from `GET /api/runs/{run}/evaluation`
(`app.js` `exportJson`), so VIEW covers it with no new endpoint.

Route relabels in wave A (everything else keeps its current level):

| Route | today | new |
|---|---|---|
| `POST /api/runs/{run}/archive` | ADMIN | OPERATE |
| `POST /api/runs/{run}/upgrade` | ADMIN | OPERATE |
| `POST /api/groups/{group}/archive` | ADMIN | OPERATE |
| `POST /api/groups/{group}/upgrade` | ADMIN | OPERATE |
| `POST /api/profiles/preview` | ADMIN | OPERATE |
| `POST /api/profiles` | ADMIN | OPERATE |
| `PUT /api/profiles/{name}` | ADMIN | OPERATE |
| `DELETE /api/profiles/{name}` | ADMIN | OPERATE |

After wave B the only ADMIN route is `PUT /api/inventory/filter`.

## Wave A — accounts, login, sessions

### Settings file leaves git

- `git rm --cached config/settings.yml`, add `config/settings.yml`,
  `config/users.yml` and `config/hostname_filter.yml` to `.gitignore`.
- `config/settings-template.yml` documents the new `auth:` block (and, in
  wave B, `inventory:`).
- **Upgrade note (lab and any other clone):** pulling the untracking commit
  deletes the local `config/settings.yml`, because git removes a tracked file
  that the commit removes. Copy it aside before `git pull` and restore it
  afterwards.

### Users file

Path from `settings.yml`:

```yaml
auth:
  users_file: config/users.yml   # default when the key or block is missing
```

`load_settings` grows an `auth` section next to `connection`; unknown keys
are rejected the same way `connection` rejects them.

Format:

```yaml
users:
  rado:
    role: admin
    password_hash: "pbkdf2_sha256$600000$<salt b64url>$<digest b64url>"
```

Written atomically (temp file in the same directory + `os.replace`) with mode
0600. Loading validates every entry: username pattern, role in
`ALLOWED_ROLES`, hash string with four `$`-separated parts and scheme
`pbkdf2_sha256`. A malformed file is a `ValueError` naming the file and the
user entry; it is never silently skipped.

### Core module `migration_validator/users.py`

No FastAPI import, so the CLI works without the `[gui]` extra.

```python
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
ALLOWED_ROLES = {"viewer", "operator", "admin"}
```

- `hash_password(password) -> str` — the user's reference implementation:
  length check (`AuthenticationError` below 12 or above 1024 characters),
  `os.urandom(16)` salt, `hashlib.pbkdf2_hmac("sha256", …, 600_000)`,
  `"$".join((scheme, iterations, b64url(salt), b64url(digest)))`.
- `verify_password(password, stored) -> bool` — parses scheme, iterations,
  salt and digest from `stored` (so iterations can be raised later without
  breaking existing hashes), recomputes, compares with
  `hmac.compare_digest`. Unknown scheme or malformed string → `False` plus a
  logged warning, never an exception to the login handler.
- `needs_rehash(stored) -> bool` — true when stored iterations are below
  `PASSWORD_ITERATIONS`. The login handler rehashes and saves on a successful
  login when true.
- `UserStore(path)` — `load() -> dict[str, User]`, `save(users)`,
  `get(username) -> User | None`. `get` caches by `(mtime_ns, size)` so the
  GUI can call it on every request cheaply and still see CLI edits
  immediately.
- `User` is a frozen dataclass `(username, role, password_hash)`.

Usernames are case-sensitive and matched exactly.

### CLI

New subcommand group under `mig-validate`:

| Command | Effect |
|---|---|
| `user add NAME --role ROLE` | prompts password twice (`getpass`), refuses an existing name |
| `user passwd NAME` | prompts new password twice |
| `user role NAME ROLE` | changes role |
| `user delete NAME` | removes user |
| `user list` | prints `NAME ROLE`, no hashes |

All take `--settings PATH` (default `config/settings.yml`) to locate the
users file. Passwords are never accepted as arguments or environment
variables. Validation errors (pattern, role, length, mismatch, unknown user)
exit with the existing tool-error exit code and a one-line message.

### Sessions

`migration_validator/gui/sessions.py`:

- `SessionStore` maps a token (`secrets.token_urlsafe(32)`) to
  `(username, created_at, last_seen)`.
- Idle timeout 8 h, absolute timeout 12 h. Expired entries are dropped on
  lookup and pruned opportunistically on login.
- Cookie `mig_session`: `Secure; HttpOnly; SameSite=Strict; Path=/`, no
  `Max-Age` (browser-session cookie; the server enforces the timeouts).
- Browsers accept `Secure` cookies on `http://127.0.0.1` / `localhost`, so
  local development without TLS still works. Plain HTTP on any other address
  will not keep a session — intended.

### Actor provider

`authz.py` keeps its shape:

- `Actor` gains `username: str`.
- New `session_actor(request)` provider: reads the cookie, looks up the
  session, then reads the user **from the users file on every request** (via
  the cached `UserStore.get`). A deleted user or a changed role takes effect
  on the next request. No session, expired session or deleted user → `None`.
- `require(permission)`: no actor → **401** `{"detail": "login required"}`;
  actor with insufficient rank → **403** (unchanged message).
- `anonymous_admin` stays in the module for tests only; `create_app` wires
  `session_actor`. Tests that do not exercise auth override
  `app.state.actor_provider`.

### Login endpoints and page

| Route | Auth | Behaviour |
|---|---|---|
| `GET /login` | none | serves `static/login.html`; redirects to `/` if a valid session exists |
| `POST /api/login` | none | body `{username, password}`; 200 `{username, role}` + cookie, or 401 `{"detail": "invalid username or password"}` |
| `POST /api/logout` | session | drops the session, clears the cookie, 204 |
| `GET /api/me` | session | `{username, role, permissions: ["view", "operate", …]}` |
| `GET /` | session | serves `index.html`; without a session 302 → `/login` |
| `/static/*` | none | unchanged; contains no data |

`login.html` + `login.js`: a small form in the existing visual style
(Inter, same tokens from `style.css`). On 200 it navigates to `/`.

Login hardening:

- Same message for unknown user and wrong password. An unknown user still
  runs `verify_password` against a fixed dummy hash, so timing does not tell
  them apart.
- Throttle: 5 consecutive failures for a `(username, client IP)` pair lock
  that pair for 5 minutes → 429 `{"detail": "too many failed logins, try
  again in N s"}`. A success resets the counter. State is in memory.
- Client IP is `request.client.host`. Behind a reverse proxy this needs
  uvicorn's proxy headers; `gui` gains `--forwarded-allow-ips` (passthrough).

CSRF: `SameSite=Strict` plus an `Origin` check middleware on
`POST/PUT/PATCH/DELETE` under `/api/`. If `Origin` is present and its host
differs from the request `Host`, the request is rejected with 403. A missing
`Origin` is allowed (non-browser clients; browsers always send it on these
methods).

### Frontend

- `app.js` routes every `fetch` through one wrapper; a 401 navigates to
  `/login`.
- On start the app loads `GET /api/me`. The header shows username, role and a
  **Logout** button.
- Controls the role cannot use are hidden (viewer: New run, New group,
  captures, mapping edit, archive/upgrade, profile create/edit/delete;
  non-admin: Settings). This is a UI hint only; the server enforces.

### Startup

`mig-validate gui` refuses to start when the users file is missing or has no
users, printing `mig-validate user add <name> --role admin`. There is no
anonymous fallback.

New passthrough flags: `--ssl-certfile`, `--ssl-keyfile`,
`--forwarded-allow-ips`.

### Audit

- `RunManifest.created_by: str | None = None`, written only when set.
  `api.create_run` and `api.create_group` accept `created_by`; the GUI passes
  the session username. Old run.yml files without it load unchanged. The run
  detail header shows "created by NAME" when present.
- Logger `migration_validator.gui.audit` writes one line per login
  (success/failure, username, IP — never the password), logout, run/group
  create, capture start, mapping edit, archive, upgrade, profile
  create/edit/delete and filter change: `user=NAME action=ACTION target=…`.

## Wave B — inventory, hostname filter, node picker

### Settings

```yaml
inventory:
  path: /etc/ansible/hosts
```

Missing key → inventory disabled; forms are manual-only and the Settings
screen says so. Configured but unreadable file → the inventory endpoints
return an error state with the path and reason; forms fall back to manual
with a notice. The GUI still starts.

### Parser `migration_validator/inventory.py`

Input example:

```
MX-POP1 ansible_host=172.20.20.4
PTX-POP1 ansible_host=172.20.20.5
```

Per line:

- Strip. Skip blank lines, lines starting with `#` or `;`, and `[group]`
  headers (including `[group:vars]` / `[group:children]` sections — their
  body lines are skipped too, since they are not hosts).
- First whitespace-separated token is the node name. Remaining tokens are
  `key=value`; `ansible_host` is taken, surrounding single or double quotes
  stripped. Other variables are ignored.
- Line without `ansible_host` → skipped, warning `line N: NAME has no
  ansible_host`.
- Same name twice with the same host → one entry. With a different host →
  first wins, warning `line N: NAME duplicates line M with a different
  host`.

Not supported (treated literally or warned, not expanded): host ranges
(`mx[01:10]`), YAML inventories, `host_vars/` directories.

`Inventory` result: sorted list of `(node, host)` plus the warnings list.
`InventorySource(path)` caches by `(mtime_ns, size)`, so edits to the file
apply without a GUI restart.

### Hostname filter

File `config/hostname_filter.yml`, owned by the GUI, gitignored, written
atomically:

```yaml
allow:
  - MX-*
  - PTX-*
```

- A node is visible when `fnmatch.fnmatchcase(node.lower(), pattern.lower())`
  holds for any pattern.
- Empty or missing `allow` → everything visible.
- Missing file = empty filter.

### API

| Route | Perm | Behaviour |
|---|---|---|
| `GET /api/inventory?q=TEXT` | VIEW | case-insensitive substring match on node name, within the filtered set, sorted by name; returns `{items: [{node, host}] (≤ 50), total, enabled}` |
| `GET /api/inventory/filter` | VIEW | `{allow: [...], visible, total, warnings: [...], enabled, error}` |
| `PUT /api/inventory/filter` | ADMIN | body `{allow: [...]}`; entries stripped, duplicates dropped, empty entry → 422; saves and returns the same shape as GET. `?dry_run=true` validates and returns the counts without saving or auditing |

Empty `q` returns the first 50 visible nodes.

### Settings screen (admin only)

Sidebar entry **Settings → Hostname filter**:

- Textarea, one pattern per line.
- Live count "N of M nodes visible" for the unsaved text: a 300 ms debounced
  `PUT /api/inventory/filter?dry_run=true` returns the counts without saving.
  An unsaved-changes marker shows until Save.
- Parser warnings listed below.
- Save button; errors from 422 shown inline.

### Node picker (new-run form and bulk group form)

Per device row:

- A search input. Typing queries `GET /api/inventory?q=…` with a 200 ms
  debounce; a dropdown shows up to 50 `MX-POP1 · 172.20.20.4` entries, with
  "N more — refine the search" when `total` exceeds the list. Arrow keys,
  Enter, Esc work.
- Selecting fills **Node** and **Host**, shown read-only, with a "×" to clear.
- A **Manual** checkbox on the row swaps the search for today's free-text
  node and host inputs. Unticking clears them.
- Platform dropdown unchanged.
- Inventory disabled or failing → row is manual, checkbox hidden.

Bulk group form: the same per row; rows are independent, so a group can mix
picked and manual boxes. Existing row validation (duplicate node, missing
host) is unchanged and runs on whichever mode the row is in.

The picker's state logic lives in `static/node_picker.js` (pure functions,
exported for `node --test`), like `view.js`.

The server does not check node/host against the inventory — manual entry is
legitimate.

## Testing

Wave A:

- `users.py`: hash round-trip, wrong password, iterations parsed from the
  stored string (a hash with 1 000 iterations still verifies and
  `needs_rehash` is true), malformed and unknown-scheme hashes return
  `False`, length limits, username pattern, role validation, atomic save and
  0600 mode, mtime cache sees an external edit.
- CLI: add/passwd/role/delete/list with `getpass` patched; mismatch and
  duplicate refusals; no hash printed by `list`.
- Settings: `auth` block parsing, default path, unknown key rejected.
- API: 401 on every protected route without a session; a **table-driven
  permission test** that walks every route × role and pins the matrix above;
  login success/failure, identical failure bodies, throttle to 429 and reset
  on success, logout invalidates the session, idle and absolute expiry (clock
  injected), deleted user and changed role take effect on next request,
  `Origin` mismatch → 403, `/` → 302 without session, `/login` → `/` with
  one, startup refusal without users, `created_by` written.

Wave B:

- Parser: comments, group headers, `:vars`/`:children` bodies skipped,
  quotes, missing `ansible_host`, duplicates same/different host, mtime
  reload.
- Filter: glob semantics (`MX-*` does not match `SOMEMX-1`), case
  insensitivity, empty allow, missing file.
- API: search limit and `total`, filtered search, disabled inventory,
  unreadable file, filter PUT validation, `dry_run` leaves the file
  untouched, PUT as operator → 403.
- JS: `node --test` for `node_picker.js` state transitions.

Lab: user's browser pass behind HTTPS on the shared server.

## Out of scope

- GUI user management, self-service password change, password reset flows.
- Per-run ownership or visibility rules; filtering existing runs by hostname.
- Persistent sessions across restarts; multiple uvicorn workers.
- Platform inference from the inventory.
- YAML inventories, host ranges, `host_vars/`.
- SSO / LDAP.
