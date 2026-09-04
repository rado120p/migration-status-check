# GUI run creation modes — single-device, two-device (migration), bulk placeholder

Date: 2026-09-04
Status: approved (brainstorm with visual companion; mockups in `.superpowers/brainstorm/861106-1788515246/content/new-run-form.html`, option A with the explanatory type cards from option B)

Spec 2 of 4 from the 2026-09-04 GUI feature brainstorm. Depends on the
permission seam from spec 1 (create run requires `operate`). Spec 3 reads
the `profile` field defined here.

## Goal

A run on a single device (pre/post snapshots around an upgrade or a
reconfiguration) today requires entering the same box twice as old and new
device. Introduce an explicit run kind chosen on the New run form, store it
in the manifest so the run view adapts, and reserve the shape a future bulk
feature will use.

## 1. Manifest

- New optional field `kind` with values `single` and `migration`.
  Missing on load → `migration`. `schema_version` stays 1. CLI-written
  manifests are unchanged and remain valid.
- New optional field `profile: <profile name> | null` (null or missing =
  server default). Written by the GUI create route; the CLI ignores it in
  this wave.
- New optional field `group: <string>` reserved for bulk: a future bulk
  action creates N single runs and stamps the same group on all of them.
  No third kind. Not written by anything in this wave; loader accepts it
  and `save_manifest` preserves it.
- `VALID_ROLES` gains `single`. A `single` run has exactly one device with
  role `single`; a `migration` run has exactly one `old` and one `new`
  (plus optional `l2-switch` as today).
- `RunManifest.kind` property returns the stored kind.

## 2. Pairing and manifest guards

- `find_pre_baseline`: the fallback to `device_with_role("old")` is
  skipped for `single` runs; the whole-box pre of the subject device is
  the baseline (already found by `_plan_same_device`). Net effect: a
  single run with pre and post yields exactly one evaluation, flagged
  `same_device=True`, no "chybi pre snimek stareho boxu" warning.
- `api.update_mapping` refuses a single run with
  `ValueError("run typu single nema interface mapping")`. GUI route maps
  it to 409 as today.
- `serializers.status_rows`: unchanged logic; a single run naturally
  produces one row per captured device with the endpoint under `old` (the
  serializer's existing else-branch). The GUI decides the column layout by
  `kind`, not by which side the endpoint is on.

## 3. Create run API

Request body of `POST /api/runs` changes to:

```json
{
  "name": "upgrade-ptx1-2026",
  "kind": "single",
  "profile": "core-only",
  "devices": [{"node": "PTX1-POP1", "host": "172.20.20.5", "platform": "junos-evo", "role": "single"}],
  "mappings": []
}
```

- `api.create_run(name, *, kind, devices, mappings=None, profile=None,
  run_root)` replaces the old/new keyword signature. Validation, in this
  order, each a `ValueError` → 409 in the route:
  - name regex as today;
  - `kind` in {single, migration};
  - single: exactly one device, role `single`, mappings empty;
  - migration: exactly one `old` and one `new` device, any number of
    mappings;
  - `profile` if given must exist in the profile store (spec 3; until
    spec 3 lands the route accepts only null).
- `GET /api/runs` adds `created` (manifest file mtime, ISO UTC) so the form
  can find the most recent run.
- Response: the run detail as today, plus `kind` and `profile`.
- `GET /api/runs` and `GET /api/runs/{run}` include `kind` and `profile`.
- Requires `operate`.

## 4. New run form

- One page. Top: three type cards (Single device / Two devices / Bulk).
  Bulk is rendered disabled with the note `bulk · pripravuje se` and cannot
  be selected. Default selection: the kind of the most recently created
  run in the list (by manifest mtime as returned by `GET /api/runs`),
  Two devices when there is none.
- Below: Run name and Profile picker (spec 3 fills the picker; until then
  it shows only "(default)" and is disabled).
- Then one device sub-form (single) or the two-device grid (migration).
  Switching from migration to single keeps the Old device values in the
  single sub-form; switching back keeps both sub-forms' values.
- The mapping card is shown only for migration.
- Validation and error display reuse `buildDeviceSubform`,
  `mappingDupErrors` and the field-error pattern.

## 5. Run view for a single run

- Header shows `single device` next to the run name.
- No `Edit mapping` action; the mapping section is not rendered.
- The results table collapses old/new columns to one `Device` column with
  pre / post / rollback flags.
- Capture form: device select has one entry; phases pre, post, rollback.
  Rollback pairs with the device's own pre as today.
- The same-device section from the 2026-09-01 redesign becomes the main
  results section for a single run (no separate "mapped" section).

## Error handling

- Loading a manifest with an unknown `kind` raises
  `ValueError("neznamy kind '<x>', ocekavano single nebo migration")`.
- A `single` manifest with two devices or a `migration` manifest without
  old/new is rejected on load with a message naming the run file.

## Testing

- `tests/runs/test_manifest.py`: round-trip with and without `kind`,
  `profile`, `group`; missing kind loads as migration; role `single`
  accepted; unknown kind rejected; single with two devices rejected.
- `tests/runs/test_pairing.py`: single run with pre+post → one evaluation,
  `same_device=True`, `reason is None`; single run with only post → one
  evaluation with a missing-baseline reason naming the device.
- `tests/gui/test_write_routes.py`: single with two devices → 409;
  migration with one device → 409; mapping PUT on a single run → 409;
  created run detail carries `kind`; create requires `operate`.
- `tests/gui/test_serializers.py`: single run rows.

## Out of scope

- Bulk creation UI and the batch API.
- CLI flag to create runs (the CLI still expects a hand-written run.yml).
