# GUI migration results — services grouped by port pairing

Date: 2026-09-08
Status: implementation pending. The user accepted the mockup's visual direction
and requested this specification for a future implementing agent. Implementation
details below resolve the remaining behavior; they are not a claim that the user
separately reviewed every technical decision.

## Goal and user intent

In a two-device migration run, services from additional port captures currently
join one flat results table. Operators cannot easily see which services belong
to which old → new port pairing. Make the pairing the visible parent of its
captures, service results, and unmatched-service findings. Add a service-type
filter to browse the paired results.

The user's references to "new" or "recently migrated" services described this
navigation problem. They explicitly did **not** request recency highlighting,
automatic focus, sorting by capture time, change-since-last-capture detection,
or capture history. Do not implement those features as part of this change.

## 1. References and boundaries

Visual references, relative to this specification:

- [Interactive mockup](../../../design_handoff_migration_gui/port-pairing-results-mockup.html)
- [Full-page preview](../../../design_handoff_migration_gui/screenshots/05-port-pairing-results.png)

The mockup demonstrates the layout and interactions with illustrative data. Use
the current application components and live API payloads for implementation;
do not copy its synthetic checks, hard-coded health messages, disabled actions,
fixed counts, or sample service names into the app. This specification wins if
the mockup simplifies a case covered below.

In scope:

- Grouped results on the overview of `kind: migration` runs with port mappings.
- Pairing headers with capture flags, counts, warnings, and expansion controls.
- Existing service check details nested within the appropriate pairing.
- Temporary service-type filtering of the paired service tables.
- Explicit handling of missing baselines and whole-device baseline fallback.
- Preservation of existing capture progress, auxiliary results, and controls.

Out of scope:

- Changing service matching, check execution, collector behavior, or snapshots.
- Correcting backend scope ownership for whole-device baseline fallback.
- Persisting GUI filter choices in profiles or run manifests.
- New evaluation-time filter API parameters or CLI options.
- Applying the new filter to single-device, snapshot, or bulk-group screens.
- Redesigning rollback, same-device, and unmapped evaluation internals.
- Status filters, service search, a frontend framework migration, or capture history.

The work is primarily in the existing plain JavaScript and CSS frontend. No new
dependencies, database, API route, or snapshot/manifest schema change is required.

## 2. Current implementation and available data

Repository paths in this section are relative to the repository root; use the
named symbols rather than relying on line numbers remaining stable.

| Location | Relevant behavior |
|---|---|
| `migration_validator/runs/pairing.py` — `plan_evaluations`, `_plan_post` | A mapped post capture produces one evaluation per mapping. Several old ports can share one new LAG and post snapshot. Each evaluation carries `step`. |
| `migration_validator/gui/app.py` — `run_evaluation` | `GET /api/runs/{run}/evaluation` returns `subject`, `baseline`, `warning`, `step`, `same_device`, and `result` for every evaluation. |
| `migration_validator/gui/serializers.py` — `status_rows`, `snapshot_list` | Run detail supplies pairing rows, capture flags, and snapshot records with `file`, `device`, `port`, `phase`, and `taken`. |
| `migration_validator/gui/static/app.js` — `collectServiceEntries`, `renderRunView` | Services from multiple evaluations are flattened into one table; unmatched and unassigned entries are aggregated separately. |
| `migration_validator/gui/static/app.js` — `buildPairingTable`, `buildFlagCell`, `rowMatchesCapture` | Existing capture presentation and task association to reuse. |
| `migration_validator/gui/static/app.js` — `buildResultsTable`, `buildDetailPanel`, `buildSameDeviceSection` | Existing table, detailed checks, and separate same-device results. |
| `migration_validator/gui/static/view.js` — `buildView`, `countStatuses` | Pure scope presentation and status counting. Preserve existing check-message semantics. |
| `migration_validator/engine.py` — `evaluate_snapshots` | Already supports profile-based `service_types`. Device and Layer1 scopes survive that filter; unmatched findings remain visible. |
| `migration_validator/profiles/catalogue.py` | Service-type catalogue exposed by `GET /api/profiles/catalogue`. |

