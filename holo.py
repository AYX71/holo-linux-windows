#!/usr/bin/env python3
"""Holo for Linux: classify taps on the desk around a laptop into four zones.

Pipeline (follows the same general idea as the macOS Holo project):
  mic stream -> adaptive onset detector -> 90 ms window -> sustained/clip gate
  -> features (log-band spectrum + temporal envelope, optional stereo cues)
  -> ridge classifier with an optional background ("none") class and
     nearest-example novelty rejection -> action

Usage:
  python holo.py devices
  python holo.py calibrate    [--device N] [--channels 1|2]
  python holo.py negatives    [--device N]   # record typing/talking/touches
  python holo.py stereo-check [--device N]   # is the mic really stereo?
  python holo.py eval         [--device N]
  python holo.py run          [--device N]

The input device can also be set with the HOLO_DEVICE environment variable.
"""
import argparse
import json
import os
import queue
import random
import statistics
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

SR = 48000
BLOCK = 480                      # 10 ms
WIN = int(0.090 * SR)            # 90 ms analysis window
ZONES = ["left_rear", "right_rear", "left_front", "right_front"]
NONE = "none"                    # background class: typing, talking, touches
CAL_PER_ZONE = 15
EVAL_PER_ZONE = 10

NEG_SECONDS = 30                 # how long to record background sounds
NEG_MIN = 8                      # fewer usable background sounds than this: skip
NEG_MAX_KEEP = 60                # keep at most this many (newest)

def app_dirs(platform, env, home):
    """Application data/config locations; empty environment values are unset."""
    home = Path(home)
    if platform == "win32":
        return (Path(env.get("LOCALAPPDATA") or home / "AppData/Local") / "holo",
                Path(env.get("APPDATA") or home / "AppData/Roaming") / "holo")
    return (Path(env.get("XDG_DATA_HOME") or home / ".local/share") / "holo",
            Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "holo")


DATA_DIR, CONF_DIR = app_dirs(sys.platform, os.environ, Path.home())
PROFILE = DATA_DIR / "profile.json"
ACTIONS = CONF_DIR / "actions.json"

# Tunables
ONSET_RATIO = 6.0        # block RMS must exceed noise floor by this factor
ONSET_MIN_RMS = 0.003    # ...and this absolute level
CLIP_PEAK = 0.99
SUSTAIN_RATIO = 0.5      # late/early energy above this => speech-like, rejected
MIN_SCORE = 0.30         # classifier score needed for the top zone
MIN_MARGIN = 0.25        # gap between best and second-best score
NOVELTY_FACTOR = 2.5     # distance allowed relative to calibration spread
RIDGE = 3.0
MAX_LAG = 32             # stereo: largest inter-channel delay searched (samples)


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------
class Detector:
    """Streaming onset detector. feed() returns a 90 ms window or None.

    Blocks may be mono (BLOCK,) or multi-channel (BLOCK, C).
    """

    def __init__(self, sensitivity=1.0):
        if not np.isfinite(sensitivity) or not 0.5 <= sensitivity <= 2.0:
            raise ValueError("Sensitivity must be between 0.5 and 2.0")
        self.sensitivity = float(sensitivity)
        self.floor = None
        self.seen = 0
        self.learn_blocks = int(0.75 * SR / BLOCK)
        self.prev = deque(maxlen=1)
        self.collecting = None
        self.refractory = 0

    def feed(self, block):
        rms = float(np.sqrt(np.mean(block ** 2))) + 1e-9
        self.seen += 1

        if self.collecting is not None:
            self.collecting.append(block)
            if sum(len(b) for b in self.collecting) >= WIN:
                window = np.concatenate(self.collecting)[:WIN]
                self.collecting = None
                self.refractory = int(0.25 * SR / BLOCK)
                self.prev.clear()
                return window
            return None

        if self.floor is None:
            self.floor = rms
        if self.seen <= self.learn_blocks:           # room-learning period
            self.floor = 0.9 * self.floor + 0.1 * rms
            self.prev.append(block)
            return None
        if self.refractory > 0:
            self.refractory -= 1
            self.floor = 0.98 * self.floor + 0.02 * min(rms, self.floor * 3)
            self.prev.append(block)
            return None

        if rms > self.threshold:
            self.collecting = list(self.prev) + [block]
            return None

        self.floor = 0.995 * self.floor + 0.005 * rms
        self.prev.append(block)
        return None

    @property
    def threshold(self):
        return max((self.floor or 0) * ONSET_RATIO, ONSET_MIN_RMS) / self.sensitivity


