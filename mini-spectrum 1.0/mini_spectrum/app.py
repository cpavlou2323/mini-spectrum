"""
Mini spectrum: a tiny always-on-top spectrum analyser for whatever your PC is playing.

It listens to your speaker output through Windows' WASAPI loopback, so it reacts to
Spotify, YouTube, games, anything. Nothing is recorded or saved.

Styles:
    Thick bars, Thin bars   pixel spectrum analyser with falling peak caps
    Waveform                the sound wave, drawn left to right
    Oscilloscope            X-Y mode like an analogue scope: left channel moves the
                            beam sideways, right channel moves it up and down.
                            Try "oscilloscope music" on YouTube and it draws pictures.

Controls:
    drag          move it anywhere
    click         switch style
    mouse wheel   make it bigger or smaller
    right-click   menu: style, size, fall speed, peak caps, always on top,
                  reconnect audio, quit

Settings and window position are saved in ~/.mini_spectrum.json.

By Christos P. MIT licence.
"""
__version__ = "1.0.0"
__author__ = "Christos P"

import ctypes
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

try:
    import numpy as np
except ImportError:
    np = None

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None

# ---------------------------------------------------------------- look & feel
W, H = 75, 17              # pixel display. Odd sizes keep the dot grid symmetrical,
                           # and 19 thick bars (3 px + 1 gap) or 38 thin bars (1 + 1)
                           # fill all 75 columns edge to edge.
XY_W, XY_H = 75, 60        # oscilloscope screen: 10 x 8 square divisions, drawn smooth
STYLES = {"thick": "Thick bars", "thin": "Thin bars", "wave": "Waveform", "xy": "Oscilloscope"}
SCALES = (2, 3, 4, 5, 6, 8)          # screen pixels per display pixel
FALL = {"slow": 1.1, "medium": 2.2, "fast": 4.0}      # bar drop, full heights per second
GRAVITY = {"slow": 1.0, "medium": 2.2, "fast": 4.5}   # peak cap acceleration
PEAK_HOLD = 0.35                     # seconds a peak cap waits before falling

BG = (0, 0, 0)
DOT = (36, 42, 38)                   # faint dots marking where the lights sit
PEAK = (205, 208, 205)
FRAME = "#3b3e3c"                    # border around the display
FRAME_RGB = (59, 62, 60)
STOPS = [                            # bar colour from bottom (0) to top (1)
    (0.00, (22, 132, 26)),
    (0.35, (46, 198, 40)),
    (0.60, (186, 222, 42)),
    (0.78, (244, 192, 32)),
    (0.90, (244, 122, 30)),
    (1.00, (232, 44, 30)),
]

# oscilloscope screen. "hot" is added where the beam is brightest so the core burns white
SCOPE_COLOURS = {
    "cyan":   {"name": "Cyan",   "screen": (4, 16, 19),  "grat": (18, 48, 52), "beam": (40, 225, 255), "hot": (190, 40, 0)},
    "green":  {"name": "Green",  "screen": (4, 17, 8),   "grat": (20, 54, 26), "beam": (70, 255, 120), "hot": (170, 0, 120)},
    "amber":  {"name": "Amber",  "screen": (19, 12, 4),  "grat": (58, 40, 16), "beam": (255, 172, 40), "hot": (0, 70, 190)},
    "ice":    {"name": "Ice",    "screen": (6, 11, 22),  "grat": (24, 40, 66), "beam": (120, 185, 255), "hot": (120, 65, 0)},
    "violet": {"name": "Violet", "screen": (13, 6, 20),  "grat": (44, 26, 62), "beam": (215, 95, 255), "hot": (30, 150, 0)},
}
PERSIST = 0.04                       # afterglow time constant, seconds
BEAM = 28000.0                       # beam energy per second of audio (per display unit)
SPOT = 1500.0                        # energy per second of the resting spot when it's quiet
TRANSPARENT = (255, 0, 254)          # this exact colour is punched out of the window
TRANSPARENT_HEX = "#ff00fe"

# ---------------------------------------------------------------- analysis
FFT_N = 2048
F_MIN, F_MAX = 50.0, 16000.0
DB_FLOOR, DB_CEIL = -64.0, -10.0     # band level (dBFS) that maps to an empty / full bar
TILT_DB = 16.0                       # lift the treble so the right side isn't always flat

