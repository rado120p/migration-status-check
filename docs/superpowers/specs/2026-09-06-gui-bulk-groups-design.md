# GUI bulk single-device runs — groups, batch capture, group view

Date: 2026-09-06
Status: approved (brainstorm with visual companion; mockups in
`.superpowers/brainstorm/14092-1788695373/content/group-view.html` option A and
`bulk-form.html` option B)

Fills the `Bulk` placeholder reserved by the 2026-09-04 run-kinds spec. Depends
on the permission seam (`authz.py`), the run kinds (`kind: single`, manifest
`group` field) and the profile store.

## Goal

A maintenance window touches tens of boxes at once. Today each needs its own
run, its own pre capture click and its own post capture click, and the
verdicts live on N separate pages. Bulk creates N single-device runs from one
device list, captures a phase on all of them with a bounded pool, and shows
one table with the state and verdict of every box.

## 1. Model and naming

- No new kind. A bulk member is a `single` run with `group: <name>` set. Its
  name is `<group>-<node.lower()>`; node names are already uppercase-friendly
  identifiers, lowering keeps `_RUN_NAME_RE` (`[a-z0-9_-]+`) untouched and the
  run tree flat. The `group` field is the only tie between members.
- Group name follows `_RUN_NAME_RE`. Same regex, same error message shape.
- A group exists when at least one non-archived run carries it. No group file
  on disk. Archiving all members makes the group disappear.
- `GET /api/runs` adds `group` per run (null when unset).
- `ConnectionSettings` gains `capture_pool: int = 10` read from settings.yml
  key `capture_pool`. It bounds concurrent captures across the whole server
  (single-run captures count too), not per group. Value below 1 is a settings
  `ValueError` like the other keys.

## 2. CaptureManager: queued state and pool

- `CaptureTask.state` gains `queued`. `start()` still refuses a device that is
  `queued` or `running` (`DeviceBusy`), so the busy check covers both states.
- The manager takes `pool: int` in its constructor and holds a
  `threading.Semaphore(pool)`. The worker acquires it before calling `fn` and
  flips the task to `running`; tasks wait as `queued`. Release in `finally`.
- `busy_run` counts `queued` and `running`.
- `to_dict` is unchanged apart from the new state value.

## 3. API

All under `migration_validator/gui/app.py` (or a `group_routes.py` router
built like `profile_routes.py` if `app.py` would exceed ~500 lines) with pure
logic in `migration_validator/api.py` and `migration_validator/gui/groups.py`.

### `GET /api/groups` (view)

`{"groups": [{"name", "runs": N, "profile", "created"}]}`. `created` = oldest
member manifest mtime. Sorted by name.

### `POST /api/groups` (operate, 201)

Body:

```json
{
  "group": "pop1-upgrade-2026-09",
  "profile": "core-only",
  "devices": [{"node": "PTX1-POP1", "host": "172.20.20.5", "platform": "junos-evo"}],
  "capture_pre": true
}
```

- `api.create_group(group, devices, *, profile, run_root)` validates in this
  order and raises `GroupError(rows: list[tuple[int | None, str]])`, mapped
  to 409 with `detail: {"message": str, "rows": [{"index", "message"}]}`.
  `index` is null for group-level errors (name regex, group already exists,
  profile missing, empty device list) and the device position otherwise
  (duplicate node in the list, derived run name already exists, empty
  node/host/platform, platform not known).
- Nothing is written until every device passed. Then N manifests are saved
  (kind single, one device with role single, profile, group). A save failure
  midway raises and the route reports it as 500 with the run names already
  written, so the operator can see the partial state; this is the one
  non-atomic edge and is documented rather than rolled back.
- When `capture_pre` is true the route starts a group capture (§ below) for
  phase `pre` after creating. Response is the group detail plus `"batch"`
  (null when no capture was requested).

### `POST /api/groups/{group}/devices` (operate, 201)

