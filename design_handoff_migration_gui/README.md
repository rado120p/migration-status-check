# Handoff: mig-validate Web GUI

## Overview
Web GUI for [rado120p/migration-status-check](https://github.com/rado120p/migration-status-check) — a validator for network service state during MX (classic Junos) → Junos EVO (ACX/PTX) migrations. The GUI is the visual equivalent of the `mig-validate` CLI: it shows run status (`status --run`), evaluation results (`evaluate --run` / `evaluate --snapshot`), the registered check list (`checks`), a capture launcher (`capture --run`) and a **run creator** — a capability the CLI does not have (`run.yml` is hand-written today), so the GUI is its first implementation. Backend target: **FastAPI + uvicorn**, with routes as thin wrappers over `migration_validator/api.py` (the module explicitly designed as "the single seam the GUI will call").

## About the Design Files
The files in this bundle are **design references created in HTML** — prototypes showing intended look and behavior, not production code to copy. The task is to recreate these designs in the target stack: a FastAPI backend serving a frontend (plain JS/htmx, React, or Vue — none exists yet, so choose what fits the team; the design is simple enough for server-rendered templates + a little JS for expand/collapse). All data shown in the prototypes is realistic sample data shaped like the repo's real models (`RunResult`, `ScopeResult`, `CheckResult`, `RunManifest`).

**Prototype vs. this README:** the interactive prototype predates the final decisions. Screens 5/6 (New run, Edit mapping), the reworked capture form (screen 4), capture progress, and the empty states exist only in this README — build them from the written spec using the design tokens below. Where the prototype and this README disagree (e.g. the capture form's free-text inputs, the "Evaluate vs baseline…" button), **this README wins**.

## Fidelity
**High-fidelity.** Colors, typography, spacing and interaction states are final. Recreate prototyped screens pixel-perfectly; screens without a prototype (5, 6, reworked parts of 4, empty states) are built from this README's written spec using the same design tokens.

## App Shell (all views)
- Fixed-width shell: 1200px, centered, `background #f7f8fa`, `border 1px solid #d3d7de`, `border-radius 10px`, shadow `0 8px 30px rgba(15,23,42,0.10)`. Page background `#e8eaed`. Horizontal scroll below 1200px (never clip).
- **Top bar** (white, `border-bottom 1px solid #e2e5ea`, padding 14px 24px): logo block (28×28, radius 6, `#1a56db`, white "mv" in mono 700 13px) + "mig-validate" (600 15px `#111827`); right side: "Profile: core-only.yml" (13px, label `#6b7280`, value mono `#111827`), **Checks** button (secondary), **New capture** button (primary).
- **Sidebar** (232px, white, `border-right 1px solid #e2e5ea`, padding 16px 12px):
  - Section headers: 600 11px uppercase, letter-spacing 0.08em, `#9ca3af`.
  - RUNS: section header row also carries a right-aligned "+ New run" link (11px 600, `#6b7280`, hover `#1a56db`) opening the New run view (screen 5).
  - RUNS: run cards (radius 6, padding 10px). Active card `background #eef2ff`, name mono 600 13px `#1a56db`, right-aligned status ("in progress" `#059669` 600 11px / "done" `#9ca3af`), sub-line 12px `#6b7280`.
  - SNAPSHOTS: one row per snapshot file — label mono 500 12px `#374151` (ellipsis), phase badge (mono 600 10px, padding 1px 6px, radius 3: pre `#e0e7ff`/`#4338ca`, post `#d1fae5`/`#047857`, rollback `#fef3c7`/`#b45309`), taken-date sub-line 11px `#9ca3af`. Selected row `background #eef2ff`, label `#1a56db`.
  - Footer (top border `#eef0f3`): "Devices 2", "Snapshots 7" — label 12px `#6b7280`, value mono `#111827`.
- Main pane: padding 24px 28px, vertical stack gap 20px.

## Buttons
- Primary: no border, `#1a56db` bg, white, 600 13px, radius 6, padding 8px 16px. Green variant `#059669` for "Evaluate run". Dark variant not used in final design.
- Secondary: `1px solid #d1d5db`, white bg, `#374151`, 500 13px, radius 6, padding 8px 16px.
- Danger-secondary (rollback contexts): border `#fca5a5`, text `#b91c1c`.
- Active-toggle state (e.g. Checks button while on checks view): border+text `#1a56db`, bg `#eef2ff`.

## Status colors (used everywhere)
| Status | Pill bg | Pill text | Accent/left border | Row tint |
|---|---|---|---|---|
| PASS | #d1fae5 | #047857 | #059669 | white |
| WARN | #fef3c7 | #b45309 | #d97706 | #fffbeb |
| FAIL | #fee2e2 | #b91c1c | #dc2626 | #fef2f2 |
| SKIP / waiting | #f3f4f6 | #6b7280 | #d1d5db | white |
| INFO | — | #2563eb | — | — |

Status pills: mono 600 11px, padding 3px 10px, radius 20px (fully round). Scope-level pills: mono 600 10.5px, padding 2px 8px, radius 4.

## Screens / Views

### 1. Run overview (default)
- **Header**: "Run mig01" (20px 700, run name in mono) + subtitle "172.20.20.4 (MX) → 172.20.20.5 (ACX/EVO)" (13px `#6b7280`).
- **Summary cards** (flex row, gap 12): five equal cards (white, border `#e2e5ea`, radius 8, padding 14px 16px) — count in mono 700 26px colored per status (PASS `#059669`, WARN `#d97706`, FAIL `#dc2626`, SKIP `#6b7280`, INFO `#2563eb`), label 12px 500 `#6b7280`. FAIL card gets border `#fecaca` + ring `0 0 0 1px rgba(220,38,38,0.08)` when count > 0.
- **Pairing table** (white, border `#e2e5ea`, radius 8): grid columns `24px 1.3fr 1.3fr 90px 90px 110px 130px`, gap 8px, padding 13px 18px per row.
  - Header row: `#f9fafb` bg, 600 11px uppercase `#6b7280`: (chevron) / Old port / New port / Pre / Post / Rollback / Result (right-aligned).
  - Rows: ports in mono 13px `#111827` ("not paired" in `#9ca3af`); Pre/Post/Rollback cells "✓" `#059669` 600 or "—" `#c3c8cf`; Result = status pill ("PASS", "WARN 2", "FAIL 1", "waiting"). Row tinted per worst status (see table above). Hover: `filter brightness(0.985)`.
  - **Collapsible level 1** — clicking a row (only if it has evaluated scopes; "waiting" rows are inert) rotates the chevron (▶ → 90°, transition 0.15s) and reveals a panel (`#fbfcfd` bg, padding 14px 18px 16px 50px) listing scope cards.
  - **Scope card**: white, border `#e2e5ea` (FAIL: `#fecaca`), left border 3px accent color, radius 6. Header row (padding 10px 14px, hover `#f9fafb`): status pill, scope id mono 500 12.5px `#111827` (e.g. "Internet vlan113 et-0/0/11.113"), note 12px `#6b7280`, ▼ chevron (rotates 180° when open).
  - **Collapsible level 2** — expanding a scope shows the check table: grid `190px 1fr 120px 120px 70px`, gap 5px 14px, 12px text. Column headers 600 10px uppercase `#9ca3af`: Check / Message / Value / Baseline / Status. Check id mono `#111827`; message `#4b5563`; value mono, colored `#b91c1c` on FAIL, `#b45309` on WARN, else `#111827`; baseline mono `#6b7280`; status 600 colored per status.
- **Footer actions** (right-aligned): "Edit mapping" (secondary, opens screen 6), "Export JSON" (secondary), "Evaluate run" (primary green `#059669`).
- Default state in prototype: failed row `ge-0/0/5` expanded with its FAIL scope's checks open.

### 2. Snapshot evaluation (single snapshot, no baseline)
Entered by clicking a snapshot in the sidebar. Equivalent of `mig-validate evaluate --snapshot <file>`.
- Breadcrumb: "run mig01 / <file>" — run link `#1a56db` clickable back, file in mono `#374151`.
- Header: "Snapshot evaluation" (20px 700) + phase pill (phase badge colors above, pill-shaped) + "no baseline" pill (`#f3f4f6`/`#6b7280`).
- Metadata bar (white card, 12.5px, labels `#6b7280`, values mono `#111827`): Device, Platform, Taken, Collectors.
- Summary chips row: "N PASS / N WARN / N FAIL / N SKIP" pills (mono 600 12px, padding 4px 12px) + right note "standalone evaluation — comparison checks skipped" (12px `#9ca3af`).
- Scope list: same scope cards as run view, but check table has **no Baseline column**: grid `190px 1fr 140px 70px` (Check / Message / Value / Status).
- Footer: "Export JSON" (secondary) only. The prototype's "Evaluate vs baseline…" button is **dropped for v1** — run-level evaluation pairs baselines automatically; manual cross-snapshot comparison stays in the CLI (`evaluate --baseline`).

### 3. Registered checks
Entered via the Checks header button. Equivalent of `mig-validate checks`.
- Breadcrumb "run mig01 / registered checks"; header "Registered checks" + count subtitle.
- Table: grid `220px 110px 110px 1fr 90px`, gap 12px; header ID / Mode / Severity / Service types / Enabled (right). Rows 12.5px, padding 11px 18px: id mono `#111827`, mode mono `#6b7280`, severity 600 11.5px uppercase (CRITICAL `#b91c1c`, ADVISORY `#b45309`), types `#4b5563`, enabled "yes" `#059669` / "off" `#9ca3af`. Disabled rows at opacity 0.55.

### 4. New capture
Entered via New capture button. Mirrors `capture --run` flags. **`run.yml` is the single source of truth**: on a run with a defined mapping the form is pure selection — the prototype's free-text Device and Port + "Maps to (old NODE:PORT)" inputs are removed. A run without a mapping (sequential workflow) keeps free entry. The form **branches on whether the selected run has an `interface_mapping`**.
- Breadcrumb "run mig01 / new capture"; header "New capture".
- Two-column grid (gap 20):
  - **Target card**:
    - **Run** select at the top, defaulting to the run currently open; everything below re-derives when it changes.
    - **Device** select (mono 13px, border `#d1d5db`, radius 6, padding 9px 12px): the run's two devices, labeled "MX1-POP1 — 172.20.20.4 (old)" / "PTX1-POP1 — 172.20.20.5 (new)".
    - **Phase** as 3-segment toggle (pre/post/rollback; selected: 2px border `#1a56db`, bg `#eef2ff`, text 600 `#1a56db`). Selecting the old device preselects `pre`, the new device preselects `post`; all three stay clickable.
    - **Port** — *mapped run:* select listing the chosen device's ports from `interface_mapping` (new-device ports deduplicated — `ae0` appears once), plus an "all (whole device)" entry. Ports that already have a snapshot for the selected phase get a "✓ captured" suffix; choosing one relabels the submit button to "Re-capture". *Mapping-less run:* free-text input, placeholder/default "all".
    - **Maps to** — *mapped run only:* read-only row (bg `#f9fafb`, radius 6) showing the paired counterpart(s) from the mapping, e.g. "ge-0/0/3, ge-0/0/4, ge-0/0/5 → ae0". Hidden for "all" and on mapping-less runs.
    - Checkbox "Regenerate inventory from config (--parse-services)" (accent `#1a56db`).
  - **Profile & auth card**: profile select (shows current, ▼); read-only rows (bg `#f9fafb`, radius 6) for Auth ("ansible · ssh keys (2) · password fallback" — from `config/settings.yml`, see Backend changes; GUI never asks for passwords) and Collectors.
  - **Baseline notice** (post phase only): driven by `find_pre_baseline` for the selected port. Pre snapshot found: `#eef2ff` bg, border `#c7d2fe`, text `#4338ca` 12.5px, names the snapshot. Not found: WARN-toned variant (`#fffbeb` bg, border `#fde68a`, text `#b45309`): "no pre baseline recorded for ge-0/0/2 — comparison checks will SKIP".
  - Actions right-aligned: Cancel (secondary), "Start capture" (primary; "Re-capture" when overwriting).
- Field labels: 12.5px 600 `#374151`; flag hints in mono `#9ca3af`.

### 5. New run
Entered via the sidebar "+ New run" link. Creates `runs/<name>/` with its `run.yml` — a capability the CLI lacks entirely.
- Breadcrumb "new run"; header "New run". Stacked cards (white, border `#e2e5ea`, radius 8, padding 16–20px):
  - **Run name card**: one mono text input; the value becomes the folder name under `runs/`. Validation: `[a-z0-9_-]+`; rejected inline if `runs/<name>/` already exists.
  - **Devices card**: two fixed sub-forms side by side headed "Old device" / "New device" (role implied by position, never typed). Fields each: node name (mono, e.g. `MX1-POP1`), host (IP/hostname), platform select (`junos` / `junos-evo`; defaults old→`junos`, new→`junos-evo`). All fields required. **v1 is fixed at exactly one old + one new device** (matches `device_with_role` and the pairing logic).
  - **Port mapping card (optional)**: dynamic row list — old port input → new port input, ✕ per row, "+ add pairing" button. Nodes are implied by the devices card, so a row is just two ports. Ports stored raw as typed after trimming whitespace (e.g. `ge-0/0/2` — `normalize_port` is for snapshot *filenames* only, never for `run.yml` content); duplicate *old* port rejected inline; several old ports → one new port (the `ae0` case) explicitly allowed. **Zero rows is valid** and creates a mapping-less run.
  - Actions: Cancel (secondary), "Create run" (primary). On success `POST /api/runs`, then navigate to the new run's overview.
- Validation errors inline under the offending field: 12px `#b91c1c`.

### 6. Edit mapping
Entered via "Edit mapping" on the run overview footer. Reuses the mapping card from screen 5, pre-filled:
- Devices shown read-only at the top — immutable after creation.
- A pairing referenced by any capture record (either endpoint — a pre on its old port or a post/rollback on its new port) renders **locked**: no ✕, a lock glyph 🔒 (or "locked" text 11px `#9ca3af`) with tooltip "has snapshots".
- Unreferenced pairings keep their ✕; new rows added freely. Same validation as screen 5.
- Save → `PUT /api/runs/{run}/mapping` with the full desired list; the backend **re-validates the lock rule server-side** (never trust the GUI's graying-out), rewrites `run.yml`, returns the fresh manifest.
- Adding the first pairing to a mapping-less run converts it to a mapped run; the capture form's branch follows automatically.

## Interactions & Behavior
- Row/scope expand-collapse: instant reveal; chevron rotation animated 0.15s. Rows without snapshots are not clickable.
- Sidebar navigation is the master switch between views; the active item (run card or snapshot row) shows `#eef2ff` highlight. Breadcrumb "run mig01" always returns to run overview.
- **Capture progress (live, per-collector).** "Start capture" POSTs and navigates back to the run overview. The affected pairing row shows a "capturing…" pill (SKIP-gray colors, subtle pulse animation) in the phase column being captured; the row's note line renders live steps from polling `GET /api/captures/{id}` (~2s): `interfaces ✓ · bgp ✓ · ping (12)…`. Granularity is per collector; the ping probe batch is one coarse step. On completion the row re-renders from disk truth (✓ appears). Failed collectors → WARN-toned note listing them (`snapshot.capture.failed_collectors()`). Hard failure (SSH/auth/timeout) → note in FAIL red with the error message, phase cell stays "—".
- **Empty states.** Zero runs: sidebar shows "no runs yet"; main pane is a centered empty state with a "Create your first run" primary button → screen 5. Run with no captures: pairing table renders all rows "waiting". Mapping-less run: instead of the pairing table, a short notice ("no port mapping — captures are per-device") with the snapshot list below.
- Hover states: table rows `filter brightness(0.985)`; scope headers bg `#f9fafb`; sidebar rows bg `#f3f4f6`.
- Links: `#1a56db`, hover `#0e3fa9`.

## State Management
- `view`: run | snapshot | checks | capture | newrun | editmapping; `selectedSnapshot` file name; `activeCaptureId` while polling.
- `openRows` (per port key), `openScopes` (per `port|scopeId` key) — independent maps so multiple can stay open.
- Data fetching per view, backed by these FastAPI routes (thin wrappers over `migration_validator.api` and `migration_validator.runs`):
  - `GET /api/runs` — list run manifests (name, progress, device roles)
  - `GET /api/runs/{run}` — status rows (`_status_rows` logic in cli.py) + summary + snapshot list
  - `GET /api/runs/{run}/evaluation` — `plan_evaluations` + `api.evaluate` per pairing; returns `RunResult.to_dict()` per port
  - `GET /api/runs/{run}/snapshots/{file}/evaluation` — load snapshot, `api.evaluate(snapshot)` with no baseline
  - `GET /api/checks` — `api.list_checks()`
  - `POST /api/runs` — new `api.create_run(name, devices, mappings)` seam (thin over `RunManifest` + `save_manifest`); creates the run directory + `run.yml`
  - `PUT /api/runs/{run}/mapping` — new `api.update_mapping()` seam; enforces the pairing lock rule server-side
  - `POST /api/captures` — `api.capture(...)` as a background task (captures take minutes); 409 if a capture is already running for the same device; poll `GET /api/captures/{id}` → `{id, run, device, port, phase, state: running|done|failed, steps: [{collector, status: running|ok|error, message?}], error?}`. Capture records are **in-memory only** — a server restart loses the progress view but never data (the snapshot either landed in `run.yml`'s `captures:` or it didn't; the overview reloads truth from disk).
- Result JSON shapes are already defined in `migration_validator/models/result.py` (`RunResult`, `ScopeResult`, `CheckResult` `.to_dict()`); use them verbatim — statuses are `PASS/WARN/FAIL/SKIP/INFO`, severities `critical/advisory`.
- Note: result messages from the engine are Czech without diacritics (by design, greppable); the GUI chrome labels are English.
- **Concurrency assumption**: single user, localhost, last-writer-wins on `run.yml`. The GUI re-reads the manifest before every write; CLI and GUI running simultaneously is tolerated but not arbitrated. No locking.

## Backend Changes Required (land in the core app before/with the GUI)

1. **Auth: `config/settings.yml` replaces `~/.config/mig-validate/auth.yml`** — app-wide (CLI included), lives in the repo under `config/`. `auth.yml` support is removed outright (still in development, no migration shim). Shape:
   ```yaml
   connection:
     netconf_port: 830
     timeout: 30
     username: ansible
     ssh_key_paths:
       - ~/.ssh/id_ed25519
       - ~/.ssh/id_rsa
     # optional fallback:
     password_env: MIG_LAB_PASSWORD   # or password: ... (0600 enforced, as today)
   ```
   Every value has a default (the ones shown above), so a missing file still works on a standard setup. Paths go through `expanduser`. **Order:** keys tried in listed order, missing key files silently skipped; if none succeeds and a password is configured, password auth is tried last; if nothing works → connection error naming what was tried. `auth.py` is rewritten around this (settings dataclass gains `ssh_key_paths`, `netconf_port`).
2. **`capture_device()` gains an optional `on_progress` callback** — called before each collector starts and after it finishes (ok/error), plus one coarse "ping (N targets)" step before the probe batch. `api.capture(...)` passes it through; the FastAPI background task writes it into the in-memory capture record.
3. **New seam functions in `api.py`**: `create_run(...)` and `update_mapping(...)` as described in the routes above.

## Design Tokens
- **Fonts**: IBM Plex Sans (400/500/600/700) for UI; IBM Plex Mono (400/500/600) for anything machine-flavored: device names, ports, filenames, check ids, counts, pills. Google Fonts.
- **Grays**: page `#e8eaed`; shell `#f7f8fa`; card white; borders `#d3d7de` (shell), `#e2e5ea` (cards), `#f1f3f6` (row dividers), `#eef0f3` (sidebar divider); text `#111827` (primary), `#374151`, `#4b5563`, `#6b7280`, `#9ca3af`, `#c3c8cf` (disabled dashes).
- **Brand**: primary `#1a56db`; selected bg `#eef2ff`; link hover `#0e3fa9`; indigo notice `#4338ca` on `#eef2ff` border `#c7d2fe`.
- **Status**: see status color table above.
- **Radii**: shell 10, cards 8, buttons/inputs/scope cards 6, badges 3–4, pills 20.
- **Type scale**: h1 20px/700; body 13px; table text 12.5–13px; sub-lines 11–12px; column headers 10–11px 600 uppercase ls 0.06–0.08em; summary counts 26px mono 700.
- **Spacing**: main pane padding 24/28; card padding 14–20px; table cell padding ~13px 18px; stack gap 20; chip/button gaps 8–12.

## Assets
None — no images or icon fonts. Chevrons are text glyphs (▶ ▼), checkmarks are ✓ (U+2713), dashes — (U+2014). Fonts from Google Fonts.

## Files
- `Migration Dashboard.dc.html` — the interactive prototype (screens 1–4 as originally designed + sample data in the logic class; the sample data objects show the exact expected API payload shapes). Predates the final decisions — see "Prototype vs. this README" above.
- `GUI Mockups.dc.html` — the original 3 design directions (1a chosen); kept for reference only.

## Screenshots
- screenshots/01-run-overview.png — run overview with expanded FAIL row
- screenshots/02-snapshot-evaluation.png — single-snapshot evaluation (no baseline)
- screenshots/03-registered-checks.png — checks table
- screenshots/04-new-capture.png — capture form