# fixed gains run in a 1-2-5 ladder like a scope's volts per division knob. Music and
# Windows' own volume mean the signal is usually well below full scale, so the useful
# settings are the big ones; 1x means an amplitude of 1.0 fills the screen.
SCOPE_GAINS = {"auto": "Auto", "1": "1×", "2": "2×", "5": "5×", "10": "10×", "20": "20×", "50": "50×"}

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".mini_spectrum.json")
DEFAULTS = {"style": "thick", "scale": 3, "fall": "medium", "peaks": True,
            "scope_colour": "cyan", "scope_gain": "auto", "ignore_volume": True, "topmost": True, "x": None, "y": None}


def colour_ramp(n):
    """n colours from bottom to top of the display."""
    out = np.zeros((n, 3), np.uint8)
    for i in range(n):
        t = i / (n - 1)
        for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
            if t <= t1:
                f = (t - t0) / (t1 - t0)
                out[i] = [round(a + (b - a) * f) for a, b in zip(c0, c1)]
                break
    return out


def box_blur(a, r):
    """Fast box blur (radius r) along both axes using running sums."""
    if r < 1:
        return a
    k = 2 * r + 1
    p = np.pad(a, ((0, 0), (r + 1, r)))
    c = np.cumsum(p, axis=1, dtype=np.float32)
    a = (c[:, k:] - c[:, :-k]) / k
    p = np.pad(a, ((r + 1, r), (0, 0)))
    c = np.cumsum(p, axis=0, dtype=np.float32)
    return (c[k:, :] - c[:-k, :]) / k


def bezel_for(scale):
    """Border thickness around the oscilloscope screen, the same on every side."""
    return max(3, scale + 1)


def rounded_sdf(h, w, r):
    """Distance from the edge of a rounded rectangle filling the image.
    Negative inside, 0 on the edge, positive outside."""
    x = np.arange(w, dtype=np.float32) + 0.5
    y = np.arange(h, dtype=np.float32) + 0.5
    qx = np.maximum(0.0, np.maximum(r - x, x - (w - r)))
    qy = np.maximum(0.0, np.maximum(r - y, y - (h - r)))
    return np.hypot(qy[:, None], qx[None, :]) - r


