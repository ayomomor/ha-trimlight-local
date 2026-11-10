# Trimlight Edge: local Home Assistant control

Controls a Trimlight Edge controller over your LAN, with no cloud account and no internet. The protocol was recovered from the official Android app (2.5.3); see [PROTOCOL.md](PROTOCOL.md). It is not made by or affiliated with Trimlight.

**Status:** tested on a real controller (2026-11-08): on/off/timer, built-in effects, solid colour, saved effects and brightness changes all work, and so does reading the state back.

## 1. Test against your controller

Use the command-line tool first. Run it from any computer that can reach the controller's IP (Python 3.10 or newer, no extra packages):

```
cd tools
python trimlight_cli.py --host 192.168.10.66 status
python trimlight_cli.py --host 192.168.10.66 off
python trimlight_cli.py --host 192.168.10.66 on
python trimlight_cli.py --host 192.168.10.66 effect "Rainbow Wave" --brightness 180
python trimlight_cli.py --host 192.168.10.66 color 255 120 0
python trimlight_cli.py --host 192.168.10.66 timer
python trimlight_cli.py effects
python trimlight_cli.py discover --broadcast 192.168.10.255
```

`discover` only works from the controller's own subnet, because it uses broadcast.

Add `-v` to any command to print every frame sent and received. If something doesn't behave as expected, that output is what's needed to fix it.

The controller serves one connection at a time: when something new connects (the phone app, this tool, Home Assistant), it drops the previous one.

## 2. Install in Home Assistant