def gate(window):
    """Return a human-readable rejection reason, or None if the window is OK."""
    if np.max(np.abs(window)) >= CLIP_PEAK:
        return "clipped: tap more lightly"
    e = window ** 2
    early = e[: int(0.3 * WIN)].sum() + 1e-12
    late = e[int(0.6 * WIN):].sum()
    if late > SUSTAIN_RATIO * early:
        return "sustained sound (speech/noise?), ignored"
    return None


# --------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------
_EDGES = np.geomspace(100, 16000, 33)
_COARSE = np.geomspace(100, 16000, 9)
_FREQS = np.fft.rfftfreq(WIN, 1 / SR)
_MASKS = [(_FREQS >= lo) & (_FREQS < hi) for lo, hi in zip(_EDGES[:-1], _EDGES[1:])]
_COARSE_MASKS = [(_FREQS >= lo) & (_FREQS < hi) for lo, hi in zip(_COARSE[:-1], _COARSE[1:])]
_HANN = np.hanning(WIN)


def _mono_features(window):
    w = window - window.mean()
    spec = np.abs(np.fft.rfft(w * _HANN)) ** 2
    bands = np.array([spec[m].sum() for m in _MASKS]) + 1e-12
    logb = np.log(bands)
    logb -= logb.mean()                     # remove overall loudness
    seg = np.array_split(w, 12)
    env = np.log(np.array([np.mean(s ** 2) for s in seg]) + 1e-12)
    env -= env.max()
    return np.concatenate([logb, env])


def _stereo_features(left, right):
    """Level difference per coarse band (8) + inter-channel delay and strength."""
    l = left - left.mean()
    r = right - right.mean()
    sl = np.abs(np.fft.rfft(l * _HANN)) ** 2
    sr = np.abs(np.fft.rfft(r * _HANN)) ** 2
    ild = [np.log(sl[m].sum() + 1e-12) - np.log(sr[m].sum() + 1e-12)
           for m in _COARSE_MASKS]
    n = 2 * WIN
    xc = np.fft.irfft(np.fft.rfft(l, n) * np.conj(np.fft.rfft(r, n)), n)
    lags = np.concatenate([xc[-MAX_LAG:], xc[: MAX_LAG + 1]])   # -MAX..+MAX
    k = int(np.argmax(lags))
    denom = np.sqrt(np.sum(l ** 2) * np.sum(r ** 2)) + 1e-12
    return np.array(ild + [(k - MAX_LAG) / MAX_LAG, lags[k] / denom])