Body `{"devices": [...]}`. Adds members to an existing group with the
group's profile. Same validation and atomicity as create; 404 when the group
has no members. Returns the group detail.

### `POST /api/groups/{group}/captures` (operate, 202)

Body `{"phase": "pre" | "post" | "rollback"}`. For every member, in run
name order, builds the same `capture_into_run` closure the single capture
route uses and calls `manager.start`. A `DeviceBusy` for one member is
recorded, not raised. Response:

```json
{"batch": "<12 hex>", "tasks": [{"run", "device", "task_id", "error"}]}
```

`task_id` is null and `error` carries the message for a busy device. The
batch id is not persisted; the client polls each `task_id` through the
existing `GET /api/captures/{id}`. Settings load failure is 503 as today.

### `GET /api/groups/{group}` (view)

```json
{
  "name": "pop1-upgrade-2026-09",
  "profile": "core-only",
  "created": "2026-09-06T08:12:00Z",
  "runs": [
    {
      "run": "pop1-upgrade-2026-09-ptx1-pop1",
      "node": "PTX1-POP1", "host": "172.20.20.5", "platform": "junos-evo",
      "phases": {"pre": "2026-09-06T08:14:02Z", "post": null, "rollback": null},
      "active_task": {"id": "...", "phase": "post", "state": "queued"} | null,
      "verdict": "PASS" | "RECV" | "WARN" | "FAIL" | null,
      "services": {"pass": 41, "recv": 0, "warn": 2, "fail": 3, "skip": 0, "info": 0},
      "checks": {...same keys...},
      "error": null | "<message>"
    }
  ],
  "verdicts": {"PASS": 3, "WARN": 1, "FAIL": 1, "none": 1}
}
```

- `phases.<phase>` is the `taken` of the latest whole-box capture of that
  phase, null when absent.
- `verdict` is the worst `Status` across the run's evaluations (same
  `plan_evaluations` + `api.evaluate` call as the per-run evaluation route),
  null when there is no post or rollback snapshot. `services` / `checks`
  are the same counts the per-run counts strip shows, summed over
  evaluations.
- `error` is set and the counts are zero when the manifest fails to load or
  snapshot files are missing (the per-run 422 text). The rest of the group
  still renders. 404 when the group has no members.
- Evaluation cache: `gui/groups.py` keeps a dict keyed by
  `(run, manifest_mtime_ns, profile_mtime_ns)` → `(verdict, services,
  checks)`. Profile mtime is the store file for a named profile, the server
  default path otherwise (0 when there is none). Entries for runs no longer
  in the group are dropped on each summary build. No TTL.

### `POST /api/groups/{group}/archive` (admin)

Refuses with 409 `skupina '<g>' ma bezici capture` while any member is
queued or running. Otherwise archives every member through
`api.archive_run` in name order. Returns `{"archived": [<archive dir names>]}`.
A failure midway reports the names archived so far in the 500 detail.

### Existing endpoints

- `GET /api/runs/{run}` detail adds `group`.
- `POST /api/runs`, `PUT .../mapping`, `POST .../archive`, `POST /api/captures`
  are unchanged. A member behaves as any single run: per-device post or
  rollback capture from its own run view works as today.

## 4. New run form: Bulk

- The `Bulk` type card becomes selectable; the `bulk · pripravuje se` note
  is removed. Default kind selection logic is unchanged (bulk is never the
  default).
- Body: Group name, Profile picker (same as the other kinds), then a device
  table with columns Node, Host, Platform (select), remove button, and a
  `+ add device` button that appends an empty row. Starts with one empty row.
  Each row is `buildDeviceSubform` rendered inline, so field errors and
  platform choices are the same as the single form.
- Below: `[x] start pre capture on all after creating` (default checked),
  `Create N runs` (N = rows without errors; disabled while any row has an
  error or there are zero rows), Cancel.
- Client validation per row: node, host and platform required; duplicate
  node marked on the later row. Server row errors from the 409 are placed
  on the matching row by `index`; group-level messages go to the form's
  submit error.