Relevant response shape (fields unrelated to this feature omitted):

```json
{
  "evaluations": [{
    "subject": "post-ae0.json",
    "baseline": "pre-ge4.json",
    "warning": null,
    "step": {
      "old": {"node": "MX1", "port": "ge-0/0/4"},
      "new": {"node": "PTX1", "port": "ae0"}
    },
    "same_device": false,
    "result": {
      "scopes": [],
      "summary": {},
      "unmatched": {"baseline": [], "subject": []},
      "unassigned": {},
      "excluded_services": [],
      "filtered": {"service_types": ["Internet"], "scopes_shown": 0, "scopes_total": 0}
    }
  }]
}
```

`filtered` and `excluded_services` are optional. Their absence is not an error.
`ScopeResult` currently has no `kind` field. Classify its raw payload centrally:

- Device context: `scope_id === "device"`.
- Physical-port context: raw `key.service_type` or `identity.service_type` is
  `"Layer1"` (the engine uses this marker); `l1:` is the existing scope-id prefix.
- Other scopes are service results, including unrecognized or missing type
  values. Keep an `Unknown` type bucket instead of silently dropping them.

Use raw `identity.service_type`, falling back to `key.service_type`, for filtering.
Do not filter on `buildView().service_type`, which may append `" (L2 cast)"`.
Test the classification against actual serialized results. Do not infer a
physical port's ownership from a service description or an interface suffix.

## 3. Pairing ownership and derived view model

Build a pure grouping helper from the run detail and evaluation response before
rendering any DOM. Put it in a small frontend module, for example
`static/run_results.js`, and expose it to `node:test` in the same style as
`view.js`. A separate module avoids mixing run navigation with CLI-equivalent
check formatting. Load it before `app.js` if introduced.

### 3.1 Stable identity

Identify a pairing by the tuple:

```text
[runName, old.node, old.port, new.node, new.port]
```

Use an unambiguous encoding such as `JSON.stringify(tuple)`. Never group only by
the new port, snapshot filename, service description, scope id, or row index.

Service expansion keys must additionally include evaluation subject, baseline,
and scope id. Include a section discriminator for fallback and same-device
results. The current subject/baseline/scope-only key can collide when several
evaluations reuse the same snapshots.

### 3.2 Partitioning

1. Seed groups from run-detail rows having both `old` and `new` endpoints, in
   their existing manifest order. A pairing remains visible before evaluation.
2. Attach an evaluation with a non-null `step` and `same_device !== true` only
   to the group whose complete endpoints match the step.
3. Use that evaluation's scopes and unmatched entries as the group's data.
   The backend has already selected services through the baseline matcher.
   Do not re-match by parsing displayed interface names.
4. Preserve evaluation boundaries if an unexpected response contains multiple
   evaluations for one pairing. Show labelled evaluation subgroups; do not
   concatenate silently or overwrite an earlier evaluation.
5. If a step is absent from the run detail (for example during a refresh race),
   append an explicitly labelled evaluation group using its actual step, with
   a metadata-mismatch notice. Preserve its results and allow a normal reload.

Partition the remaining evaluations separately:

| Evaluation | Presentation |
|---|---|
| `same_device: true` in a migration run | Existing `Same device — pre vs post` section. |
| `step: null`, subject record phase `rollback` | Separate `Rollback — original device` results labelled by snapshot/device/port. Do not present them as checks on the new port. |
| Other `step: null` | `Other evaluations` with subject/baseline labels; whole-device and unmapped evaluations remain accessible. |
| Snapshot metadata cannot be resolved | Preserve in `Other evaluations`, identify by filename, and show metadata unavailable. Never parse a snapshot filename to invent identity. |