def features(window):
    """Feature vector for a (WIN,) mono window or a (WIN, C) multi-channel one."""
    w = np.asarray(window)
    if w.ndim == 2:
        base = _mono_features(w.mean(axis=1))
        if w.shape[1] >= 2:
            return np.concatenate([base, _stereo_features(w[:, 0], w[:, 1])])
        return base
    return _mono_features(w)


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
class Model:
    def __init__(self):
        self.classes = list(ZONES)
        self.med = self.scale = self.W = None
        self.train = {}       # class -> normalized training vectors
        self.radius = {}      # class -> raw nearest-neighbor spread

    def _norm(self, X):
        return (np.asarray(X) - self.med) / self.scale

    def fit(self, X, y):
        X = np.asarray(X, float)
        y = list(y)
        self.classes = list(ZONES) + ([NONE] if NONE in y else [])
        taps = np.array([c != NONE for c in y])
        # Normalize from taps only, so adding background sounds doesn't change
        # how the zones are scaled.
        self.med = np.median(X[taps], axis=0)
        iqr = np.subtract(*np.percentile(X[taps], [75, 25], axis=0))
        self.scale = np.where(iqr < 1e-3, 1.0, iqr)
        Z = self._norm(X)
        idx = np.array([self.classes.index(c) for c in y])
        T = -np.ones((len(y), len(self.classes)))
        T[np.arange(len(y)), idx] = 1.0
        # Class-balanced sample weights (all 1.0 when classes are the same size)
        counts = np.bincount(idx, minlength=len(self.classes)).astype(float)
        sw = np.sqrt(len(y) / (len(self.classes) * counts[idx]))
        A = np.hstack([Z, np.ones((len(Z), 1))])
        Aw, Tw = A * sw[:, None], T * sw[:, None]
        reg = RIDGE * np.eye(A.shape[1])
        reg[-1, -1] = 0                      # don't penalize the bias
        self.W = np.linalg.solve(Aw.T @ Aw + reg, Aw.T @ Tw)

        self.train, self.radius = {}, {}
        for i, cls in enumerate(self.classes):
            Zi = Z[idx == i]
            self.train[cls] = Zi
            if len(Zi) < 2 or cls == NONE:
                self.radius[cls] = 1e9
                continue
            d = np.linalg.norm(Zi[:, None] - Zi[None], axis=2)
            np.fill_diagonal(d, np.inf)
            self.radius[cls] = float(np.max(d.min(axis=1)))   # raw spread
        return self

    def predict(self, x):
        """Return (zone or None, reason, scores aligned with self.classes)."""
        z = self._norm(x)
        scores = np.append(z, 1.0) @ self.W
        order = np.argsort(scores)[::-1]
        best, second = order[0], order[1]
        cls = self.classes[best]
        if cls == NONE:
            return None, "background sound", scores
        if scores[best] < MIN_SCORE:
            return None, "low confidence", scores
        if scores[best] - scores[second] < MIN_MARGIN:
            return None, "ambiguous", scores
        dist = float(np.min(np.linalg.norm(self.train[cls] - z, axis=1)))
        if dist > self.radius[cls] * NOVELTY_FACTOR:
            return None, "unlike calibration taps", scores
        return cls, "ok", scores

    def to_json(self):
        return {
            "classes": self.classes,
            "med": self.med.tolist(), "scale": self.scale.tolist(),
            "W": self.W.tolist(),
            "train": {k: v.tolist() for k, v in self.train.items()},
            "radius": self.radius,
        }

    @classmethod
    def from_json(cls, d):
        m = cls()
        m.classes = d.get("classes", list(ZONES))
        m.med, m.scale = np.array(d["med"]), np.array(d["scale"])
        m.W = np.array(d["W"])
        m.train = {k: np.array(v) for k, v in d["train"].items()}
        m.radius = d["radius"]
        return m


def leave_one_out(X, y):
    """Leave-one-out agreement for the tap samples (background samples stay in
    the training set). Optimistic: all samples come from one session."""
    X = np.asarray(X)
    per_zone = {z: [0, 0] for z in ZONES}
    hits = total = 0
    for i in range(len(X)):
        if y[i] == NONE:
            continue
        keep = np.arange(len(X)) != i
        m = Model().fit(X[keep], [y[j] for j in range(len(y)) if keep[j]])
        scores = np.append(m._norm(X[i]), 1.0) @ m.W
        pred = m.classes[int(np.argmax(scores))]
        per_zone[y[i]][1] += 1
        total += 1
        if pred == y[i]:
            per_zone[y[i]][0] += 1
            hits += 1
    return hits / max(total, 1), {z: a / max(b, 1) for z, (a, b) in per_zone.items()}


# --------------------------------------------------------------------------
# Profile storage
# --------------------------------------------------------------------------
def save_profile(model, channels, X, y, path=None):
    path = Path(path) if path is not None else PROFILE
    path.parent.mkdir(parents=True, exist_ok=True)
    d = model.to_json()
    d["channels"] = channels
    d["data"] = {"X": [np.asarray(x).tolist() for x in X], "y": list(y)}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(path)


def load_profile(path=None):
    path = Path(path) if path is not None else PROFILE
    if not path.exists():
        sys.exit("No profile yet. Run: holo calibrate")
    d = json.loads(path.read_text())
    return Model.from_json(d), int(d.get("channels", 1)), d


