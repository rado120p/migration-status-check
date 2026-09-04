# GUI run list and archiving — topbar run combobox, prominent New run, archive, permission seam

Date: 2026-09-04
Status: approved (brainstorm with visual companion; mockups in `.superpowers/brainstorm/861106-1788515246/content/run-selector.html`, option A)

This is spec 1 of 4 from the 2026-09-04 GUI feature brainstorm. The other
three are: run creation modes, profile store and editor, checks table. They
are independent plans but share the decisions in "Cross-cutting decisions".

## Goal

With many runs the sidebar card list becomes unusable, the "+ New run" link
is easy to miss although the tool cannot be used without a run, and runs
can never be removed. Replace the sidebar list with a searchable run
combobox in the topbar, make New run a primary button, and add archiving of
runs behind a permission seam that a future role model can plug into.

## Cross-cutting decisions (shared by all four specs)

- **Future access model**: admin / operator / viewer. Viewer reads runs and
  results; operator creates runs and captures; admin additionally archives
  runs, purges the archive and edits shared profiles. Not built now; only
  the seam is.
- **Permission seam**: `migration_validator/gui/authz.py` with
  `Actor(role: str)`, `Permission` enum (`view`, `operate`, `admin`) and a
  FastAPI dependency factory `require(permission)`. The only actor provider
  today returns `Actor(role="admin")` for every request. Endpoints declare
  the permission they need. A viewer hitting an operate/admin endpoint gets
  403 with detail `nedostatecne opravneni: vyzaduje <permission>`.
- **Run kind**: manifests get `kind: single | migration` (spec 2).
- **Profile per run**: manifests get `profile: <name> | null` (spec 2 sets
  it, spec 3 reads it).

## 1. Run combobox in the topbar

- Sidebar "Runs" section and run cards are removed. The sidebar keeps only
  the Snapshots section of the selected run (plus the existing footer).
- Topbar order left to right: brand · **Run combobox** · Profile label ·
  Profiles/Checks button · **+ New run** (primary) · New capture.
- Closed state shows `Run: <name>` or `Run: —` with zero runs.
- Open state: a panel with a filter input (autofocused), the filtered list
  and a footer with `+ New run`. Rows show the run name and
  `<n> snapshots`. The active run is highlighted.
- Filtering is live and case-insensitive and matches the run name and any
  device node name of the run. The match function lives in `view.js` as a
  pure function `filterRuns(runs, query)` and is tested in
  `tests/js/view.test.js` (node:test, as the existing tests there).
- Keyboard: Up/Down move, Enter selects, Escape closes. Clicking outside
  closes.
- Data source: the existing `GET /api/runs` (name, devices, snapshots,
  mapped_ports). No new endpoint.
- Selecting a run does what clicking a sidebar card does today.

## 2. New run entry point

- `+ New run` becomes a primary button in the topbar, always visible.
- Empty state (zero runs): main area shows a one-paragraph hint in Czech
  without diacritics ("Zatim zadny run. Run je vstupni bod nastroje...") and
  the same button. The guide rail explains what a run is.

## 3. Archive run

- Run overview: a secondary `Archive run` button next to the run title.
  Click opens a confirm modal: run name, number of snapshots, and the
  sentence that the run is moved to the archive and disappears from the
  list. Buttons Cancel / Archive.
- API: `POST /api/runs/{run}/archive` → 200 `{"archived_to": "<dir name>"}`.
  Requires `admin`.
  - 404 when the run does not exist.
  - 409 `run ma bezici capture` when `CaptureManager` has a running task
    for that run. New method `CaptureManager.busy_run(run) -> bool`.
  - Run names are already restricted to `[a-z0-9_-]+`; the route rejects
    anything else with 404 before touching the filesystem.
- Filesystem: `run_root/.archive/<name>-<YYYYMMDDTHHMMSSZ>/` created with
  `Path.rename`. `.archive` is created on demand. `GET /api/runs` skips
  entries whose name starts with a dot, so the archive never lists as a run.
- Implementation lives in `migration_validator/api.py` as
  `archive_run(name, run_root) -> Path` so the CLI can reuse it; the GUI
  route is a thin wrapper as everywhere else in `app.py`.
- After archiving the GUI reloads the run list and selects the first
  remaining run or shows the empty state.

**Známé omezení:** kontrola `busy_run()` a následný `rename` nejsou atomické.
Souběžný zápis mezi nimi (start capture, nebo `PUT /api/runs/{run}/mapping`,
který nemá žádnou obdobnou ochranu) může znovu vytvořit `runs/<name>/run.yml`
a po přesunu tak zůstane "duch" runu. Riziko je v této vlně akceptované, GUI
má dnes jednoho operátora — pořádný zámek na úrovni runu patří do vlny s
rolovým modelem.

## 4. Purge (CLI only)

- New subcommand `mig-validate run purge [--older-than DAYS] [--dry-run]
  [--run-root DIR]`. Lists archive entries (name, archived timestamp,
  snapshot count) and deletes those older than the threshold. Without
  `--older-than` it only lists. Deletion asks for a `y` confirmation unless
  `--yes`.
- No GUI purge in this wave; it is an admin action and the GUI has no
  admin identity yet.

## Error handling

- Archive failures on rename (permissions, cross-device) return 500 with
  the OS message; nothing is partially moved because rename is atomic on
  the same filesystem. `.archive` is always under `run_root`, so same
  filesystem is guaranteed.
- The combobox shows `(nelze nacist runy)` if `GET /api/runs` fails, with
  the existing error toast pattern.

## Testing

- `tests/gui/test_write_routes.py`: archive moves the folder and the
  archived dir contains `run.yml`; second archive call is 404; archive with
  a running capture (fake task in the manager) is 409; the archived run no
  longer appears in `GET /api/runs`; a `.archive` entry is never listed.
- `tests/gui/test_authz.py`: overriding the actor provider with a viewer
  makes archive return 403 and `GET /api/runs` still 200; operator can
  create a run but not archive.
- `tests/test_cli.py` (existing CLI test module): `run purge` dry-run lists
  entries and deletes nothing; `--older-than 0 --yes` removes them.
- `api.archive_run` unit test with `tmp_path` in `tests/test_api_runs.py`.
- `tests/js/view.test.js`: `filterRuns` matches name and node case-insensitively,
  empty query returns all, no match returns an empty list.

## Out of scope

- Restore from archive in the GUI (CLI users can move the folder back).
- Renaming runs.
- Any real authentication or role storage.