Each evaluation's main results and unmatched entries must have one presentation
location. Do not leave a duplicate flat table or duplicate unmatched list below
the new pairing groups.

### 3.3 Suggested group model

Names are illustrative; the separation of responsibilities is required:

```text
group:
  key, old, new, captureRow, sharedDestination
  evaluations[]:
    key, subjectRecord, baselineRecord, warning, ownershipNotice
    serviceEntries[], infrastructureEntries[]
    unmatchedBaseline[], unmatchedSubject[], excludedCount
  unfilteredServiceCounts, visibleServiceCounts
  visibleServiceEntries, relatedContextEntries
```

Keep the API payload immutable. Compute groups and filtered views from it so
clearing a filter restores all returned results without a refetch.

## 4. Run overview layout and interactions

Preserve the current shell, run/profile header, sidebar, guide placement, and
application actions. Reuse current spacing, fonts, colors, and status styles.
Do not add a design-preview banner to the production GUI.

For mapped migration runs, replace the separate mapping capture table and flat
mapped-results table with a `Port pairings` section. Retain whole-device capture
rows in a separate `Other captures` area when present. Keep existing auxiliary
evaluation sections below the paired results.

Each pairing header contains:

- Expand/collapse control and old → new port labels. Device names remain clear
  from the run header; expose full node:port identities in accessible labels.
- Pre, post, and rollback capture flags using the existing capture semantics.
- `Shared destination` when another pairing has the same new node and port.
- Service-result count and nonzero status counts (`PASS`, `RECV`, `WARN`,
  `FAIL`, `SKIP`, `INFO`), plus a separate unmatched count when nonzero.
- Missing-baseline, fallback, infrastructure failure, and task notices that
  remain identifiable when the group is collapsed or filtered.

Header service-status badges describe visible service rows. While filtering,
label them as visible results and show `X / Y service results`; never imply
that a filtered PASS badge is the verdict for the whole pairing. An unfiltered
attention indicator must remain when hidden services contain WARN/FAIL, when
infrastructure needs attention, or when unmatched findings exist.

Expanded content, in order:

1. Evaluation warning/fallback notice and subject/baseline snapshot references.
2. Physical-port/device context, with expandable existing check details.
3. The service table: status, service description, type, old/new interfaces,
   routing instance, finding, and expansion control. RI may be a second line
   under the description as in the mockup. Combining old/new columns into one
   is acceptable if all interface information stays accessible.
4. Unmatched baseline and subject services belonging to this evaluation, with
   their type and actual backend reason.
5. A compact `N other services on <new port> outside this comparison` note when
   `excluded_services` is nonempty. These are not evaluated rows and must not
   be inserted into the table or counted as missing services.

Service expansion reuses `buildDetailPanel` and `MigView.buildView`, including
IPv4/IPv6 sections, grouped checks, L2/L3 links, baseline deltas, deactivation,
and the current unchanged/new-since-baseline check annotations. Excluding
recency features from this project does not remove existing check annotations.

Render physical-port facts only when supplied by evaluated scopes. A capture
checkmark means a snapshot exists; it does not prove link/optics health. Missing
infrastructure results must not become the mockup's synthetic "Link up" text.

Initial state on entering a run: evaluated pairings expanded, pending pairings
collapsed, service details collapsed. Provide `Expand all` and `Collapse all`
for pairing groups; these do not alter individual service expansion choices.
Preserve explicit open/closed choices and the filter through rerenders,
evaluation refresh, and capture polling for the current run. Reset on changing
run; do not store in the manifest or profile. Do not scroll, focus, highlight,
or reorder pairings on capture completion.

Use native `details/summary` or accessible buttons with `aria-expanded`. Support
keyboard activation and visible focus. Keep focus on a filter control after
rerender; retain the operated control after expansion. Escape data using the
existing DOM text helpers, not HTML interpolation of service descriptions.
Tables may scroll horizontally at narrow widths; do not clip port names or
detach a table visually from its pairing.

