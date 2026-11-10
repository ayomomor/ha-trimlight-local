/*
 * The Trimlight page (sidebar) and the pattern editor card for the Trimlight Edge (Local) integration.
 * Served and registered by the integration itself. It has no dependencies, so it works without internet access.
 *
 * Card:  type: custom:trimlight-pattern-card
 *        entity: light.roofline
 */

const DOMAIN = "trimlight_local";
// The phone app's colour swatches, in its order. null is its "no colour" swatch, which leaves those dots unlit.
const PALETTE = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#7800ff", "#ff2600", "#ffffff", "#dc6414", "#ff0078", "#00ffff", "#000000", null];
const NEW_PATTERN = { mode: "static", speed: 128, brightness: 255, colors: [{ color: "#ff0000", dots: 5 }] };
const STRIP_DOTS = 60;
const LIVE_PREVIEW_DELAY_MS = 600;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const BASE_STYLE = `
  :host { display: block; }
  .row { display: flex; align-items: center; gap: 8px; margin: 8px 0; flex-wrap: wrap; }
  .row > label { width: 84px; flex: none; color: var(--secondary-text-color); }
  select, input[type="text"], input[type="number"], input[type="time"] {
    min-width: 0; padding: 6px 8px; border-radius: 6px; font: inherit;
    border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color);
  }
  .row > select, .row > input[type="text"] { flex: 1; }
  input[type="range"] { flex: 1; min-width: 0; accent-color: var(--primary-color); }
  input[type="checkbox"], input[type="radio"] { accent-color: var(--primary-color); }
  button {
    font: inherit; cursor: pointer; padding: 6px 12px; border-radius: 6px;
    border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color);
  }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  button.icon { padding: 2px 7px; }
  button:disabled { opacity: 0.4; cursor: default; }
  .buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .muted { color: var(--secondary-text-color); font-size: 0.9em; }
  .warning { color: var(--warning-color, #ffa600); }
  .status { min-height: 1.4em; margin-top: 8px; font-size: 0.9em; color: var(--secondary-text-color); }
  .status.error, .error { color: var(--error-color, #db4437); }
  .box { padding: 16px; border-radius: 12px; background: var(--card-background-color); border: 1px solid var(--divider-color); }
  h3 { margin: 0 0 8px; font-size: 1.1em; font-weight: 500; }
`;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const pad = (n) => String(n).padStart(2, "0");
const dayText = ([month, day]) => `${MONTHS[month - 1] || "?"} ${day}`;

function trimlightLights(hass) {
  const fromRegistry = Object.values(hass.entities || {}).filter((e) => e.platform === DOMAIN && e.entity_id.startsWith("light.")).map((e) => e.entity_id);
  if (fromRegistry.length) return fromRegistry;
  return Object.keys(hass.states).filter((id) => id.startsWith("light.") && "controller_busy" in hass.states[id].attributes);
}

// A change to any entity of the controller's device is the cue to read the state again.
function deviceStamp(hass, light) {
  const device = hass.entities && hass.entities[light] && hass.entities[light].device_id;
  const ids = device ? Object.values(hass.entities).filter((e) => e.device_id === device).map((e) => e.entity_id) : [light];
  return ids.map((id) => (hass.states[id] ? hass.states[id].last_updated : "")).join("|");
}

/* Holds the controller state for a card or the page and hands it to the editors inside. */
class StateHost {
  constructor(onData, onError) {
    this._onData = onData;
    this._onError = onError;
    this._requests = 0;
  }

  update(hass, entity) {
    this.hass = hass;
    const stamp = `${entity}#${deviceStamp(hass, entity)}`;
    if (entity !== this.entity || stamp !== this._stamp) {
      this.entity = entity;
      this._stamp = stamp;
      this.reload();
    }
  }

  async reload() {
    const request = ++this._requests;
    try {
      const data = await this.hass.callWS({ type: `${DOMAIN}/state`, entity_id: this.entity });
      if (request === this._requests) this._onData(data);
    } catch (err) {
      if (request === this._requests) this._onError(err.message || String(err));
    }
  }
}

/* Base for the editors: a shadow root, a status line and a service caller. */
class TrimlightEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._busy = false;
    this._status = el("div", { class: "status" });
  }

  setContext(hass, entity, data, reload) {
    this._hass = hass;
    this._entity = entity;
    this._reload = reload;
    const first = !this._data;
    this._data = data;
    this.dataChanged(first);
  }

  dataChanged() {}

  _frame(...children) {
    const shown = children.filter((child) => child !== null && child !== undefined && child !== false);
    this.shadowRoot.replaceChildren(el("style", { textContent: BASE_STYLE + (this.constructor.style || "") }), ...shown, this._status);
  }

  _setStatus(text, error = false) {
    this._status.textContent = text;
    this._status.className = error ? "status error" : "status";
  }

  async _call(service, data, success, { target, domain = DOMAIN, pending = "Sending…", reload = true } = {}) {
    this._busy = true;
    this.busyChanged();
    this._setStatus(pending);
    const busyHint = setTimeout(() => {
      const light = this._hass.states[this._entity];
      if (light && light.attributes.controller_busy) this._setStatus(BUSY_TEXT);
    }, BUSY_HINT_DELAY_MS);
    try {
      await this._hass.callService(domain, service, data, target || { entity_id: this._entity }, false);
      this._setStatus(success);
      if (reload && this._reload) await this._reload();
      return true;
    } catch (err) {
      this._setStatus(err.message || String(err), true);
      return false;
    } finally {
      clearTimeout(busyHint);
      this._busy = false;
      this.busyChanged();
    }
  }

  busyChanged() {}
}

/* Patterns: create, edit, preview and save custom patterns, like the app's pattern editor. */
class TrimlightPatternEditor extends TrimlightEditor {
  static style = `
    .strip { display: flex; flex-wrap: wrap; gap: 4px; padding: 10px; margin: 12px 0; background: #111; border-radius: 8px; }
    .led { width: 10px; height: 10px; border-radius: 50%; }
    .led.off { border: 1px solid #444; box-sizing: border-box; }
    .palette { display: flex; flex-wrap: wrap; gap: 5px; margin: 8px 0; }
    .swatch { width: 24px; height: 24px; padding: 0; border-radius: 50%; border: 1px solid var(--divider-color); cursor: pointer; flex: none; }
    .swatch.none, .none-chip { background: linear-gradient(45deg, transparent 45%, #e53935 45%, #e53935 55%, transparent 55%), #000; }
    .colors { display: flex; flex-direction: column; gap: 2px; }
    .color-row { display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 6px; border: 1px solid transparent; cursor: pointer; }
    .color-row.selected { border-color: var(--primary-color); background: var(--secondary-background-color); }
    .index { width: 18px; color: var(--secondary-text-color); font-size: 0.85em; text-align: right; }
    input[type="color"] { width: 40px; height: 28px; padding: 0; border: none; background: none; cursor: pointer; }
    .none-chip { width: 40px; height: 28px; border-radius: 4px; }
    .dots { width: 52px; }
    .unit { color: var(--secondary-text-color); font-size: 0.85em; flex: 1; }
    .value { width: 32px; text-align: right; font-variant-numeric: tabular-nums; }
    .radios { display: flex; gap: 14px; flex: 1; }
    .radios label, .live { display: flex; align-items: center; gap: 4px; cursor: pointer; }
    .live { margin-top: 10px; color: var(--secondary-text-color); }
  `;

  constructor() {
    super();
    this._selected = 0;
    this._live = false;
  }

  dataChanged(first) {
    // Reloads follow every light update; rebuilding the editor then would interrupt someone dragging a slider.
    const data = this._data;
    const shape = JSON.stringify([data.effects, data.modes, data.current && data.current.kind]);
    const rebuild = first || shape !== this._shape;
    this._shape = shape;
    if (!this._draft || (typeof this._draft.source === "number" && !this._effect(this._draft.source))) {
      this._draft = data.current && data.current.kind === "pattern" ? this._draftFrom(data.current, "current", "") : this._draftFrom(NEW_PATTERN, "new", "");
      this._selected = 0;
    }
    if (rebuild) this._render();
  }

  _effect(id) {
    return this._data.effects.find((e) => e.id === id);
  }

  _draftFrom(look, source, name) {
    return {
      source,
      name,
      mode: look.mode || "static",
      speed: look.speed,
      brightness: look.brightness,
      colors: (look.colors && look.colors.length ? look.colors : NEW_PATTERN.colors).map((c) => ({ color: c.color, dots: c.dots })),
    };
  }

  _render() {
    const draft = this._draft;
    const editing = typeof draft.source === "number" ? this._effect(draft.source) : null;
    const limits = this._data.limits;
    this._strip = el("div", { class: "strip", title: "How the colours repeat along the lights" });
    this._frame(
      el("div", { class: "row" }, el("label", {}, "Edit"), this._sourceSelect()),
      el("div", { class: "row" }, el("label", {}, "Name"),
        el("input", { type: "text", maxLength: limits.name, value: draft.name, placeholder: "e.g. Fourth of July", oninput: (ev) => { draft.name = ev.target.value; this.busyChanged(); } })),
      el("div", { class: "row" }, el("label", {}, "Mode"), this._modeSelect()),
      this._strip,
      this._palette(),
      this._colorRows(),
      el("button", { class: "add", disabled: draft.colors.length >= limits.colors, onclick: () => this._addColor() }, "+ Add colour"),
      this._slider("Speed", "speed"),
      this._slider("Brightness", "brightness"),
      editing ? this._overlayRow(editing) : null,
      editing && editing.used_by.length ? el("div", { class: "muted" }, `Used by ${editing.used_by.join(", ")}, so it can't be deleted.`) : null,
      this._buttons(editing),
      el("label", { class: "live" },
        el("input", { type: "checkbox", checked: this._live, onchange: (ev) => { this._live = ev.target.checked; if (this._live) this._preview(true); } }),
        "Live preview: show every change on the lights"),
    );
    this._renderStrip();
    this.busyChanged();
  }

  _sourceSelect() {
    const draft = this._draft;
    const options = [el("option", { value: "new", selected: draft.source === "new" }, "New pattern")];
    if (this._data.current && this._data.current.kind === "pattern") {
      options.push(el("option", { value: "current", selected: draft.source === "current" }, "What the lights show now"));
    }
    for (const effect of this._data.effects) {
      const pattern = effect.kind === "pattern";
      options.push(el("option", { value: String(effect.id), disabled: !pattern, selected: draft.source === effect.id }, pattern ? effect.name : `${effect.name} (built-in effect)`));
    }
    return el("select", { onchange: (ev) => this._open(ev.target.value) }, options);
  }

  _open(value) {
    if (value === "new") this._draft = this._draftFrom(NEW_PATTERN, "new", "");
    else if (value === "current") this._draft = this._draftFrom(this._data.current, "current", "");
    else {
      const effect = this._effect(Number(value));
      this._draft = this._draftFrom(effect, effect.id, effect.stored_name);
    }
    this._selected = 0;
    this._setStatus("");
    this._render();
    this._schedulePreview();
  }

  _modeSelect() {
    return el("select", { onchange: (ev) => { this._draft.mode = ev.target.value; this._schedulePreview(); } },
      this._data.modes.map((m) => el("option", { value: m.key, selected: m.key === this._draft.mode }, m.name)));
  }

  _palette() {
    return el("div", { class: "palette", title: "Click a colour to use it for the selected row" },
      PALETTE.map((color) => el("button", {
        class: color === null ? "swatch none" : "swatch",
        style: color === null ? null : `background:${color}`,
        title: color === null ? "No colour: these dots stay unlit" : color,
        onclick: () => this._setColor(this._selected, color),
      })));
  }

  _colorRows() {
    const colors = this._draft.colors;
    const limits = this._data.limits;
    return el("div", { class: "colors" }, colors.map((c, i) => el("div", {
      class: i === this._selected ? "color-row selected" : "color-row",
      onclick: () => { if (this._selected !== i) { this._selected = i; this._render(); } },
    },
    el("span", { class: "index" }, String(i + 1)),
    c.color === null
      ? el("span", { class: "none-chip", title: "No colour: these dots stay unlit" })
      : el("input", { type: "color", value: c.color, oninput: (ev) => { c.color = ev.target.value; this._changed(); } }),
    el("input", {
      type: "number", class: "dots", min: 1, max: limits.dots, value: c.dots, title: "How many dots this colour covers",
      oninput: (ev) => { const n = parseInt(ev.target.value, 10); if (n >= 1) { c.dots = clamp(n, 1, limits.dots); this._changed(); } },
      onchange: (ev) => { ev.target.value = c.dots; },
    }),
    el("span", { class: "unit" }, "dots"),
    el("button", { class: "icon", title: "Move up", disabled: i === 0, onclick: (ev) => { ev.stopPropagation(); this._move(i, -1); } }, "▲"),
    el("button", { class: "icon", title: "Move down", disabled: i === colors.length - 1, onclick: (ev) => { ev.stopPropagation(); this._move(i, 1); } }, "▼"),
    el("button", { class: "icon", title: "Remove", disabled: colors.length === 1, onclick: (ev) => { ev.stopPropagation(); this._remove(i); } }, "✕"))));
  }

  _slider(label, key) {
    const value = el("span", { class: "value" }, String(this._draft[key]));
    return el("div", { class: "row" }, el("label", {}, label),
      el("input", { type: "range", min: 0, max: 255, value: this._draft[key], oninput: (ev) => { this._draft[key] = Number(ev.target.value); value.textContent = ev.target.value; this._schedulePreview(); } }),
      value);
  }

  _overlayRow(effect) {
    const choice = (key, text) => el("label", {},
      el("input", { type: "radio", name: "overlay", checked: (effect.overlay || "none") === key, onchange: () => this._setOverlay(effect, key) }), text);
    return el("div", { class: "row" }, el("label", {}, "Overlay"), el("div", { class: "radios" }, choice("none", "None"), choice("lightning", "Lightning"), choice("snow", "Snow")));
  }

  _buttons(editing) {
    this._previewButton = el("button", { onclick: () => this._preview(false) }, "Show on lights");
    this._saveButton = el("button", { class: "primary", onclick: () => this._save() }, "Save");
    this._renameButton = editing ? el("button", { onclick: () => this._rename(editing) }, "Rename") : null;
    this._deleteButton = editing ? el("button", { onclick: () => this._delete(editing) }, "Delete") : null;
    return el("div", { class: "buttons" }, this._previewButton, this._saveButton, this._renameButton, this._deleteButton);
  }

  busyChanged() {
    if (!this._saveButton) return;
    const draft = this._draft;
    const editing = typeof draft.source === "number" ? this._effect(draft.source) : null;
    const name = draft.name.trim();
    this._previewButton.disabled = this._busy;
    this._saveButton.disabled = this._busy || !name;
    this._saveButton.textContent = editing && name !== editing.stored_name && name ? "Save as new" : "Save";
    // The Rename and Delete buttons stay on screen while a deletion runs, after the draft has already moved on to a new pattern.
    if (!editing) return;
    if (this._renameButton) this._renameButton.disabled = this._busy || !name || name === editing.stored_name;
    if (this._deleteButton) {
      this._deleteButton.disabled = this._busy || editing.used_by.length > 0;
      this._deleteButton.title = editing.used_by.length ? `Used by ${editing.used_by.join(", ")}` : "";
    }
  }

  _renderStrip() {
    const colors = this._draft.colors;
    const total = colors.reduce((sum, c) => sum + c.dots, 0);
    const leds = [];
    for (let i = 0; i < STRIP_DOTS; i++) {
      let at = i % total;
      const segment = colors.find((c) => (at -= c.dots) < 0);
      leds.push(segment.color === null
        ? el("span", { class: "led off" })
        : el("span", { class: "led", style: `background:${segment.color};box-shadow:0 0 5px ${segment.color}` }));
    }
    this._strip.replaceChildren(...leds);
  }

  _setColor(index, color) {
    const row = this._draft.colors[index];
    if (!row) return;
    row.color = color;
    this._changed(true);
  }

  _addColor() {
    const colors = this._draft.colors;
    const used = new Set(colors.map((c) => c.color));
    colors.push({ color: PALETTE.find((c) => c !== null && !used.has(c)) || "#ffffff", dots: colors[colors.length - 1].dots });
    this._selected = colors.length - 1;
    this._changed(true);
  }

  _move(index, step) {
    const colors = this._draft.colors;
    [colors[index], colors[index + step]] = [colors[index + step], colors[index]];
    this._selected = index + step;
    this._changed(true);
  }

  _remove(index) {
    this._draft.colors.splice(index, 1);
    this._selected = clamp(this._selected, 0, this._draft.colors.length - 1);
    this._changed(true);
  }

  _changed(rebuild = false) {
    if (rebuild) this._render();
    else this._renderStrip();
    this._schedulePreview();
  }

  _patternData() {
    const d = this._draft;
    return {
      mode: d.mode,
      colors: d.colors.map((c) => (c.color === null ? "none" : c.color)),
      dots: d.colors.map((c) => c.dots),
      speed: d.speed,
      brightness: d.brightness,
    };
  }

  _schedulePreview() {
    if (!this._live) return;
    clearTimeout(this._previewTimer);
    this._previewTimer = setTimeout(() => this._preview(true), LIVE_PREVIEW_DELAY_MS);
  }

  async _preview(live) {
    await this._call("show_pattern", this._patternData(), live ? "Live preview is on: changes show on the lights." : "Showing on the lights. Not saved yet.", { reload: false });
  }

  async _save() {
    const draft = this._draft;
    const name = draft.name.trim();
    const editing = typeof draft.source === "number" ? this._effect(draft.source) : null;
    const existing = this._data.effects.find((e) => e.stored_name === name);
    if (existing && existing !== editing && !window.confirm(`Replace the saved effect "${existing.name}"?`)) return;
    if (!(await this._call("save_effect", { name, ...this._patternData() }, `Saved "${name}" on the controller.`))) return;
    const saved = this._data.effects.find((e) => e.stored_name === name);
    if (saved) {
      draft.source = saved.id;
      draft.name = saved.stored_name;
    }
    this._render();
  }

