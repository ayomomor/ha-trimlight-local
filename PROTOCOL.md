# Trimlight Edge local protocol

The local network protocol of Trimlight Edge controllers, as used by this integration. Statements marked *(inferred)* are educated guesses, not confirmed on a controller.

Status, 2026-11-08: checked against a real controller (UCS1903, ports 1–200 and 1–600, 17 saved effects, module firmware "1.11", MCU firmware 0x1042). Connecting, verify, sync, heartbeat, and sub-commands 0x02, 0x03, 0x04, 0x0b and 0x0d behave as described here, and the whole sync reply decodes as described. On 2026-11-09 the memory-changing sub-commands 0x05, 0x0e, 0x0f, 0x10, 0x12, 0x13, 0x14 and 0x1b, and music sub 0x0c with the AUX source, were also tested on it, each read back after sending, and the controller was left byte-for-byte as before. Later that day, colour-order sub 0x07 and rename (cmd 0x07) were tested the same way. 0x11 (modify calendar schedule), 0x1a (older lightning), IC type 0x09 and ports 0x0a are untested. See "Behaviour on hardware" at the end.

## Ports

| Port | Proto | Direction | Use |
|------|-------|-----------|-----|
| 8586 | TCP | app → controller | control session |
| 8589 | UDP | app → broadcast | discovery request |
| 8588 | UDP | controller → app | app's discovery listener |

In hotspot (setup) mode the controller is `192.168.4.1` and its Wi-Fi password is `12345678`.

## SPTECH frame (all TCP commands except verify)

Requests and replies use the same layout.

```
offset  size  field
0       6     "SPTECH"  (53 50 54 45 43 48)
6       1     command
7       3     length of extra + body, big-endian (24-bit)
10      16    MD5 of the plaintext body
26      1     key, random 0..254
27      n     extra, then body, every byte XOR key
```

`extra` is a prefix left out of the MD5; only firmware updates use it. There is no chunking: every command, including a 3.4 KB full-state write, is one frame.

### Commands

| cmd | meaning |
|-----|---------|
| 0x01 | verify device, see below, uses the S-frame |
| 0x02 | Wi-Fi provisioning |
| 0x04 | pass-through to the LED MCU, used for all light control |
| 0x05 | Wi-Fi module firmware update; extra = `[ver hi, ver lo, 0]`, body = firmware file |
| 0x06 | discovery (UDP only) |
| 0x07 | rename device: body `[n] + UTF-8 name`, where n is the name's UTF-16 length, max 25 characters |
| 0x08 | full-state write: body `16 | len(2) | state`, see "Full-state write" |
| 0x09 | MCU firmware update; extra = `[ver hi, ver lo, flag]` |
| 0x0a | music stream: body in the sub 0x0c layout |
| 0x0b | heartbeat, body `[0x01]` |

### Pass-through body (cmd 0x04)

```
sub(1) | data length, big-endian (2) | data
```

Replies to pass-through come back as cmd 0x04 with the same body layout, starting with the sub-command. Only sync (0x15) gets a reply; the app waits for none of the others (see "Sending").

The app refuses every sub-command except 0x02, 0x15 and 0x16 unless the switch state is manual ("Change to manual mode first."). Whether the firmware enforces this is not known.