## 5. Service-type filter

This is a **display filter** over already returned paired service results.
The backend's existing profile filter determines which checks were evaluated;
this GUI control only narrows the view. Selecting a type must not trigger
capture, evaluation, profile writes, or run-manifest writes.

Use single-select chips as in the mockup: `All types`, followed by the canonical
types Internet, IPVPN, E-Line, E-LAN, Core. Reuse the profile catalogue for the
supported set; union it with types actually present in paired results so
unrecognized types remain available. Catalogue loading failure must not block
rendering/filtering; derive available choices from the returned data. Disable
zero-count type choices, except that a currently selected zero-count type
remains selected after a refresh and shows its empty state.

Rules:

- Default `All types`; selecting another chip replaces the selection. Selecting
  `All types` restores all paired service results.
- Chip counts are computed from unfiltered paired service rows returned by the
  evaluation response, not inventory, unmatched entries, or infrastructure.
- Label scope explicitly: `Filter paired service results`. Same-device,
  rollback, other evaluations, and their findings remain unaffected.
- Keep every pairing header visible. If a filter matches no services in an
  evaluated pairing, show `No <type> services in this pairing` inside it.
- Keep infrastructure, unmatched entries, excluded-service counts, capture
  issues, and missing-baseline/fallback notices independent of the selection.
- Show `X of Y service results shown` for the paired service tables.
- Show the effective profile restriction if `result.filtered.service_types`
  exists, including an empty list. `All types` means all results returned under
  that profile, not services whose checks were excluded by the profile. Keep
  the existing profile link as the route to changing evaluation configuration.

Preserve linked L2/L3 context: select scopes by raw type, then retain linked
partners present in the same evaluation using `link.peer_scope_id` and
`link.peers[].scope_id`, in either direction. Do not synthesize missing partners
or pull them from another pairing. Keep backend relative order. A partner
whose own type does not match is marked `Linked context`; report it separately
as `+ N linked context rows` so type counts remain truthful. If a link points to
a partner absent from the returned result, do not claim that its block is
displayed above/below; indicate that related context is unavailable in this
evaluation. Reuse normal link rendering when all partners are visible.

## 6. Counts and findings

Keep two distinct levels of summary:

- The top overview summary remains unfiltered and explicitly labelled
  `Full run (current profile)`. Preserve existing check totals, matched and
  unmatched totals, and `pass_unchanged` annotations. Its evaluation population
  remains the existing main run population (non-same-device evaluations for
  migration runs); auxiliary same-device comparisons stay separate.
- Pairing badges and the filter's shown count describe the paired service
  results visible in this view. All six status values remain supported.

For the mapped migration view, exclude Layer1/device scopes from the top
service-result count and pairing service counts; their checks still contribute
to check totals. Do not change single-device or bulk-group counting in this
feature. Label counted units `service results` rather than promising unique
business services: one returned service scope is one result, L2/L3 parts can
be separate scopes, and the same service may appear in distinct evaluations.
Do not globally deduplicate by description or scope id.

`summary.scopes_matched` is an engine matching count before the display filter
(and can differ from profile-filtered checked rows). Do not reuse it as the
number of displayed/evaluated services. Unmatched counts describe warnings,
not additional successful or failed checked service rows.

Unassigned BGP peers, static routes, and BFD sessions are subject-snapshot
findings, not proven pairing-owned services. Preserve them under a separate
`Unassigned — subject snapshot` section grouped by subject filename and labelled
with available device/port metadata. When several evaluations of the same
subject return identical unassigned data, render that payload once. Do not
deduplicate across different snapshots. If payloads unexpectedly differ for
the same subject, retain labelled per-evaluation lists instead of discarding
differences. A service-type filter does not hide this section.

## 7. Baselines, pending states, and capture progress

### 7.1 Pending and error states

