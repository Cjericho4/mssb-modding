// Both editors share this flag for the current window. It never belongs to a team or draft.
let enabled = false;
const changes = new EventTarget();

export function canEditStats() {
  return enabled;
}

export function onStatAccessChange(listener) {
  changes.addEventListener("change", listener);
}

function setAccess(value) {
  enabled = value;
  changes.dispatchEvent(new Event("change"));
}

export function bindStatAccess(form, fields, reset, apply, close) {
  const panel = form.querySelector(".stat-consent");
  panel.append(document.getElementById("stat-consent-controls").content.cloneNode(true));
  const locked = panel.querySelector(".stat-locked");
  const unlocked = panel.querySelector(".stat-unlocked");
  const acknowledge = panel.querySelector(".stat-acknowledge");
  const enable = panel.querySelector(".stat-enable");
  const lock = panel.querySelector(".stat-lock");

  function refresh() {
    const inputs = [...fields.querySelectorAll("input")];
    const dirty = inputs.some(input => input.value !== input.defaultValue);
    for (const input of inputs) input.readOnly = !enabled;
    reset.disabled = apply.disabled = !enabled || !inputs.length;
    close.textContent = enabled ? "Cancel" : "Close";
    locked.hidden = enabled;
    unlocked.hidden = !enabled;
    enable.disabled = !acknowledge.checked || !inputs.length;
    lock.disabled = dirty;
    lock.title = dirty ? "Apply your edits or cancel them before locking." : "";
  }

  acknowledge.addEventListener("change", refresh);
  enable.addEventListener("click", () => {
    if (!enabled && acknowledge.checked && fields.childElementCount) {
      setAccess(true);
      fields.querySelector("input")?.focus();
    }
  });
  lock.addEventListener("click", () => {
    if (lock.disabled) return;
    setAccess(false);
    acknowledge.focus();
  });
  reset.addEventListener("click", () => {
    if (!enabled || reset.disabled) return;
    for (const input of fields.querySelectorAll("input")) input.value = "";
    refresh();
  });
  fields.addEventListener("input", refresh);

  // Visiting a different editor or locking always requires a fresh acknowledgement.
  function open() {
    acknowledge.checked = false;
    refresh();
  }
  onStatAccessChange(open);
  return {open, refresh};
}