- Success navigates to the group view; when `capture_pre` was set the
  returned batch is handed to the view so polling starts at once.

## 5. Group view

New `state.view = "group"` with `state.group = <name>`.

- **Header:** group name, `group · N runs` tag, profile as a link to the
  profile editor (or `(default)`), created time.
- **Action bar:** `Capture pre on all` (primary), `Capture post on all`,
  `Capture rollback on all`, `+ Add devices`, `Archive group` (admin
  permission seam, confirm dialog like Archive run). While a batch is active
  the bar shows `<phase> capture running · k/N done` and a thin progress
  bar; capture buttons are disabled until it ends.
- **Counts strip:** `verdicts` from the summary: PASS / RECV / WARN / FAIL
  pills plus `no post N`.
- **Table** (mockup A): Device, Host, Platform, Pre, Post, Rollback,
  Verdict, Services, Checks, actions. Phase cells show `HH:MM` of the
  capture, `—` when absent, `queued` / `running…` when a task is active,
  `error` (red, message in `title`) when the last task for that phase in
  this session failed. Rows carry a left rule in the verdict colour and
  sort worst-first by default (FAIL, WARN, RECV, PASS, then no verdict,
  then error rows last); clicking a header sorts by that column.
- **Row actions:** `open ›` selects the run (existing single run view);
  a small `capture ▾` menu with pre / post / rollback starts a single
  capture for that member through `POST /api/captures` and polls it like
  the batch does. This is the per-device re-capture path from inside the
  group.
- **Polling:** every task id from a batch or a row action is polled every
  2 s through `GET /api/captures/{id}`. When all reach done or failed the
  summary is refetched once. Leaving the view stops polling; returning
  refetches the summary (server state is the truth, in-flight tasks are
  visible through `active_task`).
- **Add devices** opens a dialog with the same row editor minus profile,
  posts to `/devices`, and refetches on success.
- **Navigation:** run combobox lists group rows first (`<group>` with a
  `group · N` tag), members indented under their group, ungrouped runs
  after. Filter matches group names. Selecting a group row opens the group
  view; selecting a member opens its run and the combobox reads
  `<group> › <run>` with a `‹ group` link back. A grouped single run's
  header shows the group name as a link next to the `single device` tag.
- **Guide text** entries (Czech, diacritics) for the group view and the
  bulk form.

## Error handling

- All new `ValueError`s from `api` map to 409; missing group to 404; settings
  errors to 503; permission to 403 through `require`.
- A member whose run.yml fails to load is shown as an error row with the
  loader message; it never hides the rest of the group.
- Batch start with every device busy still returns 202 with all `error`
  fields set; the view shows the messages per row.

## Testing

- `tests/test_api.py` (or `tests/api/`): `create_group` naming, ordering of
  validation, each row-level error with its index, atomicity (no directory
  written on error), `add_devices` on existing group, `archive_group`
  ordering.
- `tests/gui/test_captures.py`: `queued` state, pool of 2 with three fake
  captures blocked on a `threading.Event` (third stays queued until one
  releases), `busy_run` counts queued, `DeviceBusy` for a queued device.
- `tests/gui/test_group_routes.py`: every endpoint incl. 403 for viewer,
  409 with row detail, 404 unknown group, batch with one busy device, archive
  refused while a task is queued, summary rows with verdict / null verdict /
  error row, cache hit not re-evaluating (spy on `api.evaluate`).
- `tests/gui/test_serializers.py`: worst-status verdict and counts sum.
- `tests/test_auth.py`: `capture_pool` parsing and the below-1 error.
- Lab pass for the JS: create a 3-box group in the lab, watch queued →
  running with pool 2 set in settings.yml, re-capture one box from the row
  menu, archive the group.

## Out of scope

- Per-device profile inside a group, moving a run between groups, CLI bulk
  creation, group-level report export, persisting batch ids across server
  restarts.
