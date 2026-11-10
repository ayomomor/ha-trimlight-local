// A simulated Home Assistant, answering like the integration's trimlight_local/state command.
const MODES = ["static", "chase_forward", "chase_backward", "chase_middle_to_out", "chase_out_to_middle", "twinkle_fade", "fade",
  "comet_forward", "comet_backward", "comet_middle_to_out", "comet_out_to_middle", "wave_forward", "wave_backward",
  "wave_middle_to_out", "wave_out_to_middle", "strobe", "solid_fade", "full_strobe", "twinkle", "firework", "inside_out"];
const BUILTINS = ["Rainbow Gradual Chase", "Rainbow Comet", "Rainbow Segment", "Rainbow Wave", "Rainbow Meteor", "Rainbow Gradual", "Rainbow Jump", "Rainbow Twinkle", "Rainbow Fade In Out", "Rainbow Rebound", "Red Stacking", "Green Stacking", "Blue Stacking", "Yellow Stacking", "Cyan Stacking", "Purple Stacking", "White Stacking", "Full Color Stack", "Red to Green Stack", "Green to Blue Stack", "Blue to Yellow Stack", "Yellow to Cyan Stack", "Cyan to Purple Stack", "Purple to White Stack", "Red Comet", "Green Comet", "Blue Comet", "Yellow Comet", "Cyan Comet", "Purple Comet", "White Comet", "Red Meteor", "Green Meteor", "Blue Meteor", "Yellow Meteor", "Cyan Meteor", "Purple Meteor", "White Meteor", "Red Wave", "Green Wave", "Blue Wave", "Yellow Wave", "Cyan Wave", "Purple Wave", "White Wave", "Red Green Wave", "Red Blue Wave", "Red Yellow Wave", "Red Cyan Wave", "Red Purple Wave", "Red White Wave", "Green Blue Wave", "Green Yellow Wave", "Green Cyan Wave", "Green Purple Wave", "Green White Wave", "Blue Yellow Wave", "Blue Cyan Wave", "Blue Purple Wave", "Blue White Wave", "Yellow Cyan Wave", "Yellow Purple Wave", "Yellow White Wave", "Cyan Purple Wave", "Cyan White Wave", "Purple White Wave", "Red Dot Pulse", "Green Dot Pulse", "Blue Dot Pulse", "Yellow Dot Pulse", "Cyan Dot Pulse", "Purple Dot Pulse", "White Dot Pulse", "Red Green Blank Pulse", "Green Blue Blank Pulse", "Blue Yellow Blank Pulse", "Yellow Cyan Blank Pulse", "Cyan Purple Blank Pulse", "Purple White Blank Pulse", "Red with Purple Pulse", "Green with Cyan Pulse", "Blue with Yellow Pulse", "Yellow with Blue Pulse", "Cyan with Green Pulse", "Purple with Purple Pulse", "Red Comet Rebound", "Green Comet Rebound", "Blue Comet Rebound", "Yellow Comet Rebound", "Cyan Comet Rebound", "Purple Comet Rebound", "White Comet Rebound", "Red Dot Rebound", "Green Dot Rebound", "Blue Dot Rebound", "Yellow Dot Rebound", "Cyan Dot Rebound", "Purple Dot Rebound", "White Dot Rebound", "Red Segment Rebound", "Green Segment Rebound", "Blue Segment Rebound", "Yellow Segment Rebound", "Cyan Segment Rebound", "Purple Segment Rebound", "White Segment Rebound", "Red Green Gradual Snake", "Red Blue Gradual Snake", "Red Yellow Gradual Snake", "Red Cyan Gradual Snake", "Red Purple Gradual Snake", "Red White Gradual Snake", "Green Blue Gradual Snake", "Green Yellow Gradual Snake", "Green Cyan Gradual Snake", "Green Purple Gradual Snake", "Green White Gradual Snake", "Blue Yellow Gradual Snake", "Blue Cyan Gradual Snake", "Blue Purple Gradual Snake", "Blue White Gradual Snake", "Yellow Cyan Gradual Snake", "Yellow Purple Gradual Snake", "Yellow White Gradual Snake", "Cyan Purple Gradual Snake", "Cyan White Gradual Snake", "Purple White Gradual Snake", "Red White Blank Snake", "Green White Blank Snake", "Blue White Blank Snake", "Yellow White Blank Snake", "Cyan White Blank Snake", "Purple White Blank Snake", "Green Yellow White Snake", "Red Green White Snake", "Red Yellow Snake", "Red White Snake", "Green White Snake", "Red Twinkle", "Green Twinkle", "Blue Twinkle", "Yellow Twinkle", "Cyan Twinkle", "Purple Twinkle", "White Twinkle", "Red Background Twinkle", "Green Background Twinkle", "Blue Background Twinkle", "Yellow Background Twinkle", "Cyan Background Twinkle", "Purple Background Twinkle", "Red White Background Twinkle", "Green White Background Twinkle", "Blue White Background Twinkle", "Yellow White Background Twinkle", "Cyan White Background Twinkle", "Purple White Background Twinkle", "White White Background Twinkle", "Red Breath", "Green Breath", "Blue Breath", "Yellow Breath", "Cyan Breath", "Purple Breath", "White Breath", "Red Yellow Fire", "Red Purple Fire", "Green Yellow Fire", "Green Cyan Fire", "Blue Purple Fire", "Blue Cyan Fire", "Red Strobe", "Green Strobe", "Blue Strobe", "Yellow Strobe", "Cyan Strobe", "Purple Strobe", "White Strobe", "Red Blue White Strobe", "Full Color Strobe"];
const STATE = {
  entities: { light: "light.porch", mode: "select.porch_mode", calendar: "calendar.porch_schedules" },
  switch_state: "off",
  control: { lit: false, by: "you", until: "2026-11-10T03:00:00+00:00", window: null, hold_minutes: 60, hold_choices: [30, 60, 120, 240, null], trying: false },
  modes: MODES.map((key) => ({ key, name: key.charAt(0).toUpperCase() + key.slice(1).replaceAll("_", " ") })),
  repeats: ["today_only", "every_day", "weekdays", "weekend"],
  combined_name: "Combined effect",
  limits: { colors: 30, dots: 90, name: 25 },
  effects: [
    { id: 6, name: "CANADA DAY", stored_name: "CANADA DAY", kind: "pattern", mode: "static", speed: 127, brightness: 255, overlay: null, used_by: [],
      colors: [{ color: "#ff0000", dots: 5 }, { color: "#ffffff", dots: 5 }, { color: "#000000", dots: 5 }] },
    { id: 7, name: "HALLOWEEN", stored_name: "HALLOWEEN", kind: "pattern", mode: "chase_forward", speed: 1, brightness: 255, overlay: "lightning",
      used_by: ["calendar schedule 5", "calendar schedule 7"], colors: [{ color: "#ff2600", dots: 5 }, { color: "#7700ff", dots: 5 }, { color: "#7800ff", dots: 5 }] },
    { id: 9, name: "CHRISTMAS", stored_name: "CHRISTMAS", kind: "pattern", mode: "twinkle_fade", speed: 49, brightness: 184, overlay: null, used_by: ["calendar schedule 6"],
      colors: [{ color: "#ff0000", dots: 5 }, { color: "#00ff00", dots: 5 }] },
    { id: 18, name: "Rainbow Wave (saved 18)", stored_name: "Rainbow Wave", kind: "builtin", effect: "Rainbow Wave", length: 30, reverse: false, speed: 100, brightness: 255, overlay: null, used_by: [] },
  ],
  current: { kind: "pattern", mode: "static", speed: 127, brightness: 255, colors: [{ color: "#ff0000", dots: 5 }, { color: "#ffffff", dots: 5 }, { color: "#000000", dots: 5 }] },
  calendar: [
    { id: 6, effect: "CHRISTMAS", start: [12, 1], end: [12, 27], on: "16:22", off: "07:06", on_sun: { event: "sunset", offset: -15 }, off_sun: null },
    { id: 5, effect: "HALLOWEEN", start: [10, 31], end: [10, 31], on: "16:00", off: "00:00", on_sun: null, off_sun: null },
    { id: 4, effect: "Combined effect", start: [6, 19], end: [6, 19], on: "17:30", off: "23:59", on_sun: null, off_sun: null },
    { id: 10, effect: "CANADA DAY", start: [11, 24], end: [11, 30], on: "16:27", off: "06:51", on_sun: null, off_sun: null, holiday: { key: "thanksgiving", start_offset: -2, end_offset: 4, problem: null, skips: [] } },
  ],
  waiting: [
    { id: "w1", effect: "HALLOWEEN", on: "18:00", off: "23:00", on_sun: null, off_sun: null, next: ["2028-10-17", "2028-10-17"], from: "2027-10-18",
      holiday: { key: "diwali", start_offset: 0, end_offset: 0, problem: null, skips: [{ year: 2027, to: "schedule 7 (HALLOWEEN)" }] } },
  ],
  holidays: [
    { key: "easter", name: "Easter", group: "Christian", dates: { 2025: "2025-04-20", 2026: "2026-04-05", 2027: "2027-03-28", 2028: "2028-04-16", 2029: "2029-04-01" } },
    { key: "thanksgiving", name: "Thanksgiving", group: "US holidays", dates: { 2025: "2025-11-27", 2026: "2026-11-26", 2027: "2027-11-25", 2028: "2028-11-23", 2029: "2029-11-22" } },
    { key: "diwali", name: "Diwali", group: "Hindu, Sikh and Buddhist", dates: { 2025: "2025-10-20", 2026: "2026-11-08", 2027: "2027-10-29", 2028: "2028-10-17", 2029: "2029-11-05" } },
  ],
  daily: [
    { enabled: false, effect: "CANADA DAY", repeat: "today_only", on: "16:25", off: "00:00", on_sun: null, off_sun: null },
    { enabled: true, effect: "Combined effect", repeat: "weekend", on: "18:00", off: "22:00", on_sun: null, off_sun: null },
  ],
  combined: { effects: ["CHRISTMAS"], interval: 5 },
  sun: { sunrise: "06:40", sunset: "16:45", location_ok: true },
  builtins: BUILTINS,
  favourites: [0],
  roofline: "two_gables",
  music: { effects: ["Full Color Rhythm Spectrum", "Single Color Rhythm Spectrum", "Full Color Rhythm Twinkle", "Single Color Rhythm Twinkle", "Gradient Energy", "Single Color Energy",
    "Gradient Pulse", "Single Color Pulse", "Full Color Ejection Forward", "Single Color Ejection Forward", "Full Color Ejection Backward", "Single Color Ejection Backward",
    "Full Color VuMeter", "Single Color VuMeter", "Independence Day", "Christmas", "HeatBeat", "Party"], source: "aux", color: "#ff0000", length: 70, sensitivity: 13 },
  device: { name: "Porch", host: "192.168.1.50", firmware: "1.11", mcu_version: "0x1042", ic_type: "UCS1903", channel_order: "RGB",
    ic_type_value: 0, channel_order_value: 0, led_session: null, max_port_dot: 2700,
    ic_types: ["UCS1903", "DMX512", "UCS5603"].map((name, value) => ({ value, name })),
    channel_orders: ["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"].map((name, value) => ({ value, name })),
    ports: [{ start: 1, end: 200, enabled: true }, { start: 1, end: 600, enabled: true }, { start: 1, end: 600, enabled: true }, { start: 1, end: 600, enabled: true }], clock: "2026-11-09T19:50", time_sync: "auto" },
};
const LED_KEYS = ["ic_type", "ic_type_value", "channel_order", "channel_order_value", "ports"];
let ledBefore = null;
// Answers the LED commands the way the integration does: a test session that remembers the settings from before it began.
function ledCommand(msg) {
  const device = STATE.device;
  const setup = () => JSON.stringify(LED_KEYS.map((k) => device[k]));
  if (msg.type === "trimlight_local/rename") {
    device.name = msg.name;
    return;
  }
  if (msg.type === "trimlight_local/led_session_end") {
    if (!msg.keep && ledBefore) LED_KEYS.forEach((k, i) => { device[k] = JSON.parse(ledBefore)[i]; });
    device.led_session = null;
    ledBefore = null;
    return;
  }
  if (!ledBefore) ledBefore = setup();
  if (msg.ic_type !== undefined) Object.assign(device, { ic_type_value: msg.ic_type, ic_type: device.ic_types[msg.ic_type].name });
  if (msg.channel_order !== undefined) Object.assign(device, { channel_order_value: msg.channel_order, channel_order: device.channel_orders[msg.channel_order].name });
  if (msg.ports) device.ports = msg.ports;
  device.led_session = { seconds_left: 60, changed: setup() !== ledBefore };
}

