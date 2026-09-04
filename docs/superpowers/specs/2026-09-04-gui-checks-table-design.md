# GUI checks table — all checks listed, defaults shown, only overrides saved

Date: 2026-09-04
Status: approved (brainstorm with visual companion; mockups in `.superpowers/brainstorm/861106-1788515246/content/profile-editor.html`, option A)

Spec 4 of 4 from the 2026-09-04 GUI feature brainstorm. Depends on spec 3
(profile store, catalogue, preview route, editor page). It adds the checks
part of the editor.

## Goal

Users should be able to tune the `checks:` section of a profile from the
GUI: enable or disable a check, change its severity, adjust tolerances.
Every check is always visible with its default so "what will run" is one
table, and the file written contains only what differs from the defaults,
the same shape a hand-written profile has.

## 1. Table

- One row per catalogue check, grouped under headers by `group` (first
  required collector), in catalogue order.
- Columns: enable toggle · check id and title · severity select (the
  `Severity` enum values: `critical`, `advisory`) · option
  fields · reset link.
- Option fields are typed from the catalogue: `number` → numeric input,
  `boolean` → toggle. A check without options shows `bez voleb` in grey.
- Defaults render grey and italic. Helper text under numeric tolerances
  comes from the check's `title`/description in the registry (sign
  convention for `tolerance_percent` is explained there already).
- A row whose enable, severity or any option differs from the catalogue is
  an **override**: yellow tint, `●` mark after the id, and a `reset` link
  that returns all three parts to defaults.

## 2. Diff model

- Form state holds `catalogue` (from `/api/profiles/catalogue`) and
  `overrides` (the `checks` object of the document).
- Pure function `checksDocument(catalogue, formState) -> overrides` in
  `migration_validator/gui/static/profile_diff.js`:
  - `enabled` written only when false;
  - `severity` written only when different from `default_severity`;
  - an option written only when different from its default; an emptied
    numeric field means "reset that key", never `null`;
  - a check with no differences is absent from the result.
- Save sends the whole document (profile section from spec 3 plus these
  overrides) to `PUT /api/profiles/{name}`.
- Loading an existing profile whose `checks` contains a key the catalogue
  does not know (a removed check) shows it in a separate `neznamy check`
  group with only a `remove` link, so the file can be cleaned without a
  text editor. Unknown option keys on a known check are shown read-only
  in the same way.

## 3. YAML preview

- Right of the table a read-only panel shows the YAML that Save will write,
  regenerated after every edit through `POST /api/profiles/preview`
  (debounced 300 ms). Below it: `<n> overrides`.
- The preview is the exact file text, not a client-side rendering.

## 4. Validation

- No client-side clamping. On save, `load_profile` errors come back as 422
  and are shown under the Save button; when the message contains a check
  id or option key the error is also shown inline in that row.
- Severity select only offers valid values, so it cannot fail validation.

## 5. Interaction with runs

- Saving a profile changes nothing already captured. Evaluation reads the
  profile on request (spec 3), so the next load of a run overview reflects
  the change.

## Testing

- `tests/gui/test_profile_routes.py` (preview part): a document with no
  overrides yields YAML without a `checks` section; `enabled: false`
  alone writes only `enabled`; a severity equal to the default is not
  written; an option equal to its default is not written; a numeric
  option given as a string is rejected with a message naming the check
  and the key.
- `tests/js/profile_diff.test.js` (node:test, like `view.test.js`):
  `checksDocument` returns `{}` when nothing differs; writes only
  `enabled: false`; omits a severity equal to the default; omits an option
  equal to its default; an emptied numeric field drops the key; an unknown
  check in the loaded overrides is preserved until removed.
- Manual lab check: create a profile that disables `interface_optics_levels`
  and lowers `interface_traffic.tolerance_percent`, assign it to a run,
  reload the run overview and confirm the optics check is absent and the
  traffic tolerance in the result detail shows the new value.

## Out of scope

- The declarative `tests:` section (see the 2026-08-13 note).
- Per-run check overrides; overrides live only in profiles.
