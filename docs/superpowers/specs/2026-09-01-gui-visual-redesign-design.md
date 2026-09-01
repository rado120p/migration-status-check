# GUI visual redesign — full-width shell, CLI-aligned results, safety-net sections

Date: 2026-09-01
Status: approved (interactive mockup session, visual companion)

## Goal

The GUI's effective content area is too small (fixed 1200px shell) and its
results rendering is less readable than the CLI report. Align the GUI's
results presentation with the CLI's information architecture and add the
missing NESPÁROVÁNO / NEZAŘAZENO sections.

Frontend-only: no backend/API changes, no CLI changes. The evaluation JSON
(`RunResult.to_dict()`) already carries `unmatched` and `unassigned`.

## 1. Layout & sizing

- Shell becomes full-width fluid: remove the fixed `width: 1200px` on
  `.shell`; small page margin remains. Sidebar stays ~232px.
- New permanent right rail (~280px): a per-screen **guide card** with short
  "what you can do here" text. Content differs per screen (run overview,
  snapshot view, capture form, new run, edit mapping, checks). Czech copy,
  tone as in mockups (e.g. run overview explains pre/post comparison,
  Nespárováno/Nezařazeno meaning).
- Main grid: `sidebar | content | guide rail`.
- Sidebar collapse and responsive breakpoints: deferred, out of scope.

## 2. Run overview

### Counts strip (replaces the five summary cards)

Two compact lines mirroring the CLI (`_counts_lines`):
`Služby: n PASS  n WARN  n FAIL  n SKIP` and
`Checky: n PASS  n WARN  n FAIL  n SKIP  n INFO`, plus the
"Spárováno X služeb, Y nespárovaná v baseline, Z v subject" sentence.

### Captures table (pure capture management)

The pairing table keeps port pairs, pre/post/rollback flags, and live
capture progress/failure notes. It loses: result pills, chevrons, row
expansion, click-through to scopes.

### Results table (new, run-wide)

One row per service across all evaluations, CLI summary-table columns:
**Stav / Služba / Typ / Starý port / Nový port / RI / Nález** (worst
finding message, empty on PASS). WARN/FAIL rows tinted
(`#fffbeb`/`#fef2f2`). Includes L1 parent rows ("fyzický port") and
L2-část rows, same as the CLI (`build_view` semantics: description,
service_type with "(L2 část)", worst_message).

The GUI consumes the same view-building logic as the CLI. Port the
`reporting/view.py` structures (sections, groups, change_text, merged
deactivation skips, worst_message) to the frontend — either by
serializing `ServiceView` per scope in the API response *only if
unavoidable*; preferred: reimplement the small pure functions in JS
against the existing checks JSON (family, group, label, value,
baseline_value, delta, mode are already serialized per check).

### Row expansion — detail panel (headerless)

Clicking a service row toggles an inline panel directly under it. The
highlighted parent row acts as the header (no repeated
služba/typ/ports/RI line — that was the alignment defect). Panel:

- Check table columns: **Stav / Check / Post (nový port) / Změna proti
  (starý port)**.
- Family section bands: `IPv4 — <addresses> [VGW …]`, `IPv6 — …`
  (indigo band, `FAMILY_ORDER = (None, 4, 6)`); family-less rows first,
  under no band.
- Named groups (e.g. `BGP peer 10.1.2.2 · ACME-VRF.inet.0`) as indented
  sub-headers, group rows indented under them.
- Změna column per CLI `change_text` rules: empty for state-mode checks,
  empty when equal, `bylo <baseline>  <delta>` otherwise, "bez baseline"
  only where the CLI shows it.
- Deactivation SKIPs merged to one "Ostatní checky: N přeskočeno" row
  (mirror `_merge_deactivation_skips`).
- Colored status left edge on the panel (pass/warn/fail/skip colors).
- L2+L3 link notes ("L2 část: … (blok níže)") rendered as a note line.

### Safety-net sections (new)

Always rendered at the bottom of the run overview, never filtered:

- **Nespárováno — N**: rows `baseline|subject / description / (typ) /
  reason` from `result.unmatched`. "(nic)" when empty. Warning-tinted
  card when non-empty.
- **Nezařazeno (jen subject) — N**: rows from `result.unassigned` in CLI
  order (BGP peer, Statická routa, BFD session), identity/detail split
  per `_unassigned_row` (RI, `→ next-hop` vs `via`, "(aggregate)"
  suffix). Same empty/tint behavior.

Multiple evaluations in one run: aggregate the sections across
evaluations (label rows with the evaluation's port pair when there is
more than one).

### Same-device section

Keeps its role, restyled to the same table language (no separate visual
dialect).

## 3. Snapshot view

- **Standalone-only.** The baseline compare picker is removed (revert of
  the snapshot-view picker from f134cf5); same-device comparison lives
  exclusively in the run overview. Backend endpoint's `baseline` param
  may stay; the GUI stops using it.
- No Změna column, no "vs" pill.
- Same design language: breadcrumb, phase pill, meta bar, Služby/Checky
  counts strip, results table with a single **Port** column, headerless
  expansion, **Nezařazeno** section. No Nespárováno (cannot exist
  without a baseline).

## 4. Out of scope

- Backend/API and CLI changes.
- Checks registry screen and forms beyond inheriting the full-width
  shell + guide rail.
- Sidebar collapse, responsive/mobile, filtering UI (the flat results
  table is designed to host status/text filters later, like the CLI's
  `--status`/`--text`).

## Error handling

Unchanged: existing notice components for evaluation errors/422s keep
their placement above the results table.

## Testing

- Existing GUI tests updated where they assert the old structures
  (summary cards, pairing-row expansion, scope cards, compare bar).
- New JS-side view logic (sections/groups/change text/merge) mirrored
  from `reporting/view.py` must be covered against the same fixture
  shapes the CLI view tests use, so the two implementations can be
  compared check-for-check.
- Safety-net sections: tests for empty ("(nic)"), non-empty, and
  filter-immunity (once filters exist) plus the aggregate/multi-pair
  labeling.

## Mockups

Approved mockups live in `.superpowers/brainstorm/2019884-1788277948/content/`
(layout-size, guide-placement, results-style, summary-table,
full-run-overview, expansion-align-v2, snapshot-view).
