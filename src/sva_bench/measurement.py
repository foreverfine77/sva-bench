"""Configure and acquire traces from a SIGLENT SVA-series analyzer."""

from dataclasses import dataclass, replace
import math

from ._driver import SaSetup, SvaError, connect, sa_axis


@dataclass(frozen=True)
class Sweep:
    axis_name: str
    axis: list[float]
    level_dbm: list[float]
    settings: dict


def open_device(device: str):
    """Open an explicit USB VISA resource, LAN hostname, or offline fake."""
    if not device.strip():
        raise ValueError("An explicit analyzer device is required")
    return connect(device)


def identify(device, *, simulated: bool = False) -> dict:
    return {"idn": device.idn(), "options": device.options(), "simulated": simulated}


def _checked_settings(device, setup: SaSetup) -> dict:
    actual = device.settings(include_controls=True)
    for key in ("center_hz", "span_hz", "rbw_hz", "vbw_hz", "attenuation_db", "preamp"):
        expected = float(getattr(setup, key))
        value = actual.get(key)
        if not isinstance(value, (int, float)) or not math.isclose(value, expected, abs_tol=1e-6):
            raise SvaError(f"{key}: expected {expected}, read back {value}")
    if (actual.get("instrument_mode") != "SA" or actual.get("power_unit") != "DBM"
            or actual.get("continuous_sweep") != 0 or actual.get("sweep_count") != 1):
        raise SvaError("Analyzer must report SA, dBm and single-sweep mode")
    return actual


def prepare(device, setup: SaSetup) -> dict:
    """Apply SA settings and return verified readback before the source is enabled."""
    setup.check()
    device.mode("SA")
    device.sa_setup(setup)
    return _checked_settings(device, setup)


def capture(device, setup: SaSetup, *, timeout_s: float = 30) -> Sweep:
    """Capture one prepared sweep and reject invalid readback or trace data."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("Sweep timeout must be finite and positive")
    device.sweep(1, timeout=timeout_s)
    levels = device.trace()
    actual = _checked_settings(device, setup)
    duration = actual.get("sweep_s")
    if (len(levels) < 2 or not all(math.isfinite(level) for level in levels)
            or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0):
        raise SvaError("Invalid trace or sweep time")
    scan = replace(setup, center_hz=actual["center_hz"], span_hz=actual["span_hz"], sweep_s=duration)
    axis_name = "time_s" if scan.span_hz == 0 else "frequency_hz"
    return Sweep(axis_name, sa_axis(scan, len(levels)), levels, actual)
