/* Node picker (wave B): pure state helpers shared by the new-run form and
   the bulk device rows. No DOM here - app.js builds the picker element and
   calls into these functions, so the state transitions are testable with
   `node --test` without a browser. */

function pickerMode(device, inventoryAvailable) {
  return !inventoryAvailable || device.manual === true ? "manual" : "pick";
}

function applyPick(device, item) {
  device.node = item.node;
  device.host = item.host;
  device.picked = true;
  device.manual = false;
}

function clearPick(device) {
  device.node = "";
  device.host = "";
  device.picked = false;
}

// Ticking or unticking Manual always clears node/host: unticking clears a
// picked value, ticking starts the free-text inputs from empty (spec §
// "Node picker").
function setManual(device, manual) {
  device.manual = manual;
  device.node = "";
  device.host = "";
  device.picked = false;
}

function moveHighlight(index, delta, length) {
  if (length === 0) return -1;
  return (index + delta + length) % length;
}

function moreLabel(total, shown) {
  return total > shown ? `${total - shown} more — refine the search` : null;
}

function itemLabel(item) {
  return `${item.node} · ${item.host}`;
}

// finding #2: inventory configured but failing (enabled && error) must not
// fall back to manual silently - the caller renders this as a notice above
// the device sub-forms/bulk table/Add-devices modal. null when there is
// nothing to say (not configured, or configured and working).
function unavailableNotice(inv) {
  if (inv && inv.enabled && inv.error) {
    return `Inventory unavailable: ${inv.error} — manual entry only.`;
  }
  return null;
}

// finding #5: a non-OK, non-401 /api/inventory response (e.g. a 500 with a
// FastAPI {detail} body) used to render an empty dropdown. 401 is excluded -
// the global fetch wrapper is already navigating to /login by the time this
// would run, so no note should flash first.
function searchErrorNote(status, body) {
  if (status === 401) return null;
  if (body && typeof body.detail === "string" && body.detail) return body.detail;
  return `HTTP ${status}`;
}

// finding #8: ArrowDown on an empty input with nothing shown yet should
// browse the first page of hosts, same as typing would once debounced.
function shouldBrowseOnArrowDown(query, itemsShown) {
  return itemsShown === 0 && query.trim() === "";
}

const MigPicker = {
  pickerMode,
  applyPick,
  clearPick,
  setManual,
  moveHighlight,
  moreLabel,
  itemLabel,
  unavailableNotice,
  searchErrorNote,
  shouldBrowseOnArrowDown,
};

if (typeof module !== "undefined" && module.exports) module.exports = MigPicker;
