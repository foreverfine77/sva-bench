"""Siglent SVA1075X over SCPI sockets (ADR-0043).

Commands follow the Siglent programming guide PG0703P_E02B (SSA3000X Plus / SSA3000X-R / SVA1000X): LAN socket
port 5025, one command per line, queries end in "?". Only what ADR-0043 needs is wrapped: identity and options,
spectrum-analyzer setup (center / span, 0 Hz span = zero span, RBW / VBW, sweep time, detector, trace mode,
average, reference level, attenuation, preamp, video trigger), one synchronised single sweep, trace data as
ASCII, marker peak and frequency counter, and the VNA S21 sweep.

The analyzer never drives the board: this module only talks to the instrument. ``FakeSva`` answers the same
commands offline for the tests and for a dry run (``--sva fake``).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import os
import socket
import struct
import time

DEFAULT_HOST = os.environ.get("SVA_BENCH_HOST", "localhost")
PORT = 5025
RBW_HZ = (1, 3, 10, 30, 100, 300, 1_000, 3_000, 10_000, 30_000, 100_000, 300_000, 1_000_000)
VBW_HZ = RBW_HZ                       # the guide lists the same 1 Hz .. 1 MHz set for VBW
DETECTORS = ("POSitive", "NEGative", "SAMPle", "AVERage", "NORMAL")
TRACE_MODES = ("WRITe", "MAXHold", "MINHold", "VIEW", "BLANk", "AVERage")
MIN_SWEEP_S = 450e-6                  # [:SENSe]:SWEep:TIME range 450 us .. 1500 s
TRACE_POINTS = 751


class SvaError(RuntimeError):
    pass


@dataclass(frozen=True)
class SaSetup:
    """One spectrum-analyzer measurement. ``span_hz == 0`` is zero span (X axis is time over ``sweep_s``)."""
    center_hz: float
    span_hz: float
    rbw_hz: int
    vbw_hz: int
    ref_level_dbm: float = -20.0
    attenuation_db: int = 10
    preamp: bool = False
    detector: str = "POSitive"
    trace_mode: str = "WRITe"
    average: int = 0                  # > 0: trace mode AVERage with this count
    sweep_s: float | None = None      # None: auto
    trigger: str = "IMMediate"        # IMMediate | VIDeo
    video_level_dbm: float = -60.0

    def check(self) -> None:
        if self.rbw_hz not in RBW_HZ or self.vbw_hz not in VBW_HZ:
            raise ValueError(f"RBW / VBW must be one of {RBW_HZ} Hz")
        if self.span_hz != 0 and self.span_hz < 100:
            raise ValueError("span must be 0 (zero span) or >= 100 Hz")
        if self.detector not in DETECTORS or self.trace_mode not in TRACE_MODES:
            raise ValueError("unknown detector or trace mode")
        if self.sweep_s is not None and not MIN_SWEEP_S <= self.sweep_s <= 1500:
            raise ValueError("sweep time must be 450 us .. 1500 s")
        if self.span_hz == 0 and self.sweep_s is None:
            raise ValueError("zero span needs an explicit sweep time")
        if self.trigger not in ("IMMediate", "VIDeo"):
            raise ValueError("trigger must be IMMediate or VIDeo")
        if not 0 <= self.attenuation_db <= 50:
            raise ValueError("input attenuation must be 0 .. 50 dB")

    def as_json(self) -> dict:
        return asdict(self)


class Sva:
    """A SCPI socket to the analyzer. Every write is followed by an error-queue-free check only where it matters:
    a single sweep is closed with *OPC? so the trace read afterwards belongs to that sweep."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = PORT, timeout: float = 10.0):
        self.host, self.port = host, port
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(0.3)
        try:                          # some firmware greets on connect (the guide's example reads once)
            self.sock.recv(4096)
        except OSError:
            pass
        self.sock.settimeout(timeout)
        self._buf = b""

    def close(self) -> None:
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def write(self, cmd: str) -> None:
        self.sock.sendall(cmd.encode("ascii") + b"\n")

    def _line(self, timeout: float | None = None) -> str:
        if timeout is not None:
            self.sock.settimeout(timeout)
        while b"\n" not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise SvaError("analyzer closed the connection")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("ascii", "replace").strip()

    def query(self, cmd: str, timeout: float | None = None) -> str:
        self.write(cmd)
        return self._line(timeout)

    # --- common -------------------------------------------------------------------------------------------
    def idn(self) -> str:
        return self.query("*IDN?")

    def options(self) -> str:
        return self.query(":SYSTem:OPTions?")

    def mode(self, name: str) -> None:
        if name not in ("SA", "MA", "DTF", "VNA"):
            raise ValueError("mode is SA, MA, DTF or VNA")
        self.write(f":INSTrument:SELect {name}")
        self.query("*OPC?", timeout=30)

    # --- spectrum analyzer --------------------------------------------------------------------------------
    def sa_setup(self, s: SaSetup) -> None:
        s.check()
        # Forced SWE can reject narrow RBW on SVA1075X firmware. Let AUTO
        # select the frequency-sweep engine; zero span keeps timed SWE.
        sweep_mode = "SWEep" if s.span_hz == 0 else "AUTO"
        cmds = [":INITiate:CONTinuous OFF", ":SWEep:COUNt 1",
                ":UNIT:POWer DBM", f":SWEep:MODE {sweep_mode}", ":FILTer:TYPE GAUSS", ":AVERage:TYPE POWer",
                ":FREQuency:OFFSet 0 Hz", ":DISPlay:WINDow:TRACe:Y:SCALe:RLEVel:OFFSet 0",
                ":CORRection:OFF",
                f":FREQuency:CENTer {s.center_hz:.0f} Hz",
                f":FREQuency:SPAN {s.span_hz:.0f} Hz",
                ":BWIDth:AUTO OFF", f":BWIDth {s.rbw_hz} Hz",
                ":BWIDth:VIDeo:AUTO OFF", f":BWIDth:VIDeo {s.vbw_hz} Hz",
                f":DISPlay:WINDow:TRACe:Y:RLEVel {s.ref_level_dbm:g} DBM",
                ":POWer:ATTenuation:AUTO OFF", f":POWer:ATTenuation {s.attenuation_db}",
                f":POWer:GAIN {'ON' if s.preamp else 'OFF'}",
                f":DETector:TRACe1 {s.detector}",
                f":TRIGger:SOURce {s.trigger}"]
        if s.trigger == "VIDeo":
            cmds.append(f":TRIGger:VIDeo:LEVel {s.video_level_dbm:g} dBm")
        if s.sweep_s is None:
            cmds.append(":SWEep:TIME:AUTO ON")
        else:
            cmds += [":SWEep:TIME:AUTO OFF", f":SWEep:TIME {s.sweep_s * 1e6:.0f} us"]
        if s.average > 0:
            cmds += [":TRACe1:MODE AVERage", f":AVERage:TRACe1:COUNt {s.average}", ":AVERage:TRACe1:CLEar"]
        else:
            cmds.append(f":TRACe1:MODE {s.trace_mode}")
        for c in cmds:
            self.write(c)
        self.query("*OPC?", timeout=30)

    def sweep(self, sweeps: int = 1, timeout: float = 120.0) -> None:
        """``sweeps`` single sweeps (one per average count), each closed with *OPC?."""
        for _ in range(max(1, sweeps)):
            self.write(":INITiate:IMMediate")
            if self.query("*OPC?", timeout=timeout) != "1":
                raise SvaError("sweep did not complete")

    def trace(self, n: int = 1) -> list[float]:
        self.write(":FORMat ASCii")
        text = self.query(f":TRACe:DATA? {n}", timeout=30)
        values = [float(v) for v in text.replace(";", ",").split(",") if v.strip()]
        if not values:
            raise SvaError("empty trace")
        return values

    def peak(self) -> tuple[float, float]:
        self.write(":CALCulate:MARKer1:STATe ON")
        self.write(":CALCulate:MARKer1:MAXimum")
        return float(self.query(":CALCulate:MARKer1:X?")), float(self.query(":CALCulate:MARKer1:Y?"))

    def screenshot(self) -> bytes:
        """Read the native BMP without initiating a sweep; preserve binary LF bytes over USB."""
        if hasattr(self, "inst"):
            previous = self.inst.read_termination
            try:
                self.inst.read_termination = None
                self.write(":HCOPy:SDUMp:DATA?")
                raw = self.inst.read_raw()
            finally:
                self.inst.read_termination = previous
        else:
            self.write(":HCOPy:SDUMp:DATA?")
            raw = b""
            while len(raw) < 14:
                part = self.sock.recv(65536)
                if not part:
                    raise SvaError("screen connection closed before BMP header")
                raw += part
            if raw[:2] != b"BM":
                raise SvaError("screen response is not a BMP")
            size = int.from_bytes(raw[2:6], "little")
            if not 54 <= size <= 20_000_000:
                raise SvaError("invalid BMP screen length")
            while len(raw) < size:
                part = self.sock.recv(min(65536, size - len(raw)))
                if not part:
                    raise SvaError("screen connection closed before complete BMP")
                raw += part
        if raw.startswith(b"#") and len(raw) >= 2:
            digits = int(chr(raw[1]))
            raw = raw[2:] if not digits else raw[2+digits:2+digits+int(raw[2:2+digits])]
        if len(raw) < 54 or raw[:2] != b"BM":
            raise SvaError("screen response is not a BMP")
        size = int.from_bytes(raw[2:6], "little")
        if not 54 <= size <= 20_000_000 or len(raw) < size:
            raise SvaError("incomplete BMP screen")
        return raw[:size]

    def count(self) -> float:
        """Frequency counter on marker 1 (put it on the peak first)."""
        self.write(":CALCulate:MARKer:FCOunt ON")
        self.sweep()
        value = float(self.query(":CALCulate:MARKer:FCOunt:X?"))
        self.write(":CALCulate:MARKer:FCOunt OFF")
        return value

    def settings(self, *, include_controls: bool = False) -> dict:
        """What the analyzer reports back, saved beside every trace."""
        q = {"center_hz": ":FREQuency:CENTer?", "span_hz": ":FREQuency:SPAN?", "rbw_hz": ":BWIDth?",
             "vbw_hz": ":BWIDth:VIDeo?", "sweep_s": ":SWEep:TIME?", "attenuation_db": ":POWer:ATTenuation?",
             "ref_level_dbm": ":DISPlay:WINDow:TRACe:Y:RLEVel?", "preamp": ":POWer:GAIN?",
             "frequency_offset_hz": ":FREQuency:OFFSet?",
             "reference_offset_db": ":DISPlay:WINDow:TRACe:Y:SCALe:RLEVel:OFFSet?",
             "corrections": ":CORRection:CSET:ALL:STATe?", "average_count": ":AVERage:TRACe1:COUNt?"}
        out = {}
        for k, cmd in q.items():
            try:
                out[k] = float(self.query(cmd))
            except (ValueError, OSError) as exc:
                out[k] = f"unread: {exc}"
        for key, cmd in {"filter_type": ":FILTer:TYPE?", "average_type": ":AVERage:TYPE?",
                         "power_unit": ":UNIT:POWer?", "sweep_mode": ":SWEep:MODE?",
                         "detector": ":DETector:TRACe1?", "trace_mode": ":TRACe1:MODE?"}.items():
            out[key] = self.query(cmd).strip().upper()
        if include_controls:
            numeric={"continuous_sweep":":INITiate:CONTinuous?", "sweep_count":":SWEep:COUNt?",
                     "rbw_auto":":BWIDth:AUTO?", "vbw_auto":":BWIDth:VIDeo:AUTO?",
                     "sweep_time_auto":":SWEep:TIME:AUTO?", "attenuation_auto":":POWer:ATTenuation:AUTO?",
                     "scale_db_per_div":":DISPlay:WINDow:TRACe:Y:PDIVision?"}
            for key,cmd in numeric.items():
                raw=self.query(cmd,timeout=5).strip().upper()
                try: out[key]=float({"ON":"1","OFF":"0"}.get(raw,raw))
                except ValueError: out[key]=f"unread: {raw}"
            for key,cmd in {"trigger_source":":TRIGger:SOURce?", "instrument_mode":":INSTrument:SELect?",
                            "sweep_rule":":SWEep:SPEed?", "scale_type":":DISPlay:WINDow:TRACe:Y:SPACing?"}.items():
                out[key]=self.query(cmd,timeout=5).strip().upper()
        return out

    # --- vector network analyzer --------------------------------------------------------------------------
    def vna_s21(self, start_hz: float, stop_hz: float, points: int = 201, average: int = 16,
                *, confirm_average=None) -> list[float]:
        """Raw (uncorrected) |S21| in dB; normalise against a thru sweep in software."""
        if not 101 <= points <= 751:
            raise ValueError("VNA points 101 .. 751")
        if not 1 <= average <= 999:
            raise ValueError("VNA average count 1 .. 999")
        if average > 1 and confirm_average is None:
            raise SvaError("VNA averaging needs confirmation of the displayed completed count")
        self.last_vna_average = {"requested_count": average, "completion": "unverified"}
        self.mode("VNA")
        for c in (":INITiate:CONTinuous OFF", f":FREQuency:STARt {start_hz:.0f} Hz",
                  f":FREQuency:STOP {stop_hz:.0f} Hz", f":SWEep:POINts {points}",
                  ":CALCulate:PARameter1:DEFine S21", ":CALCulate:PARameter1:SELect",
                  ":CALCulate:FORMat MLOGarithmic", f":AVERage:COUNt {average}", ":AVERage:STATe ON"):
            self.write(c)
        self.query("*OPC?", timeout=30)
        if average > 1:
            # INIT:IMM restarts the measurement; repeated calls do not accumulate averages.
            try:
                self.write(":INITiate:CONTinuous ON")
                if confirm_average(average) is not True:
                    raise SvaError("VNA average completion was not confirmed; export refused")
                self.last_vna_average["completion"] = "operator_confirmed_display"
            finally:
                self.write(":INITiate:CONTinuous OFF")
                self.query("*OPC?", timeout=30)
        else:
            self.sweep(1, timeout=300)
            self.last_vna_average["completion"] = "single_sweep_opc"
        self.write(":FORMat ASCii")
        self.last_vna_raw = self.query(":CALCulate:DATA:FDATa?", timeout=30)
        values = [float(v) for v in self.last_vna_raw.split(",") if v.strip()]
        if len(values) != 2 * points or not all(math.isfinite(v) for v in values):
            raise SvaError("VNA FDATa must contain two finite values per measurement point")
        first, second = values[0::2], values[1::2]
        expected = [start_hz + (stop_hz - start_hz) * i / (points - 1) for i in range(points)]
        if all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1) for a, b in zip(first, expected)):
            self.last_vna_layout = "frequency_hz,level_db"
            levels = second
        elif all(abs(v) <= 1e-12 for v in second):
            self.last_vna_layout = "formatted_level_db,zero"
            levels = first
        else:
            raise SvaError("unrecognised VNA FDATa layout; raw response must be inspected")
        if not all(-400 <= v <= 200 for v in levels):
            raise SvaError("VNA log magnitude outside plausible dB range; refusing frequency values as dB")
        return levels