  async _rename(effect) {
    const name = this._draft.name.trim();
    if (await this._call("rename_saved_effect", { effect: effect.name, new_name: name }, `Renamed "${effect.stored_name}" to "${name}".`)) this._render();
  }

  async _delete(effect) {
    if (!window.confirm(`Delete "${effect.name}" from the controller?`)) return;
    this._draft = this._draftFrom(NEW_PATTERN, "new", "");
    this._selected = 0;
    await this._call("delete_saved_effect", { effect: effect.name }, `Deleted "${effect.name}".`);
    this._render();
  }

  async _setOverlay(effect, overlay) {
    await this._call("set_overlay", { effect: effect.name, overlay }, overlay === "none" ? `Removed the overlay from "${effect.name}".` : `"${effect.name}" now has ${overlay}.`);
    this._render();
  }
}

/* The dashboard card: the pattern editor in a card. */
class TrimlightPatternCard extends HTMLElement {
  static getStubConfig(hass) {
    return { entity: trimlightLights(hass)[0] || "" };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("Set entity to a Trimlight light, for example light.roofline");
    this._config = config;
    if (this._header) this._header.textContent = config.title || "Trimlight patterns";
  }

  getCardSize() {
    return 10;
  }

  set hass(hass) {
    if (!this._host) this._setup();
    this._host.update(hass, this._config.entity);
  }

  _setup() {
    this._editor = el("trimlight-pattern-editor");
    this._message = el("div", { class: "muted" }, "Loading…");
    this._header = el("div", { class: "header" }, this._config.title || "Trimlight patterns");
    this.attachShadow({ mode: "open" }).append(
      el("style", { textContent: `ha-card { display: block; padding: 16px; } .header { font-size: 1.25em; font-weight: 500; margin-bottom: 4px; } .muted { color: var(--secondary-text-color); } .error { color: var(--error-color, #db4437); }` }),
      el("ha-card", {}, this._header, this._message, this._editor));
    this._host = new StateHost(
      (data) => {
        this._message.hidden = true;
        this._editor.setContext(this._host.hass, this._host.entity, data, () => this._host.reload());
      },
      (error) => {
        this._message.hidden = false;
        this._message.className = "error";
        this._message.textContent = error;
      },
    );
  }
}

/* ---------- The Trimlight page in the sidebar: Home Assistant's look, a roofline preview, and a tab for each part of the app ---------- */

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WORD_COLOURS = { Red: "#ff2a2a", Green: "#22e05a", Blue: "#2f5bff", Yellow: "#ffd400", Cyan: "#14e0f0", Purple: "#9b3cff", White: "#f4f1ea" };
const RAINBOW = ["#ff2a2a", "#ff8c00", "#ffd400", "#22e05a", "#14e0f0", "#2f5bff", "#9b3cff"];
const MOTIONS = ["Snake", "Wave", "Rebound", "Twinkle", "Pulse", "Comet", "Meteor", "Stack", "Strobe", "Breath", "Fire", "Segment"];
const PAGE_TABS = [["patterns", "Patterns"], ["effects", "Effects"], ["music", "Music"], ["schedules", "Schedules"], ["daily", "Daily"], ["combined", "Combined effect"], ["settings", "Settings"]];
const REPEAT_CHIPS = [["today_only", "Today only"], ["every_day", "Every day"], ["weekdays", "Weekdays"], ["weekend", "Weekend"]];
const SCHEDULE_PAUSE = "the controller takes about 15 seconds to confirm a schedule change";
const SYNC_HINT_DELAY_MS = 500;
// Schedules and the daily timer use this id for the combined effect.
const COMBINED_ID = 200;
const BUSY_TEXT = "The controller is busy with another app, probably the Trimlight app on a phone. It talks to one app at a time, so a change made here can take up to 30 seconds while Home Assistant waits for it to let go. Closing that app makes changes go through straight away.";
const BUSY_HINT_DELAY_MS = 6000;
const SLOW_CHANGE_MS = 1500;
const CONTROLLER_NAME = /^[ a-zA-Z0-9_-]{1,25}$/;
const portOk = (port, max) => Number.isInteger(port.start) && Number.isInteger(port.end) && port.start >= 1 && port.start <= port.end && port.end <= max;

const ICONS = {
  power: '<path d="M12 3v9"/><path d="M6.4 6.4a8 8 0 1 0 11.2 0"/>',
  bulb: '<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  sunset: '<path d="M7 18a5 5 0 0 1 10 0"/><path d="M3 18h18M12 9V5M8.5 9.5 7 8M15.5 9.5 17 8M3 21h18"/>',
  star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  play: '<path d="M7 5v14l11-7z"/>',
  save: '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  left: '<path d="m15 6-6 6 6 6"/>', right: '<path d="m9 6 6 6-6 6"/>', up: '<path d="m6 15 6-6 6 6"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>', menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  bolt: '<path d="m13 3-7 10h5l-1 8 7-10h-5z"/>', snow: '<path d="M12 2v20M4.9 7l14.2 10M19.1 7 4.9 17"/>',
  pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m14 6 4 4"/>',
};
const icon = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const modeLabel = (key) => (key ? key.charAt(0).toUpperCase() + key.slice(1).replaceAll("_", " ") : "Pattern");
const percent = (value) => `${Math.round((value / 255) * 100)}%`;
const shortDay = ([month, day]) => `${MONTHS[month - 1] || "?"} ${day}`;
const clockTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const HOLD_LABELS = { 30: "30 minutes", 60: "1 hour", 120: "2 hours", 240: "4 hours" };
const holdLabel = (minutes) => (minutes ? HOLD_LABELS[minutes] || `${minutes} minutes` : "Until changed");
const sunLabel = (sun) => `${sun.event === "sunset" ? "Sunset" : "Sunrise"}${sun.offset ? ` ${sun.offset < 0 ? "−" : "+"} ${Math.abs(sun.offset)} min` : ""}`;

function shiftClock(clock, minutes) {
  const [h, m] = clock.split(":").map(Number);
  const total = (((h * 60 + m + minutes) % 1440) + 1440) % 1440;
  return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
}

function describeBuiltin(name) {
  const motion = MOTIONS.find((m) => name.includes(m)) || (name.includes("Rainbow") ? "Rainbow" : "Other");
  let colours = name.split(" ").map((w) => WORD_COLOURS[w]).filter(Boolean);
  if (/Rainbow|Full Color/.test(name) || !colours.length) colours = RAINBOW;
  return { motion, colours, gap: /Blank/.test(name), background: /Background/.test(name) };
}

function covers(schedule, month, day) {
  const value = ([m, d]) => m * 100 + d;
  const x = month * 100 + day;
  const start = value(schedule.start);
  const end = value(schedule.end);
  return start <= end ? x >= start && x <= end : x >= start || x <= end;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const dayName = (d) => `${WEEKDAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}`;

function holidayWindow(holiday, year, from, to) {
  const iso = holiday && holiday.dates[String(year)];
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  return [new Date(y, m - 1, d + from), new Date(y, m - 1, d + to)];
}

/* The holiday's occurrences from the one still to come (or under way) onwards, as [year, first night, last night]. */
function holidayWindows(holiday, from, to, count) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const out = [];
  for (let year = today.getFullYear() - 1; year <= today.getFullYear() + 4 && out.length < count; year++) {
    const w = holidayWindow(holiday, year, from, to);
    if (w && w[1] >= today) out.push([year, ...w]);
  }
  return out;
}

function aroundText(name, from, to) {
  const days = (n) => (n === 1 ? "1 day" : `${n} days`);
  const the = (n) => (n === 1 ? "the day" : `the ${n} days`);
  if (from === 0 && to === 0) return name;
  if (from === to) return `${days(Math.abs(from))} ${from < 0 ? "before" : "after"} ${name}`;
  if (from < 0 && to > 0) return `${days(-from)} before to ${days(to)} after ${name}`;
  if (from === 0) return `${name} and ${the(to)} after`;
  if (to === 0) return `${name} and ${the(-from)} before`;
  if (to < 0) return `${-to} to ${days(-from)} before ${name}`;
  return `${from} to ${days(to)} after ${name}`;
}

/* An approximate animation of the controller's modes; the controller's own rendering isn't known in detail. */
function motionOf(text) {
  const t = (text || "").toLowerCase();
  if (/chase|snake|segment|inside|out_to|to_out/.test(t)) return "chase";
  if (/comet|meteor|rebound/.test(t)) return "comet";
  if (/wave|gradual/.test(t)) return "wave";
  if (/twinkle|firework|fire/.test(t)) return "twinkle";
  if (/strobe|flash|jump/.test(t)) return "strobe";
  if (/fade|breath|pulse/.test(t)) return "fade";
  if (/spectrum|vumeter|energy|ejection|rhythm|party|heatbeat|music/.test(t)) return "music";
  return "static";
}

function ledColour(look, i, t, n) {
  if (look.off) return [null, 0];
  const total = look.dots.reduce((a, b) => a + b, 0) || 1;
  const rate = 0.4 + look.speed / 60;
  const shift = look.motion === "chase" || look.motion === "wave" ? Math.floor(t * rate) : 0;
  let k = (((i - shift) % total) + total) % total;
  let colour = look.colors[look.colors.length - 1];
  for (let j = 0; j < look.colors.length; j++) {
    if (k < look.dots[j]) { colour = look.colors[j]; break; }
    k -= look.dots[j];
  }
  let level = Math.max(0.15, look.brightness / 255);
  if (look.motion === "comet") { const head = (t * rate * 1.6) % (n + 12); const d = head - i; level *= d >= 0 && d < 10 ? 1 - d / 10 : 0.06; }
  if (look.motion === "twinkle") level *= 0.25 + 0.75 * Math.max(0, Math.sin(i * 12.9898 + t * 0.9 * rate + Math.sin(i * 78.2)));
  if (look.motion === "strobe") level *= Math.floor(t * rate * 0.5) % 2 ? 1 : 0.08;
  if (look.motion === "fade") level *= 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(t * 0.25 * rate));
  if (look.motion === "wave") level *= 0.45 + 0.55 * (0.5 + 0.5 * Math.sin(i * 0.35 - t * 0.3 * rate));
  if (look.motion === "music") { const h = 0.5 + 0.5 * Math.sin(t * 0.7 + Math.sin(t * 0.23) * 3); level *= i / n < h ? 1 : 0.05; }
  return colour ? [colour, level] : [null, 0];
}

/*
 * House drawings for the preview, in fractions of their own width and height. A shape with an aspect ratio keeps its
 * proportions and stands centred on the ground; one without stretches across the preview. Runs are where the lights go.
 */
const ROOFLINES = {
  house: {
    name: "Simple house",
    ground: 0.88,
    outline: [[0.1, 0.88], [0.1, 0.52], [0.27, 0.2], [0.42, 0.52], [0.6, 0.52], [0.75, 0.3], [0.9, 0.52], [0.9, 0.88]],
    windows: [[0.2, 0.6, 0.05, 0.12], [0.31, 0.6, 0.05, 0.12], [0.66, 0.62, 0.05, 0.12], [0.79, 0.62, 0.05, 0.12]],
    runs: [[[0.1, 0.52], [0.27, 0.2], [0.42, 0.52], [0.6, 0.52], [0.75, 0.3], [0.9, 0.52]]],
  },
  two_gables: {
    name: "Two gables with a porch",
    aspect: 2.23,
    outline: [[0.017, 1], [0.017, 0.962], [0.03, 0.86], [0.075, 0.86], [0.075, 0.56], [0.05, 0.545], [0.146, 0.29], [0.31, 0.18], [0.346, 0.155],
      [0.42, 0.27], [0.833, 0.27], [0.98, 0.53], [0.965, 0.54], [0.958, 0.98], [0.958, 1]],
    roofs: [[[0.017, 0.962], [0.03, 0.86], [0.726, 0.86], [0.726, 0.962]], [[0.42, 0.27], [0.833, 0.27], [0.98, 0.53], [0.648, 0.54], [0.556, 0.54], [0.558, 0.478]]],
    windows: [[0.287, 0.548, 0.13, 0.256], [0.126, 0.62, 0.035, 0.13], [0.749, 0.73, 0.12, 0.11]],
    runs: [
      [[0.055, 0.535], [0.346, 0.155], [0.558, 0.478], [0.556, 0.54], [0.648, 0.54], [0.809, 0.289], [0.975, 0.533]],
      [[0.017, 0.962], [0.726, 0.962]],
    ],
  },
};
const LED_SPACING = 10;

function drawRoofline(canvas, look, t, shapeName) {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
  }
  const ctx = canvas.getContext("2d");
  const w = rect.width;
  const h = rect.height;
  const shape = ROOFLINES[shapeName] || ROOFLINES.house;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const sky = ctx.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#050813");
  sky.addColorStop(1, "#16213c");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, w, h);
  for (let s = 0; s < 70; s++) {
    ctx.fillStyle = `rgba(220,230,255,${0.25 + 0.15 * (s % 3)})`;
    ctx.fillRect((Math.sin(s * 91.7) * 0.5 + 0.5) * w, (Math.sin(s * 47.3) * 0.5 + 0.5) * 0.45 * h, 1 + (s % 3) * 0.6, 1 + (s % 3) * 0.6);
  }
  let left = 0;
  let top = 0;
  let width = w;
  let height = h;
  if (shape.aspect) {
    height = Math.min(h * 0.86, (w * 0.94) / shape.aspect);
    width = height * shape.aspect;
    left = (w - width) / 2;
    top = h * 0.95 - height;
  }
  const at = ([x, y]) => [left + x * width, top + y * height];
  const ground = shape.aspect ? top + height : h * shape.ground;
  ctx.fillStyle = "#0b1122";
  ctx.fillRect(0, ground, w, h - ground);
  const fill = (points, colour) => {
    ctx.fillStyle = colour;
    ctx.beginPath();
    points.map(at).forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.fill();
  };
  fill(shape.outline, "#04060d");
  (shape.roofs || []).forEach((roof) => fill(roof, "#070a14"));
  ctx.fillStyle = "rgba(255,190,110,0.16)";
  shape.windows.forEach(([x, y, ww, wh]) => ctx.fillRect(left + x * width, top + y * height, ww * width, wh * height));
  const points = [];
  shape.runs.forEach((run) => {
    const corners = run.map(at);
    for (let s = 0; s < corners.length - 1; s++) {
      const [x1, y1] = corners[s];
      const [x2, y2] = corners[s + 1];
      const steps = Math.max(1, Math.round(Math.hypot(x2 - x1, y2 - y1) / LED_SPACING));
      for (let j = 0; j < steps; j++) points.push([x1 + ((x2 - x1) * j) / steps, y1 + ((y2 - y1) * j) / steps]);
    }
    points.push(corners[corners.length - 1]);
  });
  points.forEach(([x, y], i) => {
    const [colour, level] = ledColour(look, i, t, points.length);
    if (!colour || level < 0.02) {
      ctx.fillStyle = "rgba(120,130,150,0.2)";
      ctx.beginPath();
      ctx.arc(x, y, 2.6, 0, 7);
      ctx.fill();
      return;
    }
    ctx.globalAlpha = Math.min(1, level);
    ctx.shadowColor = colour;
    ctx.shadowBlur = 13;
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, 7);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 1;
  });
}