| sub | meaning | data |
|-----|---------|------|
| 0x02 | set switch state | `[state]`: 0 off, 1 manual, 2 timer |
| 0x03 | preview built-in effect | `[mode, speed, brightness, pixelLen, reverse]` |
| 0x04 | show effect from the controller's library | `[effectId]` |
| 0x05 | remove effect from library | `[effectId]` |
| 0x07 | set channel order | `0 RGB, 1 RBG, 2 GRB, 3 GBR, 4 BRG, 5 BGR` |
| 0x09 | set IC type | `0 UCS1903, 1 DMX512, 2 UCS5603` |
| 0x0a | set one port's dots | `[port 0–3, start (2), end (2), enabled]`, big-endian, start 1–2700, end ≥ start; the app sends one frame per port |
| 0x0b | preview solid colour | `[R, G, B]` |
| 0x0c | music settings and bands | see "Music" |
| 0x0d | preview custom effect | `[mode, speed, brightness] + segments` |
| 0x0e | modify library effect | a library entry, existing id |
| 0x0f | add effect to library | a library entry, new id |
| 0x10 | add calendar schedule | 10-byte calendar entry, new id |
| 0x11 | modify calendar schedule | 10-byte calendar entry, existing id |
| 0x12 | delete calendar schedule | `[id]` |
| 0x13 | modify daily schedule | `[index 0/1, enabled, effectId, repeat, onH, onM, offH, offM, month, day]` |
| 0x14 | set combined effect | `[n, n effect ids, interval minutes]` |
| 0x15 | sync data, also sets the controller clock | see below |
| 0x16 | full state, sent with cmd 0x08 | see "Full-state write" |
| 0x1a | set lightning (older firmware) | effect ids with lightning, no count byte |
| 0x1b | set lightning/snow overlays | `[kind, effectId]` pairs, no count byte; kind 0 lightning, 1 snow |
| 0x1c | time-sync mode | `[0 auto / 1 manual, yy, mm, dd, weekday Mon=1..Sun=7, hh, mi, ss]` |

Built-in effect fields, from the effect editor's sliders:

- `mode`: 0..179, index into the 180 names in `effects.py`.
- `speed`: 0..255.
- `brightness`: 0..255.
- `pixelLen`: 1..90.
- `reverse`: 0 or 1.

Custom patterns have no pixelLen or direction; direction comes from the mode (e.g. CHASE FORWARD / CHASE BACKWARD).

### Custom pattern segments

Custom patterns have 30 segments. Their format depends on the MCU firmware version (sync data bytes 19..20, also in the verify reply): below 19 it is codec v1, 19 and above codec v2.

- v1: `[count, R, G, B]` per segment
- v2: `[count, R, G, B, flag]` per segment

`count` is the number of dots the colour covers, 1..90 ("Color pixels is up to 90."); 0 marks an unused segment. `flag` is 1 in every segment the app writes, unused ones included, except for the editor's "no colour" swatch (black with a red X), which writes 0. What the controller does with flag 0 is not known *(inferred: no light)*.

The app shows 18 custom modes for v1 firmware and 21 for v2. Mode 0 is `STATIC`. A new pattern starts as mode 0, speed 128, brightness 255, segment 0 = `05 ff 00 00 01`.

### Library entry

The same layout is sent by 0x0e/0x0f and returned in the sync reply. Its length depends on the category:

| category | meaning | length (v2) | length (v1) |
|---|---|---|---|
| 1 | built-in effect ("Add to library" on the built-in screen) | 32 | 32 |
| 2 | custom pattern | 182 | 152 |
| 3 | music | 33 | 33 |

```
id(1) | name(25) | category(1) | mode(1) | speed(1) | brightness(1) | pixelLen(1) | reverse(1) | segments (category 2 only)
```

- **id**: chosen by the app as the smallest unused id from 1; 200 is skipped, because 200 means the combined effect.
- **name**: one byte per UTF-16 code unit, so effectively Latin-1, NUL-padded, cut to 25. Reading stops at the first 00 or ff byte.
- **pixelLen and reverse**: used by built-in entries; 0 for custom patterns.

The app allows 60 saved effects and refuses to delete the last custom pattern. It also refuses to delete one that a calendar schedule, a daily schedule or the combined effect uses ("This pattern is being used and could not be deleted.").

### Calendar schedule entry

10 bytes, the same in the sync reply and in 0x10/0x11:

```
id | effectId | startMonth | startDay | endMonth | endDay | onHour | onMinute | offHour | offMinute
```