// Events as Home Assistant's calendar API returns them for the Trimlight calendar: one per schedule night, plus enabled daily schedules.
function calendarEvents(path) {
  const query = new URLSearchParams(path.split("?")[1]);
  const events = [];
  for (let day = new Date(query.get("start")); day < new Date(query.get("end")); day.setDate(day.getDate() + 1)) {
    const md = [day.getMonth() + 1, day.getDate()];
    const iso = `${day.getFullYear()}-${String(md[0]).padStart(2, "0")}-${String(md[1]).padStart(2, "0")}`;
    for (const s of STATE.calendar) {
      const after = (a, b) => a[0] > b[0] || (a[0] === b[0] && a[1] >= b[1]);
      if (after(md, s.start) && after(s.end, md)) {
        events.push({ uid: `${s.id}-${iso}`, summary: s.effect, start: { dateTime: `${iso}T${s.on}:00` }, end: { dateTime: `${iso}T23:59:00` } });
      }
    }
    if ([0, 6].includes(day.getDay())) {
      events.push({ uid: `daily2-${iso}`, summary: "Combined effect (daily)", start: { dateTime: `${iso}T18:00:00` }, end: { dateTime: `${iso}T22:00:00` } });
    }
  }
  return events;
}

const calls = [];
const wsCalls = [];
const stamp = (id) => ({ entity_id: id, state: "on", last_updated: "1", attributes: {} });
const hass = {
  states: {
    "light.porch": { ...stamp("light.porch"), state: "off", attributes: { effect: "CANADA DAY", controller_busy: false, friendly_name: "Porch" } },
    "select.porch_mode": { ...stamp("select.porch_mode"), state: "off" },
    "calendar.porch_schedules": stamp("calendar.porch_schedules"),
  },
  entities: Object.fromEntries(["light.porch", "select.porch_mode", "calendar.porch_schedules"].map((id) => [id, { entity_id: id, platform: "trimlight_local", device_id: "dev1" }])),
  callWS: async (msg) => {
    if (msg.type === "trimlight_local/roofline") {
      wsCalls.push(msg);
      return { roofline: msg.roofline };
    }
    if (msg.type === "trimlight_local/favourites") {
      wsCalls.push(msg);
      return { favourites: msg.favourites };
    }
    if (msg.type === "trimlight_local/waiting_delete") {
      wsCalls.push(msg);
      STATE.waiting = STATE.waiting.filter((w) => w.id !== msg.waiting_id);
      return {};
    }
    if (/^trimlight_local\/(try|hold_minutes)$/.test(msg.type)) {
      wsCalls.push(msg);
      return {};
    }
    if (/^trimlight_local\/(led_test|led_setup|led_session_end|rename)$/.test(msg.type)) {
      wsCalls.push(msg);
      ledCommand(msg);
      return {};
    }
    if (msg.type !== "trimlight_local/state" || msg.entity_id !== "light.porch") throw new Error(`unexpected ${JSON.stringify(msg)}`);
    return JSON.parse(JSON.stringify(STATE));
  },
  callService: async (domain, service, data, target) => {
    calls.push({ domain, service, data, target });
  },
  callApi: async (method, path) => {
    if (method !== "GET" || !path.startsWith("calendars/calendar.porch_schedules?")) throw new Error(`unexpected ${method} ${path}`);
    return calendarEvents(path);
  },
  locale: { first_weekday: "sunday" },
};
window.confirm = () => true;