class PixelDisplay:
    """Thick bars, thin bars and waveform on the 75 x 17 pixel grid."""

    def __init__(self):
        up = colour_ramp(H)                                  # index 0 = bottom row
        self.bar_colours = np.ascontiguousarray(np.broadcast_to(up[::-1][:, None, :], (H, W, 3)))
        mid = (H - 1) // 2                                   # exact centre row
        dist = np.abs(np.arange(H) - mid) / (H / 2)
        scope_rows = up[np.minimum(H - 1, ((0.2 + 0.8 * dist) * (H - 1)).astype(int))]
        self.scope_colours = np.ascontiguousarray(np.broadcast_to(scope_rows[:, None, :], (H, W, 3)))

        def dots(columns):
            bg = np.zeros((H, W, 3), np.uint8)
            bg[:] = BG
            for y in range(0, H, 2):                         # rows 0, 2 ... 16: symmetrical
                bg[y, columns] = DOT
            return bg
        # the dots sit in the middle of every bar, never in the gaps between them
        self.bg = {"thick": dots(np.arange(1, W, 4)),
                   "thin": dots(np.arange(0, W, 2)),
                   "wave": dots(np.arange(0, W, 2))}
        self.rows = np.arange(H)[:, None]
        self.window = np.hanning(FFT_N).astype(np.float32)
        self._band_key = None
        self.reset()

    def reset(self):
        self.bars = np.zeros(38)
        self.peaks = np.zeros(38)
        self.hold = np.zeros(38)
        self.vel = np.zeros(38)

    def _bands(self, n, rate):
        key = (n, rate)
        if key != self._band_key:
            hz = rate / FFT_N
            top = min(F_MAX, rate / 2 * 0.95)
            edges = F_MIN * (top / F_MIN) ** (np.arange(n + 1) / n)
            lo = np.floor(edges[:-1] / hz).astype(int)
            hi = np.maximum(lo + 1, np.ceil(edges[1:] / hz).astype(int))
            self._bins = list(zip(lo, hi))
            self._tilt = TILT_DB * np.arange(n) / max(1, n - 1)
            self._band_key = key
        return self._bins, self._tilt

    def levels(self, mono, n, rate):
        mag = np.abs(np.fft.rfft(mono[-FFT_N:] * self.window)) / (FFT_N / 4)
        bins, tilt = self._bands(n, rate)
        band = np.array([mag[a:b].max() for a, b in bins])
        db = 20 * np.log10(band + 1e-9) + tilt
        return np.clip((db - DB_FLOOR) / (DB_CEIL - DB_FLOOR), 0.0, 1.0)

    def render(self, mono, rate, style, dt, fall, peaks_on):
        if style == "wave":
            return self._wave(mono)
        n, cw = (19, 4) if style == "thick" else (38, 2)
        target = self.levels(mono, n, rate) if mono is not None else np.zeros(n)
        b, p, hold, vel = self.bars[:n], self.peaks[:n], self.hold[:n], self.vel[:n]

        b[:] = np.maximum(target, b - FALL[fall] * dt)
        rising = b >= p
        p[rising] = b[rising]
        hold[rising] = PEAK_HOLD
        vel[rising] = 0
        waiting = ~rising & (hold > 0)
        hold[waiting] -= dt
        dropping = ~rising & ~waiting
        vel[dropping] += GRAVITY[fall] * dt
        p[dropping] = np.maximum(0, p[dropping] - vel[dropping] * dt)

        col_h = np.repeat(np.rint(b * H).astype(int), cw)[:W]   # last bar ends on the final column
        col_h[cw - 1::cw] = 0                                  # 1-pixel gap between bars
        lit = self.rows >= (H - col_h)[None, :]
        frame = np.where(lit[..., None], self.bar_colours, self.bg[style])
        if peaks_on:
            py = np.minimum(H - 1, np.rint(p * H).astype(int))
            for i in np.nonzero(p > 0.02)[0]:
                frame[H - 1 - py[i], i * cw:i * cw + cw - 1] = PEAK
        return frame

    def _wave(self, mono):
        frame = self.bg["wave"].copy()
        mid = (H - 1) // 2
        if mono is None:
            ys = np.full(W, mid)
        else:
            span, search = 512, 1024
            seg = mono[-(span + search):]
            cross = np.nonzero((seg[:search - 1] < 0) & (seg[1:search] >= 0))[0]
            start = int(cross[0]) + 1 if len(cross) else 0   # rising zero-crossing keeps it steady
            pts = seg[start + np.arange(W) * span // W]
            ys = np.clip(np.rint(mid - pts * 1.3 * (H / 2)), 0, H - 1).astype(int)
        prev = ys[0]
        for x in range(W):
            a, b = (prev, ys[x]) if prev <= ys[x] else (ys[x], prev)
            frame[a:b + 1, x] = self.scope_colours[a:b + 1, x]
            prev = ys[x]
        return frame


class Oscilloscope:
    """X-Y mode: left channel moves the beam sideways, right channel up and down.
    The beam leaves more light where it moves slowly and fades like real phosphor."""

    def __init__(self, colour="cyan", gain="auto"):
        self.scale = None
        self.colour = colour
        self.gain = gain
        self.agc = 0.05

    def _setup(self, s):
        self.scale = s
        c = SCOPE_COLOURS.get(self.colour, SCOPE_COLOURS["cyan"])
        self.beam, self.hot_tint = c["beam"], c["hot"]
        self.bezel = b = bezel_for(s)
        w, h = XY_W * s, XY_H * s
        self.w, self.h = w, h
        self.acc = np.zeros((h, w), np.float32)
        cx, cy = (w - 1) / 2, (h - 1) / 2
        base = np.empty((h, w, 3), np.float32)
        base[:] = c["screen"]
        for i in range(1, 10):                                # 10 x 8 divisions
            base[:, round(i * (w - 1) / 10)] = c["grat"]
        for j in range(1, 8):
            base[round(j * (h - 1) / 8), :] = c["grat"]
        t = max(1, s // 2 + 1)                                # small ticks along the centre lines
        for k in range(1, 50):
            base[round(cy) - t:round(cy) + t + 1, round(k * (w - 1) / 50)] = c["grat"]
        for k in range(1, 40):
            base[round(k * (h - 1) / 40), round(cx) - t:round(cx) + t + 1] = c["grat"]
        yy, xx = np.mgrid[0:h, 0:w]
        r2 = ((xx - cx) / (w / 2)) ** 2 + ((yy - cy) / (h / 2)) ** 2
        base *= (1 - 0.22 * np.clip(r2, 0, 2))[..., None]     # darker towards the corners
        self.base = base
        self.unit = min(w, h) * 0.5                           # full scale = 4 divisions, same on both axes
        self.glow_r = max(2, round(1.6 * s))

        # the resting spot: a small filled disc of light
        rad = max(1.0, 0.85 * s)
        step = 0.5
        gy, gx = np.mgrid[-rad:rad + step:step, -rad:rad + step:step]
        inside = gx * gx + gy * gy <= rad * rad
        xs, ys = gx[inside].astype(np.float32), gy[inside].astype(np.float32)
        self.spot = (xs, ys, np.full(len(xs), SPOT / len(xs), np.float32))

        # rounded screen, and a bezel of the same thickness all the way round it
        r_in = 6 * s
        edge = np.clip(0.5 - rounded_sdf(h, w, r_in), 0, 1)   # soft join between screen and bezel
        self.screen_cover = edge[..., None]
        full_h, full_w = h + 2 * b, w + 2 * b
        full = np.empty((full_h, full_w, 3), np.float32)
        full[:] = FRAME_RGB
        self.full = full

        # the outer curve is cut after the screen is placed, so the border keeps its width,
        # and the last pixel or two fade dark so the curve doesn't look like stairs
        R = r_in + b
        d = rounded_sdf(full_h, full_w, R)
        self.edge_shade = (0.18 + 0.82 * np.clip(-d / 2.4, 0, 1) ** 0.75)[..., None].astype(np.float32)
        outside = d > 0
        self.corners = []
        for ys, xs in ((slice(0, R), slice(0, R)),
                       (slice(0, R), slice(full_w - R, full_w)),
                       (slice(full_h - R, full_h), slice(0, R)),
                       (slice(full_h - R, full_h), slice(full_w - R, full_w))):
            self.corners.append((ys, xs, outside[ys, xs]))

    def _splat(self, px, py, wt):
        """Add light at sub-pixel positions, shared between the 4 nearest pixels."""
        w, h = self.w, self.h
        x0 = np.floor(px).astype(np.int64)
        y0 = np.floor(py).astype(np.int64)
        fx = (px - x0).astype(np.float32)
        fy = (py - y0).astype(np.float32)
        idx, val = [], []
        for ox, oy, f in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                          (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
            xi, yi = x0 + ox, y0 + oy
            ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
            idx.append(yi[ok] * w + xi[ok])
            val.append((wt * f)[ok])
        self.acc += np.bincount(np.concatenate(idx), weights=np.concatenate(val),
                                minlength=w * h).reshape(h, w).astype(np.float32)

    def _trace(self, x, y, energy):
        """Draw the beam path through the points; each step between samples gets equal energy."""
        dx, dy = np.diff(x), np.diff(y)
        steps = np.clip(np.ceil(np.hypot(dx, dy)).astype(np.int64), 1, 300)
        total = int(steps.sum())
        if total > 300000:                                    # keep huge jumps affordable
            steps = np.maximum(1, steps * 300000 // total)
            total = int(steps.sum())
        seg = np.repeat(np.arange(len(steps)), steps)
        first = np.repeat(np.cumsum(steps) - steps, steps)
        t = (np.arange(total) - first + 0.5) / steps[seg]
        self._splat(x[:-1][seg] + dx[seg] * t, y[:-1][seg] + dy[seg] * t, (energy / steps)[seg])

    def render(self, left, right, rate, dt, s):
        if self.scale != s:
            self._setup(s)
        self.acc *= math.exp(-dt / PERSIST)
        cx, cy = (self.w - 1) / 2, (self.h - 1) / 2
        if left is None or len(left) < 2:
            # no signal: a resting spot in the middle, like a real scope in X-Y mode
            xs, ys, wt = self.spot
            self._splat(cx + xs, cy + ys, wt * dt)
        else:
            if self.gain == "auto":
                # size it on the loudest 3% of samples, so the odd spike doesn't shrink everything
                loud = float(np.percentile(np.maximum(np.abs(left), np.abs(right)), 97))
                self.agc = max(loud, self.agc * math.exp(-dt / 0.7))
                g = self.unit * 0.98 / max(self.agc, 0.02)
            else:
                g = self.unit * float(self.gain)               # fixed, like a volts per division knob
            self._trace(cx + left * g, cy - right * g, BEAM * s / rate)
        core = 1 - np.exp(-self.acc)
        glow = box_blur(box_blur(core, self.glow_r), self.glow_r)
        light = core * 0.85 + glow * 1.1
        hot = np.clip(light - 0.7, 0, None) ** 2
        screen = self.base + light[..., None] * self.beam + hot[..., None] * self.hot_tint
        screen = screen * self.screen_cover + FRAME_RGB * (1 - self.screen_cover)
        frame = self.full.copy()
        b, h, w = self.bezel, self.h, self.w
        frame[b:b + h, b:b + w] = screen
        frame *= self.edge_shade                           # soft dark edge all the way round
        for ys, xs, outside in self.corners:               # corners punched out of the window
            patch = frame[ys, xs]
            patch[outside] = TRANSPARENT
            frame[ys, xs] = patch
        return np.clip(frame, 0, 255).astype(np.uint8)


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]


def _guid(text):
    g = _GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


def _method(ptr, index, restype, *argtypes):
    """Call slot `index` of a COM object's function table."""
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])


class SystemVolume:
    """The Windows volume slider, read through Core Audio.

    Windows turns the volume down before the loopback tap, so quiet music arrives as a
    quiet signal and the display shrinks with it. Reading the slider lets us undo that."""
    CLSID_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
    IID_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
    IID_ENDPOINT_VOLUME = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

    def __init__(self):
        self.endpoint = None
        self.failures = 0

    def _open(self):
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, 2)                       # fine if COM is already started
        enum = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_guid(self.CLSID_ENUMERATOR)), None, 23,
                                    ctypes.byref(_guid(self.IID_ENUMERATOR)), ctypes.byref(enum))
        if hr or not enum:
            raise OSError(hr)
        try:
            dev = ctypes.c_void_p()
            get_default = _method(enum, 4, ctypes.c_long, ctypes.c_int, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_void_p))
            hr = get_default(enum, 0, 0, ctypes.byref(dev))  # default playback device, console role
        finally:
            _method(enum, 2, ctypes.c_ulong)(enum)
        if hr or not dev:
            raise OSError(hr)
        try:
            vol = ctypes.c_void_p()
            activate = _method(dev, 3, ctypes.c_long, ctypes.POINTER(_GUID), ctypes.c_int,
                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            hr = activate(dev, ctypes.byref(_guid(self.IID_ENDPOINT_VOLUME)), 23, None, ctypes.byref(vol))
        finally:
            _method(dev, 2, ctypes.c_ulong)(dev)
        if hr or not vol:
            raise OSError(hr)
        self.endpoint = vol

    def level(self):
        """Master output volume from 0 to 1, or None if it can't be read."""
        if sys.platform != "win32" or self.failures > 4:
            return None
        try:
            if self.endpoint is None:
                self._open()
            out = ctypes.c_float()
            get_scalar = _method(self.endpoint, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_float))
            if get_scalar(self.endpoint, ctypes.byref(out)):
                raise OSError("GetMasterVolumeLevelScalar failed")
            self.failures = 0
            return float(out.value)
        except Exception:  # noqa: BLE001 - any COM trouble just means no compensation
            self.close()
            self.failures += 1
            return None

    def close(self):
        if self.endpoint:
            try:
                _method(self.endpoint, 2, ctypes.c_ulong)(self.endpoint)
            except Exception:  # noqa: BLE001
                pass
        self.endpoint = None