def sa_axis(s: SaSetup, n: int = TRACE_POINTS) -> list[float]:
    """Frequency (Hz) per point, or time (s) per point in zero span."""
    if s.span_hz == 0:
        return [s.sweep_s * i / (n - 1) for i in range(n)]
    lo = s.center_hz - s.span_hz / 2
    return [lo + s.span_hz * i / (n - 1) for i in range(n)]


class FakeSva:
    """Offline stand-in: records every command and answers queries with plausible values."""

    def __init__(self, trace_fn=None):
        self.log: list[str] = []
        self.trace_fn = trace_fn or (lambda: [-90.0] * TRACE_POINTS)
        self.setup: SaSetup | None = None
        self.vna_values = {}
        self.control_values={":INITiate:CONTinuous?":"1",":SWEep:COUNt?":"1",":BWIDth:AUTO?":"1",
            ":BWIDth:VIDeo:AUTO?":"1",":SWEep:TIME:AUTO?":"1",":POWer:ATTenuation:AUTO?":"1",
            ":TRIGger:SOURce?":"IMMEDIATE",":INSTrument:SELect?":"SA",":SWEep:SPEed?":"NORMAL",
            ":DISPlay:WINDow:TRACe:Y:PDIVision?":"10",":DISPlay:WINDow:TRACe:Y:SPACing?":"LOG"}

    def sa_setup(self, setup: SaSetup) -> None:
        self.setup = setup
        Sva.sa_setup(self, setup)
        self.state = {"center": 1.5e9, "span": 1e6}

    def close(self) -> None:
        pass

    def screenshot(self) -> bytes:
        self.log.append(":HCOPy:SDUMp:DATA?")
        return (b"BM" + struct.pack("<IHHI", 58, 0, 0, 54)
                + struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 2835, 2835, 0, 0)
                + bytes(4))

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass

    def write(self, cmd: str) -> None:
        self.log.append(cmd)
        for query in self.control_values:
            prefix=query[:-1]
            if cmd.startswith(prefix+' '):
                value=cmd[len(prefix)+1:].strip().upper()
                self.control_values[query]={"ON":"1","OFF":"0"}.get(value,value)
        for prefix in (":FREQuency:STARt", ":FREQuency:STOP", ":SWEep:POINts", ":CALCulate:FORMat"):
            if cmd.startswith(prefix + " "):
                self.vna_values[prefix + "?"] = cmd.split()[1]

    def query(self, cmd: str, timeout: float | None = None) -> str:
        self.log.append(cmd)
        if cmd in self.control_values:
            return self.control_values[cmd]
        if cmd == "*IDN?":
            return "Siglent Technologies,SVA1075X,FAKE0000000000,offline"
        if cmd == ":SYSTem:OPTions?":
            return "FAKE"
        if cmd == "*OPC?":
            return "1"
        if cmd.startswith(":TRACe:DATA?"):
            return ",".join(f"{v:.3f}" for v in self.trace_fn())
        if cmd == ":CALCulate:DATA:FDATa?":
            values = self.trace_fn()
            points = int(self.vna_values.get(":SWEep:POINts?", len(values)))
            return ",".join(f"{values[round(i * (len(values) - 1) / (points - 1))]:.3f},0"
                            for i in range(points))
        if cmd in self.vna_values:
            return self.vna_values[cmd]
        if self.setup is not None:
            s = self.setup
            values = {":FREQuency:CENTer?": s.center_hz, ":FREQuency:SPAN?": s.span_hz,
                      ":BWIDth?": s.rbw_hz, ":BWIDth:VIDeo?": s.vbw_hz,
                      ":SWEep:TIME?": s.sweep_s or 0.1, ":POWer:ATTenuation?": s.attenuation_db,
                      ":DISPlay:WINDow:TRACe:Y:RLEVel?": s.ref_level_dbm,
                      ":POWer:GAIN?": int(s.preamp), ":FILTer:TYPE?": "GAUSS",
                      ":AVERage:TYPE?": "POW", ":UNIT:POWer?": "DBM", ":SWEep:MODE?": "SWE",
                      ":DETector:TRACe1?": s.detector.upper(),
                      ":AVERage:TRACe1:COUNt?": s.average,
                      ":TRACe1:MODE?": "AVER" if s.average else s.trace_mode.upper(),
                      ":CALCulate:MARKer1:X?": s.center_hz,
                      ":CALCulate:MARKer1:Y?": max(self.trace_fn()),
                      ":CALCulate:MARKer:FCOunt:X?": s.center_hz}
            if cmd in values:
                return str(values[cmd])
        if cmd.endswith("?"):
            return "0"
        return ""

    def vna_s21(self, *args, confirm_average=None, **kwargs):
        return Sva.vna_s21(self, *args, confirm_average=confirm_average or (lambda _: True), **kwargs)

    idn, options, mode, sweep, trace, peak, count, settings = (
        Sva.idn, Sva.options, Sva.mode, Sva.sweep, Sva.trace, Sva.peak, Sva.count, Sva.settings,
    )