With [HACS](https://hacs.xyz):

1. In HACS, open **⋮ → Custom repositories**, add `https://github.com/ayomomor/ha-trimlight-local` with the type **Integration**.
2. Find **Trimlight Edge (Local)** in HACS and download it.
3. Restart Home Assistant.

Or by hand: copy `custom_components/trimlight_local` into your Home Assistant `config/custom_components/` folder, then restart Home Assistant.

Then:

1. Go to **Settings → Devices & services → Add integration → Trimlight Edge (Local)**.
2. Enter the controller's IP address, for example `192.168.10.66`. Controllers on the same subnet as Home Assistant are filled in automatically.

The Home Assistant machine must be able to reach the controller on **TCP 8586**. Give the controller a fixed IP address (a DHCP reservation in your router).

Tested with Home Assistant 2026.10 and 2026.11.

## What you get

- **A Trimlight page in the sidebar**, installed with the integration. See below.
- **Light**
  - on/off
  - brightness
  - RGB colour
  - your saved effects, by name (read from the controller's library)
  - all 180 built-in effects, with names matching the app
- **Mode select**
  - Off
  - Manual: whatever was last shown
  - Timer: the schedules stored on the controller
- **Effect speed** (0–255) and **Effect length** (1–90 dots) sliders, and a **Reverse direction** switch.
  - They show the settings of the effect on the lights, including one started from the phone app.
  - Changing one updates the effect on the lights straight away.
  - Speed applies to every effect. Length and direction apply to built-in effects only.
  - When the lights are off or on a timer, a change is kept and used for the next effect.
- **Schedules calendar**: the controller's schedules, one event per night, with the effect as the title and the schedule number in the description.
  - Enabled daily schedules appear too, marked "(daily)".
  - Schedules run when Mode is Timer.
  - The calendar also shows in Home Assistant's own Calendar page.
- **Daily schedules 1 and 2**, on the device page under Configuration. Each has:
  - an on/off switch;
  - an effect;
  - a repeat setting: today only, every day, weekdays or weekend;
  - an on time and an off time.
- **Actions on the light**:
  - `show_pattern`: a custom pattern with your own colours, like the app's pattern editor. It is not saved on the controller.
  - `save_effect`: saves what the lights show now, or a pattern you give it, in the controller's library under a name. Saving under an existing name replaces that effect.
  - `rename_saved_effect`, `delete_saved_effect`.
  - `set_overlay`: lightning, snow or none over a saved effect.
  - `set_combined_effect`: the saved effects the combined effect plays in turn, and the minutes each plays.
  - `set_daily_schedule`: changes several settings of a daily schedule in one write. The controller pauses for about 8 seconds after each write.
  - `show_effect`: one of the 180 built-in effects with its speed, length, direction and brightness, in one command.
  - `start_music`: one of the 18 music effects, driven by the controller's audio input. Colour, length and sensitivity are optional and keep the controller's current settings.
  - `show_saved_effect`: shows a saved effect by its slot number (`effect_id`).
- **Actions on the calendar**: `set_schedule` adds a schedule, or changes one when given its `schedule_id`; `delete_schedule` deletes one.
- **Pattern editor card** for your dashboards, like the app's pattern editor. See below.
- **Light attributes**:
  - `overlays`: which saved effects have lightning or snow;
  - `combined_effect` and `combined_effect_interval`: the combined effect's contents and timing.

Example: a red, white and blue chase.

```yaml
action: trimlight_local.show_pattern
target:
  entity_id: light.trimlight_192_168_10_66
data:
  mode: chase_forward
  colors: [red, white, "#0000ff"]
  dots: 5
  speed: 120
  brightness: 200
```

`mode` is one of the 21 pattern modes, for example `static`, `chase_forward`, `twinkle_fade`, `comet_middle_to_out`, `wave_backward`, `strobe`, `firework` or `inside_out`.

`dots` is how many dots each colour covers. Give one number for all colours, or a list with one number per colour.

Example: save that pattern, then show it every night from Dec 1 to Dec 27, 16:30 to 23:00.

```yaml
action: trimlight_local.save_effect
target:
  entity_id: light.trimlight_192_168_10_66
data:
  name: Porch flag
  mode: chase_forward
  colors: [red, white, "#0000ff"]
---
action: trimlight_local.set_schedule
target:
  entity_id: calendar.trimlight_192_168_10_66_schedules
data:
  effect: Porch flag
  start_date: "2026-12-01"
  end_date: "2026-12-27"
  on_sun: sunset
  on_offset: -15
  off_time: "23:00"
```

Schedules can show saved effects and the combined effect, but not built-in effects directly; save a built-in effect first with `save_effect` while it is showing. The year in the dates is ignored: schedules repeat every year.

### Sunrise and sunset times

Any schedule's on or off time can follow sunset or sunrise, with an offset in minutes. Use `on_sun` / `off_sun` and `on_offset` / `off_offset` in place of `on_time` / `off_time`, or pick "At sunset" on the Trimlight page.

The controller only stores clock times. The phone app looks sunset up online once and stores that fixed time. Home Assistant does better, from its own location:

- **When you save**, it works out the clock time for the next night the schedule runs, and writes that.
- **Every day at noon**, it rewrites the time of each sun-following schedule that runs that day, since sunset moves a minute or two a day.
  - The controller pauses for about 15 seconds after each calendar-schedule change.
  - Noon is used because it is outside evening and early-morning on/off times.
- **The calendar** shows each night's own sun time.
- **If you change that schedule's time in the phone app**, Home Assistant notices at its next update and stops adjusting it, so the app's change stays.
- **Setting a clock time** for a daily schedule (for example with its time entity) stops that time following the sun.
- **A "Today only" daily schedule** gets its sun time when saved and isn't rewritten afterwards. Rewriting it would move it to the day of the rewrite.

Sun times use Home Assistant's **home location** (Settings → System → General), which must match its time zone. If it doesn't (for example, a location never set while the time zone is set), sunset comes out in the morning. The Trimlight page warns about that.

### Holidays that move

A schedule can follow a holiday whose date changes every year, instead of fixed dates: give `holiday` plus `holiday_start_offset` and `holiday_end_offset` (days from the holiday, negative is before) in place of `start_date` and `end_date`, or pick **Follow a holiday** on the Trimlight page.

```yaml
action: trimlight_local.set_schedule
target:
  entity_id: calendar.trimlight_192_168_10_66_schedules
data:
  effect: Simple
  holiday: thanksgiving
  holiday_start_offset: -2
  holiday_end_offset: 4
  on_sun: sunset
  off_time: "23:00"
```

The page lists 56 holidays in groups:

- **US holidays**: the fixed ones (New Year's Day, Valentine's Day, St. Patrick's Day, Juneteenth, Independence Day, Halloween, Veterans Day, Christmas Eve, Christmas Day, New Year's Eve) and the moving ones (Martin Luther King Jr. Day, Presidents' Day, Mardi Gras, Good Friday, Easter, Mother's Day, Memorial Day, Father's Day, Labor Day, Grandparents Day, Columbus Day / Indigenous Peoples' Day, Election Day, Thanksgiving). Fixed dates could be an ordinary schedule too; they are listed so every holiday can be picked the same way.
- **Christian**: Ash Wednesday, Palm Sunday, Pentecost, Orthodox Christmas, Orthodox Easter.
- **Jewish**: Purim, Passover, Shavuot, Rosh Hashanah, Yom Kippur, Sukkot, Hanukkah. Jewish holidays begin at sunset the evening before, so a first night of -1 starts with that evening.
- **Islamic** (estimated: the actual day depends on the moon sighting): Eid al-Fitr, Eid al-Adha, Islamic New Year, the Prophet's Birthday.
- **Hindu, Sikh and Buddhist**: Holi, Vesak / Buddha Purnima, Raksha Bandhan, Janmashtami, Ganesh Chaturthi, Dussehra, Diwali, Guru Nanak Jayanti.
- **East Asian**: Lunar New Year, Dragon Boat Festival, Mid-Autumn Festival.
- **Other days**: Nowruz, Cinco de Mayo, Canada Day, Nigerian Independence Day, Day of the Dead, Kwanzaa.

US and Christian dates are worked out by rule, for any year. The Jewish, Islamic, Hindu, Sikh, Buddhist, East Asian dates and Nowruz come from the holidays library that ships with Home Assistant, whose lunar calendars reach about ten years ahead (Indian holidays to 2035). Past that, the schedule stays where it is and the page says so.

The controller allows one schedule per night, so when two schedules want the same nights:

1. **Schedules with fixed dates always keep them.** Halloween on Oct 26-30 wins over Diwali when Diwali falls on Oct 29.
2. **US holidays win over every other holiday.** Thanksgiving week wins over Guru Nanak Jayanti when that falls on Nov 24.
3. **Otherwise the holiday that starts first wins.**

The schedule that loses skips that year and lights again the next year it gets its nights. If a US holiday needs nights another holiday's schedule is holding on the controller, that schedule is taken off first.

The controller only stores a month and day, and repeats them every year. So Home Assistant:

- **When you save**, writes the dates of the next nights the schedule gets, or the current ones while they are running.
- **Every day at noon**, moves each schedule on once its last night has passed.
- **Only writes dates once they can't come round early.** Diwali on Oct 17, 2028 can't be written in November 2026, or the controller would light it on Oct 17, 2027 too. Until it can, the schedule keeps its old dates if those can't come round either. Otherwise Home Assistant takes it off the controller and holds it. A held schedule shows on the Schedules tab with a dashed outline, saying when it next lights and when it goes back on.
- **The calendar** shows each year's own nights, without the years a schedule skips.
- **If you change its dates in the phone app**, stops moving it, as with sun times.

Example automation action. A controller added by IP address is named "Trimlight 192.168.10.66", which gives this entity ID:

```yaml
action: light.turn_on
target:
  entity_id: light.trimlight_192_168_10_66
data:
  effect: Red Green White Snake
  brightness: 200
```

## The Trimlight page

The integration adds **Trimlight** to the sidebar, a local stand-in for the phone app. There is nothing else to install. It uses Home Assistant's theme, light or dark.

- **At the top**:
  - a night-time roofline that previews what you are editing, or shows what the lights are doing. The preview is approximate: the controller's own animation isn't known in detail.
  - a **Now** strip: the current effect, or the next schedule when Mode is Timer.
  - **Mode** buttons: Off, Manual, Timer.
- **Patterns**: create, edit, preview and save patterns.
  - Saved patterns are listed down the side.
  - Colours are tiles, with dot counts, a palette, any custom colour, and "no colour" for unlit dots.
  - Also here: speed, brightness, lightning or snow, Show on lights, Live preview, Save or Save as new, Rename and Delete.
  - Lightning and snow belong to a saved effect, so they only show when the controller plays that effect from its memory. Turning one on plays the effect, and Show on lights does the same for a saved pattern you haven't changed. A changed pattern is sent as a preview, which shows without its lightning or snow.
- **Effects**: all 180 built-in effects.
  - Search, filter by motion (snake, wave, comet…) or colour, and star favourites; favourites are kept in Home Assistant.
  - Tapping one shows it on the lights. Speed, length, brightness and direction sit beside it, and **Save** puts it in the controller's library so schedules can use it.
- **Music**: the 18 music effects, driven by the controller's own audio (AUX) input, with colour, length and sensitivity.
- **Schedules**:
  - Views: **Year** (12 small months, coloured like each schedule's pattern), **Month** (multi-day bars) and **List**.
  - Click a day to open its schedule, or to add one starting that day. The editor opens at the side.
  - On and off times can be clock times or follow sunset or sunrise, with an offset.
  - Dates can be the same every year, or follow a holiday such as Easter or Thanksgiving. The editor previews the coming three years and flags any that would overlap another schedule.
- **Daily**: both daily schedules, with a 24-hour bar showing when each is on.
- **Combined effect**: the saved effects it plays in turn, the minutes each plays, and a one-cycle timeline.
- **Settings**:
  - **LED setup**, for when lights are added or replaced. Every change here is a trial: the lights switch to Manual, and the change is undone after a minute unless you tap **Keep**. **Undo** puts it back straight away. Either way, the effect and mode from before come back.
    - **Test colours**: red, green, blue, white, or blocks of all four, on every light.
    - **LED chip**: tap a chip type to try it. Flickering or random colours mean the chip type is wrong.
    - **Colour order**: tap one to try it, or use **Find the right order**: it shows red, then green, and asks what colour you see each time. It works out the order from your two answers, then asks you to check it.
    - **Ports**: each output's first and last dot, and whether it is used. **Try** sends all four.
  - **Controller**: rename it (the name the phone app shows), plus its address, firmware and clock.
  - **Roofline picture** for the preview: a simple house, or two gables with a porch. The choice is kept in Home Assistant for each controller.

With more than one controller, a picker appears in the header. On a phone, the menu button at the top left opens the sidebar.

## Pattern editor card

The integration installs a dashboard card for creating and editing patterns; there is nothing else to install.

Recent Home Assistant versions start with an auto-generated dashboard (Home, Areas). It only takes extra cards after **Take control**, which stops it updating itself. A separate Trimlight dashboard is easier, and it gets its own place in the sidebar:

1. Go to **Settings → Dashboards → Add dashboard → New dashboard from scratch**.
2. Title it "Trimlight" and tick **Add to sidebar**.
3. Open it. To get the ready-made layout, choose **pencil → ⋮ → Raw configuration editor** and replace everything with [dashboard.yaml](dashboard.yaml). That layout has:
   - the light, Mode and effect controls;
   - the pattern editor;
   - the schedules calendar and both daily schedules.
4. Or choose **Add card** and search for **Trimlight pattern editor**.

Or add a manual card:

```yaml
type: custom:trimlight-pattern-card
entity: light.trimlight_192_168_10_66
```

In the card:

- **Edit** picks what to work on:
  - a new pattern;
  - what the lights show now;
  - one of your saved patterns. Saved built-in effects are listed but can't be edited here; change them with the light's controls and Save effect.
- **Colours**: click a row, then a swatch. Or use the row's own colour picker. The crossed-out swatch is "no colour", which leaves those dots unlit.
  - Set how many dots each colour covers, reorder colours with the arrows, and add up to 30.
  - The strip above shows how the colours repeat along the lights. It doesn't animate the mode.
- **Speed**, **Brightness**, and **Overlay** (lightning or snow, for saved patterns).
- **Show on lights** previews the pattern without saving it. With **Live preview** ticked, every change shows on the lights after a short pause.
- **Save** stores the pattern under the name shown. Change the name and the button becomes **Save as new**; the original stays.
- **Rename** renames the saved pattern to the name shown.
- **Delete** removes it from the controller. It is greyed out while a schedule or the combined effect uses the pattern.

## Limitations

- Home Assistant shows the effect and brightness the controller reports, including changes made from the phone app or by schedules (within one 30 s poll). For a solid colour the controller doesn't report the colour, so Home Assistant shows the last colour it set.
- The controller doesn't acknowledge commands and occasionally ignores one. For a change to its memory, Home Assistant notices and sends it again (see below). For anything else, it corrects its display at the next poll; turning the light on again resends the command.
- The Trimlight page works like the phone app: a tap shows its result at once and the command goes out in the background. A small "Saving" sign in the header shows while changes are still being confirmed, and a message appears only if one fails.
- A solid colour has no separate brightness on the controller, so brightness is applied by scaling the colour.
- A saved effect at its stored brightness is shown as saved. At any other brightness, its pattern is re-sent with the new brightness; the copy saved on the controller is not changed.
- The controller serves one connection at a time. When the phone app connects, the controller drops Home Assistant's connection.
  - Home Assistant then stops polling for 5 minutes, so it doesn't cut the app off, and keeps showing the last known state.
  - The light's `polling_paused` attribute is true during that time.
  - Anything you ask Home Assistant to do still goes through straight away, and takes the connection back.
- Home Assistant polls every 30 s. Each poll also sets the controller's clock, using Home Assistant's timezone, as the app does when it connects.
- Changes to the controller's memory are checked the same way:
  - **Covered**: saved effects, schedules, overlays and the combined effect.
  - **How**: the controller never confirms a change, so Home Assistant reads it back straight after. If the change isn't there, it sends it again, up to three times, before reporting an error.
  - **Any mode**: the phone app only makes these changes in Manual mode, but the controller accepts them in any mode (tested), so changing schedules doesn't turn the lights on.
  - **Schedule changes are slow to confirm**: after one, the controller ignores everything for 10–20 seconds, so the action takes that long to finish.
- The app's checks are applied here too:
  - at most 60 saved effects and 60 calendar schedules;
  - one calendar schedule per date;
  - the two daily schedules can't overlap;
  - an effect used by a schedule or the combined effect can't be deleted.
- Music mode follows the controller's own audio input. Following music played through Home Assistant isn't built yet.
- Changing the colour order and renaming the controller have been tested on a real controller. Changing the chip type or the ports follows the app's byte layout, but hasn't been tried on one yet. The one-minute trial is there so a wrong setting puts itself back.
- Not included:
  - Wi-Fi setup;
  - firmware updates.

## Debug logging

```yaml
logger:
  logs:
    custom_components.trimlight_local: debug
```

## Tests

- **Client and protocol**: `python -m unittest discover -s tests` (no dependencies).
- **Home Assistant integration**: `pip install pytest-homeassistant-custom-component holidays`, then `pytest` from this folder. Run the client tests separately: Home Assistant's test plugin blocks the local socket they use.
- **Page and card**: open `tests/card/harness.html#test` in a browser. It clicks through the card and every tab of the page against a simulated Home Assistant, and lists PASS/FAIL lines.
  - `#card` shows the card.
  - `#page-schedules` (or `patterns`, `daily`, `combined`) shows a tab.
  - Add `-narrow` for the phone layout.