const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
const results = [];
const check = (label, ok, detail = "") => results.push(`${ok ? "PASS" : "FAIL"} ${label}${ok ? "" : `  ${detail}`}`);
const inside = (root) => ({
  $: (selector) => root.shadowRoot.querySelector(selector),
  $$: (selector) => [...root.shadowRoot.querySelectorAll(selector)],
  button: (text) => [...root.shadowRoot.querySelectorAll("button")].find((b) => b.textContent === text),
});
async function choose(select, value) {
  select.value = value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await tick();
}
async function type(input, value) {
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await tick();
}

function makeCard() {
  const card = document.createElement("trimlight-pattern-card");
  card.setConfig({ entity: "light.porch" });
  document.getElementById("card").append(card);
  card.hass = hass;
  return card;
}

function makePage() {
  const page = document.createElement("trimlight-panel");
  const narrow = location.hash.endsWith("-narrow");
  page.narrow = narrow;
  // Headless Chrome will not make its window narrower than about 500 px, so a phone-sized width is set on the container instead.
  if (narrow) document.getElementById("page").style.width = "390px";
  document.getElementById("page").append(page);
  page.hass = hass;
  return page;
}

async function testCard() {
  const card = makeCard();
  await tick();
  const editor = inside(card.shadowRoot.querySelector("trimlight-pattern-editor"));
  const { $, $$, button } = editor;
  const source = () => $("select");
  check("card: opens what the lights show", source().value === "current", source().value);
  check("card: strip has 60 dots", $$(".led").length === 60, $$(".led").length);
  check("card: built-in saved effect is listed but not editable", [...source().options].some((o) => o.textContent === "Rainbow Wave (saved 18) (built-in effect)" && o.disabled));

  await choose(source(), "7");
  check("card: HALLOWEEN loads its name, mode and colours", $("input[type=text]").value === "HALLOWEEN" && $$("select")[1].value === "chase_forward"
    && $$("input[type=color]").map((i) => i.value).join() === "#ff2600,#7700ff,#7800ff");
  check("card: overlay shows lightning", $$("input[type=radio]")[1].checked);
  check("card: delete is blocked while schedules use it", button("Delete").disabled);

  $$(".swatch")[11].click();
  await tick();
  check("card: no-colour swatch makes the selected row unlit", $$(".color-row")[0].querySelector(".none-chip") !== null && $$(".led.off").length === 20);
  $$(".color-row")[2].click();
  await tick();
  $$(".swatch")[6].click();
  await tick();
  await type($$("input.dots")[1], "12");
  button("Show on lights").click();
  await tick();
  const shown = calls.at(-1);
  check("card: Show on lights sends the edited pattern to the light",
    shown.service === "show_pattern" && shown.target.entity_id === "light.porch"
    && JSON.stringify(shown.data) === JSON.stringify({ mode: "chase_forward", colors: ["none", "#7700ff", "#ffffff"], dots: [5, 12, 5], speed: 1, brightness: 255 }), JSON.stringify(shown));

  await type($("input[type=text]"), "Spooky");
  check("card: a new name turns Save into Save as new", button("Save as new") !== undefined);
  button("Save as new").click();
  await tick();
  check("card: Save as new calls save_effect with the new name", calls.at(-1).service === "save_effect" && calls.at(-1).data.name === "Spooky", JSON.stringify(calls.at(-1)));

  await choose(source(), "6");
  button("+ Add colour").click();
  await tick();
  check("card: add colour adds a row", $$(".color-row").length === 4, $$(".color-row").length);
  $$(".color-row")[3].querySelector("button[title=Remove]").click();
  await tick();
  $$(".color-row")[0].querySelector("button[title='Move down']").click();
  await tick();
  check("card: remove and move work", $$(".color-row").length === 3 && $$("input[type=color]")[0].value === "#ffffff");
  const deleteButton = button("Delete");
  const before = `delete button: ${deleteButton ? `disabled=${deleteButton.disabled}` : "missing"}, source=${source().value}`;
  deleteButton.click();
  await tick();
  check("card: delete calls delete_saved_effect by name and starts a new pattern",
    calls.at(-1).service === "delete_saved_effect" && calls.at(-1).data.effect === "CANADA DAY" && source().value === "new", `${before}; last call ${JSON.stringify(calls.at(-1))}`);
  card.remove();
}