SIGLENT_USB_VID = 0xF4EC


def usb_resource(resources, wanted: str = "usb") -> str:
    """Pick the VISA resource: ``usb`` = the only Siglent USBTMC device, or a full ``USB0::...::INSTR`` string."""
    if wanted.upper().startswith("USB") and "::" in wanted:
        return wanted
    found = [r for r in resources if r.upper().startswith("USB")
             and any(f"::{v}::" in r.upper() for v in (f"0X{SIGLENT_USB_VID:04X}", str(SIGLENT_USB_VID)))]
    if len(found) != 1:
        raise SvaError(f"expected one Siglent USB device, VISA lists {list(resources)}")
    return found[0]


class SvaUsb(Sva):
    """The same SCPI over USBTMC (the analyzer's rear USB Device port) through pyvisa and the installed VISA, so
    the PC's only Ethernet port stays on the board."""

    def __init__(self, wanted: str = "usb", timeout: float = 10.0):
        try:
            import pyvisa
        except ImportError as exc:
            raise SvaError("USB needs pyvisa: python -m pip install pyvisa") from exc
        self.rm = pyvisa.ResourceManager()
        self.host, self.port = usb_resource(self.rm.list_resources(), wanted), 0
        self.inst = self.rm.open_resource(self.host)
        self.inst.read_termination, self.inst.write_termination = "\n", "\n"
        self.timeout = timeout
        self.inst.timeout = int(timeout * 1000)

    def close(self) -> None:
        self.inst.close()
        self.rm.close()

    def write(self, cmd: str) -> None:
        self.inst.write(cmd)

    def _line(self, timeout: float | None = None) -> str:
        self.inst.timeout = int((timeout or self.timeout) * 1000)
        try:
            return self.inst.read().strip()
        except Exception as exc:                 # pyvisa.VisaIOError; keep the import local
            raise SvaError(f"USB read failed: {exc}") from exc


def connect(host: str = DEFAULT_HOST, port: int = PORT):
    """``host == "fake"`` gives the offline stand-in; ``usb`` (or a ``USB0::...`` resource) uses USBTMC."""
    if host == "fake":
        return FakeSva()
    if host.lower() == "usb" or host.upper().startswith("USB0::"):
        return SvaUsb(host)
    return Sva(host, port)


def gaussian_width(t: list[float], y_db: list[float], level_db: float = 3.0) -> float:
    """-``level_db`` width of the single burst around the maximum of a zero-span trace (linear interpolation)."""
    i = max(range(len(y_db)), key=y_db.__getitem__)
    top = y_db[i] - level_db

    def edge(step: int) -> float:
        j = i
        while 0 <= j + step < len(y_db) and y_db[j + step] > top:
            j += step
        k = j + step
        if not 0 <= k < len(y_db):
            return math.nan
        f = (y_db[j] - top) / (y_db[j] - y_db[k])
        return t[j] + f * (t[k] - t[j])

    return edge(1) - edge(-1)