const PAGE_STYLE = `
  :host {
    display: block; min-height: 100vh;
    --bg: var(--primary-background-color, #fafafa); --surface: var(--card-background-color, #fff); --surface-2: var(--secondary-background-color, #f1f3f5);
    --fg: var(--primary-text-color, #212121); --muted: var(--secondary-text-color, #6b7178); --line: var(--divider-color, rgba(0, 0, 0, 0.12));
    --accent: var(--primary-color, #03a9f4); --accent-ink: var(--text-primary-color, #fff); --accent-soft: color-mix(in srgb, var(--primary-color, #03a9f4) 14%, transparent);
    --radius: var(--ha-card-border-radius, 12px); --radius-s: 8px; --danger: var(--error-color, #db4437); --warn: var(--warning-color, #ffa600);
    --mono: var(--ha-font-family-code, ui-monospace, Consolas, monospace); --led-ground: #0d1119;
    background: var(--bg); color: var(--fg);
  }
  * { box-sizing: border-box; }
  button, input, select { font: inherit; color: inherit; }
  button { cursor: pointer; }
  button:disabled { cursor: default; opacity: 0.45; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  svg.i { width: 18px; height: 18px; stroke: currentColor; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; flex: none; }
  .page { display: flex; flex-direction: column; min-height: 100vh; position: relative; }
  .toolbar { display: flex; align-items: center; gap: 12px; padding: 0 16px; height: 56px; background: var(--app-header-background-color, var(--primary-color, #03a9f4)); color: var(--app-header-text-color, #fff); }
  .toolbar .title { font-size: 20px; font-weight: 400; }
  .toolbar .grow { flex: 1; }
  .toolbar select { background: transparent; color: inherit; border: 1px solid currentColor; border-radius: 6px; padding: 4px 6px; }
  .toolbar select option { color: #000; }
  .menu { display: none; background: none; border: 0; color: inherit; padding: 6px; }
  :host([narrow]) .menu { display: inline-grid; }
  .hero { position: relative; height: 220px; background: #0a0f1d; }
  .hero canvas { width: 100%; height: 100%; display: block; }
  .hero .tag { position: absolute; left: 16px; bottom: 12px; font-size: 12px; color: #c7cfe6; background: rgba(5, 8, 19, 0.6); padding: 3px 10px; border-radius: 999px; }
  .now { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px; padding: 14px 20px; background: var(--surface); border-bottom: 1px solid var(--line); }
  .now .what { flex: 1 1 260px; min-width: 0; }
  .now b { font-size: 16px; font-weight: 500; }
  .now small { display: block; color: var(--muted); font-size: 13px; }
  .busy-note { display: block; margin-top: 4px; font-size: 12px; color: var(--warn); }
  .now-actions { display: flex; flex-wrap: wrap; gap: 8px; }
  .rail .item { position: relative; }
  .editor-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; }
  .editor-head h2 { margin: 0; }
  .rail .item > button:first-child { width: 100%; padding-right: 40px; }
  .rail .play { position: absolute; top: 6px; right: 4px; display: grid; place-items: center; width: 32px; height: 32px; padding: 0; border-radius: 50%; color: var(--accent); }
  .rail .play svg { width: 18px; height: 18px; }
  .modes { display: inline-flex; background: var(--surface-2); border-radius: 999px; padding: 3px; gap: 2px; }
  .modes button { display: flex; align-items: center; gap: 6px; border: 0; background: none; padding: 6px 12px; border-radius: 999px; color: var(--muted); font-weight: 500; }
  .modes button[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); }
  .tabs { display: flex; gap: 2px; padding: 0 12px; border-bottom: 1px solid var(--line); overflow-x: auto; scrollbar-width: none; background: var(--surface); }
  .tab { border: 0; background: none; padding: 13px 14px 11px; color: var(--muted); font-weight: 500; white-space: nowrap; border-bottom: 2px solid transparent; }
  .tab[aria-selected="true"] { color: var(--accent); border-bottom-color: var(--accent); }
  .content { padding: 20px; flex: 1; min-width: 0; width: 100%; max-width: 1280px; margin: 0 auto; }
  .sync { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; opacity: 0.9; }
  .sync::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: sync 1s ease-in-out infinite alternate; }
  .sync[hidden] { display: none; }
  @keyframes sync { from { opacity: 0.25; } to { opacity: 1; } }
  .legend button.waiting, .list button.waiting { border-style: dashed; }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px; min-width: 0; box-shadow: var(--ha-card-box-shadow, none); }
  .card h2 { margin: 0 0 4px; font-size: 17px; font-weight: 500; }
  .sub { color: var(--muted); margin: 0 0 12px; font-size: 13px; }
  .label { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 600; }
  .field { display: grid; gap: 6px; min-width: 0; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  .input, .select { background: var(--surface-2); border: 1px solid transparent; border-bottom-color: var(--line); border-radius: var(--radius-s) var(--radius-s) 4px 4px; padding: 9px 11px; min-width: 0; }
  .btn { border: 1px solid var(--line); background: var(--surface); padding: 8px 16px; border-radius: 999px; font-weight: 500; display: inline-flex; align-items: center; gap: 7px; }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .btn.tonal { background: var(--accent-soft); border-color: transparent; color: var(--accent); }
  .btn.ghost { border-color: transparent; background: none; color: var(--muted); }
  .btn.danger { color: var(--danger); }
  .seg { display: inline-flex; border: 1px solid var(--line); border-radius: 999px; overflow: hidden; }
  .seg button { border: 0; background: none; padding: 6px 12px; color: var(--muted); font-weight: 500; display: inline-flex; align-items: center; gap: 6px; }
  .seg button + button { border-left: 1px solid var(--line); }
  .seg button[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); }
  input[type="range"] { accent-color: var(--accent); width: 100%; }
  input[type="checkbox"], input[type="radio"] { accent-color: var(--accent); }
  .slider { display: grid; grid-template-columns: 92px minmax(0, 1fr) 44px; align-items: center; gap: 10px; }
  .slider output { font: 500 13px var(--mono); text-align: right; color: var(--muted); }
  .chip { border: 1px solid var(--line); background: var(--surface); padding: 5px 11px; border-radius: 999px; font-size: 13px; color: var(--muted); display: inline-flex; align-items: center; gap: 6px; }
  .chip[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); border-color: transparent; }
  .note { font-size: 12.5px; color: var(--muted); margin: 0; }
  .warn-box { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 14px; border-radius: var(--radius-s); background: var(--accent-soft); margin-bottom: 14px; }
  .warn-box b { color: var(--warn); }
  .badge { font-size: 11px; padding: 1px 7px; border-radius: 6px; background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
  .two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
  .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  .grow { flex: 1; }
  .empty { padding: 32px; text-align: center; color: var(--muted); }
  .toast { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); background: var(--fg); color: var(--bg); padding: 10px 16px; border-radius: 10px; font-size: 13px; z-index: 20; max-width: calc(100% - 32px); box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25); }
  .toast.error { background: var(--danger); color: #fff; }

  .patterns { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 18px; }
  .rail { display: grid; gap: 4px; align-content: start; max-height: 720px; overflow-y: auto; padding-right: 4px; }
  .rail button { display: grid; gap: 6px; text-align: left; border: 1px solid transparent; background: none; padding: 9px 10px; border-radius: var(--radius-s); }
  .rail button[aria-current="true"] { background: var(--accent-soft); }
  .rail button:hover { background: var(--surface-2); }
  .rail .bar { height: 6px; border-radius: 3px; display: flex; overflow: hidden; background: var(--led-ground); }
  .rail .bar i { flex: 1; }
  .rail .meta { font-size: 12px; color: var(--muted); }
  .rail .new { display: flex; align-items: center; gap: 8px; color: var(--accent); font-weight: 500; }
  .editor { display: grid; gap: 18px; align-content: start; }
  .colors { display: flex; flex-wrap: wrap; gap: 10px; }
  .tile { position: relative; display: grid; justify-items: center; gap: 4px; padding: 8px 6px 6px; border-radius: var(--radius-s); border: 1px solid var(--line); background: var(--surface); width: 80px; cursor: pointer; }
  .tile.sel { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
  .tile .sw { width: 38px; height: 38px; border-radius: 50%; border: 1px solid rgba(127, 127, 127, 0.35); }
  .unlit { background: linear-gradient(45deg, transparent 45%, #e53935 45%, #e53935 55%, transparent 55%), #000; }
  .tile .dots { display: flex; align-items: center; gap: 2px; }
  .tile .dots button { border: 0; background: none; padding: 0 3px; color: var(--muted); }
  .tile .dots input { width: 34px; text-align: center; border: 0; background: none; font: 500 12px var(--mono); padding: 0; -moz-appearance: textfield; }
  .tile .num { position: absolute; top: 3px; left: 6px; font-size: 10.5px; color: var(--muted); }
  .add-tile { width: 80px; border: 1px dashed var(--line); border-radius: var(--radius-s); background: none; color: var(--accent); display: grid; place-items: center; min-height: 82px; }
  .palette { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .palette .sw { width: 28px; height: 28px; border-radius: 50%; border: 1px solid rgba(127, 127, 127, 0.4); padding: 0; }
  .palette input[type="color"] { width: 30px; height: 26px; border: 0; padding: 0; background: none; }

  .fx-top { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .search { display: flex; align-items: center; gap: 8px; flex: 1 1 260px; min-width: 0; }
  .search .input { flex: 1; }
  .filters { display: flex; flex-wrap: wrap; gap: 6px; }
  .split { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 18px; margin-top: 14px; align-items: start; }
  .fx-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 10px; }
  .fx { text-align: left; border: 1px solid var(--line); background: var(--surface); border-radius: var(--radius-s); padding: 10px; display: grid; gap: 8px; position: relative; }
  .fx[aria-pressed="true"] { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
  .fx .look { height: 22px; border-radius: 6px; background: var(--led-ground); position: relative; overflow: hidden; }
  .fx .look i { position: absolute; inset: 7px 8px; border-radius: 4px; }
  .fx .name { font-weight: 500; font-size: 13px; padding-right: 22px; }
  .fx .kind { font-size: 11.5px; color: var(--muted); }
  .fx .star { position: absolute; top: 6px; right: 6px; color: var(--muted); border: 0; background: none; padding: 2px; }
  .fx .star.on { color: #f5b301; }
  .fx .star.on svg { fill: currentColor; }
  .detail { position: sticky; top: 12px; display: grid; gap: 14px; }
  .count { font-size: 12.5px; color: var(--muted); margin: 0 0 8px; }

  .mx-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
  .mx { border: 1px solid var(--line); background: var(--surface); border-radius: var(--radius-s); padding: 10px; display: grid; gap: 8px; text-align: left; }
  .mx[aria-pressed="true"] { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
  .mx .eq { height: 38px; display: flex; align-items: flex-end; gap: 3px; background: var(--led-ground); border-radius: 6px; padding: 5px 6px; }
  .mx .eq i { flex: 1; border-radius: 2px 2px 0 0; }
  .mx .name { font-size: 12.5px; font-weight: 500; }
  .source { display: grid; gap: 8px; }
  .source label { display: flex; gap: 10px; align-items: flex-start; border: 1px solid var(--line); border-radius: var(--radius-s); padding: 10px; }
  .source small { display: block; color: var(--muted); }
  .source label[data-act] { cursor: pointer; }
  .source label[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); }
  .follow { display: grid; gap: 10px; margin-top: 14px; }
  .follow-status { margin: 0; font-weight: 500; }

  .sch-top { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 14px; }
  .year { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 18px; align-items: start; }
  .months { display: grid; grid-template-columns: repeat(auto-fill, minmax(176px, 1fr)); gap: 16px; }
  .mini h3 { margin: 0 0 6px; font-size: 13px; font-weight: 500; }
  .mini .g { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
  .mini .g span { aspect-ratio: 1; max-width: 100%; border-radius: 4px; font-size: 9.5px; display: grid; place-items: center; color: var(--muted); background: var(--surface-2); cursor: pointer; }
  .mini .g span.blank { background: none; cursor: default; }
  .mini .g span.on { color: #fff; text-shadow: 0 0 2px rgba(0, 0, 0, 0.7); }
  .mini .g span.today { outline: 2px solid var(--accent); outline-offset: 1px; }
  .legend { display: grid; gap: 6px; }
  .legend button { display: grid; grid-template-columns: 14px minmax(0, 1fr) auto; gap: 10px; align-items: center; text-align: left; border: 1px solid var(--line); background: var(--surface); padding: 8px 10px; border-radius: var(--radius-s); }
  .legend i { width: 14px; height: 14px; border-radius: 4px; }
  .legend small { display: block; color: var(--muted); }
  .legend .t { font: 500 12px var(--mono); color: var(--muted); text-align: right; }
  .month { border: 1px solid var(--line); border-radius: var(--radius-s); overflow: hidden; background: var(--surface); }
  .month .dow, .month .week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
  .month .dow span { padding: 6px 8px; font-size: 11.5px; color: var(--muted); border-bottom: 1px solid var(--line); }
  .month .week { position: relative; min-height: 88px; border-bottom: 1px solid var(--line); }
  .month .week > span { border-right: 1px solid var(--line); padding: 4px 6px; font-size: 12px; color: var(--muted); cursor: pointer; }
  .month .week > span.out { opacity: 0.45; }
  .month .week > span.today b { background: var(--accent); color: var(--accent-ink); border-radius: 10px; padding: 1px 6px; }
  .month .bars { position: absolute; inset: 26px 0 auto; display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 3px 0; padding: 0 3px; pointer-events: none; }
  .month .bars button { pointer-events: auto; border: 0; border-radius: 6px; padding: 3px 8px; font-size: 12px; color: #fff; text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin: 0 2px; text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6); }
  .month .bars button.daily { opacity: 0.75; border: 1px dashed rgba(255, 255, 255, 0.7); }
  .list { display: grid; gap: 8px; }
  .list button { display: grid; grid-template-columns: 6px 150px 190px minmax(0, 1fr); gap: 14px; align-items: center; text-align: left; border: 1px solid var(--line); background: var(--surface); padding: 12px; border-radius: var(--radius-s); }
  .list button i { align-self: stretch; border-radius: 3px; }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(400px, 100%); background: var(--surface); border-left: 1px solid var(--line); box-shadow: -12px 0 30px rgba(0, 0, 0, 0.2); z-index: 10; padding: 20px; display: grid; gap: 14px; align-content: start; overflow-y: auto; }
  .drawer h2 { margin: 0; font-size: 18px; font-weight: 500; }
  .drawer .seg { justify-self: start; }
  .dates { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .timeset { display: grid; gap: 8px; }
  .timeset .input { width: 116px; }
  .hint { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--muted); }
  .years { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; font-size: 13px; }
  .years li { display: flex; flex-wrap: wrap; gap: 4px 10px; }
  .years b { font-family: var(--mono); font-weight: 500; min-width: 40px; }
  .years .clash { color: var(--danger); }

  .daily { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }
  .switch { width: 40px; height: 22px; border-radius: 11px; background: var(--surface-2); border: 1px solid var(--line); position: relative; padding: 0; flex: none; }
  .switch::after { content: ""; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: var(--muted); transition: left 0.15s; }
  .switch[aria-checked="true"] { background: var(--accent); border-color: var(--accent); }
  .switch[aria-checked="true"]::after { left: 20px; background: var(--accent-ink); }
  .day24 { position: relative; height: 26px; border-radius: 6px; background: var(--surface-2); overflow: hidden; }
  .day24 i { position: absolute; top: 0; bottom: 0; background: var(--accent); }
  .hours { display: flex; justify-content: space-between; font: 11px var(--mono); color: var(--muted); margin-top: 3px; }
  .combo { display: grid; gap: 8px; }
  .combo .item { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; gap: 10px; align-items: center; border: 1px solid var(--line); border-radius: var(--radius-s); padding: 8px 10px; }
  .combo .bar { height: 5px; border-radius: 3px; display: flex; overflow: hidden; margin-top: 4px; background: var(--led-ground); }
  .combo .bar i { flex: 1; }
  .timeline { display: flex; height: 28px; border-radius: 8px; overflow: hidden; }
  .timeline i { flex: 1; display: grid; place-items: center; font-size: 11px; font-style: normal; color: #fff; text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6); }
  .facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 10px 24px; margin: 0; }
  .facts dt { color: var(--muted); }
  .facts dd { margin: 0; }
  .ports { border-collapse: collapse; font-variant-numeric: tabular-nums; }
  .ports th, .ports td { text-align: left; padding: 4px 16px 4px 0; }
  .ports th { color: var(--muted); font-weight: 500; font-size: 12.5px; }
  .ports .input.num { width: 86px; padding: 6px 8px; }
  .ports tr.bad .input { border-bottom-color: var(--danger); color: var(--danger); }
  .ports th:last-child, .ports td:last-child { text-align: center; padding-right: 0; }
  .table-wrap { overflow-x: auto; }
  .led-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px 36px; }
  .led-col { display: grid; gap: 20px; align-content: start; min-width: 0; }
  .session { position: sticky; top: 8px; z-index: 3; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px; padding: 12px 14px; margin-bottom: 18px; border-radius: var(--radius-s); background: color-mix(in srgb, var(--warn) 14%, var(--surface)); border: 1px solid color-mix(in srgb, var(--warn) 50%, transparent); }
  .session > .grow { flex: 1 1 240px; }
  .session small { display: block; color: var(--muted); margin-top: 2px; }
  .countdown { font-family: var(--mono); font-weight: 600; color: var(--fg); }
  .wizard { display: grid; gap: 10px; padding: 12px 14px; border-radius: var(--radius-s); background: var(--surface-2); }
  .wizard p { margin: 0; }
  .dot { width: 12px; height: 12px; border-radius: 50%; border: 1px solid rgba(127, 127, 127, 0.45); flex: none; display: inline-block; }
  .dot.rgbw { background: conic-gradient(#ff2a2a 0 25%, #22e05a 0 50%, #2f5bff 0 75%, #f4f1ea 0); }

  @media (max-width: 760px) {
    .content { padding: 14px; }
    .now { padding: 12px 14px; }
    .hero { height: 170px; }
    .patterns, .split, .year, .two { grid-template-columns: minmax(0, 1fr); }
    .rail { grid-auto-flow: column; grid-auto-columns: 150px; overflow-x: auto; max-height: none; padding-bottom: 4px; }
    .detail { position: static; order: -1; }
    .list button { grid-template-columns: 6px minmax(0, 1fr); }
    .list button span:nth-child(n + 3) { grid-column: 2; }
    .month .bars button { font-size: 0; height: 7px; padding: 0; }
    .slider { grid-template-columns: 78px minmax(0, 1fr) 40px; }
    .facts { grid-template-columns: minmax(0, 1fr); gap: 2px; }
    .facts dd { margin-bottom: 10px; }
    .led-grid { grid-template-columns: minmax(0, 1fr); }
    .ports th, .ports td { padding-right: 8px; }
    .ports .input.num { width: 68px; }
  }
  @media (prefers-reduced-motion: reduce) { .switch::after { transition: none; } .sync::before { animation: none; } }
`;

class TrimlightPanel extends HTMLElement {
  constructor() {
    super();
    const today = new Date();
    this._s = {
      tab: "patterns", source: null, draft: null, selected: 0, trying: false,
      fxQuery: "", fxMotion: "All", fxColour: "All", fx: null, fxSpeed: 100, fxLength: 30, fxReverse: false, fxBright: 255, fxName: "",
      music: null, musicColour: "#ff0000", musicLength: 70, musicSens: 13, musicSource: null,
      view: "year", month: new Date(today.getFullYear(), today.getMonth(), 1), drawer: null,
      daily: null, combined: null,
      led: null, ports: null, ctlName: null,
    };
    this._busy = false;
  }

  set narrow(value) {
    this.toggleAttribute("narrow", !!value);
  }

  set panel(value) {
    this._panel = value;
  }