async function testPage() {
  const page = makePage();
  await tick();
  const { $, $$ } = inside(page);
  const act = (name, key) => $$(`[data-act="${name}"]`).find((b) => key === undefined || b.dataset.key === String(key));
  const tab = async (key) => { act("tab", key).click(); await tick(); };
  const last = () => calls.at(-1);

  check("page: seven tabs", $$(".tab").map((t) => t.textContent).join() === "Patterns,Effects,Music,Schedules,Daily,Combined effect,Settings", $$(".tab").map((t) => t.textContent).join());
  const withLight = (state, stamp) => ({ ...hass, states: { ...hass.states, "light.porch": { ...hass.states["light.porch"], state, last_updated: stamp } } });
  check("page: Now says the lights are off, who set that and until when", $(".now b").textContent === "Lights off" && $(".now small").textContent.startsWith("Set by you · back to schedule at"), $(".now small").textContent);
  check("page: one power button instead of Off, Manual and Timer", $$('[data-act="mode"]').length === 0 && act("power").textContent.trim() === "Turn on" && !!act("keep") && !!act("resume"));
  check("page: the roofline shows the roof, dark while off", $(".hero .tag").textContent === "Lights off", $(".hero .tag").textContent);
  act("power").click();
  check("page: Turn on shows at once, before the controller answers", $(".now b").textContent === "CANADA DAY" && $(".hero .tag").textContent === "On the roof now · approximate", $(".now b").textContent);
  await tick();
  check("page: Turn on turns the light on", JSON.stringify(calls.find((c) => c.domain === "light")) === JSON.stringify({ domain: "light", service: "turn_on", data: {}, target: { entity_id: "light.porch" } }), JSON.stringify(calls));
  page.hass = withLight("on", "on1");
  await tick();
  act("resume").click();
  check("page: Back to schedule hands over at once", $(".now small").textContent.startsWith("By schedule"), $(".now small").textContent);
  await tick();
  check("page: Back to schedule calls its service", last().service === "resume_schedule" && last().domain === "trimlight_local", JSON.stringify(last()));
  page.hass = hass;
  await tick();

  // Patterns
  check("patterns: opens the saved pattern on the roof, with no unsaved copy in the rail", $$('[data-act="open"]').map((b) => b.dataset.key).join() === "new,6,7,9" && $(".editor-head h2").textContent === "CANADA DAY", $(".editor-head h2") ? $(".editor-head h2").textContent : "");
  check("patterns: each saved pattern has a play button, and the editor has no Show or Live preview", $$('[data-act="show-saved"]').map((b) => b.dataset.key).join() === "6,7,9" && !act("show-pattern") && !$('[data-in="live"]') && !!$('[data-in="try"]'));
  act("open", "new").click();
  await tick();
  check("patterns: a new pattern says so, with a name hint instead of a form label", $(".editor-head h2").textContent === "New pattern" && $('[data-in="name"]').placeholder === "e.g. Fourth of July");
  act("open", 7).click();
  await tick();
  check("patterns: HALLOWEEN opens with its name, mode and colours", $('[data-in="name"]').value === "HALLOWEEN" && $('[data-in="mode"]').value === "chase_forward" && $$(".tile").length === 3);
  check("patterns: overlay shows lightning, delete is blocked", act("overlay", "lightning").getAttribute("aria-pressed") === "true" && act("delete-pattern").disabled);
  act("show-saved", 7).click();
  await tick();
  check("patterns: the play button shows the saved pattern from the controller's memory, overlay and all", JSON.stringify(last().data) === JSON.stringify({ effect_id: 7 }) && last().service === "show_saved_effect", JSON.stringify(last()));
  page.hass = withLight("on", "on2");
  await tick();
  const before = calls.length;
  act("overlay", "snow").click();
  check("patterns: Snow shows as chosen at once", act("overlay", "snow").getAttribute("aria-pressed") === "true");
  await tick();
  await tick();
  check("patterns: Snow is saved, then the effect plays so it can be seen",
    JSON.stringify(calls.slice(before).map((c) => [c.service, c.data])) === JSON.stringify([["set_overlay", { effect: "HALLOWEEN", overlay: "snow" }], ["show_saved_effect", { effect_id: 7 }]]),
    JSON.stringify(calls.slice(before)));
  act("pick", "").click();
  await tick();
  check("patterns: the no-colour swatch leaves dots unlit", $$(".tile")[0].querySelector(".sw.unlit") !== null);
  $('[data-act="move"][data-step="1"]').click();
  await tick();
  check("patterns: move right swaps colours", $$(".tile")[1].querySelector(".sw.unlit") !== null);
  const tryBox = $('[data-in="try"]');
  tryBox.checked = true;
  tryBox.dispatchEvent(new Event("change", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 800));
  check("patterns: Try on the lights starts trying, then shows the edited pattern",
    JSON.stringify(wsCalls.at(-1)) === JSON.stringify({ type: "trimlight_local/try", entity_id: "light.porch", on: true })
    && JSON.stringify(last().data) === JSON.stringify({ mode: "chase_forward", colors: ["#7700ff", "none", "#7800ff"], dots: [5, 5, 5], speed: 1, brightness: 255 }), JSON.stringify([wsCalls.at(-1), last()]));
  check("patterns: while trying, the roofline and the note say so", $(".hero .tag").textContent === "Trying on the lights · approximate" && $$(".editor .note").some((n) => n.textContent.includes("go back")), $(".hero .tag").textContent);
  const tryAgain = $('[data-in="try"]');
  tryAgain.checked = false;
  tryAgain.dispatchEvent(new Event("change", { bubbles: true }));
  await tick();
  check("patterns: stopping puts the lights back", JSON.stringify(wsCalls.at(-1)) === JSON.stringify({ type: "trimlight_local/try", entity_id: "light.porch", on: false }) && $(".hero .tag").textContent !== "Trying on the lights · approximate", JSON.stringify(wsCalls.at(-1)));
  page.hass = hass;
  await tick();
  const name = $('[data-in="name"]');
  name.value = "Spooky";
  name.dispatchEvent(new Event("input", { bubbles: true }));
  await tick();
  check("patterns: a new name offers Save as new and Rename", act("save-pattern").textContent.trim() === "Save as new" && !act("rename-pattern").disabled);
  act("save-pattern").click();
  await tick();
  check("patterns: Save as new saves under the new name", last().service === "save_effect" && last().data.name === "Spooky" && last().data.colors[1] === "none", JSON.stringify(last()));

  // Effects
  await tab("effects");
  check("effects: all 180 are listed", $(".count").textContent.startsWith("180 effects"), $(".count").textContent);
  act("motion", "Snake").click();
  await tick();
  check("effects: the Snake filter narrows the list", $$(".fx").length > 10 && $$(".fx").every((f) => f.querySelector(".name").textContent.includes("Snake")), $$(".fx").length);
  act("motion", "All").click();
  await tick();
  const search = $('[data-in="fx-query"]');
  search.value = "rainbow wave";
  search.dispatchEvent(new Event("input", { bubbles: true }));
  await tick();
  check("effects: search finds Rainbow Wave", $$(".fx").length === 1 && $(".fx .name").textContent === "Rainbow Wave");
  $(".fx").click();
  await tick();
  check("effects: tapping a card shows it with its settings",
    JSON.stringify(last()) === JSON.stringify({ domain: "trimlight_local", service: "show_effect", data: { effect: "Rainbow Wave", speed: 100, length: 30, reverse: false, brightness: 255 }, target: { entity_id: "light.porch" } }), JSON.stringify(last()));
  act("favourite", 3).click();
  await tick();
  check("effects: the star saves favourites in Home Assistant", JSON.stringify(wsCalls.at(-1)) === JSON.stringify({ type: "trimlight_local/favourites", entity_id: "light.porch", favourites: [0, 3] }), JSON.stringify(wsCalls.at(-1)));
  act("save-effect").click();
  await tick();
  await tick();
  check("effects: Save shows the effect, then saves it by name", calls.at(-2).service === "show_effect" && last().service === "save_effect" && last().data.name === "Rainbow Wave", JSON.stringify(calls.slice(-2)));

  // Music
  await tab("music");
  check("music: 18 effects and the AUX source", $$(".mx").length === 18 && $(".source label").textContent.includes("Your controller is set up this way"));
  check("music: only the source that works is offered", $$(".source label").length === 1 && !$(".source .badge") && !$(".source input"));
  const withBusy = (busy, stamp) => ({ ...hass, states: { ...hass.states, "light.porch": { ...hass.states["light.porch"], last_updated: stamp, attributes: { ...hass.states["light.porch"].attributes, controller_busy: busy } } } });
  page.hass = withBusy(true, "busy");
  await tick();
  check("page: a busy controller explains the wait", !!$(".now .busy-note") && $(".now .busy-note").textContent.includes("up to 30 seconds") && $(".now .busy-note").textContent.includes("Trimlight app"), $(".now").textContent);
  page.hass = withBusy(false, "free");
  await tick();
  check("page: the warning goes once the controller is free", !$(".now .busy-note"));
  page.hass = hass;
  await tick();
  act("music", 7).click();
  await tick();
  check("music: a single-colour effect enables the colour", !$('[data-in="music-colour"]').disabled);
  act("start-music").click();
  await tick();
  check("music: Start sends the effect and settings",
    JSON.stringify(last().data) === JSON.stringify({ effect: "Single Color Pulse", length: 70, sensitivity: 13, color: [255, 0, 0] }), JSON.stringify(last()));

  // Schedules
  await tab("schedules");
  check("schedules: opens on the year view with 12 months", $$(".mini").length === 12);
  const pausedBox = $$(".warn-box").find((b) => b.textContent.includes("Schedules are paused"));
  check("schedules: says they are paused by your change, with a way back", !!pausedBox && pausedBox.textContent.includes("take over again at") && !!pausedBox.querySelector('[data-act="resume"]'), pausedBox ? pausedBox.textContent : "none");
  check("schedules: Dec 1-27 is coloured like CHRISTMAS", $$(".mini")[11].querySelectorAll(".g span.on").length === 27);
  check("schedules: the legend lists every schedule, and the waiting one", $$(".legend button").length === 5 && $$(".legend button.waiting").length === 1);
  $$(".mini")[11].querySelectorAll('.g span[data-act="day"]')[9].click();
  await tick();
  let drawer = $(".drawer");
  check("schedules: a day with a schedule opens it", drawer && drawer.querySelector("h2").textContent === "Schedule 6");
  check("schedules: its sunset time opens as sunset - 15", act("time-mode", "on") && $$('[data-act="time-mode"][data-key="on"]').find((b) => b.getAttribute("aria-pressed") === "true").dataset.mode === "sunset" && $('[data-in="offset"][data-key="on"]').value === "-15");
  check("schedules: the hint shows today's time", drawer.querySelector(".hint span").textContent === "today 16:30", drawer.querySelector(".hint span") && drawer.querySelector(".hint span").textContent);
  act("save-schedule").click();
  await tick();
  check("schedules: Save sends the schedule with its sun time",
    JSON.stringify(last()) === JSON.stringify({ domain: "trimlight_local", service: "set_schedule",
      data: { effect: "CHRISTMAS", start_date: "2000-12-01", end_date: "2000-12-27", on_sun: "sunset", on_offset: -15, off_time: "07:06", schedule_id: 6 }, target: { entity_id: "calendar.porch_schedules" } }), JSON.stringify(last()));
  $$(".mini")[2].querySelectorAll('.g span[data-act="day"]')[14].click();
  await tick();
  drawer = $(".drawer");
  check("schedules: a free day starts a new schedule there", drawer.querySelector("h2").textContent === "New schedule" && $('[data-in="drawer-month"][data-key="start"]').value === "3" && $('[data-in="drawer-day"][data-key="start"]').value === "15");
  act("save-schedule").click();
  await tick();
  check("schedules: the new schedule starts at sunset", JSON.stringify(last().data) === JSON.stringify({ effect: "CANADA DAY", start_date: "2000-03-15", end_date: "2000-03-15", on_sun: "sunset", on_offset: 0, off_time: "23:00" }), JSON.stringify(last()));
  act("view", "month").click();
  await tick();
  check("schedules: the month view has a week grid", $$(".month .week").length >= 4);
  act("view", "list").click();
  await tick();
  const rows = $$(".list button:not(.waiting)");
  check("schedules: the list is in date order with sun times", rows.map((r) => r.querySelector("b").textContent).join("|") === "Jun 19|Oct 31|Nov 24 – Nov 30|Dec 1 – Dec 27" && rows[3].querySelector(".mono").textContent === "Sunset − 15 min – 07:06",
    rows.map((r) => r.querySelector(".mono").textContent).join("|"));

  // Holidays
  act("view", "year").click();
  await tick();
  const held = $(".legend button.waiting");
  check("holidays: the waiting Diwali schedule says when it lights and when it goes back on",
    held.querySelector("small").textContent === "Follows Diwali, skips 2027. Next on Tue Oct 17, 2028; on the controller from Oct 18, 2027", held.querySelector("small").textContent);
  held.click();
  await tick();
  check("holidays: it opens as a waiting schedule following Diwali", $(".drawer h2").textContent === "Waiting schedule" && $('[data-in="drawer-holiday"]').value === "diwali"
    && act("date-mode", "holiday").getAttribute("aria-pressed") === "true" && act("delete-schedule") !== undefined);
  act("save-schedule").click();
  check("holidays: Save closes the editor at once", !$(".drawer"));
  await tick();
  check("holidays: saving it replaces the waiting one", JSON.stringify(last().data) === JSON.stringify({ effect: "HALLOWEEN", holiday: "diwali", holiday_start_offset: 0, holiday_end_offset: 0, on_time: "18:00", off_time: "23:00", waiting_id: "w1" }),
    JSON.stringify(last().data));
  $(".legend button.waiting").click();
  await tick();
  act("delete-schedule").click();
  await tick();
  check("holidays: Delete removes the waiting schedule", JSON.stringify(wsCalls.at(-1)) === JSON.stringify({ type: "trimlight_local/waiting_delete", entity_id: "light.porch", waiting_id: "w1" }) && !$(".legend button.waiting"),
    JSON.stringify(wsCalls.at(-1)));
  const thanksgivingWeek = $$(".legend button").find((b) => b.dataset.key === "10");
  check("holidays: the legend says schedule 10 follows Thanksgiving", thanksgivingWeek.querySelector("small").textContent === "Nov 24 – Nov 30 · follows Thanksgiving", thanksgivingWeek.querySelector("small").textContent);
  thanksgivingWeek.click();
  await tick();
  const years = () => $$(".drawer .years li").map((li) => li.textContent.trim());
  const offsetIn = (key) => $(`[data-in="drawer-hoff"][data-key="${key}"]`);
  check("holidays: it opens following Thanksgiving, from 2 days before to 4 after", act("date-mode", "holiday").getAttribute("aria-pressed") === "true"
    && $('[data-in="drawer-holiday"]').value === "thanksgiving" && offsetIn("from").value === "-2" && offsetIn("to").value === "4");
  check("holidays: the preview names the days and lists coming years", years()[0] === "2 days before to 4 days after Thanksgiving:" && years().includes("2027Tue Nov 23 – Mon Nov 29") && years().length === 4, years().join(" | "));
  await type(offsetIn("to"), "6");
  check("holidays: running into December shows CHRISTMAS keeps its nights", years().some((y) => y.endsWith("skipped: schedule 6 (CHRISTMAS) keeps those nights")) && $(".drawer .years .clash") !== null, years().join(" | "));
  await type(offsetIn("from"), "7");
  check("holidays: a first night after the last can't be saved", act("save-schedule").disabled && years()[0].includes("can't come before"), years().join(" | "));
  await type(offsetIn("from"), "-2");
  await type(offsetIn("to"), "4");
  STATE.calendar.find((x) => x.id === 10).holiday.problem = "Thanksgiving 2027 would overlap schedule 6, so this schedule stays on its old dates.";
  act("save-schedule").click();
  await tick();
  check("holidays: Save sends the holiday and its days instead of dates",
    JSON.stringify(last().data) === JSON.stringify({ effect: "CANADA DAY", holiday: "thanksgiving", holiday_start_offset: -2, holiday_end_offset: 4, on_time: "16:27", off_time: "06:51", schedule_id: 10 }), JSON.stringify(last().data));
  check("holidays: a schedule that couldn't move is flagged", $$(".warn-box b").some((b) => b.textContent === "Schedule 10 didn't move."));
  STATE.calendar.find((x) => x.id === 10).holiday.problem = null;
  act("new-schedule").click();
  await tick();
  check("holidays: a new schedule starts with dates, not a holiday", act("date-mode", "fixed").getAttribute("aria-pressed") === "true" && !$('[data-in="drawer-holiday"]'));
  act("date-mode", "holiday").click();
  await tick();
  check("holidays: Follow a holiday picks one", ["easter", "thanksgiving", "diwali"].includes($('[data-in="drawer-holiday"]').value));
  await choose($('[data-in="drawer-holiday"]'), "easter");
  check("holidays: picking Easter previews its dates", years().includes("2027Sun Mar 28"), years().join(" | "));
  act("save-schedule").click();
  await tick();
  check("holidays: a new holiday schedule sends no dates",
    JSON.stringify(last().data) === JSON.stringify({ effect: "CANADA DAY", holiday: "easter", holiday_start_offset: 0, holiday_end_offset: 0, on_sun: "sunset", on_offset: 0, off_time: "23:00" }), JSON.stringify(last().data));
  act("view", "month").click();
  await tick();
  const thisMonth = new Date();
  for (let n = (2027 - thisMonth.getFullYear()) * 12 + 10 - thisMonth.getMonth(); n > 0; n--) {
    $$('[data-act="step"]').find((b) => b.dataset.step === "1").click();
    await tick();
  }
  const bars = $$('.month .bars button[data-key="10"]').map((b) => b.style.gridColumn);
  check("holidays: November 2027 shows that year's Thanksgiving week, Tue 23 to Mon 29", JSON.stringify(bars) === JSON.stringify(["3 / 8", "1 / 3"]), JSON.stringify(bars));
  act("view", "year").click();
  await tick();

  // Daily
  await tab("daily");
  check("daily: two cards, Save starts disabled", $$(".daily .card").length === 2 && act("daily-save", 0).disabled);
  act("daily-enabled", 0).click();
  await tick();
  $$('[data-act="time-mode"][data-key="daily0on"]').find((b) => b.dataset.mode === "sunset").click();
  await tick();
  act("daily-save", 0).click();
  await tick();
  check("daily: Save sends the schedule with a sunset time",
    JSON.stringify(last().data) === JSON.stringify({ schedule: 1, enabled: true, effect: "CANADA DAY", repeat: "today_only", on_sun: "sunset", on_offset: 0, off_time: "00:00" }), JSON.stringify(last()));

  // Combined effect
  await tab("combined");
  check("combined: shows its effect and users", $$(".combo .item").length === 1 && $(".card:last-child .note").textContent.includes("the Jun 19 schedule"));
  const pick = $('[data-in="combined-pick"]');
  pick.value = "HALLOWEEN";
  act("combined-add").click();
  await tick();
  act("combined-up", 1).click();
  await tick();
  const interval = $('[data-in="combined-interval"]');
  interval.value = "7";
  interval.dispatchEvent(new Event("input", { bubbles: true }));
  act("combined-save").click();
  await tick();
  check("combined: Save sends the order and interval", JSON.stringify(last().data) === JSON.stringify({ effects: ["HALLOWEEN", "CHRISTMAS"], interval: 7 }), JSON.stringify(last()));

  // Settings
  await tab("settings");
  const pressed = (name) => $$(`[data-act="${name}"]`).filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.textContent.trim()).join();
  const lastWs = () => wsCalls.at(-1);
  const sent = (n) => JSON.stringify(wsCalls.slice(-n).map(({ type, entity_id, ...rest }) => [type.split("/")[1], rest]));
  check("settings: shows the firmware, chip, colour order and four ports", $(".facts").textContent.includes("1.11") && pressed("led-chip") === "UCS1903" && pressed("led-order") === "RGB"
    && $$(".ports tbody tr").length === 4 && $('[data-in="port-end"][data-key="0"]').value === "200", `${pressed("led-chip")} ${pressed("led-order")}`);
  check("settings: no test banner before a test", !$(".session"));
  act("led-test", "red").click();
  await tick();
  check("settings: a test colour shows red and a Done banner", sent(1) === '[["led_test",{"show":"red"}]]' && $(".session b").textContent === "Showing a test" && act("led-end", "undo").textContent === "Done", sent(1));
  act("led-chip", 1).click();
  await tick();
  check("settings: tapping a chip type tries it with Keep and Undo", sent(1) === '[["led_setup",{"ic_type":1}]]' && pressed("led-chip") === "DMX512" && $(".session b").textContent === "Trying new LED settings"
    && act("led-end", "keep") && $(".countdown").textContent === "60", sent(1));
  act("led-end", "undo").click();
  await tick();
  check("settings: Undo ends the test and the old chip type is back", sent(1) === '[["led_session_end",{"keep":false}]]' && pressed("led-chip") === "UCS1903" && !$(".session"), sent(1));

  act("led-wizard", "start").click();
  await tick();
  check("settings: finding the order starts from RGB and shows red", sent(2) === '[["led_setup",{"channel_order":0}],["led_test",{"show":"red"}]]' && $(".wizard p").textContent.startsWith("1 of 2"), sent(2));
  act("led-wizard", "G").click();
  await tick();
  check("settings: then it shows green", sent(1) === '[["led_test",{"show":"green"}]]' && $(".wizard p").textContent.startsWith("2 of 2"), sent(1));
  act("led-wizard", "B").click();
  await tick();
  check("settings: red seen as green and green as blue tries GBR", sent(2) === '[["led_setup",{"channel_order":3}],["led_test",{"show":"red"}]]' && $(".wizard b").textContent === "GBR" && pressed("led-order") === "GBR", sent(2));
  act("led-wizard", "no").click();
  await tick();
  check("settings: if red is still wrong it tries BRG", sent(2) === '[["led_setup",{"channel_order":4}],["led_test",{"show":"red"}]]' && $(".wizard b").textContent === "BRG", sent(2));
  act("led-wizard", "yes").click();
  await tick();
  check("settings: Yes keeps it and closes the questions", sent(1) === '[["led_session_end",{"keep":true}]]' && pressed("led-order") === "BRG" && !$(".wizard") && !$(".session"), sent(1));
  act("led-wizard", "start").click();
  await tick();
  act("led-wizard", "X").click();
  await tick();
  check("settings: Something else points at the chip type", $(".wizard p").textContent.includes("chip type is probably wrong"));
  act("led-end", "undo").click();
  await tick();
  check("settings: undoing the test closes the questions and puts BRG back", !$(".wizard") && pressed("led-order") === "BRG");

  const portInput = (field, i) => $(`[data-in="port-${field}"][data-key="${i}"]`);
  check("settings: Try starts disabled", act("ports-try").disabled && act("ports-revert").disabled);
  await type(portInput("end", 1), "450");
  check("settings: a new last dot updates the count and enables Try", $$(".ports .count")[1].textContent === "450" && !act("ports-try").disabled);
  await type(portInput("start", 1), "500");
  check("settings: a first dot after the last one is refused", $$(".ports tbody tr")[1].classList.contains("bad") && act("ports-try").disabled && $$(".ports .count")[1].textContent === "–");
  await type(portInput("start", 1), "1");
  portInput("on", 2).click();
  await tick();
  act("ports-try").click();
  await tick();
  check("settings: Try sends all four ports", sent(1) === JSON.stringify([["led_setup", { ports: [{ start: 1, end: 200, enabled: true }, { start: 1, end: 450, enabled: true }, { start: 1, end: 600, enabled: false }, { start: 1, end: 600, enabled: true }] }]])
    && $(".session b").textContent === "Trying new LED settings" && act("ports-try").disabled, sent(1));
  act("led-end", "keep").click();
  await tick();
  check("settings: Keep keeps the ports", sent(1) === '[["led_session_end",{"keep":true}]]' && portInput("end", 1).value === "450" && !portInput("on", 2).checked, sent(1));

  check("settings: changes last 1 hour by default, with Ecobee-style choices", pressed("hold") === "1 hour" && $$('[data-act="hold"]').map((b) => b.textContent.trim()).join() === "30 minutes,1 hour,2 hours,4 hours,Until changed", pressed("hold"));
  act("hold", "keep").click();
  check("settings: Until changed is chosen at once", pressed("hold") === "Until changed", pressed("hold"));
  await tick();
  check("settings: the choice is sent to Home Assistant", sent(1) === '[["hold_minutes",{"minutes":null}]]', sent(1));
  const nameInput = $('[data-in="ctl-name"]');
  check("settings: the name shows with Rename disabled", nameInput.value === "Porch" && act("rename-controller").disabled);
  await type(nameInput, "Roof/line");
  check("settings: a name with a slash is refused", act("rename-controller").disabled);
  await type(nameInput, "Roofline ");
  act("rename-controller").click();
  await tick();
  check("settings: Rename sends the trimmed name", sent(1) === '[["rename",{"name":"Roofline"}]]' && $('[data-in="ctl-name"]').value === "Roofline" && act("rename-controller").disabled, sent(1));
  check("settings: the roofline picture shows the chosen house", act("roofline", "two_gables").getAttribute("aria-pressed") === "true");
  act("roofline", "house").click();
  await tick();
  check("settings: picking a roofline saves it in Home Assistant", JSON.stringify(wsCalls.at(-1)) === JSON.stringify({ type: "trimlight_local/roofline", entity_id: "light.porch", roofline: "house" }) && act("roofline", "house").getAttribute("aria-pressed") === "true");
  page.remove();
}