def cap_negatives(X, y):
    idx = [i for i, c in enumerate(y) if c == NONE]
    drop = set(idx[:-NEG_MAX_KEEP]) if len(idx) > NEG_MAX_KEEP else set()
    return ([x for i, x in enumerate(X) if i not in drop],
            [c for i, c in enumerate(y) if i not in drop])


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------
class NativeRateAdapter:
    """Streaming linear upsampling to 48 kHz; emits complete detector blocks.

    Used only when a headset cannot open at 48 kHz. Interpolation cannot
    restore frequencies the microphone did not capture.
    """

    def __init__(self, sample_rate, channels=1):
        if not 8000 <= sample_rate <= SR:
            raise ValueError("Headset sample rate must be between 8000 and 48000 Hz")
        self.step = float(sample_rate) / SR
        self.channels = channels
        self.source = np.empty((0, channels), dtype="float32")
        self.pending = np.empty((0, channels), dtype="float32")
        self.position = 0.0

    def feed(self, block):
        self.source = np.concatenate([self.source, np.asarray(block).reshape(-1, self.channels)])
        count = max(0, int(np.floor((len(self.source) - 1 - self.position) / self.step)) + 1)
        if not count:
            return []
        positions = self.position + np.arange(count) * self.step
        samples = np.column_stack([np.interp(positions, np.arange(len(self.source)),
                                            self.source[:, ch]) for ch in range(self.channels)])
        self.position += count * self.step
        consumed = min(int(self.position), len(self.source) - 1)
        self.source = self.source[consumed:]
        self.position -= consumed
        self.pending = np.concatenate([self.pending, samples.astype("float32")])
        blocks = []
        while len(self.pending) >= BLOCK:
            block = self.pending[:BLOCK].copy()
            blocks.append(block[:, 0] if self.channels == 1 else block)
            self.pending = self.pending[BLOCK:]
        return blocks