class LoopbackCapture:
    """Listens to the default Windows output device through WASAPI loopback, in stereo."""
    RING = 16384

    def __init__(self):
        self.left = np.zeros(self.RING, np.float32)
        self.right = np.zeros(self.RING, np.float32)
        self.written = 0                     # total samples received, to find new ones
        self.lock = threading.Lock()
        self.last = 0.0
        self.rate = 48000
        self.channels = 2
        self.name = ""
        self.error = ""
        self.pa = None
        self.stream = None

    def start(self):
        try:
            self.pa = pyaudio.PyAudio()
            dev = self._find_loopback()
            self.channels = max(1, int(dev["maxInputChannels"]))
            self.rate = int(dev["defaultSampleRate"])
            self.name = dev["name"].replace(" [Loopback]", "")
            self.stream = self.pa.open(
                format=pyaudio.paInt16, channels=self.channels, rate=self.rate,
                input=True, input_device_index=dev["index"],
                frames_per_buffer=512, stream_callback=self._callback)
            self.error = ""
            return True
        except Exception as exc:  # noqa: BLE001 - show whatever went wrong
            self.error = str(exc) or exc.__class__.__name__
            self.close()
            return False

    def _find_loopback(self):
        wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = self.pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if speakers.get("isLoopbackDevice"):
            return speakers
        for dev in self.pa.get_loopback_device_info_generator():
            if speakers["name"] in dev["name"]:
                return dev
        raise RuntimeError(f"No loopback device found for {speakers['name']}")

    def _callback(self, data, frames, info, status):
        a = np.frombuffer(data, dtype=np.int16).astype(np.float32) * (1 / 32768)
        if self.channels > 1:
            a = a.reshape(-1, self.channels)
            l, r = a[:, 0], a[:, 1]
        else:
            l = r = a
        n = len(l)
        with self.lock:
            for ring, new in ((self.left, l), (self.right, r)):
                if n >= self.RING:
                    ring[:] = new[-self.RING:]
                elif n:
                    ring[:-n] = ring[n:]
                    ring[-n:] = new
            self.written += n
            self.last = time.monotonic()
        return (None, pyaudio.paContinue)

    def snapshot(self):
        """(left, right, total written), or None when nothing is playing.
        (Windows stops sending loopback data during silence, so a stale buffer means quiet.)"""
        with self.lock:
            if time.monotonic() - self.last > 0.2:
                return None
            return self.left.copy(), self.right.copy(), self.written

    def close(self):
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.pa:
                self.pa.terminate()
        except Exception:  # noqa: BLE001
            pass
        self.stream = self.pa = None


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    if cfg["style"] == "scope":                # older name for the waveform
        cfg["style"] = "wave"
    if cfg["style"] not in STYLES:
        cfg["style"] = DEFAULTS["style"]
    if cfg["scale"] not in SCALES:
        cfg["scale"] = DEFAULTS["scale"]
    if cfg["fall"] not in FALL:
        cfg["fall"] = DEFAULTS["fall"]
    if cfg["scope_colour"] not in SCOPE_COLOURS:
        cfg["scope_colour"] = DEFAULTS["scope_colour"]
    if str(cfg["scope_gain"]) not in SCOPE_GAINS:
        cfg["scope_gain"] = DEFAULTS["scope_gain"]
    return cfg


