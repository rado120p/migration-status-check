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

const MigPicker = {
  pickerMode,
  applyPick,
  clearPick,
  setManual,
  moveHighlight,
  moreLabel,
  itemLabel,
};

if (typeof module !== "undefined" && module.exports) module.exports = MigPicker;