class Listener:
    """Owns the input stream; yields detected tap windows."""

    def __init__(self, device=None, channels=1, sensitivity=1.0, headset=False):
        import sounddevice as sd
        self.channels = channels
        self.q = queue.Queue(maxsize=50)  # at most half a second, never an endless backlog
        self.sensitivity = sensitivity
        self.detector = Detector(sensitivity)
        self.dropped_blocks = 0
        self.input_overflows = 0
        self.sample_rate = SR
        self.adapter = None
        self.ready_blocks = deque()
        try:
            self.stream = sd.InputStream(
                samplerate=SR, blocksize=BLOCK, channels=channels, dtype="float32",
                device=device, callback=self._cb, latency="high")
        except Exception:
            if not headset:
                raise
            native = float(sd.query_devices(device, "input")["default_samplerate"])
            if not 8000 <= native < SR:
                raise
            self.sample_rate = native
            self.adapter = NativeRateAdapter(native, channels)
            self.stream = sd.InputStream(
                samplerate=native, blocksize=round(native * BLOCK / SR), channels=channels,
                dtype="float32", device=device, callback=self._cb, latency="high")

    def _cb(self, indata, frames, t, status):
        block = indata[:, 0].copy() if self.channels == 1 else indata.copy()
        gap = bool(status)
        if gap:
            self.input_overflows += 1
        if self.q.full():
            # Callback must never wait for the consumer. Keep the newest audio.
            while True:
                try:
                    self.q.get_nowait()
                    self.dropped_blocks += 1
                except queue.Empty:
                    break
            gap = True
        self.q.put_nowait((time.monotonic(), block, gap))

    def read_block(self, timeout=0.2):
        end = time.monotonic() + timeout
        while True:
            if self.ready_blocks:
                t, block = self.ready_blocks.popleft()
                if time.monotonic() - t <= 0.5:
                    return t, block
                self._reset_audio()
                raise queue.Empty
            t, block, gap = self.q.get(timeout=max(0, end - time.monotonic()))
            if gap:
                self._reset_audio()
            if time.monotonic() - t > 0.5:
                self._reset_audio()
                raise queue.Empty
            if self.adapter is None:
                return t, block
            self.ready_blocks.extend((t, b) for b in self.adapter.feed(block))

    def _reset_audio(self):
        self.detector = Detector(self.sensitivity)
        self.ready_blocks.clear()
        if self.adapter is not None:
            self.adapter = NativeRateAdapter(self.sample_rate, self.channels)

    def __enter__(self):
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            raise
        return self

    def __exit__(self, *a):
        try:
            self.stream.stop()
        finally:
            self.stream.close()

    def drain(self):
        while not self.q.empty():
            self.q.get_nowait()
        self._reset_audio()
        time.sleep(0.9)          # let the detector relearn the room
        while not self.q.empty():
            self.q.get_nowait()

    def next_window(self, timeout=None):
        """Block until a window is detected. Returns (window, t) or (None, None)
        if the timeout expires first."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            if end is not None and time.monotonic() > end:
                return None, None
            try:
                t, block = self.read_block(timeout=0.2)
            except queue.Empty:
                continue
            w = self.detector.feed(block)
            if w is not None:
                return w, t


def open_listener(device, channels):
    try:
        return Listener(device, channels)
    except Exception as e:                       # PortAudio errors vary
        sys.exit(f"Could not open the microphone ({channels} channel(s)): {e}")


def collect(listener, n, label):
    feats = []
    listener.detector = Detector()
    listener.drain()
    print(f"  Listening... tap {n} times around the {label} area, pausing between taps.")
    while len(feats) < n:
        w, _ = listener.next_window()
        reason = gate(w)
        if reason:
            print(f"    skipped: {reason}")
            continue
        feats.append(features(w))
        print(f"    accepted {len(feats)}/{n}")
    return feats


def collect_background(listener, seconds=NEG_SECONDS):
    """Record sounds that are NOT taps. Only sounds that would get past the
    gate are kept, because only those could cause a false trigger."""
    listener.drain()
    print(f"  Recording for {seconds} seconds. Type on the keyboard, talk, click the")
    print("  trackpad lightly, put things down on the desk.")
    print("  Do NOT move or lift the laptop, and do NOT tap the four zones.")
    feats, heard = [], 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        w, _ = listener.next_window(timeout=0.5)
        if w is None:
            continue
        heard += 1
        if gate(w):
            continue
        feats.append(features(w))
    print(f"  Heard {heard} sounds; kept {len(feats)} that could cause false triggers.")
    return feats


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_devices(_):
    import sounddevice as sd
    print(sd.query_devices())


def cmd_calibrate(args):
    channels = args.channels
    print("Keep the laptop exactly where it will stay. Moving it invalidates the profile.\n")
    X, y = [], []
    with open_listener(args.device, channels) as ls:
        for zone in ZONES:
            input(f"Zone '{zone}': press Enter, then start tapping. ")
            for f in collect(ls, CAL_PER_ZONE, zone):
                X.append(f)
                y.append(zone)
            time.sleep(0.5)
        ans = input("\nRecord background sounds (typing, talking, ...) to reduce "
                    "false triggers? Optional and experimental. [y/N] ")
        if ans.strip().lower() in ("y", "yes"):
            neg = collect_background(ls)
            if len(neg) >= NEG_MIN:
                X.extend(neg)
                y.extend([NONE] * len(neg))
                X, y = cap_negatives(X, y)
            else:
                print(f"  Only {len(neg)} usable sounds (need {NEG_MIN}); skipping "
                      "the background class. Run 'holo negatives' to try again.")
    acc, per = leave_one_out(X, y)
    print(f"\nLeave-one-out agreement: {acc:.0%}  (optimistic; run 'eval' for a real test)")
    for z, a in per.items():
        print(f"  {z:12s} {a:.0%}")
    weakest = min(per, key=per.get)
    if per[weakest] < 0.8:
        print(f"Weak zone: {weakest}. Consider recalibrating before relying on it.")
    model = Model().fit(X, y)
    save_profile(model, channels, X, y)
    print(f"Saved profile to {PROFILE}")


def cmd_negatives(args):
    model, channels, d = load_profile()
    data = d.get("data")
    if not data:
        sys.exit("This profile has no stored calibration data. Run 'holo calibrate' "
                 "once; after that 'holo negatives' can add background sounds.")
    X = [np.array(x) for x in data["X"]]
    y = list(data["y"])
    with open_listener(args.device, channels) as ls:
        neg = collect_background(ls)
    if len(neg) < NEG_MIN:
        sys.exit(f"Only {len(neg)} usable sounds (need {NEG_MIN}). Try again and make "
                 "more keyboard clicks, claps of the hand on the laptop, and so on.")
    X.extend(neg)
    y.extend([NONE] * len(neg))
    X, y = cap_negatives(X, y)
    model = Model().fit(X, y)
    save_profile(model, channels, X, y)
    print(f"Saved profile with {y.count(NONE)} background sounds. Run 'holo eval' to "
          "check that real taps are still recognized.")


def cmd_stereo_check(args):
    print("Recording 6 seconds from 2 channels.")
    print("Tap the desk LEFT of the laptop 3 times, then RIGHT of it 3 times.")
    chunks = []
    with open_listener(args.device, 2) as ls:
        end = time.monotonic() + 6
        while time.monotonic() < end:
            try:
                _, b = ls.read_block(timeout=0.2)
            except queue.Empty:
                continue
            chunks.append(b)
    if not chunks:
        sys.exit("No audio received.")
    x = np.concatenate(chunks)
    left, right = x[:, 0], x[:, 1]
    if max(np.max(np.abs(left)), np.max(np.abs(right))) < 0.01:
        sys.exit("Heard almost nothing. Check the input device and tap louder.")
    rl = float(np.sqrt(np.mean(left ** 2))) + 1e-12
    rr = float(np.sqrt(np.mean(right ** 2))) + 1e-12
    identical = float(np.max(np.abs(left - right))) < 1e-6
    corr = 1.0 if identical else float(np.corrcoef(left, right)[0, 1])
    print(f"\nLevel difference left vs right: {20 * np.log10(rl / rr):+.1f} dB")
    print(f"Correlation between channels:   {corr:.3f}")
    if identical:
        print("Verdict: the two channels are identical (mono duplicated). Stereo "
              "features cannot help here.")
    elif corr > 0.995:
        print("Verdict: the channels are almost identical. Stereo features will add "
              "little.")
    else:
        print("Verdict: the channels carry different signals, so stereo cues are "
              "possible. Try: holo calibrate --channels 2, then holo eval.")


def cmd_eval(args):
    model, channels, _ = load_profile()
    order = [z for z in ZONES for _ in range(EVAL_PER_ZONE)]
    random.shuffle(order)
    confusion = {z: {p: 0 for p in ZONES + ["rejected"]} for z in ZONES}
    latencies = []
    with open_listener(args.device, channels) as ls:
        ls.drain()
        for i, zone in enumerate(order, 1):
            print(f"[{i}/{len(order)}] tap: {zone}")
            while True:
                w, t_block = ls.next_window()
                if gate(w):
                    continue                      # not a clean tap, try again
                pred, why, scores = model.predict(features(w))
                if pred is None:
                    print(f"    rejected: {why}  " + " ".join(
                        f"{c}={v:.2f}" for c, v in zip(model.classes, scores)))
                latencies.append((time.monotonic() - t_block) * 1000)
                confusion[zone][pred or "rejected"] += 1
                break
    correct = sum(confusion[z][z] for z in ZONES)
    total = len(order)
    print(f"\nOverall accuracy: {correct / total:.0%}  (target >= 80%)")
    for z in ZONES:
        n = sum(confusion[z].values())
        print(f"  {z:12s} {confusion[z][z] / n:.0%}   {confusion[z]}")
    med = statistics.median(latencies)
    print(f"Processing latency after the 90 ms window fills: {med:.0f} ms "
          f"(tap-to-decision is about 90 ms + this; target < 200)")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / f"eval-{int(time.time())}.json"
    out.write_text(json.dumps({"accuracy": correct / total, "confusion": confusion,
                               "latencies_ms": latencies}))
    print(f"Saved {out}")


_last_fired = {}
COOLDOWN = 1.5   # seconds before the same zone can fire again


def spawn(command):
    """Launch a shell action without blocking or opening a Windows console."""
    options = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    return subprocess.Popen(command, shell=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **options)


def run_action(zone):
    if not ACTIONS.exists():
        return
    now = time.monotonic()
    if now - _last_fired.get(zone, -1e9) < COOLDOWN:
        return
    _last_fired[zone] = now
    cmd = json.loads(ACTIONS.read_text()).get(zone)
    if cmd:
        spawn(cmd)


def cmd_run(args):
    model, channels, _ = load_profile()
    print("Running. Ctrl+C to stop. Actions come from", ACTIONS, flush=True)
    with open_listener(args.device, channels) as ls:
        ls.drain()
        try:
            while True:
                w, _ = ls.next_window()
                reason = gate(w)
                if reason:
                    continue
                zone, why, scores = model.predict(features(w))
                if zone:
                    print(f"{zone}  (score {scores[model.classes.index(zone)]:.2f})", flush=True)
                    run_action(zone)
                else:
                    print(f"rejected: {why}", flush=True)
        except KeyboardInterrupt:
            print("\nStopped.")


def resolve_device(arg):
    if arg is not None:
        return arg
    env = os.environ.get("HOLO_DEVICE", "").strip()
    return int(env) if env else None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in [("devices", cmd_devices), ("calibrate", cmd_calibrate),
                     ("negatives", cmd_negatives), ("stereo-check", cmd_stereo_check),
                     ("eval", cmd_eval), ("run", cmd_run)]:
        sp = sub.add_parser(name)
        sp.add_argument("--device", type=int, default=None)
        if name == "calibrate":
            sp.add_argument("--channels", type=int, choices=[1, 2], default=1,
                            help="2 adds experimental stereo cues (see stereo-check)")
        sp.set_defaults(fn=fn)
    args = p.parse_args()
    args.device = resolve_device(args.device)
    args.fn(args)


if __name__ == "__main__":
    main()