async function run() {
  const hash = location.hash.slice(1);
  if (hash === "card") {
    const card = makeCard();
    await tick();
    await choose(card.shadowRoot.querySelector("trimlight-pattern-editor").shadowRoot.querySelector("select"), "7");
    return;
  }
  if (hash.startsWith("page-")) {
    const page = makePage();
    await tick();
    const key = hash.slice(5).replace("-narrow", "");
    const view = { month: "schedules", list: "schedules", drawer: "schedules", holiday: "schedules", "settings-test": "settings" }[key] || key;
    inside(page).$(`[data-act="tab"][data-key="${view}"]`).click();
    await tick();
    if (key === "month" || key === "list") {
      inside(page).$(`[data-act="view"][data-key="${key}"]`).click();
      await tick();
    }
    if (key === "holiday") {
      inside(page).$('.legend button[data-key="10"]').click();
      await tick();
    }
    if (key === "drawer") {
      inside(page).$$(".mini")[11].querySelectorAll('.g span[data-act="day"]')[9].click();
      await tick();
    }
    if (key === "effects") {
      inside(page).$('[data-act="fx"][data-key="3"]').click();
      await tick();
    }
    if (key === "settings-test") {
      for (const [act, k] of [["led-chip", 1], ["led-wizard", "start"], ["led-wizard", "G"]]) {
        inside(page).$(`[data-act="${act}"][data-key="${k}"]`).click();
        await tick();
      }
    }
    if (key === "music") {
      inside(page).$('[data-act="music"][data-key="0"]').click();
      await tick();
    }
    return;
  }
  if (hash === "test") {
    await testCard();
    await testPage();
  }
}

run().catch((err) => check("harness ran without errors", false, err.stack)).finally(() => {
  if (results.length) document.getElementById("results").textContent = results.join("\n") + `\nDONE ${results.filter((r) => r.startsWith("FAIL")).length} failed of ${results.length}`;
});
