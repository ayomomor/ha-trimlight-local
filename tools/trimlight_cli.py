"""Command-line test tool for a Trimlight Edge controller on the local network.

Examples:
    python trimlight_cli.py discover
    python trimlight_cli.py --host 192.168.1.50 status
    python trimlight_cli.py --host 192.168.1.50 on
    python trimlight_cli.py --host 192.168.1.50 effect "Rainbow Wave" --brightness 180
    python trimlight_cli.py --host 192.168.1.50 color 255 120 0
    python trimlight_cli.py -v --host 192.168.1.50 raw 15
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "trimlight_local"


def _load(name: str):
    # Loaded by path: the component folder also holds select.py, which would shadow the stdlib module on sys.path.
    spec = importlib.util.spec_from_file_location(name, COMPONENT_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("trimlight")
_load("effects")

from effects import BUILTIN_EFFECTS  # noqa: E402
from trimlight import (  # noqa: E402
    CATEGORY_BUILTIN,
    CATEGORY_CUSTOM,
    CHANNEL_ORDERS,
    COMBINED_EFFECT_ID,
    IC_TYPES,
    TCP_PORT,
    OverlayKind,
    Repeat,
    SwitchState,
    TrimlightClient,
    TrimlightError,
    discover,
)


def effect_index(value: str) -> int:
    if value.isdigit():
        return int(value)
    matches = [i for i, name in enumerate(BUILTIN_EFFECTS) if name.lower() == value.lower()]
    if not matches:
        raise argparse.ArgumentTypeError(f"unknown effect {value!r}; run 'effects' to list them")
    return matches[0]


def print_state(client: TrimlightClient) -> None:
    state = client.state
    assert state is not None
    mode = SwitchState(state.switch_state).name.lower() if state.switch_state in SwitchState._value2member_map_ else state.switch_state
    print(f"verified:      {client.verified}")
    print(f"switch state:  {mode}")
    print(f"IC type:       {IC_TYPES.get(state.ic_type, state.ic_type)}")
    print(f"channel order: {CHANNEL_ORDERS.get(state.channel_order, state.channel_order)}")
    print("ports:         " + ", ".join(f"{start}-{end}" for start, end in state.ports))
    if client.firmware is not None:
        mcu = client.firmware.mcu_version
        print(f"firmware:      {client.firmware.version} (module {client.firmware.module_version}, MCU {mcu:#06x})" if mcu is not None else f"firmware:      {client.firmware.version}")
    print(f"clock:         {state.clock}")
    names = {e.id: e.name for e in state.library} | {COMBINED_EFFECT_ID: "Combined effect"}
    print(f"saved effects ({len(state.library)}):")
    for effect in state.library:
        kind = "pattern" if effect.category == CATEGORY_CUSTOM else "built-in" if effect.category == CATEGORY_BUILTIN else f"category {effect.category}"
        print(f"  {effect.id:3d}  {effect.name:25}  {kind:8} mode={effect.mode:3d} speed={effect.speed:3d} brightness={effect.brightness:3d}")
    if state.combined is not None:
        print(f"combined effect: {', '.join(names.get(i, str(i)) for i in state.combined.effect_ids)}, {state.combined.interval} min each")
    for overlay in state.overlays:
        print(f"overlay:       {OverlayKind(overlay.kind).name.lower() if overlay.kind in OverlayKind._value2member_map_ else overlay.kind} on {names.get(overlay.effect_id, overlay.effect_id)}")
    print(f"calendar schedules ({len(state.calendar)}):")
    for s in state.calendar:
        print(f"  {s.id:3d}  {s.start[0]:02d}-{s.start[1]:02d} to {s.end[0]:02d}-{s.end[1]:02d}  {s.on_time:%H:%M}-{s.off_time:%H:%M}  {names.get(s.effect_id, s.effect_id)}")
    for i, d in enumerate(state.daily, 1):
        repeat = Repeat(d.repeat).name.lower().replace("_", " ") if d.repeat in Repeat._value2member_map_ else d.repeat
        print(f"daily {i}:       {'on ' if d.enabled else 'off'}  {repeat:10}  {d.on_time:%H:%M}-{d.off_time:%H:%M}  {names.get(d.effect_id, d.effect_id)}")
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        print(f"raw sync data ({len(state.raw)} bytes):")
        for i in range(0, len(state.raw), 32):
            print(f"  {i:04x}  {state.raw[i:i + 32].hex(' ')}")


async def run(args: argparse.Namespace) -> int:
    if args.command == "discover":
        devices = await discover(timeout=args.timeout, broadcast=args.broadcast)
        for d in devices:
            print(f"{d.host}  name={d.name!r}  code={d.device_code}  category={d.category}  reserve={d.reserve}  hashKey={d.hash_key}")
        if not devices:
            print("No controllers answered. Discovery is a broadcast, so it only works on the controller's own subnet.")
        return 0 if devices else 1
    if args.command == "effects":
        for i, name in enumerate(BUILTIN_EFFECTS):
            print(f"{i:3d}  {name}")
        return 0
    if not args.host:
        print("--host is required for this command", file=sys.stderr)
        return 2

    client = TrimlightClient(args.host, args.port)
    try:
        await client.connect()
        if args.command == "status":
            print_state(client)
        elif args.command in ("on", "off", "timer"):
            await client.set_switch_state({"on": SwitchState.MANUAL, "off": SwitchState.OFF, "timer": SwitchState.TIMER}[args.command])
        elif args.command == "effect":
            await client.set_switch_state(SwitchState.MANUAL)
            await client.preview_builtin(args.mode, brightness=args.brightness, speed=args.speed, pixel_len=args.pixel_len, reverse=args.reverse)
            print(f"showing {args.mode}: {BUILTIN_EFFECTS[args.mode]}")
        elif args.command == "color":
            await client.set_switch_state(SwitchState.MANUAL)
            await client.preview_color(args.red, args.green, args.blue)
        elif args.command == "saved":
            await client.set_switch_state(SwitchState.MANUAL)
            await client.show_library_effect(args.effect_id)
        elif args.command == "raw":
            reply = await client.request_passthrough(int(args.sub, 16), bytes.fromhex(args.data), timeout=args.timeout)
            print(f"reply sub=0x{reply.sub:02x} md5_ok={reply.md5_ok} data={reply.data.hex(' ')}")
        await asyncio.sleep(0.3)
        return 0
    except TrimlightError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", help="controller IP address")
    parser.add_argument("--port", type=int, default=TCP_PORT)
    parser.add_argument("-v", "--verbose", action="store_true", help="log every frame sent and received")
    parser.add_argument("--timeout", type=float, default=5.0)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("discover", help="find controllers on this subnet")
    p.add_argument("--broadcast", default="255.255.255.255", help="broadcast address, e.g. 192.168.1.255")
    sub.add_parser("effects", help="list the 180 built-in effects")
    sub.add_parser("status", help="connect, verify, sync and print the state")
    sub.add_parser("on", help="switch to manual mode (lights on)")
    sub.add_parser("off", help="switch off")
    sub.add_parser("timer", help="switch to timer (schedule) mode")
    p = sub.add_parser("effect", help="show a built-in effect")
    p.add_argument("mode", type=effect_index, help="effect number 0-179 or exact name")
    p.add_argument("--brightness", type=int, default=255)
    p.add_argument("--speed", type=int, default=100)
    p.add_argument("--pixel-len", type=int, default=30)
    p.add_argument("--reverse", action="store_true")
    p = sub.add_parser("color", help="show a solid colour")
    for c in ("red", "green", "blue"):
        p.add_argument(c, type=int)
    p = sub.add_parser("saved", help="show an effect saved in the controller's library")
    p.add_argument("effect_id", type=int)
    p = sub.add_parser("raw", help="send a pass-through sub-command and print the reply")
    p.add_argument("sub", help="sub-command in hex, e.g. 15")
    p.add_argument("data", nargs="?", default="", help="data bytes in hex")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