| Condition | Required behavior |
|---|---|
| Pairing has no captures | Show the pairing and absent flags; neutral `Awaiting captures`, no PASS/FAIL. |
| Old-port pre exists, no post evaluation | `Awaiting post`; expandable explanation. No fabricated services. |
| Post exists, baseline absent | Show `evaluation.warning` and `No baseline — service ownership unverified`. Retain returned standalone results, labelled as such, without claiming a successful comparison. |
| Evaluation has zero service scopes | Show a zero-result explanation; preserve infrastructure and findings. Distinguish from pending capture. |
| Filter hides every service | Filter-empty message, not `Awaiting post` or `All checks passed`. |
| API evaluation request fails | Keep capture/pairing context where available and show the existing error; do not render a successful empty summary. |

### 7.2 Whole-device baseline limitation

Current `_plan_post` can use a whole-device pre snapshot when no old-port pre
exists. `evaluate_snapshots(step=...)` does not narrow that baseline to
`step.old.port`. During review, a synthetic baseline with service A on old port
4 and B on old port 5, plus a shared destination containing both, returned A
and B for both mapping evaluations. A group heading alone cannot fix this.

Detect fallback by resolving `evaluation.baseline` against the run's snapshot
records and checking `port === null` on a mapped evaluation. Mark its header
`Whole-device baseline` and display:

> This comparison uses a whole-device baseline. Its services may span several
> old ports and are not guaranteed to belong only to this pairing.

Keep the supplied result in its comparison group, with unverified-ownership
labelling, and count it as evaluation rows. Do not silently reassign services,
deduplicate them across pairings, or claim port-specific ownership. Missing
baseline metadata likewise produces an ownership-unavailable notice.

Exact capture flags must stay exact: a whole-device fallback does not mean an
old-port pre capture exists. Explain the actual baseline independently of the
pre flag. A backend scope-narrowing fix is a separate follow-up, not a hidden
requirement for shipping this GUI change.

### 7.3 Capture progress and compatibility

Reuse existing task matching by run, node, and port. A capture on a shared new
LAG relates to every pairing with that new endpoint, so all corresponding
headers show its progress. Preserve phase-specific progress, collector errors,
failed-task messages, completed captures with warnings, and standalone progress
for captures that have no pairing row. Keep progress/error indicators visible
when groups are collapsed. Preserve queued-state behavior where supported by
the current capture API; do not change the capture state machine.

Retain current buttons and permissions for New capture, Evaluate run, Export
JSON, Edit mapping, Archive run, profile navigation, and snapshot navigation.
The mockup's disabled placeholders do not replace these controls. Export JSON
continues exporting the complete cached evaluation response under the effective
profile; the display filter does not silently change its contents. Clarify its
full-result scope when a type filter is active.

For `kind: single`, mapping-less migrations, individual snapshot evaluations,
and bulk-group screens, preserve their current rendering and operation. In a
mapped migration that also contains whole-device or same-device evaluations,
preserve those results according to section 3.2.

## 8. Implementation outline

1. Add pure grouping/classification/filter/count helpers and fixtures covering
   separate and shared destination ports. Keep API objects immutable.
2. Build pairing headers/panels using existing capture, service detail, and
   unmatched renderers. Add pairing-scoped expansion state.
3. Integrate the mapped-migration rendering branch, preserving auxiliary
   evaluations and subject-scoped unassigned findings.
4. Add filter chips, profile-restriction indication, filtered counts, and
   accessible interactions. Preserve filter state across current-run reloads.
5. Refine CSS against the mockup and validate the regression matrix below.

Expected production touchpoints:

- `migration_validator/gui/static/app.js`
- `migration_validator/gui/static/style.css`
- A small pure helper module such as `migration_validator/gui/static/run_results.js`
- `migration_validator/gui/static/index.html` if a helper script is introduced
- `migration_validator/gui/static/view.js` only if needed for context-aware
  link text; preserve Python/JavaScript check-presentation parity