  set route(value) {
    this._route = value;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._host) this._setup();
    const lights = trimlightLights(hass);
    if (!lights.includes(this._entity)) this._entity = lights[0];
    this._lights = lights;
    if (!this._entity) {
      this._content.innerHTML = `<div class="empty">No Trimlight controller is set up yet. Add the Trimlight Edge (Local) integration first.</div>`;
      return;
    }
    if (this._data) {
      // Home Assistant hands over a new state object on every change anywhere; redrawing only on this controller's changes keeps clicks from being lost.
      const light = hass.states[this._data.entities.light];
      const mode = hass.states[this._data.entities.mode];
      const key = `${light && light.last_updated}|${mode && mode.state}`;
      if (key !== this._nowKey) {
        this._nowKey = key;
        this._renderNow();
      }
    }
    this._host.update(hass, this._entity);
  }

  connectedCallback() {
    this._animate = true;
    requestAnimationFrame((t) => this._frame(t));
    this._ledTimer();
  }

  disconnectedCallback() {
    this._animate = false;
    this._ledTimer();
    if (this._s.trying) this._setTrying(false);
  }

  _setup() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${PAGE_STYLE}</style>
      <div class="page">
        <header class="toolbar"><button class="menu" type="button" data-act="menu" aria-label="Open the sidebar">${icon("menu")}</button><span class="title">Trimlight</span><span class="grow"></span><span class="sync" role="status" hidden>Saving</span><span class="controllers"></span></header>
        <div class="hero"><canvas aria-label="Roofline preview"></canvas><span class="tag"></span></div>
        <section class="now"></section>
        <nav class="tabs" role="tablist"></nav>
        <div class="content"><div class="empty">Loading…</div></div>
      </div>`;
    this._pageEl = root.querySelector(".page");
    this._canvas = root.querySelector(".hero canvas");
    this._tag = root.querySelector(".hero .tag");
    this._nowEl = root.querySelector(".now");
    this._tabsEl = root.querySelector(".tabs");
    this._content = root.querySelector(".content");
    this._controllersEl = root.querySelector(".controllers");
    root.addEventListener("click", (ev) => this._onClick(ev));
    root.addEventListener("input", (ev) => this._onInput(ev));
    root.addEventListener("change", (ev) => this._onChange(ev));
    root.addEventListener("focusout", () => setTimeout(() => this._flushPending(), 0));
    root.addEventListener("keydown", (ev) => {
      if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches('[role="button"]')) {
        ev.preventDefault();
        ev.target.click();
      }
    });
    this._host = new StateHost((data) => this._onData(data), (error) => this._toast(error, true));
  }

  _frame(time) {
    if (!this._animate) return;
    if (this._canvas && this._data && !document.hidden && this.offsetParent !== null) {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      drawRoofline(this._canvas, this._previewLook(), reduce ? 0 : time / 100, this._data.roofline);
    }
    requestAnimationFrame((t) => this._frame(t));
  }

  _onData(data) {
    const first = !this._data;
    this._data = data;
    const s = this._s;
    if (first) {
      if (data.music) {
        s.musicColour = data.music.color || s.musicColour;
        s.musicLength = data.music.length || s.musicLength;
        s.musicSens = data.music.sensitivity || s.musicSens;
      }
      if (data.current && data.current.kind === "music") s.music = data.music.effects.indexOf(data.current.effect);
      if (data.current && data.current.kind === "builtin") {
        s.fx = data.builtins.indexOf(data.current.effect);
        Object.assign(s, { fxSpeed: data.current.speed, fxLength: data.current.length || 30, fxReverse: data.current.reverse, fxBright: data.current.brightness });
      }
    }
    if (!s.draft || (typeof s.source === "number" && !this._effect(s.source))) {
      const onRoof = this._savedMatch(data.current);
      this._openPattern(onRoof ? onRoof.id : "new");
    }
    if (!s.daily || !s.daily.some((d) => d.dirty)) s.daily = data.daily.map((d) => this._dailyDraft(d));
    if (!s.combined || !s.combined.dirty) s.combined = this._combinedDraft();
    if (!s.ports || !s.ports.dirty) s.ports = this._portsDraft(data.device);
    const session = data.device && data.device.led_session;
    if (session) this._ledDeadline = Date.now() + session.seconds_left * 1000;
    else if (!this._busy) s.led = null;
    this._ledTimer();
    // A reload after every light update must not rebuild the page under someone typing or dragging a slider.
    const active = this.shadowRoot.activeElement;
    if (!first && active && /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName)) this._pending = true;
    else this.render();
  }

  _flushPending() {
    const active = this.shadowRoot.activeElement;
    if (this._pending && !(active && /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName))) {
      this._pending = false;
      this.render();
    }
  }

  _effect(id) {
    return this._data.effects.find((e) => e.id === id);
  }

  _effectByName(name) {
    return this._data.effects.find((e) => e.name === name);
  }

  _colourOf(name) {
    const effect = this._effectByName(name === this._data.combined_name && this._data.combined ? this._data.combined.effects[0] : name);
    if (effect && effect.kind === "pattern") {
      const lit = effect.colors.find((c) => c.color && c.color !== "#000000");
      if (lit) return lit.color === "#ffffff" ? "#c9c3b4" : lit.color;
    }
    if (effect && effect.kind === "builtin") return describeBuiltin(effect.effect).colours[0];
    return "var(--accent)";
  }

  /* ---------- Rendering ---------- */

  render() {
    if (!this._data) return;
    this._renderControllers();
    this._renderNow();
    this._tabsEl.innerHTML = PAGE_TABS.map(([key, label]) => `<button class="tab" role="tab" type="button" data-act="tab" data-key="${key}" aria-selected="${this._s.tab === key}">${label}</button>`).join("");
    const views = { patterns: this._patterns, effects: this._effects, music: this._music, schedules: this._schedules, daily: this._daily, combined: this._combinedView, settings: this._settings };
    this._content.innerHTML = views[this._s.tab].call(this);
    const drawer = this.shadowRoot.querySelector(".drawer");
    if (drawer) drawer.remove();
    if (this._s.tab === "schedules" && this._s.drawer) this._pageEl.insertAdjacentHTML("beforeend", this._drawerView());
  }

  _renderControllers() {
    const lights = this._lights || [];
    this._controllersEl.innerHTML = lights.length < 2 ? "" : `<select data-in="controller" aria-label="Controller">${lights.map((id) => `<option value="${esc(id)}" ${id === this._entity ? "selected" : ""}>${esc((this._hass.states[id] && this._hass.states[id].attributes.friendly_name) || id)}</option>`).join("")}</select>`;
  }

  _modeState() {
    const entity = this._data.entities.mode;
    const state = (entity && this._hass.states[entity] && this._hass.states[entity].state) || this._data.switch_state;
    if (this._modeShown === state) this._modeShown = null;
    return this._modeShown || state;
  }

  _litState() {
    const light = this._hass.states[this._data.entities.light];
    const state = light ? light.state === "on" : !!(this._data.control && this._data.control.lit);
    if (this._litShown === state) this._litShown = null;
    return this._litShown === null || this._litShown === undefined ? state : this._litShown;
  }

  _heldByYou() {
    const minutes = this._data.control.hold_minutes;
    return { by: "you", until: minutes ? new Date(Date.now() + minutes * 60000).toISOString() : null, window: null };
  }

  _controlText() {
    const c = this._data.control || {};
    if (c.trying) return "Trying a pattern · the lights go back when you stop";
    if (c.by === "music") {
      const id = c.music && c.music.speaker;
      return `Following the music on ${(id && this._hass.states[id] && this._hass.states[id].attributes.friendly_name) || "a speaker"} · the lights go back when it stops`;
    }
    if (c.by === "schedule") return c.window ? `By schedule until ${clockTime(c.window.end)}` : `By schedule · ${this._nextSchedule()}`;
    const who = c.by === "app" ? "Set in the Trimlight app" : "Set by you";
    return c.until ? `${who} · back to schedule at ${clockTime(c.until)}` : `${who} · stays until you change it`;
  }

  _renderNow() {
    const data = this._data;
    const light = this._hass.states[data.entities.light];
    const c = data.control || {};
    const current = data.current || {};
    const lit = this._litState();
    if (light && light.attributes.effect === this._showingName) this._showingName = null;
    const name = this._showingName || (c.window && c.window.effect) || (light && light.attributes.effect)
      || (current.kind === "music" ? current.effect : current.kind === "color" ? "Solid colour" : current.kind === "builtin" ? current.effect : current.kind === "pattern" ? "Unsaved pattern" : "");
    const busy = light && light.attributes.controller_busy;
    const held = c.by !== "schedule" && c.by !== "music" && !c.trying;
    this._nowEl.innerHTML = `<div class="what"><b>${esc(lit ? name || "Lights on" : "Lights off")}</b><small>${esc(this._controlText())}</small>
        ${busy ? `<span class="busy-note" role="status">${esc(BUSY_TEXT)}</span>` : ""}</div>
      <div class="now-actions">
        <button class="btn ${lit ? "" : "primary"}" type="button" data-act="power">${icon("power")}${lit ? "Turn off" : "Turn on"}</button>
        ${held && c.until ? `<button class="btn" type="button" data-act="keep">Keep</button>` : ""}
        ${held ? `<button class="btn tonal" type="button" data-act="resume">${icon("clock")}Back to schedule</button>` : ""}
      </div>`;
    this._tag.textContent = c.trying ? "Trying on the lights · approximate" : lit ? "On the roof now · approximate" : "Lights off";
  }

  _nextSchedule() {
    const data = this._data;
    const now = new Date();
    for (let n = 0; n < 367; n++) {
      const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + n);
      const schedule = data.calendar.find((s) => covers(s, day.getMonth() + 1, day.getDate()));
      if (schedule) {
        const [h, m] = schedule.on.split(":").map(Number);
        if (n > 0 || h * 60 + m > now.getHours() * 60 + now.getMinutes()) {
          const when = schedule.on_sun ? sunLabel(schedule.on_sun).toLowerCase() : schedule.on;
          return `next: ${n === 0 ? "today" : n === 1 ? "tomorrow" : shortDay([day.getMonth() + 1, day.getDate()])}, ${schedule.effect || "a saved effect"} at ${when}`;
        }
      }
    }
    return data.daily.some((d) => d.enabled) ? "daily schedules only" : "no schedule ahead";
  }

  _previewLook() {
    const s = this._s;
    const data = this._data;
    const c = data.control || {};
    const current = data.current || {};
    const fromBuiltin = (name, speed, brightness) => {
      const d = describeBuiltin(name);
      const colors = d.gap ? d.colours.flatMap((x) => [x, null]) : d.colours;
      return { colors, dots: colors.map(() => 4), motion: motionOf(name), speed, brightness };
    };
    const ofEffect = (effect) => (effect.kind === "pattern"
      ? { colors: effect.colors.map((x) => x.color), dots: effect.colors.map((x) => x.dots), motion: motionOf(effect.mode), speed: effect.speed, brightness: effect.brightness }
      : fromBuiltin(effect.effect, effect.speed, effect.brightness));
    if (s.trying && s.draft) {
      return { colors: s.draft.colors.map((x) => x.color), dots: s.draft.colors.map((x) => x.dots), motion: motionOf(s.draft.mode), speed: s.draft.speed, brightness: s.draft.brightness };
    }
    if (!this._litState()) return { off: true, colors: [], dots: [], motion: "static", speed: 0, brightness: 0 };
    const shown = (this._showingName && this._effectByName(this._showingName))
      || (c.window && this._effect(c.window.effect_id === COMBINED_ID && data.combined ? (this._effectByName(data.combined.effects[0]) || {}).id : c.window.effect_id));
    if (shown) return ofEffect(shown);
    if (current.kind === "pattern") return { colors: current.colors.map((x) => x.color), dots: current.colors.map((x) => x.dots), motion: motionOf(current.mode), speed: current.speed, brightness: current.brightness };
    if (current.kind === "builtin") return fromBuiltin(current.effect, current.speed, current.brightness);
    if (current.kind === "music") return { colors: RAINBOW, dots: RAINBOW.map(() => 3), motion: "music", speed: 160, brightness: 255 };
    const light = this._hass.states[data.entities.light];
    const rgb = light && light.attributes.rgb_color;
    return { colors: [rgb ? `rgb(${rgb.join(",")})` : "#f4f1ea"], dots: [1], motion: "static", speed: 0, brightness: current.brightness || 255 };
  }

  /* Patterns */

  _openPattern(source) {
    const s = this._s;
    let look = NEW_PATTERN;
    let name = "";
    let overlay = "none";
    if (source === "current") look = this._data.current;
    else if (typeof source === "number") {
      const effect = this._effect(source);
      look = effect;
      name = effect.stored_name;
      overlay = effect.overlay || "none";
    }
    s.source = source;
    s.selected = 0;
    s.draft = {
      name,
      mode: look.mode || "static",
      speed: look.speed,
      brightness: look.brightness,
      overlay,
      colors: (look.colors && look.colors.length ? look.colors : NEW_PATTERN.colors).map((c) => ({ color: c.color, dots: c.dots })),
    };
  }

  _patterns() {
    const s = this._s;
    const data = this._data;
    const d = s.draft;
    const editing = typeof s.source === "number" ? this._effect(s.source) : null;
    const saved = data.effects.filter((e) => e.kind === "pattern");
    const bar = (colors) => `<span class="bar">${colors.map((c) => `<i style="background:${c.color || "transparent"}"></i>`).join("")}</span>`;
    const name = d.name.trim();
    const saveLabel = editing && name && name !== editing.stored_name ? "Save as new" : "Save";
    const sel = d.colors[s.selected] || d.colors[0];
    return `<div class="patterns">
      <div class="rail" aria-label="Saved patterns">
        <button type="button" data-act="open" data-key="new" aria-current="${s.source === "new"}"><span class="new">${icon("plus")} New pattern</span></button>
        ${data.current && data.current.kind === "pattern" && !this._savedMatch(data.current) ? `<button type="button" data-act="open" data-key="current" aria-current="${s.source === "current"}"><span>Unsaved pattern on the roof</span>${bar(data.current.colors)}<span class="meta">Save it to keep it</span></button>` : ""}
        ${saved.map((e) => `<div class="item"><button type="button" data-act="open" data-key="${e.id}" aria-current="${s.source === e.id}"><span>${esc(e.name)}</span>${bar(e.colors)}<span class="meta">${esc(modeLabel(e.mode))}${e.overlay ? ` · ${e.overlay === "snow" ? "Snow" : "Lightning"}` : ""}</span></button>
          <button type="button" class="play" data-act="show-saved" data-key="${e.id}" title="Show on the lights" aria-label="Show ${esc(e.name)} on the lights">${icon("play")}</button></div>`).join("")}
      </div>
      <div class="editor">
        <div class="editor-head"><h2>${esc(editing ? editing.name : s.source === "current" ? "Unsaved pattern on the roof" : "New pattern")}</h2>
          <span class="note">${editing ? "Saved on the controller." : s.source === "current" ? "Copied from the lights. Give it a name and save it to keep it." : "Pick its colours, then give it a name and save it."}</span></div>
        <div class="card two">
          <label class="field"><span class="label">Name</span><input class="input" data-in="name" maxlength="${data.limits.name}" value="${esc(d.name)}" placeholder="e.g. Fourth of July"></label>
          <label class="field"><span class="label">Mode</span><select class="select" data-in="mode">${data.modes.map((m) => `<option value="${m.key}" ${m.key === d.mode ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></label>
        </div>
        <div class="card">
          <div class="row"><h2>Colours</h2><span class="grow"></span><span class="note">In order along the lights · ${d.colors.length} of ${data.limits.colors}</span></div>
          <div class="colors" style="margin-top:12px">${d.colors.map((c, i) => `<div class="tile ${i === s.selected ? "sel" : ""}" data-act="select" data-key="${i}">
              <span class="num">${i + 1}</span>
              <span class="sw ${c.color ? "" : "unlit"}" style="${c.color ? `background:${c.color}` : ""}" title="${c.color ? c.color : "No colour: these dots stay unlit"}"></span>
              <span class="dots"><button type="button" data-act="dots" data-key="${i}" data-step="-1" aria-label="Fewer dots">−</button><input type="number" min="1" max="${data.limits.dots}" value="${c.dots}" data-in="dots" data-key="${i}" aria-label="Dots for colour ${i + 1}"><button type="button" data-act="dots" data-key="${i}" data-step="1" aria-label="More dots">+</button></span>
            </div>`).join("")}
            ${d.colors.length < data.limits.colors ? `<button class="add-tile" type="button" data-act="add-colour" aria-label="Add a colour">${icon("plus")}</button>` : ""}
          </div>
          <div class="field" style="margin-top:14px"><span class="label">Colour ${s.selected + 1}</span>
            <div class="palette">${PALETTE.map((c) => `<button type="button" class="sw ${c ? "" : "unlit"}" data-act="pick" data-key="${c || ""}" style="${c ? `background:${c}` : ""}" title="${c || "No colour: these dots stay unlit"}"></button>`).join("")}
              <input type="color" data-in="custom" value="${sel.color || "#000000"}" title="Any colour">
              <span class="grow"></span>
              <button class="btn ghost" type="button" data-act="move" data-step="-1" ${s.selected === 0 ? "disabled" : ""} aria-label="Move left">${icon("left")}</button>
              <button class="btn ghost" type="button" data-act="move" data-step="1" ${s.selected === d.colors.length - 1 ? "disabled" : ""} aria-label="Move right">${icon("right")}</button>
              <button class="btn ghost danger" type="button" data-act="remove-colour" ${d.colors.length === 1 ? "disabled" : ""}>${icon("trash")}Remove</button>
            </div></div>
        </div>
        <div class="card" style="display:grid;gap:12px">
          <div class="slider"><span class="label">Speed</span><input type="range" min="0" max="255" value="${d.speed}" data-in="speed"><output>${d.speed}</output></div>
          <div class="slider"><span class="label">Brightness</span><input type="range" min="0" max="255" value="${d.brightness}" data-in="brightness"><output>${percent(d.brightness)}</output></div>
          ${editing ? `<div class="row"><span class="label" style="width:92px">Overlay</span><div class="seg" role="group" aria-label="Overlay">${[["none", "", "None"], ["lightning", "bolt", "Lightning"], ["snow", "snow", "Snow"]].map(([k, ic, l]) => `<button type="button" data-act="overlay" data-key="${k}" aria-pressed="${(editing.overlay || "none") === k}">${ic ? icon(ic) : ""}${l}</button>`).join("")}</div></div>` : ""}
          ${editing && editing.used_by.length ? `<p class="note">Used by ${esc(editing.used_by.join(", "))}, so it can't be deleted.</p>` : ""}
        </div>
        <div class="actions">
          <label class="chip"><input type="checkbox" data-in="try" ${s.trying ? "checked" : ""}> Try on the lights</label>
          <span class="grow"></span>
          ${editing ? `<button class="btn ghost danger" type="button" data-act="delete-pattern" ${editing.used_by.length ? "disabled" : ""}>${icon("trash")}Delete</button>
            <button class="btn" type="button" data-act="rename-pattern" ${!name || name === editing.stored_name ? "disabled" : ""}>${icon("pencil")}Rename</button>` : ""}
          <button class="btn primary" type="button" data-act="save-pattern" ${name ? "" : "disabled"}>${icon("save")}${saveLabel}</button>
        </div>
        ${s.trying ? '<p class="note">Changes show on the lights as you make them. When you stop trying, save, or leave this tab, the lights go back to what they were showing.</p>' : ""}
      </div>
    </div>`;
  }

  _savedMatch(look) {
    if (!look || look.kind !== "pattern") return null;
    const key = (x) => JSON.stringify([x.mode, x.colors.map((c) => [c.color, c.dots])]);
    return this._data.effects.find((e) => e.kind === "pattern" && key(e) === key(look)) || null;
  }

  _patternData() {
    const d = this._s.draft;
    return { mode: d.mode, colors: d.colors.map((c) => c.color || "none"), dots: d.colors.map((c) => c.dots), speed: d.speed, brightness: d.brightness };
  }

  _draftIsSaved(saved) {
    const stored = { mode: saved.mode, colors: saved.colors.map((c) => c.color || "none"), dots: saved.colors.map((c) => c.dots), speed: saved.speed, brightness: saved.brightness };
    return JSON.stringify(this._patternData()) === JSON.stringify(stored);
  }

  /* Effects */

  _effects() {
    const s = this._s;
    const data = this._data;
    const favourites = new Set(data.favourites);
    const q = s.fxQuery.toLowerCase();
    const all = data.builtins.map((name, index) => ({ name, index, ...describeBuiltin(name) }));
    const shown = all.filter((f) => (!q || f.name.toLowerCase().includes(q))
      && (s.fxMotion === "All" || (s.fxMotion === "Favourites" ? favourites.has(f.index) : f.motion === s.fxMotion))
      && (s.fxColour === "All" || (s.fxColour === "Rainbow" ? f.colours === RAINBOW : f.name.split(" ").includes(s.fxColour))));
    const look = (f) => {
      const stops = f.gap ? f.colours.flatMap((c) => [c, "transparent"]) : f.colours;
      const css = stops.map((c, i) => `${c} ${(i / stops.length) * 100}% ${((i + 1) / stops.length) * 100}%`).join(",");
      return `<span class="look"><i style="background:linear-gradient(90deg,${css});${f.background ? "opacity:.55" : ""}"></i></span>`;
    };
    const sel = s.fx !== null && s.fx >= 0 ? all[s.fx] : null;
    const motions = ["All", "Favourites", ...MOTIONS, "Rainbow"];
    return `<div class="fx-top">
        <label class="search">${icon("search")}<input class="input" data-in="fx-query" placeholder="Search ${all.length} effects" value="${esc(s.fxQuery)}"></label>
        <select class="select" data-in="fx-colour" aria-label="Colour">${["All", "Rainbow", ...Object.keys(WORD_COLOURS)].map((c) => `<option value="${c}" ${c === s.fxColour ? "selected" : ""}>${c === "All" ? "Any colour" : c}</option>`).join("")}</select>
      </div>
      <div class="filters" style="margin-top:10px">${motions.map((m) => `<button type="button" class="chip" data-act="motion" data-key="${m}" aria-pressed="${s.fxMotion === m}">${m === "Favourites" ? icon("star") : ""}${m}</button>`).join("")}</div>
      <div class="split">
        <div><p class="count">${shown.length} effect${shown.length === 1 ? "" : "s"}${s.fxMotion === "Favourites" && !shown.length ? ". Star an effect to add it here." : ""}</p>
          <div class="fx-grid">${shown.map((f) => `<div class="fx" role="button" tabindex="0" data-act="fx" data-key="${f.index}" aria-pressed="${s.fx === f.index}">
            ${look(f)}<span class="name">${esc(f.name)}</span><span class="kind">${f.motion} · #${f.index}</span>
            <button type="button" class="star ${favourites.has(f.index) ? "on" : ""}" data-act="favourite" data-key="${f.index}" aria-label="${favourites.has(f.index) ? "Remove from favourites" : "Add to favourites"}">${icon("star")}</button></div>`).join("")}</div></div>
        <div class="card detail">${sel ? `
          <div><h2>${esc(sel.name)}</h2><p class="sub">Built-in effect #${sel.index} · ${sel.motion}. Tap a card to show it on the lights.</p></div>
          <div class="slider"><span class="label">Speed</span><input type="range" min="0" max="255" value="${s.fxSpeed}" data-in="fx-speed"><output>${s.fxSpeed}</output></div>
          <div class="slider"><span class="label">Length</span><input type="range" min="1" max="90" value="${s.fxLength}" data-in="fx-length"><output>${s.fxLength}</output></div>
          <div class="slider"><span class="label">Brightness</span><input type="range" min="0" max="255" value="${s.fxBright}" data-in="fx-bright"><output>${percent(s.fxBright)}</output></div>
          <div class="row"><span class="label" style="width:92px">Direction</span><div class="seg"><button type="button" data-act="reverse" data-key="0" aria-pressed="${!s.fxReverse}">Forward</button><button type="button" data-act="reverse" data-key="1" aria-pressed="${s.fxReverse}">Reverse</button></div></div>
          <div class="field"><span class="label">Save to the controller's library</span>
            <div class="row"><input class="input" style="flex:1" data-in="fx-name" maxlength="${data.limits.name}" value="${esc(s.fxName || sel.name.slice(0, data.limits.name))}"><button class="btn primary" type="button" data-act="save-effect">${icon("save")}Save</button></div>
            <p class="note">Saved effects can be scheduled and used in the combined effect.</p></div>` : '<p class="note">Pick an effect to see its settings.</p>'}
        </div>
      </div>`;
  }

  _effectData() {
    const s = this._s;
    return { effect: this._data.builtins[s.fx], speed: s.fxSpeed, length: s.fxLength, reverse: s.fxReverse, brightness: s.fxBright };
  }

  /* Music */

  _music() {
    const s = this._s;
    const effects = this._data.music.effects;
    const bars = (name, i) => {
      const colours = name.startsWith("Single") ? [s.musicColour] : RAINBOW;
      return Array.from({ length: 9 }, (_, k) => `<i style="height:${18 + Math.round(Math.abs(Math.sin(k * 1.7 + i)) * 80)}%;background:${colours[k % colours.length]}"></i>`).join("");
    };
    const sel = s.music !== null && s.music >= 0 ? effects[s.music] : null;
    const single = sel && sel.startsWith("Single");
    const follow = this._data.control.music;
    const source = s.musicSource || (follow.on ? "ha" : "aux");
    const speakers = Object.keys(this._hass.states).filter((id) => id.startsWith("media_player.")).map((id) => [id, this._hass.states[id].attributes.friendly_name || id]).sort((a, b) => a[1].localeCompare(b[1]));
    const action = source === "ha"
      ? (follow.on
        ? `<button class="btn" type="button" data-act="follow" data-key="off">${icon("music")}Stop following</button>`
        : `<button class="btn primary" type="button" data-act="follow" data-key="on" ${follow.speaker ? "" : "disabled"}>${icon("music")}Follow music</button>`)
      : `<button class="btn primary" type="button" data-act="start-music">${icon("music")}Start music</button>`;
    const hint = source === "ha"
      ? (follow.on ? "Changes here apply straight away. When the song stops, the lights go back to what they were showing." : follow.speaker ? "Uses this effect whenever the speaker plays a song." : "Pick a speaker first.")
      : "To stop, pick any pattern or effect, or turn the lights off.";
    return `<div class="split" style="margin-top:0">
      <div>
        <div class="card" style="margin-bottom:16px"><h2>Sound source</h2><p class="sub">Where the lights hear the music.</p>
          <div class="source">
            <label data-act="music-source" data-key="aux" aria-pressed="${source === "aux"}"><span><b>The controller's audio input (AUX)</b><small>A line from your amplifier, or a small microphone, plugged into the controller. It listens itself, so the lights stay in time.${this._data.music.source === "aux" ? " Your controller is set up this way." : ""}</small></span></label>
            <label data-act="music-source" data-key="ha" aria-pressed="${source === "ha"}"><span><b>Music played through Home Assistant</b><small>The lights follow a speaker while it plays a song Home Assistant can read itself, such as a file in My media. Spotify and radio apps don't share their sound.</small></span></label>
          </div>
          ${source === "ha" ? `<div class="follow">
            <label class="field"><span class="label">Speaker</span><select class="select" data-in="follow-speaker"><option value="">Pick a speaker</option>${speakers.map(([id, name]) => `<option value="${esc(id)}" ${id === follow.speaker ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></label>
            <div class="slider"><span class="label">Delay</span><input type="range" min="${follow.delay_range[0]}" max="${follow.delay_range[1]}" step="50" value="${follow.delay_ms}" data-in="follow-delay"><output>${follow.delay_ms} ms</output></div>
            <p class="note">Raise the delay if the lights run ahead of the music, lower it if they lag behind.</p>
            <p class="follow-status" role="status">${esc(this._followText())}</p>
          </div>` : ""}</div>
        <p class="count">${effects.length} music effects</p>
        <div class="mx-grid">${effects.map((m, i) => `<button type="button" class="mx" data-act="music" data-key="${i}" aria-pressed="${s.music === i}"><span class="eq">${bars(m, i)}</span><span class="name">${esc(m)}</span></button>`).join("")}</div>
      </div>
      <div class="card detail">${sel ? `
        <div><h2>${esc(sel)}</h2><p class="sub">${single ? "Uses the colour below." : "Uses the full colour range."}</p></div>
        <div class="row"><span class="label" style="width:92px">Colour</span><input type="color" data-in="music-colour" value="${s.musicColour}" ${single ? "" : "disabled"} style="width:44px;height:30px;border:0;background:none;padding:0">${single ? "" : '<span class="note">Only for single-colour effects</span>'}</div>
        <div class="slider"><span class="label">Length</span><input type="range" min="1" max="150" value="${s.musicLength}" data-in="music-length"><output>${s.musicLength}</output></div>
        <div class="slider"><span class="label">Sensitivity</span><input type="range" min="1" max="16" value="${s.musicSens}" data-in="music-sens"><output>${s.musicSens}</output></div>
        <div class="actions">${action}</div>
        <p class="note">${hint}</p>` : '<p class="note">Pick a music effect.</p>'}
      </div>
    </div>`;
  }

  _followText() {
    const f = this._data.control.music;
    const name = (id) => (id && this._hass.states[id] && this._hass.states[id].attributes.friendly_name) || id || "the speaker";
    const speaker = name(f.speaker);
    if (!f.on) return f.speaker ? `Not following ${speaker}.` : "Pick the speaker that plays your music.";
    if (f.status === "following") return `Following ${speaker}${f.title ? `: ${f.title}` : ""}.`;
    if (f.status === "loading") return `Getting the song on ${speaker} ready…`;
    if (f.status === "unsupported") return `${speaker} is playing something the lights can't follow. ${f.detail || ""}`.trim();
    if (f.status === "error") return `Stopped following: ${f.detail || "the controller didn't answer"}.`;
    return f.detail ? `${f.detail}.` : `Waiting for ${speaker} to play a song.`;
  }

  _musicLook() {
    const s = this._s;
    const look = { length: s.musicLength, sensitivity: s.musicSens, color: [1, 3, 5].map((n) => parseInt(s.musicColour.slice(n, n + 2), 16)) };
    if (s.music !== null && s.music >= 0) look.effect = s.music;
    return look;
  }

  _sendFollow(fields) {
    const f = this._data.control.music;
    const before = { ...f };
    return this._ws("music_follow", fields, null, { quiet: true, apply: () => Object.assign(f, fields), undo: () => Object.assign(f, before) });
  }

  /* Schedules */

  _schedules() {
    const s = this._s;
    const data = this._data;
    const mode = this._modeState();
    const sorted = [...data.calendar].sort((a, b) => a.start[0] - b.start[0] || a.start[1] - b.start[1]);
    const range = (x) => (x.start[0] === x.end[0] && x.start[1] === x.end[1] ? shortDay(x.start) : `${shortDay(x.start)} – ${shortDay(x.end)}`);
    const follows = (x) => (x.holiday ? ` · follows ${esc(this._holidayName(x.holiday.key))}${x.holiday.skips && x.holiday.skips.length ? `, skips ${x.holiday.skips.map((k) => k.year).join(", ")}` : ""}` : "");
    const onText = (x) => (x.on_sun ? sunLabel(x.on_sun) : x.on);
    const offText = (x) => (x.off_sun ? sunLabel(x.off_sun) : `${x.off}${!x.on_sun && x.off <= x.on ? " next day" : ""}`);
    const sunWarning = data.sun && data.sun.location_ok === false
      ? `<div class="warn-box"><b>Check the home location.</b> Home Assistant puts sunset at ${esc(data.sun.sunset)} here, so its home location doesn't match its time zone. Set it under Settings → System → General before using sunset or sunrise times.</div>` : "";
    const waiting = data.waiting || [];
    const stuck = [...data.calendar, ...waiting].filter((x) => x.holiday && x.holiday.problem)
      .map((x) => `<div class="warn-box"><b>${typeof x.id === "number" ? `Schedule ${x.id} didn't move.` : `The waiting ${esc(this._holidayName(x.holiday.key))} schedule is stuck.`}</b><span>${esc(x.holiday.problem)}</span><button class="btn tonal" type="button" data-act="edit" data-key="${x.id}">Open it</button></div>`).join("");
    const waitingRow = (w) => `<button type="button" class="waiting" data-act="edit" data-key="${w.id}"><i style="background:${this._colourOf(w.effect)}"></i><span>${esc(w.effect || "Unknown effect")}<small>${esc(this._waitingText(w))}</small></span><span class="t">${icon("clock")}</span></button>`;
    const c = data.control || {};
    const paused = c.by === "schedule" ? "" : `<div class="warn-box">${icon("clock")}<span><b>Schedules are paused</b> while ${c.by === "app" ? "a change made in the Trimlight app" : "your change"} is showing. ${c.until ? `They take over again at ${clockTime(c.until)}.` : "It stays until you change it."}</span>
        <button class="btn tonal" type="button" data-act="resume">Back to schedule</button></div>`;
    let head = `${sunWarning}${stuck}${paused}
      <div class="sch-top"><div class="seg" role="group" aria-label="View">${[["year", "Year"], ["month", "Month"], ["list", "List"]].map(([k, l]) => `<button type="button" data-act="view" data-key="${k}" aria-pressed="${s.view === k}">${l}</button>`).join("")}</div>
        ${s.view === "month" ? `<button class="btn ghost" type="button" data-act="step" data-step="-1" aria-label="Previous month">${icon("left")}</button><b>${MONTH_NAMES[s.month.getMonth()]} ${s.month.getFullYear()}</b><button class="btn ghost" type="button" data-act="step" data-step="1" aria-label="Next month">${icon("right")}</button>` : s.view === "year" ? `<b>Every year</b>` : ""}
        <span class="grow"></span><button class="btn primary" type="button" data-act="new-schedule" ${data.entities.calendar ? "" : "disabled"}>${icon("plus")}Add schedule</button></div>`;
    let body;
    const today = new Date();
    if (s.view === "year") {
      const year = today.getFullYear();
      body = `<div class="year"><div class="months">${MONTH_NAMES.map((name, mi) => {
        const first = new Date(year, mi, 1).getDay();
        const days = new Date(year, mi + 1, 0).getDate();
        const cells = Array.from({ length: first }, () => '<span class="blank"></span>').concat(Array.from({ length: days }, (_, k) => {
          const x = data.calendar.find((c) => covers(c, mi + 1, k + 1));
          const isToday = mi === today.getMonth() && k + 1 === today.getDate();
          return `<span class="${x ? "on" : ""} ${isToday ? "today" : ""}" data-act="day" data-key="${mi + 1}-${k + 1}" ${x ? `style="background:${this._colourOf(x.effect)}" title="${esc(x.effect || "")}, ${esc(onText(x))}"` : 'title="Add a schedule"'}>${k + 1}</span>`;
        }));
        return `<div class="mini"><h3>${name}</h3><div class="g">${cells.join("")}</div></div>`;
      }).join("")}</div>
        <div class="legend">${sorted.length ? sorted.map((x) => `<button type="button" data-act="edit" data-key="${x.id}"><i style="background:${this._colourOf(x.effect)}"></i><span>${esc(x.effect || "Unknown effect")}<small>${range(x)}${follows(x)}</small></span><span class="t">${x.on_sun ? icon("sunset") : esc(x.on)}<br>${esc(x.off_sun ? "" : x.off)}</span></button>`).join("") : '<p class="note">No schedules yet. Click a day to add one.</p>'}${waiting.map(waitingRow).join("")}</div></div>`;
    } else if (s.view === "month") {
      const m = s.month;
      const first = new Date(m);
      first.setDate(1 - m.getDay());
      const weeks = Math.ceil((m.getDay() + new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate()) / 7);
      let rows = "";
      for (let w = 0; w < weeks; w++) {
        const days = Array.from({ length: 7 }, (_, k) => new Date(first.getFullYear(), first.getMonth(), first.getDate() + w * 7 + k));
        const cells = days.map((d) => `<span class="${d.getMonth() !== m.getMonth() ? "out" : ""} ${d.toDateString() === today.toDateString() ? "today" : ""}" data-act="day" data-key="${d.getMonth() + 1}-${d.getDate()}"><b>${d.getDate()}</b></span>`).join("");
        const bars = [];
        let k = 0;
        while (k < 7) {
          const x = [...data.calendar, ...waiting].find((c) => this._coversOn(c, days[k]));
          if (!x) { k++; continue; }
          let e = k;
          while (e + 1 < 7 && this._coversOn(x, days[e + 1])) e++;
          bars.push(`<button type="button" data-act="edit" data-key="${x.id}" style="grid-column:${k + 1} / ${e + 2};background:${this._colourOf(x.effect)}" title="${esc(x.effect || "")}, ${esc(onText(x))} – ${esc(offText(x))}">${esc(onText(x))} ${esc(x.effect || "")}</button>`);
          k = e + 1;
        }
        data.daily.forEach((d, i) => {
          if (!d.enabled || d.repeat === "today_only") return;
          const runs = days.map((day) => d.repeat === "every_day" || (d.repeat === "weekend") === (day.getDay() === 0 || day.getDay() === 6));
          runs.forEach((on, col) => { if (on) bars.push(`<button type="button" class="daily" data-act="tab" data-key="daily" style="grid-column:${col + 1};background:${this._colourOf(d.effect)}" title="Daily schedule ${i + 1}">${esc(d.on_sun ? sunLabel(d.on_sun) : d.on)} daily</button>`); });
        });
        rows += `<div class="week">${cells}<div class="bars">${bars.join("")}</div></div>`;
      }
      body = `<div class="month"><div class="dow">${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => `<span>${d}</span>`).join("")}</div>${rows}</div>`;
    } else {
      body = `<div class="list">${sorted.length ? sorted.map((x) => `<button type="button" data-act="edit" data-key="${x.id}"><i style="background:${this._colourOf(x.effect)}"></i><span><b>${range(x)}</b>${x.holiday ? `<small class="note" style="display:block">${follows(x).slice(3)}</small>` : ""}</span><span class="mono">${esc(onText(x))} – ${esc(offText(x))}</span><span>${esc(x.effect || "Unknown effect")}</span></button>`).join("") : '<p class="note">No schedules yet.</p>'}${waiting.map(waitingRow).join("")}</div>`;
    }
    return `${head}${body}<p class="note" style="margin-top:14px">Each schedule repeats every year, one per date, or follows a holiday such as Easter or Thanksgiving and moves with it. Times can follow sunset or sunrise. Home Assistant keeps both up to date. Saving carries on in the background: ${SCHEDULE_PAUSE}.</p>`;
  }

  _timeSet(label, setting, key) {
    const sunToday = this._data.sun;
    const base = setting.mode !== "time" && sunToday ? sunToday[setting.mode] : null;
    return `<div class="timeset"><span class="label">${label}</span>
      <div class="seg" role="group" aria-label="${label}">${[["time", "clock", "Time"], ["sunset", "sunset", "Sunset"], ["sunrise", "sun", "Sunrise"]].map(([k, ic, l]) => `<button type="button" data-act="time-mode" data-key="${key}" data-mode="${k}" aria-pressed="${setting.mode === k}">${icon(ic)}${l}</button>`).join("")}</div>
      <div class="row">${setting.mode === "time"
        ? `<input class="input mono" type="time" value="${setting.clock}" data-in="clock" data-key="${key}" aria-label="${label} time">`
        : `<input class="input mono" type="number" step="5" min="-240" max="240" value="${setting.offset}" data-in="offset" data-key="${key}" aria-label="Minutes after; negative is before" style="width:90px"><span class="note">min</span>${base ? `<span class="hint">${icon("sun")}<span>today ${shiftClock(base, setting.offset)}</span></span>` : ""}`}</div></div>`;
  }

  _timeTarget(key) {
    return key.startsWith("daily") ? this._s.daily[Number(key.slice(5, 6))][key.slice(6)] : this._s.drawer[key];
  }

  _drawerView() {
    const dr = this._s.drawer;
    const data = this._data;
    const names = [...data.effects.map((e) => e.name), data.combined_name];
    const monthSel = (key) => `<select class="select" data-in="drawer-month" data-key="${key}">${MONTHS.map((m, i) => `<option value="${i + 1}" ${i + 1 === dr[key][0] ? "selected" : ""}>${m}</option>`).join("")}</select>`;
    const existing = dr.id !== null ? data.calendar.find((c) => c.id === dr.id) : dr.waiting ? (data.waiting || []).find((w) => w.id === dr.waiting) : null;
    return `<aside class="drawer" aria-label="Schedule">
      <div class="row"><h2>${dr.waiting ? "Waiting schedule" : dr.id === null ? "New schedule" : `Schedule ${dr.id}`}</h2><span class="grow"></span><button class="btn ghost" type="button" data-act="close-drawer" aria-label="Close">${icon("close")}</button></div>
      ${dr.waiting && existing ? `<div class="warn-box">${icon("clock")}<span>${esc(this._waitingText(existing))}. Home Assistant holds it off the controller until then, because the controller repeats a schedule's dates every year and would light it on the wrong night.</span></div>` : ""}
      <label class="field"><span class="label">Effect</span><select class="select" data-in="drawer-effect">${names.map((n) => `<option ${n === dr.effect ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
      ${data.holidays && data.holidays.length ? `<div class="field"><span class="label">Dates</span><div class="seg" role="group" aria-label="Dates">${[["fixed", "Same every year"], ["holiday", "Follow a holiday"]].map(([k, l]) => `<button type="button" data-act="date-mode" data-key="${k}" aria-pressed="${dr.dateMode === k}">${l}</button>`).join("")}</div></div>` : ""}
      ${dr.dateMode === "holiday" ? this._holidayFields() : `<div class="dates"><label class="field"><span class="label">From</span><span class="row">${monthSel("start")}<input class="input mono" style="width:64px" type="number" min="1" max="31" value="${dr.start[1]}" data-in="drawer-day" data-key="start"></span></label>
        <label class="field"><span class="label">To</span><span class="row">${monthSel("end")}<input class="input mono" style="width:64px" type="number" min="1" max="31" value="${dr.end[1]}" data-in="drawer-day" data-key="end"></span></label></div>`}
      ${this._timeSet("On", dr.on, "on")}${this._timeSet("Off", dr.off, "off")}
      <p class="note">An off time earlier than the on time is the next morning. Built-in effects can't be scheduled directly: save one on the Effects tab first.</p>
      ${existing && existing.holiday && existing.holiday.problem ? `<div class="warn-box"><b>${dr.waiting ? "It's stuck." : "It didn't move."}</b><span>${esc(existing.holiday.problem)}</span></div>` : ""}
      <div class="actions"><button class="btn primary" type="button" data-act="save-schedule" ${dr.dateMode === "holiday" && dr.from > dr.to ? "disabled" : ""}>${icon("save")}Save</button>${existing ? `<button class="btn ghost danger" type="button" data-act="delete-schedule">${icon("trash")}Delete</button>` : ""}</div>
    </aside>`;
  }

  _holidayFields() {
    const dr = this._s.drawer;
    const groups = [...new Set(this._data.holidays.map((h) => h.group))];
    const options = groups.map((g) => `<optgroup label="${esc(g)}">${this._data.holidays.filter((h) => h.group === g).map((h) => `<option value="${h.key}" ${h.key === dr.holiday ? "selected" : ""}>${esc(h.name)}</option>`).join("")}</optgroup>`).join("");
    const offset = (key, label) => `<label class="field"><span class="label">${label}</span><span class="row"><input class="input mono" style="width:72px" type="number" min="-60" max="60" value="${dr[key]}" data-in="drawer-hoff" data-key="${key}"><span class="note">days</span></span></label>`;
    return `<label class="field"><span class="label">Holiday</span><select class="select" data-in="drawer-holiday">${options}</select></label>
      <div class="dates">${offset("from", "First night")}${offset("to", "Last night")}</div>
      <p class="note">Days from the holiday: −2 is two days before it, 0 is the day itself.</p>
      <div class="field"><span class="label">Coming years</span><ul class="years">${this._holidayPreview()}</ul></div>`;
  }

  _holidayPreview() {
    const dr = this._s.drawer;
    const holiday = this._data.holidays.find((h) => h.key === dr.holiday);
    if (dr.from > dr.to) return '<li class="clash">The last night can\'t come before the first.</li>';
    const windows = holidayWindows(holiday, dr.from, dr.to, 3);
    if (!windows.length) return "<li>Home Assistant doesn't know this holiday's coming dates.</li>";
    const others = [...this._data.calendar.filter((x) => x.id !== dr.id), ...(this._data.waiting || []).filter((w) => w.id !== dr.waiting)];
    const rank = (key) => (key === null ? 0 : (this._data.holidays.find((h) => h.key === key) || {}).group === "US holidays" ? 1 : 2);
    const label = (x) => (typeof x.id === "number" ? `schedule ${x.id}${x.effect ? ` (${x.effect})` : ""}` : `the waiting ${this._holidayName(x.holiday.key)} schedule`);
    return `<li><span>${esc(aroundText(holiday.name, dr.from, dr.to))}:</span></li>` + windows.map(([year, first, last]) => {
      let keeps = null;
      let takes = null;
      for (const day = new Date(first); day <= last && !keeps; day.setDate(day.getDate() + 1)) {
        for (const x of others.filter((o) => this._coversOn(o, day))) {
          if (rank(x.holiday ? x.holiday.key : null) <= rank(dr.holiday)) keeps = x;
          else takes = takes || x;
        }
      }
      const span = first.getTime() === last.getTime() ? dayName(first) : `${dayName(first)} – ${dayName(last)}`;
      const note = keeps ? `skipped: ${esc(label(keeps))} keeps those nights` : takes ? `takes those nights from ${esc(label(takes))}` : "";
      return `<li class="${keeps ? "clash" : ""}"><b>${year}</b><span>${span}</span>${note ? `<span>${note}</span>` : ""}</li>`;
    }).join("");
  }

  _refreshHolidayPreview() {
    const list = this.shadowRoot.querySelector(".drawer .years");
    if (list) list.innerHTML = this._holidayPreview();
    const save = this.shadowRoot.querySelector('.drawer [data-act="save-schedule"]');
    if (save) save.disabled = this._s.drawer.from > this._s.drawer.to;
  }

  _holidayName(key) {
    const holiday = (this._data.holidays || []).find((h) => h.key === key);
    return holiday ? holiday.name : key;
  }

  _coversOn(x, day) {
    if (x.holiday) {
      const holiday = (this._data.holidays || []).find((h) => h.key === x.holiday.key);
      const skipped = new Set((x.holiday.skips || []).map((k) => k.year));
      let known = false;
      for (const year of [day.getFullYear() - 1, day.getFullYear(), day.getFullYear() + 1]) {
        const w = holidayWindow(holiday, year, x.holiday.start_offset, x.holiday.end_offset);
        if (!w) continue;
        known = known || year === day.getFullYear();
        if (day >= w[0] && day <= w[1]) return !skipped.has(w[0].getFullYear());
      }
      if (known || !x.start) return false;
    }
    return covers(x, day.getMonth() + 1, day.getDate());
  }

  _waitingText(w) {
    const parse = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
    const name = this._holidayName(w.holiday.key);
    if (!w.next) return `Follows ${name}; its next dates aren't known`;
    const next = parse(w.next[0]);
    const from = parse(w.from);
    const skips = (w.holiday.skips || []).map((k) => k.year);
    return `Follows ${name}${skips.length ? `, skips ${skips.join(", ")}` : ""}. Next on ${dayName(next)}, ${next.getFullYear()}; on the controller from ${MONTHS[from.getMonth()]} ${from.getDate()}, ${from.getFullYear()}`;
  }

  _nextHoliday() {
    const holidays = this._data.holidays || [];
    const coming = holidays.map((h) => [h.key, (holidayWindows(h, 0, 0, 1)[0] || [])[1]]).filter(([, d]) => d);
    coming.sort((a, b) => a[1] - b[1]);
    return coming.length ? coming[0][0] : holidays.length ? holidays[0].key : null;
  }

  _openSchedule(id, day = null) {
    const data = this._data;
    const names = [...data.effects.map((e) => e.name), data.combined_name];
    const setting = (clock, sun) => (sun ? { mode: sun.event, offset: sun.offset, clock } : { mode: "time", offset: 0, clock });
    if (typeof id === "string") {
      const w = (data.waiting || []).find((x) => x.id === id);
      const h = w.holiday;
      this._s.drawer = { id: null, waiting: w.id, effect: w.effect || names[0], start: [12, 1], end: [12, 1], on: setting(w.on || "18:00", w.on_sun), off: setting(w.off || "23:00", w.off_sun),
        dateMode: "holiday", holiday: h.key, from: h.start_offset, to: h.end_offset };
    } else if (id !== null) {
      const x = data.calendar.find((c) => c.id === id);
      const h = x.holiday;
      this._s.drawer = { id: x.id, effect: x.effect || names[0], start: [...x.start], end: [...x.end], on: setting(x.on, x.on_sun), off: setting(x.off, x.off_sun),
        dateMode: h ? "holiday" : "fixed", holiday: h ? h.key : this._nextHoliday(), from: h ? h.start_offset : 0, to: h ? h.end_offset : 0 };
    } else {
      const start = day || [12, 1];
      this._s.drawer = { id: null, effect: names[0], start: [...start], end: day ? [...start] : [12, 31], on: { mode: "sunset", offset: 0, clock: "17:00" }, off: { mode: "time", offset: 0, clock: "23:00" },
        dateMode: "fixed", holiday: this._nextHoliday(), from: 0, to: 0 };
    }
  }

  /* Daily */

  _dailyDraft(d) {
    const setting = (clock, sun) => (sun ? { mode: sun.event, offset: sun.offset, clock } : { mode: "time", offset: 0, clock });
    return { enabled: d.enabled, effect: d.effect, repeat: d.repeat, on: setting(d.on, d.on_sun), off: setting(d.off, d.off_sun), dirty: false };
  }

  _daily() {
    const data = this._data;
    if (!data.daily.length) return '<div class="empty">The controller did not report its daily schedules.</div>';
    const names = [...data.effects.map((e) => e.name), data.combined_name];
    const minutes = (t) => {
      const clock = t.mode === "time" ? t.clock : data.sun && data.sun[t.mode] ? shiftClock(data.sun[t.mode], t.offset) : t.clock;
      const [h, m] = clock.split(":").map(Number);
      return h * 60 + m;
    };
    return `<div class="daily">${this._s.daily.map((d, i) => {
      const on = minutes(d.on);
      const off = minutes(d.off);
      const spans = off > on ? [[on, off]] : [[on, 1440], [0, off]];
      return `<div class="card" style="display:grid;gap:14px">
        <div class="row"><h2>Daily schedule ${i + 1}</h2><span class="grow"></span><button class="switch" type="button" role="switch" data-act="daily-enabled" data-key="${i}" aria-checked="${d.enabled}" aria-label="Use daily schedule ${i + 1}"></button></div>
        <div><div class="day24">${spans.map(([a, b]) => `<i style="left:${(a / 1440) * 100}%;width:${((b - a) / 1440) * 100}%;opacity:${d.enabled ? 0.85 : 0.3}"></i>`).join("")}</div><div class="hours"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div></div>
        <label class="field"><span class="label">Effect</span><select class="select" data-in="daily-effect" data-key="${i}">${d.effect ? "" : '<option value="" selected disabled>Choose an effect</option>'}${names.map((n) => `<option ${n === d.effect ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
        <div class="field"><span class="label">Repeat</span><div class="filters">${REPEAT_CHIPS.map(([k, l]) => `<button type="button" class="chip" data-act="daily-repeat" data-key="${i}" data-mode="${k}" aria-pressed="${d.repeat === k}">${l}</button>`).join("")}</div></div>
        <div class="two">${this._timeSet("On", d.on, `daily${i}on`)}${this._timeSet("Off", d.off, `daily${i}off`)}</div>
        <div class="actions"><span class="grow"></span><button class="btn ghost" type="button" data-act="daily-revert" data-key="${i}" ${d.dirty ? "" : "disabled"}>Revert</button><button class="btn primary" type="button" data-act="daily-save" data-key="${i}" ${d.dirty && d.effect ? "" : "disabled"}>${icon("save")}Save</button></div>
      </div>`;
    }).join("")}</div><p class="note" style="margin-top:14px">The two can't overlap on a day they share. Daily schedules run in Timer mode, and saving takes about 8 seconds.</p>`;
  }

  /* Combined */

  _combinedDraft() {
    const combined = this._data.combined || { effects: [], interval: 3 };
    return { effects: [...combined.effects], interval: combined.interval || 3, dirty: false };
  }

  _combinedView() {
    const data = this._data;
    const draft = this._s.combined;
    const users = [
      ...data.calendar.filter((x) => x.effect === data.combined_name).map((x) => `the ${shortDay(x.start)} schedule`),
      ...data.daily.map((d, i) => (d.effect === data.combined_name ? `daily schedule ${i + 1}` : null)).filter(Boolean),
    ];
    const bar = (name) => {
      const effect = this._effectByName(name);
      return effect && effect.colors ? `<span class="bar">${effect.colors.map((c) => `<i style="background:${c.color || "transparent"}"></i>`).join("")}</span>` : "";
    };
    return `<div class="two"><div class="card" style="display:grid;gap:14px">
        <h2>Plays in turn</h2>
        <div class="combo">${draft.effects.length ? draft.effects.map((name, i) => `<div class="item"><span class="mono note">${i + 1}</span><span>${esc(name)}${bar(name)}</span>
          <span class="row"><button class="btn ghost" type="button" data-act="combined-up" data-key="${i}" ${i === 0 ? "disabled" : ""} aria-label="Move up">${icon("up")}</button><button class="btn ghost" type="button" data-act="combined-remove" data-key="${i}" aria-label="Remove">${icon("close")}</button></span></div>`).join("") : '<p class="note">Add at least one saved effect.</p>'}</div>
        <div class="row"><select class="select" style="flex:1" data-in="combined-pick">${data.effects.map((e) => `<option>${esc(e.name)}</option>`).join("")}</select><button class="btn tonal" type="button" data-act="combined-add">${icon("plus")}Add</button></div>
        <div class="slider"><span class="label">Each plays</span><input type="range" min="1" max="10" value="${draft.interval}" data-in="combined-interval"><output>${draft.interval} min</output></div>
        <div class="actions"><span class="grow"></span><button class="btn ghost" type="button" data-act="combined-revert" ${draft.dirty ? "" : "disabled"}>Revert</button><button class="btn primary" type="button" data-act="combined-save" ${draft.dirty && draft.effects.length ? "" : "disabled"}>${icon("save")}Save</button></div></div>
      <div class="card"><h2>One cycle</h2><p class="sub">${draft.effects.length * draft.interval} minutes, then it starts again.</p>
        <div class="timeline">${draft.effects.map((name) => `<i style="background:${this._colourOf(name)}">${draft.interval}m</i>`).join("")}</div>
        <p class="note" style="margin-top:14px">Used by ${users.length ? esc(users.join(", ")) : "no schedule yet"}. Pick "${esc(data.combined_name)}" as a schedule's effect to use it.</p></div></div>`;
  }

  /* Settings */

  _settings() {
    const device = this._data.device || {};
    const fact = (label, value) => `<dt>${label}</dt><dd>${esc(value ?? "Not reported")}</dd>`;
    const name = this._s.ctlName ?? device.name ?? "";
    return `${this._ledCard(device)}
      <div class="two" style="margin-top:18px">
        <div class="card"><h2>Controller</h2><p class="sub">What the controller reports about itself.</p>
          <div class="field" style="margin-bottom:16px"><span class="label">Name</span>
            <div class="row"><input class="input" style="flex:1" data-in="ctl-name" maxlength="25" value="${esc(name)}" aria-label="Controller name"><button class="btn" type="button" data-act="rename-controller" ${this._nameChanged() ? "" : "disabled"}>${icon("pencil")}Rename</button></div>
            <p class="note">The name the phone app shows: up to 25 letters, digits, spaces, - and _.</p></div>
          <dl class="facts">${fact("Address", device.host)}${fact("Firmware", device.firmware)}${fact("MCU firmware", device.mcu_version)}${fact("Controller clock", device.clock ? device.clock.replace("T", " ") : null)}${fact("Clock sync", device.time_sync === "auto" ? "Automatic: Home Assistant sets it on every update" : device.time_sync === "manual" ? "Manual" : null)}</dl></div>
        <div class="card"><h2>Changes you make</h2><p class="sub">Timer is the controller's normal mode: its schedules run the lights. A change made here, in Home Assistant or in the Trimlight app lasts this long, or until a schedule next switches the lights on or off if that's sooner. Then the controller goes back to Timer.</p>
          <div class="seg" role="group" aria-label="Changes last">${(this._data.control.hold_choices || []).map((m) => `<button type="button" data-act="hold" data-key="${m ?? "keep"}" aria-pressed="${this._data.control.hold_minutes === m}">${holdLabel(m)}</button>`).join("")}</div></div>
        <div class="card"><h2>Roofline picture</h2><p class="sub">The house the preview at the top lights up.</p>
          <div class="seg" role="group" aria-label="Roofline picture">${Object.entries(ROOFLINES).map(([key, shape]) => `<button type="button" data-act="roofline" data-key="${key}" aria-pressed="${this._data.roofline === key}">${esc(shape.name)}</button>`).join("")}</div></div>
      </div>`;
  }

  _ledCard(device) {
    const s = this._s;
    const session = device.led_session;
    const max = device.max_port_dot || 2700;
    const swatch = (colour) => `<i class="dot" style="background:${colour}"></i>`;
    const banner = !session ? "" : `<div class="session" role="status">
        <span class="grow"><b>${session.changed ? "Trying new LED settings" : "Showing a test"}</b>
          <small>${session.changed ? "They're undone" : "What was on before comes back"} in <span class="countdown">${this._ledSecondsLeft()}</span> s${session.changed ? " unless you keep them" : ""}. Every test or change restarts the countdown.</small></span>
        ${session.changed
          ? `<button class="btn" type="button" data-act="led-end" data-key="undo">Undo</button><button class="btn primary" type="button" data-act="led-end" data-key="keep">${icon("save")}Keep</button>`
          : `<button class="btn" type="button" data-act="led-end" data-key="undo">Done</button>`}
      </div>`;
    const tests = `<div class="field"><span class="label">Test colours</span>
        <div class="filters">${["Red", "Green", "Blue", "White"].map((c) => `<button type="button" class="chip" data-act="led-test" data-key="${c.toLowerCase()}">${swatch(WORD_COLOURS[c])}${c}</button>`).join("")}<button type="button" class="chip" data-act="led-test" data-key="pattern"><i class="dot rgbw"></i>All four in blocks</button></div>
        <p class="note">Shows a test on every light, then puts back what was on.</p></div>`;
    const chips = `<div class="field"><span class="label">LED chip</span>
        <div class="filters" role="group" aria-label="LED chip">${(device.ic_types || []).map((c) => `<button type="button" class="chip" data-act="led-chip" data-key="${c.value}" aria-pressed="${device.ic_type_value === c.value}">${esc(c.name)}</button>`).join("")}</div>
        <p class="note">Flickering or random colours mean the chip type is wrong. Tap another one to try it.</p></div>`;
    const orders = `<div class="field"><span class="label">Colour order</span>
        <div class="filters" role="group" aria-label="Colour order">${(device.channel_orders || []).map((o) => `<button type="button" class="chip mono" data-act="led-order" data-key="${o.value}" aria-pressed="${device.channel_order_value === o.value}">${esc(o.name)}</button>`).join("")}</div>
        ${s.led ? this._ledWizard(device) : `<div class="row"><button class="btn tonal" type="button" data-act="led-wizard" data-key="start">${icon("bulb")}Find the right order</button><span class="note">Red showing as green or blue? Two questions fix it.</span></div>`}</div>`;
    const rows = s.ports.rows.map((p, i) => {
      const ok = portOk(p, max);
      return `<tr data-port="${i}" class="${ok ? "" : "bad"}"><td>Port ${i + 1}</td>
        <td><input class="input num" type="number" min="1" max="${max}" value="${Number.isFinite(p.start) ? p.start : ""}" data-in="port-start" data-key="${i}" aria-label="Port ${i + 1} first dot"></td>
        <td><input class="input num" type="number" min="1" max="${max}" value="${Number.isFinite(p.end) ? p.end : ""}" data-in="port-end" data-key="${i}" aria-label="Port ${i + 1} last dot"></td>
        <td class="mono count">${ok ? p.end - p.start + 1 : "–"}</td>
        <td><input type="checkbox" data-in="port-on" data-key="${i}" ${p.enabled ? "checked" : ""} aria-label="Use port ${i + 1}"></td></tr>`;
    }).join("");
    const ports = `<div class="field"><span class="label">Ports</span>
        <div class="table-wrap"><table class="ports"><thead><tr><th>Output</th><th>First dot</th><th>Last dot</th><th>Dots</th><th>On</th></tr></thead><tbody>${rows}</tbody></table></div>
        <p class="note">Dots past a port's last dot stay dark. Each port can use dots 1 to ${max}.</p>
        <div class="actions"><span class="grow"></span><button class="btn ghost" type="button" data-act="ports-revert" ${s.ports.dirty ? "" : "disabled"}>Revert</button><button class="btn primary" type="button" data-act="ports-try" ${this._portsReady() ? "" : "disabled"}>${icon("play")}Try</button></div></div>`;
    return `<div class="card led"><h2>LED setup</h2><p class="sub">How the controller drives the lights. A change is tried for a minute and undone unless you keep it.</p>
      ${banner}
      <div class="led-grid"><div class="led-col">${tests}${chips}${orders}</div><div class="led-col">${ports}</div></div></div>`;
  }

  _ledWizard(device) {
    const w = this._s.led;
    const answer = (key, label, colour) => `<button class="btn" type="button" data-act="led-wizard" data-key="${key}">${colour ? `<i class="dot" style="background:${colour}"></i>` : ""}${label}</button>`;
    const colours = ["Red", "Green", "Blue"].map((c) => answer(c[0], c, WORD_COLOURS[c])).join("") + answer("X", "Something else");
    const steps = {
      red: `<p><b>1 of 2.</b> Every light should be red now. What colour are they?</p><div class="actions">${colours}</div>`,
      green: `<p><b>2 of 2.</b> Now they should be green. What colour are they?</p><div class="actions">${colours}</div>`,
      check: `<p>The colour order is now <b>${esc(w.name)}</b>, so the lights should be red. Are they?</p><div class="actions">${answer("yes", "Yes, keep it")}${answer("no", "No")}</div>`,
      chip: "<p>The colour order can't fix that. The LED chip type is probably wrong: try the others above until every light shows one steady colour, then find the order again.</p>",
      failed: "<p>That didn't fix it. Undo puts back the settings from before.</p>",
    };
    const done = w.step === "chip" || w.step === "failed";
    return `<div class="wizard">${steps[w.step]}<div class="row"><span class="grow"></span><button class="btn ghost" type="button" data-act="led-wizard" data-key="stop">${done ? "Close" : "Stop"}</button></div></div>`;
  }

  _portsDraft(device) {
    return { dirty: false, rows: ((device && device.ports) || []).map((p) => ({ start: p.start, end: p.end, enabled: p.enabled !== false })) };
  }

  _portsReady() {
    const { dirty, rows } = this._s.ports;
    return dirty && rows.length === 4 && rows.every((p) => portOk(p, this._data.device.max_port_dot));
  }

  _nameChanged() {
    const name = (this._s.ctlName ?? "").trim();
    return CONTROLLER_NAME.test(name) && name !== (this._data.device || {}).name;
  }

  /* ---------- Events ---------- */

  _onClick(ev) {
    const target = ev.target.closest("[data-act]");
    if (!target || target.disabled) return;
    const act = target.dataset.act;
    const key = target.dataset.key;
    const s = this._s;
    const data = this._data;
    if (act === "favourite") ev.stopPropagation();
    switch (act) {
      case "menu": this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })); return;
      case "tab": if (s.trying && key !== "patterns") this._setTrying(false); s.tab = key; s.drawer = null; break;
      case "power": {
        const lit = this._litState();
        const c = data.control;
        const before = { ...c };
        this._call("light", lit ? "turn_off" : "turn_on", {}, { entity_id: data.entities.light }, null, {
          apply: () => { this._litShown = !lit; Object.assign(c, this._heldByYou()); },
          undo: () => { this._litShown = null; Object.assign(c, before); },
        });
        return;
      }
      case "resume": {
        const c = data.control;
        const before = { ...c };
        this._call(DOMAIN, "resume_schedule", {}, null, null, { apply: () => { Object.assign(c, { by: "schedule", until: null }); this._litShown = null; }, undo: () => Object.assign(c, before) });
        return;
      }
      case "keep": {
        const c = data.control;
        const before = c.until;
        this._call(DOMAIN, "keep_change", {}, null, null, { apply: () => { c.until = null; }, undo: () => { c.until = before; } });
        return;
      }
      case "hold": {
        const c = data.control;
        const before = c.hold_minutes;
        const minutes = key === "keep" ? null : Number(key);
        this._ws("hold_minutes", { minutes }, null, { apply: () => { c.hold_minutes = minutes; }, undo: () => { c.hold_minutes = before; } });
        return;
      }
      case "show-saved": {
        const effect = this._effect(Number(key));
        const c = data.control;
        const before = { ...c };
        this._call(DOMAIN, "show_saved_effect", { effect_id: effect.id }, null, null, {
          apply: () => { this._litShown = true; this._showingName = effect.name; Object.assign(c, this._heldByYou()); },
          undo: () => { this._litShown = null; this._showingName = null; Object.assign(c, before); },
        });
        return;
      }
      case "mode":
        this._call("select", "select_option", { option: key }, { entity_id: data.entities.mode }, null, {
          apply: () => { this._modeShown = key; },
          undo: () => { this._modeShown = null; },
        });
        return;
      case "open": this._openPattern(key === "new" || key === "current" ? key : Number(key)); this._livePreview(); break;
      case "select": if (!ev.target.closest("button, input")) s.selected = Number(key); else return; break;
      case "dots": { const c = s.draft.colors[Number(key)]; c.dots = clamp(c.dots + Number(target.dataset.step), 1, data.limits.dots); this._livePreview(); break; }
      case "add-colour": { const used = new Set(s.draft.colors.map((c) => c.color)); s.draft.colors.push({ color: PALETTE.find((c) => c && !used.has(c)) || "#ffffff", dots: s.draft.colors[s.draft.colors.length - 1].dots }); s.selected = s.draft.colors.length - 1; this._livePreview(); break; }
      case "pick": s.draft.colors[s.selected].color = key || null; this._livePreview(); break;
      case "move": { const i = s.selected; const j = i + Number(target.dataset.step); [s.draft.colors[i], s.draft.colors[j]] = [s.draft.colors[j], s.draft.colors[i]]; s.selected = j; this._livePreview(); break; }
      case "remove-colour": s.draft.colors.splice(s.selected, 1); s.selected = clamp(s.selected, 0, s.draft.colors.length - 1); this._livePreview(); break;
      case "overlay": {
        const effect = this._effect(s.source);
        const before = effect.overlay;
        const overlay = key === "none" ? null : key;
        this._call(DOMAIN, "set_overlay", { effect: effect.name, overlay: key }, null, null, {
          apply: () => { effect.overlay = overlay; },
          undo: () => { effect.overlay = before; },
          after: () => { if (overlay) this._call(DOMAIN, "show_saved_effect", { effect_id: effect.id }, null, null, { reload: false }); },
        });
        if (overlay) this._toast(`${effect.name} now has ${overlay}. It plays on the lights once that's saved.`);
        return;
      }
      case "show-pattern": {
        const saved = typeof s.source === "number" ? this._effect(s.source) : null;
        // A saved effect played from the controller's memory shows its lightning or snow; a pattern sent as a preview doesn't.
        if (saved && this._draftIsSaved(saved)) this._call(DOMAIN, "show_saved_effect", { effect_id: saved.id }, null, null, { reload: false });
        else this._call(DOMAIN, "show_pattern", this._patternData(), null, null, { reload: false });
        return;
      }
      case "save-pattern": this._savePattern(); return;
      case "rename-pattern": { const effect = this._effect(s.source); const name = s.draft.name.trim(); this._call(DOMAIN, "rename_saved_effect", { effect: effect.name, new_name: name }, null, `Renamed to ${name}.`); return; }
      case "delete-pattern": {
        const effect = this._effect(s.source);
        if (!window.confirm(`Delete ${effect.name} from the controller?`)) return;
        this._openPattern("new");
        this._call(DOMAIN, "delete_saved_effect", { effect: effect.name }, null, `Deleted ${effect.name}.`);
        break;
      }
      case "motion": s.fxMotion = key; break;
      case "fx": s.fx = Number(key); s.fxName = ""; this._call(DOMAIN, "show_effect", this._effectData(), null, `Showing ${data.builtins[s.fx]}.`, { reload: false }); break;
      case "favourite": {
        const index = Number(key);
        const favourites = data.favourites.includes(index) ? data.favourites.filter((i) => i !== index) : [...data.favourites, index];
        data.favourites = favourites;
        this._hass.callWS({ type: `${DOMAIN}/favourites`, entity_id: this._entity, favourites }).catch((err) => this._toast(err.message || String(err), true));
        break;
      }
      case "reverse": s.fxReverse = key === "1"; this._call(DOMAIN, "show_effect", this._effectData(), null, `Showing ${data.builtins[s.fx]}.`, { reload: false }); break;
      case "show-effect": this._call(DOMAIN, "show_effect", this._effectData(), null, `Showing ${data.builtins[s.fx]}.`, { reload: false }); return;
      case "save-effect": this._saveEffect(); return;
      case "music": s.music = Number(key); if (data.control.music.on && (s.musicSource || "ha") === "ha") this._sendFollow(this._musicLook()); break;
      case "music-source": s.musicSource = key; break;
      case "follow": this._sendFollow({ ...this._musicLook(), on: key === "on" }); return;
      case "roofline":
        data.roofline = key;
        this._hass.callWS({ type: `${DOMAIN}/roofline`, entity_id: this._entity, roofline: key }).catch((err) => this._toast(err.message || String(err), true));
        break;
      case "start-music": {
        const effect = data.music.effects[s.music];
        const fields = { effect, length: s.musicLength, sensitivity: s.musicSens };
        if (effect.startsWith("Single")) fields.color = [1, 3, 5].map((n) => parseInt(s.musicColour.slice(n, n + 2), 16));
        this._call(DOMAIN, "start_music", fields, null, `Music: ${effect}. The lights follow the controller's audio input.`);
        return;
      }
      case "view": s.view = key; break;
      case "step": s.month = new Date(s.month.getFullYear(), s.month.getMonth() + Number(target.dataset.step), 1); break;
      case "day": {
        const [month, day] = key.split("-").map(Number);
        const own = data.calendar.find((x) => covers(x, month, day));
        this._openSchedule(own ? own.id : null, own ? null : [month, day]);
        break;
      }
      case "edit": this._openSchedule(/^\d+$/.test(key) ? Number(key) : key); break;
      case "new-schedule": this._openSchedule(null); break;
      case "close-drawer": s.drawer = null; break;
      case "date-mode": s.drawer.dateMode = key; break;
      case "time-mode": { const t = this._timeTarget(key); t.mode = target.dataset.mode; if (key.startsWith("daily")) s.daily[Number(key.slice(5, 6))].dirty = true; break; }
      case "save-schedule": this._saveSchedule(); return;
      case "delete-schedule": {
        const drawer = s.drawer;
        const keep = { apply: () => { s.drawer = null; }, undo: () => { s.drawer = drawer; }, confirm: true };
        if (drawer.waiting) {
          if (!window.confirm("Delete this waiting schedule?")) return;
          this._ws("waiting_delete", { waiting_id: drawer.waiting }, "Waiting schedule deleted.", keep);
          return;
        }
        if (!window.confirm(`Delete schedule ${drawer.id}?`)) return;
        this._call(DOMAIN, "delete_schedule", { schedule_id: drawer.id }, { entity_id: data.entities.calendar }, `Schedule ${drawer.id} deleted.`, keep);
        return;
      }
      case "daily-enabled": { const d = s.daily[Number(key)]; d.enabled = !d.enabled; d.dirty = true; break; }
      case "daily-repeat": { const d = s.daily[Number(key)]; d.repeat = target.dataset.mode; d.dirty = true; break; }
      case "daily-revert": s.daily[Number(key)] = this._dailyDraft(data.daily[Number(key)]); break;
      case "daily-save": this._saveDaily(Number(key)); return;
      case "combined-up": { const e = s.combined.effects; const i = Number(key); [e[i - 1], e[i]] = [e[i], e[i - 1]]; s.combined.dirty = true; break; }
      case "combined-remove": s.combined.effects.splice(Number(key), 1); s.combined.dirty = true; break;
      case "combined-add": s.combined.effects.push(this.shadowRoot.querySelector('[data-in="combined-pick"]').value); s.combined.dirty = true; break;
      case "combined-revert": s.combined = this._combinedDraft(); break;
      case "combined-save": this._call(DOMAIN, "set_combined_effect", { effects: s.combined.effects, interval: s.combined.interval }, null, "Combined effect saved.", { after: () => { s.combined = this._combinedDraft(); } }); return;
      case "led-test": this._ws("led_test", { show: key }, null); return;
      case "led-chip": case "led-order": {
        if (target.getAttribute("aria-pressed") === "true") return;
        const field = act === "led-chip" ? "ic_type" : "channel_order";
        this._ws("led_setup", { [field]: Number(key) }, `Trying ${target.textContent.trim()}. Keep it if the colours look right.`, { confirm: true });
        return;
      }
      case "led-wizard": this._ledWizardStep(key); return;
      case "led-end": {
        const keep = key === "keep";
        this._ws("led_session_end", { keep }, keep ? "LED settings kept." : "Back to how it was.", { confirm: true, after: () => { s.led = null; } });
        return;
      }
      case "ports-revert": s.ports = this._portsDraft(data.device); break;
      case "ports-try":
        this._ws("led_setup", { ports: s.ports.rows }, "Trying the new ports. Keep them if the right lights are on.", { confirm: true, after: () => { s.ports = this._portsDraft(this._data.device); } });
        return;
      case "rename-controller": {
        if (!this._nameChanged()) return;
        const name = s.ctlName.trim();
        this._ws("rename", { name }, `The controller is now called ${name}.`, { after: () => { s.ctlName = null; } });
        return;
      }
      default: return;
    }
    this.render();
  }

  _onInput(ev) {
    const t = ev.target;
    const field = t.dataset.in;
    if (!field) return;
    const s = this._s;
    const out = t.parentElement && t.parentElement.querySelector("output");
    const number = () => Number(t.value);
    switch (field) {
      case "name": s.draft.name = t.value; this._refreshPatternButtons(); return;
      case "dots": { const n = parseInt(t.value, 10); if (n >= 1) { s.draft.colors[Number(t.dataset.key)].dots = clamp(n, 1, this._data.limits.dots); this._livePreview(); } return; }
      case "custom": s.draft.colors[s.selected].color = t.value; this._livePreview(); return;
      case "speed": s.draft.speed = number(); out.textContent = t.value; this._livePreview(); return;
      case "brightness": s.draft.brightness = number(); out.textContent = percent(number()); this._livePreview(); return;
      case "fx-query": { s.fxQuery = t.value; const pos = t.selectionStart; this.render(); const q = this.shadowRoot.querySelector('[data-in="fx-query"]'); q.focus(); q.setSelectionRange(pos, pos); return; }
      case "fx-speed": s.fxSpeed = number(); out.textContent = t.value; return;
      case "fx-length": s.fxLength = number(); out.textContent = t.value; return;
      case "fx-bright": s.fxBright = number(); out.textContent = percent(number()); return;
      case "fx-name": s.fxName = t.value; return;
      case "music-length": s.musicLength = number(); out.textContent = t.value; return;
      case "music-sens": s.musicSens = number(); out.textContent = t.value; return;
      case "follow-delay": out.textContent = `${t.value} ms`; return;
      case "combined-interval": s.combined.interval = number(); s.combined.dirty = true; out.textContent = `${t.value} min`; return;
      case "clock": { this._timeTarget(t.dataset.key).clock = t.value; this._markDaily(t.dataset.key); return; }
      case "offset": {
        const target = this._timeTarget(t.dataset.key);
        target.offset = clamp(parseInt(t.value, 10) || 0, -240, 240);
        this._markDaily(t.dataset.key);
        const hint = t.parentElement.querySelector(".hint span");
        if (hint && this._data.sun) hint.textContent = `today ${shiftClock(this._data.sun[target.mode], target.offset)}`;
        return;
      }
      case "drawer-day": s.drawer[t.dataset.key][1] = clamp(parseInt(t.value, 10) || 1, 1, 31); return;
      case "drawer-hoff": s.drawer[t.dataset.key] = clamp(parseInt(t.value, 10) || 0, -60, 60); this._refreshHolidayPreview(); return;
      case "ctl-name": {
        s.ctlName = t.value;
        const rename = this.shadowRoot.querySelector('[data-act="rename-controller"]');
        if (rename) rename.disabled = !this._nameChanged();
        return;
      }
      case "port-start": case "port-end":
        s.ports.rows[Number(t.dataset.key)][field === "port-start" ? "start" : "end"] = t.value === "" ? NaN : Number(t.value);
        s.ports.dirty = true;
        this._refreshPorts();
        return;
      default:
    }
  }

  _onChange(ev) {
    const t = ev.target;
    const field = t.dataset.in;
    if (!field) return;
    const s = this._s;
    switch (field) {
      case "mode": s.draft.mode = t.value; this._livePreview(); return;
      case "try": this._setTrying(t.checked); return;
      case "fx-colour": s.fxColour = t.value; break;
      case "fx-speed": case "fx-length": case "fx-bright": this._call(DOMAIN, "show_effect", this._effectData(), null, `Showing ${this._data.builtins[s.fx]}.`, { reload: false }); return;
      case "music-colour": s.musicColour = t.value; if (this._data.control.music.on) this._sendFollow(this._musicLook()); break;
      case "music-length": case "music-sens": if (this._data.control.music.on) this._sendFollow(this._musicLook()); return;
      case "follow-speaker": this._sendFollow({ speaker: t.value || null, ...(t.value ? {} : { on: false }) }); break;
      case "follow-delay": this._sendFollow({ delay_ms: Number(t.value) }); return;
      case "drawer-effect": s.drawer.effect = t.value; return;
      case "drawer-holiday": s.drawer.holiday = t.value; this._refreshHolidayPreview(); return;
      case "drawer-month": s.drawer[t.dataset.key][0] = Number(t.value); return;
      case "daily-effect": { const d = s.daily[Number(t.dataset.key)]; d.effect = t.value; d.dirty = true; break; }
      case "controller": this._entity = t.value; this._data = null; this._host.update(this._hass, this._entity); return;
      case "port-on": s.ports.rows[Number(t.dataset.key)].enabled = t.checked; s.ports.dirty = true; this._refreshPorts(); return;
      case "dots": case "clock": case "offset": case "combined-interval": break;
      default: return;
    }
    this.render();
  }

  _markDaily(key) {
    if (key.startsWith("daily")) this._s.daily[Number(key.slice(5, 6))].dirty = true;
  }

  _refreshPatternButtons() {
    const s = this._s;
    const editing = typeof s.source === "number" ? this._effect(s.source) : null;
    const name = s.draft.name.trim();
    const save = this.shadowRoot.querySelector('[data-act="save-pattern"]');
    const rename = this.shadowRoot.querySelector('[data-act="rename-pattern"]');
    if (save) {
      save.disabled = !name;
      save.lastChild.textContent = editing && name && name !== editing.stored_name ? "Save as new" : "Save";
    }
    if (rename) rename.disabled = !name || name === editing.stored_name;
    this._renderNow();
  }

  _refreshPorts() {
    const max = this._data.device.max_port_dot;
    this._s.ports.rows.forEach((p, i) => {
      const row = this.shadowRoot.querySelector(`tr[data-port="${i}"]`);
      if (!row) return;
      row.classList.toggle("bad", !portOk(p, max));
      row.querySelector(".count").textContent = portOk(p, max) ? p.end - p.start + 1 : "–";
    });
    const enable = (act, on) => {
      const button = this.shadowRoot.querySelector(`[data-act="${act}"]`);
      if (button) button.disabled = !on;
    };
    enable("ports-try", this._portsReady());
    enable("ports-revert", this._s.ports.dirty);
  }

  _ledSecondsLeft() {
    return Math.max(0, Math.ceil(((this._ledDeadline || 0) - Date.now()) / 1000));
  }

  _ledTimer() {
    const session = this._animate && this._data && this._data.device && this._data.device.led_session;
    if (!session) {
      clearInterval(this._ledTick);
      this._ledTick = null;
      return;
    }
    if (this._ledTick) return;
    this._ledTick = setInterval(() => {
      const left = this._ledSecondsLeft();
      for (const node of this.shadowRoot.querySelectorAll(".countdown")) node.textContent = left;
      // Home Assistant undoes the test on its own when the time is up; this picks up the result.
      if (left === 0 && !this._busy && Date.now() - (this._ledPolled || 0) > 3000) {
        this._ledPolled = Date.now();
        this._host.reload();
      }
    }, 1000);
  }

  _setTrying(on) {
    const s = this._s;
    if (s.trying === on) return;
    s.trying = on;
    clearTimeout(this._liveTimer);
    const c = this._data && this._data.control;
    this._ws("try", { on }, null, {
      quiet: true,
      reload: !on,
      apply: () => { if (c) c.trying = on; },
      undo: () => { s.trying = !on; if (c) c.trying = !on; },
    }).then((ok) => { if (ok && on && s.trying) this._livePreview(); });
    if (this.shadowRoot) this._safeRender();
  }

  _livePreview() {
    if (!this._s.trying) return;
    clearTimeout(this._liveTimer);
    this._liveTimer = setTimeout(() => this._call(DOMAIN, "show_pattern", this._patternData(), null, "Live preview is on: changes show on the lights.", { reload: false, quiet: true }), LIVE_PREVIEW_DELAY_MS);
  }

  /* ---------- Actions ---------- */

  _savePattern() {
    const s = this._s;
    const name = s.draft.name.trim();
    const editing = typeof s.source === "number" ? this._effect(s.source) : null;
    const existing = this._data.effects.find((e) => e.stored_name === name);
    if (existing && existing !== editing && !window.confirm(`Replace the saved effect ${existing.name}?`)) return;
    this._call(DOMAIN, "save_effect", { name, ...this._patternData() }, null, `Saved ${name} on the controller.`, {
      after: () => {
        const saved = this._data.effects.find((e) => e.stored_name === name);
        if (saved) this._openPattern(saved.id);
        if (s.trying) this._setTrying(false);
      },
    });
  }

  async _saveEffect() {
    const s = this._s;
    const name = (s.fxName || this._data.builtins[s.fx].slice(0, this._data.limits.name)).trim();
    if (!name) return;
    const existing = this._data.effects.find((e) => e.stored_name === name);
    if (existing && !window.confirm(`Replace the saved effect ${existing.name}?`)) return;
    // Save effect stores whatever the lights show, so the effect is shown first.
    if (await this._call(DOMAIN, "show_effect", this._effectData(), null, "", { reload: false, quiet: true })) {
      await this._call(DOMAIN, "save_effect", { name }, null, `Saved ${name}. Schedules and the combined effect can use it now.`);
    }
  }

  _saveSchedule() {
    const dr = this._s.drawer;
    const date = ([month, day]) => `2000-${pad(month)}-${pad(day)}`;
    const time = (prefix, t) => (t.mode === "time" ? { [`${prefix}_time`]: t.clock } : { [`${prefix}_sun`]: t.mode, [`${prefix}_offset`]: t.offset });
    const dates = dr.dateMode === "holiday"
      ? { holiday: dr.holiday, holiday_start_offset: dr.from, holiday_end_offset: dr.to }
      : { start_date: date(dr.start), end_date: date(dr.end) };
    const fields = { effect: dr.effect, ...dates, ...time("on", dr.on), ...time("off", dr.off) };
    if (dr.id !== null) fields.schedule_id = dr.id;
    if (dr.waiting) fields.waiting_id = dr.waiting;
    this._call(DOMAIN, "set_schedule", fields, { entity_id: this._data.entities.calendar }, dr.id === null ? "Schedule saved." : `Schedule ${dr.id} saved.`,
      { apply: () => { this._s.drawer = null; }, undo: () => { this._s.drawer = dr; }, confirm: true });
  }

  _saveDaily(index) {
    const d = this._s.daily[index];
    const time = (prefix, t) => (t.mode === "time" ? { [`${prefix}_time`]: t.clock } : { [`${prefix}_sun`]: t.mode, [`${prefix}_offset`]: t.offset });
    this._call(DOMAIN, "set_daily_schedule", { schedule: index + 1, enabled: d.enabled, effect: d.effect, repeat: d.repeat, ...time("on", d.on), ...time("off", d.off) }, null,
      `Daily schedule ${index + 1} saved.`, { confirm: true, after: () => { this._s.daily[index] = this._dailyDraft(this._data.daily[index]); } });
  }

  async _ledWizardStep(answer) {
    const s = this._s;
    const w = s.led || {};
    const orders = this._data.device.channel_orders;
    const showRed = async (name, next) => {
      const order = orders.find((o) => o.name === name).value;
      if ((await this._ws("led_setup", { channel_order: order }, null)) && (await this._ws("led_test", { show: "red" }, null, { quiet: true }))) s.led = next;
    };
    if (answer === "stop") s.led = null;
    else if (answer === "start") await showRed("RGB", { step: "red" });
    else if (answer === "X") s.led = { step: "chip" };
    else if (w.step === "red") {
      if (await this._ws("led_test", { show: "green" }, null, { quiet: true })) s.led = { step: "green", red: answer };
    } else if (w.step === "green") {
      // Sent as RGB, red lands on the strip's first channel and green on its second, so the two answers spell the strip's own order.
      const name = w.red + answer + "RGB".replace(w.red, "").replace(answer, "");
      if (answer === w.red) s.led = { step: "chip" };
      else await showRed(name, { step: "check", name });
    } else if (w.step === "check") {
      // GBR and BRG are each other's reverse, and which way round the controller reads them is untested.
      const other = { GBR: "BRG", BRG: "GBR" }[w.name];
      if (answer === "yes") {
        if (await this._ws("led_session_end", { keep: true }, `Colour order ${w.name} kept.`, { confirm: true })) s.led = null;
      } else if (other && !w.swapped) await showRed(other, { step: "check", name: other, swapped: true });
      else s.led = { step: "failed" };
    }
    this.render();
  }

  _call(domain, service, data, target, success, options) {
    return this._run(() => this._hass.callService(domain, service, data, target || { entity_id: this._entity }, false), success, options);
  }

  _ws(command, fields, success, options) {
    return this._run(() => this._hass.callWS({ type: `${DOMAIN}/${command}`, entity_id: this._entity, ...fields }), success, options);
  }

  /* Like the phone app, the page never waits on the controller: `apply` shows the change at once, the request runs in the
     background, and `undo` puts things back if it fails. Success is only announced for changes that took a while, or with `confirm`. */
  _run(send, success, { reload = true, after = null, quiet = false, apply = null, undo = null, confirm = false } = {}) {
    if (apply) {
      apply();
      this._safeRender();
    }
    const started = Date.now();
    this._inflight = (this._inflight || 0) + 1;
    this._busy = true;
    clearTimeout(this._syncTimer);
    this._syncTimer = setTimeout(() => this._showSync(), SYNC_HINT_DELAY_MS);
    let request;
    try {
      request = Promise.resolve(send());
    } catch (err) {
      request = Promise.reject(err);
    }
    return request.then(
      async () => {
        if (reload) await this._host.reload();
        if (after) after();
        if (success && !quiet && (confirm || Date.now() - started > SLOW_CHANGE_MS)) this._toast(success);
        return true;
      },
      async (err) => {
        if (undo) undo();
        this._toast(err.message || String(err), true);
        await this._host.reload();
        return false;
      },
    ).finally(() => {
      this._inflight -= 1;
      this._busy = this._inflight > 0;
      this._showSync();
      this._safeRender();
    });
  }

  _showSync() {
    const hint = this.shadowRoot && this.shadowRoot.querySelector(".sync");
    if (hint) hint.hidden = !(this._inflight > 0);
  }

  _safeRender() {
    const active = this.shadowRoot.activeElement;
    if (active && /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName)) this._pending = true;
    else this.render();
  }

  _clearToast() {
    clearTimeout(this._toastTimer);
    const box = this.shadowRoot.querySelector(".toast");
    if (box) box.remove();
  }

  _toast(text, error = false, duration = error ? 7000 : 3200) {
    clearTimeout(this._toastTimer);
    let box = this.shadowRoot.querySelector(".toast");
    if (!box) {
      box = document.createElement("div");
      box.setAttribute("role", "status");
      this._pageEl.append(box);
    }
    box.className = error ? "toast error" : "toast";
    box.textContent = text;
    if (duration) this._toastTimer = setTimeout(() => box.remove(), duration);
  }
}

const ELEMENTS = {
  "trimlight-pattern-editor": TrimlightPatternEditor,
  "trimlight-pattern-card": TrimlightPatternCard,
  "trimlight-panel": TrimlightPanel,
};
for (const [tag, element] of Object.entries(ELEMENTS)) {
  if (!customElements.get(tag)) customElements.define(tag, element);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "trimlight-pattern-card")) {
  window.customCards.push({
    type: "trimlight-pattern-card",
    name: "Trimlight pattern editor",
    description: "Create and edit the patterns saved on a Trimlight Edge controller.",
  });
}