- There is no year; entries repeat every year *(inferred)*.
- An end date before the start date wraps past New Year.
- `effectId` is a library id, or 200 for the combined effect. The app's picker offers only these, so built-in effects cannot be scheduled directly.
- The id is chosen like library ids: the smallest unused, from 1.
- **App rules**:
  - at most 60 entries;
  - a new entry may not share any date with another;
  - a one-day entry needs different on and off times.
- An off time before the on time is a window that crosses midnight *(inferred: e.g. 16:22 → 07:06 on the user's controller)*.

Example: `09 09 0c 14 01 05 10 1e 17 00` is entry 9, effect 9, Dec 20 → Jan 5, 16:30–23:00.

### Daily schedule entry

Two of them, always, 7 bytes each in the sync reply:

```
enabled | effectId | repeat | onHour | onMinute | offHour | offMinute
```

- `repeat` is one of:
  - 0 Today only
  - 1 Every day
  - 2 Weekdays (Mon–Fri)
  - 3 Weekend (Sat–Sun)
- `effectId` may be 200 (the combined effect).
- The command 0x13 adds the index first and the phone's month and day last. That date is how the controller knows which day "Today only" means *(inferred)*.
- The app rejects equal on and off times.
- The app also rejects two enabled schedules whose times overlap on a shared day ("The time of the two daily schedules cannot be crossed."). Weekdays and Weekend never share a day.

### Combined effect

Saved effects played in turn, each for the same interval:

```
n | n effect ids (play order) | interval (minutes, 1..10)
```

There is no command to play it directly. Schedules use it through effect id 200 *(inferred: what 0x04 `[200]` does is untested)*.

### Overlays (lightning and snow)

Each saved effect can have one overlay, drawn whenever it plays *(inferred)*. Overlays have no colour or speed settings. The app always sends the whole table:
- MCU firmware with `(version & 0xfff) >= 60` gets sub 0x1b with `[kind, effectId]` pairs (kind 0 lightning, 1 snow);
- older firmware gets sub 0x1a with the lightning effect ids only.

### Music

Sub 0x0c data, 23 bytes:

| offset | field |
|---|---|
| 0 | source: 0 phone player, 1 phone microphone, 2 the controller's AUX input |
| 1 | music effect 0..17 |
| 2..4 | effect colour R, G, B |
| 5 | effect length 1..150 |
| 6 | sensitivity 1..16 |
| 7..22 | 16 frequency bands |

The phone streams the bands with cmd 0x0a about 10 times a second. With the AUX source it sends one cmd 0x04 frame with zero bands and nothing more, so the controller presumably runs music effects on its own *(inferred, untested)*.

The music effects, by number: 0 Full Color Rhythm Spectrum, 1 Single Color Rhythm Spectrum, 2 Full Color Rhythm Twinkle, 3 Single Color Rhythm Twinkle, 4 Gradient Energy, 5 Single Color Energy, 6 Gradient Pulse, 7 Single Color Pulse, 8 Full Color Ejection Forward, 9 Single Color Ejection Forward, 10 Full Color Ejection Backward, 11 Single Color Ejection Backward, 12 Full Color VuMeter, 13 Single Color VuMeter, 14 Independence Day, 15 Christmas, 16 HeatBeat, 17 Party.

### Sync (sub 0x15)

Request data: `[year % 2000, month, day, weekday (Mon=1..Sun=7), hour, minute, second]`. In auto time-sync mode the controller sets its clock from it.

The reply data follows the 3-byte pass-through header. Several blocks have variable length, so the reply has to be read in order. Each block after the library is only present if the reply is long enough; older firmware may stop early.

| field | size |
|---|---|
| switch state (0 off, 1 manual, 2 timer) | 1 |
| IC type | 1 |
| channel order | 1 |
| 4 ports, each start and end as 16-bit big-endian, e.g. `00 01 00 c8` = 1–200 | 16 |
| MCU firmware version, big-endian | 2 |
| music sensitivity | 1 |
| number of saved effects (n) | 1 |
| n library entries | 32, 182 or 33 each |
| combined effect: count, ids, interval | count + 2 |
| number of calendar entries (c) | 1 |
| c calendar entries | 10 each |
| 2 daily schedules | 14 |
| current effect: `category, mode, speed, brightness, pixelLen, reverse` + 30 segments when category = 2 | 6 or 156 |
| music source, music colour R G B, music length | 5 |
| port enable mask, bit i = port i+1 | 1 |
| older lightning list: count, effect ids (ff = empty) | 1 + count |
| overlay list: count, `[kind, effectId]` pairs | 1 + 2 × count |
| time-sync mode: 0 auto, 1 manual | 1 |
| controller clock `yy mm dd hh mi ss` | 6 |
| unknown; 0 on a Sunday, 1 on a Monday, so probably the weekday *(inferred)* | 1 |

On the test controller the reply was 3,417 bytes. Its blocks:
- the combined effect was `01 0c 05` (effect 12, 5 minutes);
- there were 11 calendar entries;
- the tail was `02 ff 00 00 46 0f 00 00 00 1a 0b 08 13 32 2d 00`: AUX input, red, length 70, all ports on, no overlays, auto time, 2026-11-08 19:50:45.

The current effect is what manual mode shows:
- **Length:** 6 bytes, plus 150 for a custom pattern, except during music: then it is only `03 | music effect`, 2 bytes (seen on the controller).
- **Category 1** is built-in, `mode` 0..179.
- **A solid colour** preview (sub 0x0b) is recorded as category 1, mode 181. The colour itself is not stored; the other fields keep the previous effect's values.
- **Category 2** is a custom pattern.
- **Category 3** is a music effect.

The reply can arrive split across TCP reads, so keep reading until the declared length has arrived.

### Full-state write (cmd 0x08, sub 0x16)

The body is `16 | len(2) | state`, where `state` is the controller's whole configuration in the sync-reply layout. The app uses it to copy one controller to others on the LAN (group sync) and to restore a cloud backup.

Whether the controller accepts a sync reply sent back verbatim is untested.

## Verify handshake (cmd 0x01)

The app sends this right after connecting. It lets the app check the controller; the client side has nothing secret.

1. Pick random bytes `a, b, c` (0..255).
2. Encode `[a, b, c]` with Base64 using this alphabet (exactly 4 characters, no padding):
   ```
   0dY8ubicPgrReph1ENo9jvCn67y2tFQ5HWJIwOVZzDfkT3K4GSsABlqUXaMLxm+/
   ```
3. Wrap the ASCII in an S-frame:
   ```
   0x53 | frameCount | frameIndex | totalLen (2, big-endian) | frameLen (1) | chunk
   ```
   For 4 bytes this is `53 01 00 00 04 04` followed by the 4 characters.
4. The reply is also an S-frame. Its payload is custom-Base64 text that decodes to `[check, module version (2), n, version text (n), MCU version (2)]`. `check` should equal `(((a ^ c) << 2) ^ b) & 0xff`. The app waits 5 s. The real controller's reply decoded to `29 | 00 0c | 04 "1.11" | 10 42`, and the check matched.

## Connection flow

1. TCP connect to port 8586 (10 s timeout).
2. Verify.
3. Wait 400 ms.
4. Sync (sub 0x15) with the current time.

This is the only place the app syncs; it does not re-read the state after a change.

## Sending

- **No replies awaited.** The app writes each frame, then waits 100 ms. It waits for no reply to any command except verify, sync and heartbeat.
- **Coalescing.** When commands come faster than they can be written, for example while a slider is dragged, only the latest is sent.
- **Heartbeat check.** If more than 5 s have passed since the last activity and the module firmware version is 8 or above, the app first sends a heartbeat and waits up to 5 s for a cmd 0x0b reply. If none arrives, it reconnects.

## Discovery (UDP)

Request, broadcast to the subnet's broadcast address on port 8589 once per second:

```
53 50 54 45 43 48 06 00 00 00
```

Reply. This frame has no MD5 and no XOR.

```
"SPTECH" | 0x06 | len(3), must equal datagram length - 10 |
category(1) | reserve(1) |
L, deviceCode (ASCII) | L, name (UTF-8) | L, deviceHashKey (ASCII, optional)
```

The controller's IP address is the reply's source address.

## Other app behaviour

- **Sunrise and sunset** times in the app's schedule editor come from a cloud service, using the phone's location. They are written to the controller as fixed times. The controller has no location or timezone, only its local clock.
- **"Auto Mode"** is a Bluetooth-assisted Wi-Fi setup option, not a light mode.
- **Leaving the schedule screen** while not in timer mode, with schedules set up, makes the app offer to switch to timer mode.

## Built-in effect names

180 names, indexed by `mode`, are in `custom_components/trimlight_local/effects.py`.

## Behaviour on hardware

Observed on 2026-11-08:

- **Heartbeat reply is short-form.** The controller answers `53 50 54 45 43 48 0b 00 00 01 00`: header, cmd, length 1, body `00`, with no MD5 and no key. Discovery replies are short-form too.
- **No acknowledgement.** Pass-through commands that change things (0x02, 0x03, 0x04, 0x0b, 0x0d) get no reply. The next sync shows whether they took effect; all 12 sent in testing did.
- **Dropped requests.** A request sent within a few milliseconds of the previous reply is sometimes ignored: 1 of 2 back-to-back syncs was dropped, while 6 of 6 sent after a 0.2 s pause were answered. A dropped request never gets a late answer. Even with a 0.3 s pause, 1 sync in about 17 requests went unanswered and needed a retry.
- **Timing.** Sync replies (about 3.4 KB) take 0.55–1.0 s.
- **Previews persist.** What a preview shows becomes the current effect, and switching off and back to manual resumes it.
- **One client at a time.** When a second TCP client connects, the controller closes the first connection. When the first reconnects, the second is closed in turn. Both clients ran on the same computer.

Observed on 2026-11-09, with clients on two devices:

- **A client on another device waits.** While Home Assistant on one computer was connected and polling every 30 s, a second Home Assistant on another device could open TCP 8586, but its verify request got no answer, in two attempts about a minute apart. The first connection was not closed. Once the first client disconnected, a new client was answered straight away.
- *(inferred)* The controller lets a new connection from the same IP address replace the old one, and ignores other addresses while it is serving one.

Observed on 2026-11-09, writing to the controller's memory:

- **No manual mode needed.** Every write was accepted while the switch state was off, although the app itself only sends them in manual mode.
- **Silence after schedule writes.** After a calendar-schedule add or delete the controller answered no request for 11–16 s; after a daily-schedule change, for about 8 s. Saved-effect, overlay and combined-effect writes were readable within about 1 s.
- **Calendar entries are kept sorted by id**: a new entry 9 appeared between 8 and 10.
- **Music from the AUX input works.** In manual mode, sub 0x0c with source 2 and 16 zero bands switched the current effect to the music effect at once. Its current-effect record is then 2 bytes, as described under Sync.
- **Colour order takes effect at once.** Sub 0x07 changed RGB to GRB and back, visibly on the lights and in sync byte 2; nothing else in the sync reply changed. The first read-back took 12 s to answer, the second about 2 s.
- **Modify calendar schedule (0x11) is accepted.** Re-sending schedule 10 unchanged left the calendar identical, and the controller answered within 2 s, without the silence that follows an add or delete. Whether a changed entry takes effect is still unconfirmed.
- **Dropped commands.** On 2026-11-09, 2 of 6 overlay writes (sub 0x1b) were lost without a trace. The sync sent 0.3 s after each lost write went unanswered too, while the connection stayed usable. Sending the write again worked both times.
- **Rename takes effect at once.** Cmd 0x07 with `[n] + name` changed the name in the discovery reply within 5 s, and the TCP connection stayed open. A discovery request sent straight to the controller's address is answered like a broadcast one.