- Focused JavaScript tests and existing GUI/API regression tests

Do not broadly refactor the app, change backend matching, or update unrelated
checks while implementing this spec. Inspect the worktree first and preserve
other ongoing changes. The original estimate was 2–4 hours of active agent
work for the main GUI change; treat it as a rough planning estimate, not a
deadline or reason to omit edge-case handling or verification.

## 9. Acceptance criteria and verification

Use synthetic snapshots/results and temporary run directories. Do not start
network-device captures as part of verification.

### Pure helper tests (`node:test`)

- Two independent pairings render separate service sets in manifest order.
- Two old ports sharing `ae0` and its post snapshot remain separate by full
  endpoint identity; IRB and linked services stay in their evaluation group.
- Identical subject/baseline/scope ids under different pairings have independent
  expansion keys. Identical scope ids in different runs also cannot collide.
- No-post pairings remain present; no evaluation is silently lost or rendered
  twice across mapped, same-device, rollback, and fallback partitions.
- All/type selections, raw L2 service types, unknown types, absent catalogue,
  zero results, and linked-context retention have correct visible counts.
- Type filtering leaves infrastructure, unmatched warnings, excluded counts,
  and the original API payload unchanged.
- Full-run counts remain unfiltered; service-result totals exclude device/L1;
  all six statuses and check-level unchanged annotations remain intact.
- Missing baseline, whole-device baseline, and unresolved metadata produce
  explicit notices; whole-device fallback is not treated as exact ownership.
- Identical unassigned payloads from one subject are shown once; different
  subjects and unexpectedly different payloads are preserved.

### Route/engine regression coverage

Reuse or extend tests in `tests/gui/test_evaluation_routes.py` to exercise a
shared destination with two distinct per-port baselines, producing two steps
with the correct service sets. Verify using existing pairing and engine tests
that the backend result shape and profile filtering remain unchanged. No new
HTTP contract is expected for this feature.

Relevant existing suites:

```bash
node --test tests/js/*.test.js
.venv/bin/python -m pytest tests/gui tests/runs/test_pairing.py tests/test_engine.py tests/reporting
```

During the initial review, 120 focused Python tests and both then-existing
JavaScript test files passed. GUI API tests could not start because the local
Starlette test client required missing `httpx2`. This is historical environment
information, not verification of the future implementation. Recheck the actual
environment when implementing, resolve/report the test dependency as appropriate,
and do not claim route tests pass if they could not run.

### Browser acceptance

1. Render the mockup-equivalent case: two expanded pairings sharing a LAG, a
   third independent pairing, and one waiting for a post capture. Services,
   capture flags, checks, and unmatched findings are visually bound to a pair.
2. Expand a service in each shared-LAG pairing; toggling one does not toggle
   the other. Pair expansion and filter choices survive ordinary rerenders.
3. Select IPVPN, then a type absent from one pairing, then All types. Counts,
   empty states, linked context, and retained warnings agree with the spec.
4. Collapse all while a mocked capture is running or has failed. Its affected
   pair headers still show progress/error. Re-evaluation preserves user choices.
5. Verify a profile-filtered run does not imply hidden types can be recovered
   merely by selecting All types. Export still contains the full cached response.
6. Verify whole-device baseline and missing-baseline notices with synthetic data.
7. Open a migration with rollback, same-device, and whole-device results; all
   existing findings remain accessible with accurate ownership labels.
8. Smoke-test single-device, mapping-less, snapshot, and bulk-group navigation,
   plus existing capture/edit/export/archive controls and permissions.
9. Check keyboard operation, focus retention, long port/service names, and table
   overflow at ordinary laptop and narrow viewport widths. No layout clipping.

Completion means the real GUI renders and filters real response data according
to this spec, required tests and browser checks have been run, and verification
limits are reported. A standalone mockup alone does not complete implementation.