def virtual_screen(root):
    """Bounds of all monitors together, so the widget can live on a second screen."""
    try:
        import ctypes
        m = ctypes.windll.user32.GetSystemMetrics
        return m(76), m(77), m(78), m(79)       # x, y, width, height of the virtual screen
    except Exception:  # noqa: BLE001
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class App:
    def __init__(self):
        self.cfg = load_config()
        try:  # crisp pixels on high-DPI displays instead of Windows' blurry stretch
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass

        self.root = root = tk.Tk()
        root.title("Mini spectrum")
        try:
            root.iconbitmap(default=ICON)
        except tk.TclError:
            pass
        root.overrideredirect(True)
        root.configure(bg=FRAME)
        try:      # lets the rounded corners of the oscilloscope show the desktop through
            root.attributes("-transparentcolor", TRANSPARENT_HEX)
        except tk.TclError:
            pass
        root.attributes("-topmost", self.cfg["topmost"])
        self.label = tk.Label(root, bd=0, bg="black")
        self.label.pack(padx=2, pady=2)

        self.pixels = PixelDisplay()
        self.scope = Oscilloscope(self.cfg["scope_colour"], str(self.cfg["scope_gain"]))
        self.audio = LoopbackCapture()
        self.sysvol = SystemVolume()
        self.boost = 1.0                     # undoes the Windows volume slider
        self.boost_checked = 0.0
        self.photo = None
        self.prev_frame = None
        self.seen = 0                        # samples already drawn by the oscilloscope
        self.last_t = time.perf_counter()

        self.style = tk.StringVar(value=self.cfg["style"])
        self.scale = tk.IntVar(value=self.cfg["scale"])
        self.fall = tk.StringVar(value=self.cfg["fall"])
        self.peaks = tk.BooleanVar(value=self.cfg["peaks"])
        self.scope_colour = tk.StringVar(value=self.cfg["scope_colour"])
        self.scope_gain = tk.StringVar(value=str(self.cfg["scope_gain"]))
        self.ignore_volume = tk.BooleanVar(value=self.cfg["ignore_volume"])
        self.topmost = tk.BooleanVar(value=self.cfg["topmost"])
        self._build_menu()

        self.label.bind("<ButtonPress-1>", self._press)
        self.label.bind("<B1-Motion>", self._drag)
        self.label.bind("<ButtonRelease-1>", self._release)
        self.label.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        root.bind("<MouseWheel>", self._wheel)
        root.bind("<Escape>", lambda e: self.quit())
        root.protocol("WM_DELETE_WINDOW", self.quit)

        self._apply_border()
        self._render(force=True)
        self._place(self.cfg["x"], self.cfg["y"])
        self._connect(first=True)
        self._tick()

    # ---------------------------------------------------------------- menu
    def _build_menu(self):
        m = self.menu = tk.Menu(self.root, tearoff=0)
        m.add_command(label=f"Mini spectrum {__version__} by {__author__}", state="disabled")
        m.add_command(label="Not connected", state="disabled")
        m.add_separator()
        style = tk.Menu(m, tearoff=0)
        for key, name in STYLES.items():
            style.add_radiobutton(label=name, value=key, variable=self.style, command=self._style_changed)
        m.add_cascade(label="Style", menu=style)
        size = tk.Menu(m, tearoff=0)
        for s in SCALES:
            size.add_radiobutton(label=f"{s}×", value=s, variable=self.scale, command=self._scale_changed)
        m.add_cascade(label="Size", menu=size)
        fall = tk.Menu(m, tearoff=0)
        for key in FALL:
            fall.add_radiobutton(label=key.capitalize(), value=key, variable=self.fall, command=self._save)
        m.add_cascade(label="Fall speed", menu=fall)
        colour = tk.Menu(m, tearoff=0)
        for key, c in SCOPE_COLOURS.items():
            colour.add_radiobutton(label=c["name"], value=key, variable=self.scope_colour,
                                   command=self._colour_changed)
        m.add_cascade(label="Scope colour", menu=colour)
        gain = tk.Menu(m, tearoff=0)
        for key, name in SCOPE_GAINS.items():
            gain.add_radiobutton(label=name, value=key, variable=self.scope_gain, command=self._gain_changed)
        m.add_cascade(label="Scope gain", menu=gain)
        m.add_checkbutton(label="Peak caps", variable=self.peaks, command=self._save)
        m.add_checkbutton(label="Ignore Windows volume", variable=self.ignore_volume,
                          command=self._volume_mode_changed)
        m.add_checkbutton(label="Always on top", variable=self.topmost, command=self._topmost_changed)
        m.add_separator()
        m.add_command(label="Reconnect audio", command=self._connect)
        m.add_command(label="Quit", command=self.quit)

    def _colour_changed(self):
        self.scope.colour = self.scope_colour.get()
        self.scope.scale = None          # rebuilds the screen in the new colour
        self._render(force=True)
        self._save()

    def _volume_mode_changed(self):
        self.boost, self.boost_checked = 1.0, 0.0
        self._save()

    def _gain_changed(self):
        self.scope.gain = self.scope_gain.get()
        self.scope.agc = 0.05
        self._save()

    def _connect(self, first=False):
        self.audio.close()
        self.sysvol.close()                  # the volume control belongs to the old device
        ok = self.audio.start()
        self.seen = 0
        self.boost, self.boost_checked = 1.0, 0.0
        self.menu.entryconfigure(1, label=f"Listening to: {self.audio.name}" if ok else "Not connected")
        if not ok:
            messagebox.showwarning(
                "Mini spectrum",
                "Couldn't listen to your speaker output.\n\n"
                f"{self.audio.error}\n\n"
                "Check that a playback device is set as default in Windows sound settings, "
                "then right-click the widget and choose Reconnect audio.",
                parent=self.root)

    def _style_changed(self):
        self.pixels.reset()
        self._apply_border()
        self._render(force=True)
        self._place(self.root.winfo_x(), self.root.winfo_y())
        self._save()

    def _scale_changed(self):
        self._apply_border()
        self._render(force=True)
        self._place(self.root.winfo_x(), self.root.winfo_y())
        self._save()

    def _topmost_changed(self):
        self.root.attributes("-topmost", self.topmost.get())
        self._save()

    def _apply_border(self):
        # the oscilloscope draws its own rounded bezel, so the window itself adds nothing
        pad = 0 if self.style.get() == "xy" else 2
        self.border = pad
        self.label.pack_configure(padx=pad, pady=pad)

    # ---------------------------------------------------------------- mouse
    def _press(self, e):
        self._start = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())
        self._moved = False
        self.root.focus_force()   # so the mouse wheel and Esc reach the widget

    def _drag(self, e):
        sx, sy, wx, wy = self._start
        dx, dy = e.x_root - sx, e.y_root - sy
        if abs(dx) + abs(dy) > 3:
            self._moved = True
        if self._moved:
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def _release(self, e):
        if self._moved:
            self._save()
        else:  # a plain click cycles the style
            keys = list(STYLES)
            self.style.set(keys[(keys.index(self.style.get()) + 1) % len(keys)])
            self._style_changed()

    def _wheel(self, e):
        i = SCALES.index(self.scale.get())
        i = min(len(SCALES) - 1, i + 1) if e.delta > 0 else max(0, i - 1)
        if SCALES[i] != self.scale.get():
            self.scale.set(SCALES[i])
            self._scale_changed()

    # ---------------------------------------------------------------- window
    def _size(self):
        s = self.scale.get()
        if self.style.get() == "xy":
            b = bezel_for(s)
            return XY_W * s + 2 * b, XY_H * s + 2 * b
        return W * s + 2 * self.border, H * s + 2 * self.border

    def _place(self, x, y):
        w, h = self._size()
        vx, vy, vw, vh = virtual_screen(self.root)
        if x is None or y is None:  # first run: bottom-right, above the taskbar
            x, y = self.root.winfo_screenwidth() - w - 24, self.root.winfo_screenheight() - h - 72
        x = min(max(vx, int(x)), vx + vw - w)
        y = min(max(vy, int(y)), vy + vh - h)
        self.root.geometry(f"+{x}+{y}")

    def _save(self):
        self.cfg.update(style=self.style.get(), scale=self.scale.get(), fall=self.fall.get(),
                        peaks=self.peaks.get(), scope_colour=self.scope_colour.get(),
                        scope_gain=self.scope_gain.get(), ignore_volume=self.ignore_volume.get(),
                        topmost=self.topmost.get(),
                        x=self.root.winfo_x(), y=self.root.winfo_y())
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f)
        except OSError:
            pass

    # ---------------------------------------------------------------- drawing
    def _render(self, force=False, snap=None, dt=0.0):
        s, style = self.scale.get(), self.style.get()
        if style == "xy":
            left = right = None
            if snap is not None:
                l, r, written = snap
                new = written - self.seen
                if 0 < new:
                    n = min(new + 1, len(l), int(self.audio.rate * 0.1))
                    left, right = l[-n:] * self.boost, r[-n:] * self.boost
                self.seen = written
            frame = self.scope.render(left, right, self.audio.rate, dt, s)
        else:
            mono = None
            if snap is not None:
                mono = (snap[0] + snap[1]) * (0.5 * self.boost)
                self.seen = snap[2]
            frame = self.pixels.render(mono, self.audio.rate, style, dt, self.fall.get(), self.peaks.get())
            frame = frame.repeat(s, axis=0).repeat(s, axis=1)
        if not force and self.prev_frame is not None and np.array_equal(frame, self.prev_frame):
            return False
        self.prev_frame = frame
        h, w = frame.shape[:2]
        ppm = b"P6 %d %d 255 " % (w, h) + frame.tobytes()
        self.photo = tk.PhotoImage(width=w, height=h, data=ppm, format="PPM")
        self.label.configure(image=self.photo)
        return True

    def _tick(self):
        now = time.perf_counter()
        dt, self.last_t = min(0.1, now - self.last_t), now
        if now - self.boost_checked > 0.5:               # keep up with the volume slider
            self.boost_checked = now
            level = self.sysvol.level() if self.ignore_volume.get() else None
            self.boost = min(20.0, 1.0 / max(level, 0.05)) if level else 1.0
        snap = self.audio.snapshot()
        changed = self._render(snap=snap, dt=dt)
        # ~60 fps while something moves, a lazy 20 fps while everything is still
        self.root.after(15 if (snap is not None or changed) else 50, self._tick)

    def quit(self):
        self._save()
        self.audio.close()
        self.sysvol.close()
        self.root.destroy()


def fail(message):
    """Show an error even when there's no console (pythonw, the .exe), then quit."""
    try:
        r = tk.Tk()
        r.withdraw()
        try:
            r.iconbitmap(default=ICON)
        except tk.TclError:
            pass
        messagebox.showerror("Mini spectrum", message, parent=r)
        r.destroy()
    except Exception:  # noqa: BLE001
        pass
    sys.exit(message)


def main():
    if sys.platform != "win32":
        fail("Mini spectrum listens to Windows' speaker output (WASAPI loopback), "
             "so it only runs on Windows.")
    if np is None or pyaudio is None:
        fail("Some parts are missing. Run:\n\n    python -m pip install numpy pyaudiowpatch")
    app = App()
    app.root.mainloop()


if __name__ == "__main__":
    main()
