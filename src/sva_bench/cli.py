"""Command-line access to identity and independent SA captures."""

import argparse
import json
from pathlib import Path
from . import SaSetup, SvaError, capture, identify, open_device, prepare
from .archive import save_sweep


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    identity_command = commands.add_parser("identify", help="read instrument identity and options")
    inspect = commands.add_parser("inspect", help="read current SA settings")
    sweep = commands.add_parser("sweep", help="apply an SA recipe and capture one trace")
    for command in (identity_command, inspect, sweep):
        command.add_argument("--device", required=True, help="usb, fake, LAN hostname or VISA resource")
    sweep.add_argument("--recipe", type=Path, required=True)
    sweep.add_argument("--out", type=Path, required=True, help="new output directory")
    sweep.add_argument("--screenshot", action="store_true")
    sweep.add_argument("--timeout-s", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        if args.command == "sweep":
            setup = SaSetup(**json.loads(args.recipe.read_text(encoding="utf-8")))
            setup.check()
            args.out.mkdir(parents=True, exist_ok=False)
        with open_device(args.device) as device:
            identity = identify(device, simulated=args.device == "fake")
            if args.command == "identify":
                print(json.dumps(identity, indent=2))
                return 0
            if args.command == "inspect":
                print(json.dumps({"identity": identity, "settings": device.settings(include_controls=True)}, indent=2))
                return 0
            configured = prepare(device, setup)
            trace = capture(device, setup, timeout_s=args.timeout_s)
            save_sweep(args.out, trace, identity, setup, configured)
            if args.screenshot:
                (args.out / "screen.bmp").write_bytes(device.screenshot())
        label = "SIMULATED" if identity["simulated"] else "CAPTURED"
        print(f"{label} {len(trace.axis)} points | {trace.axis_name} | {trace.axis[0]:g} to {trace.axis[-1]:g}")
        for name in ("spectrum.csv", "settings.json", "screen.bmp"):
            path = args.out / name
            if path.is_file():
                print(f"  {path.as_posix()}")
        return 0
    except (OSError, TypeError, ValueError, SvaError) as exc:
        parser.exit(1, f"SVA capture failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
