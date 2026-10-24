"""Tests on synthetic audio. They check that the code works, not how well it
works on a real desk; use `holo eval` for that."""
import pathlib
import sys
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import holo  # noqa: E402

RNG = np.random.default_rng(0)
PARAMS = {"left_rear": (900, 60), "right_rear": (1500, 80),
          "left_front": (2300, 50), "right_front": (3400, 90)}


def resonant_tap(zone):
    f, d = PARAMS[zone]
    f *= RNG.uniform(0.97, 1.03)
    t = np.arange(int(0.08 * holo.SR)) / holo.SR
    return RNG.uniform(0.15, 0.4) * np.exp(-d * t) * np.sin(2 * np.pi * f * t)


def click():
    """Keyboard-like click: short broadband burst."""
    n = int(0.006 * holo.SR)
    env = np.exp(-np.arange(n) / (0.0015 * holo.SR))
    return RNG.uniform(0.1, 0.3) * RNG.normal(0, 1, n) * env


def stream(event, lead=1.2, total=1.8, noise=0.0005):
    x = RNG.normal(0, noise, int(total * holo.SR))
    s = int(lead * holo.SR)
    x[s:s + len(event)] += event
    return x


def windows(x):
    det, out = holo.Detector(), []
    for i in range(0, len(x) - holo.BLOCK + 1, holo.BLOCK):
        w = det.feed(x[i:i + holo.BLOCK].astype("float32"))
        if w is not None:
            out.append(w)
    return out


def one_window(event):
    ws = windows(stream(event))
    assert len(ws) == 1, len(ws)
    return ws[0]


def dataset(n, with_none=False):
    X, y = [], []
    for z in holo.ZONES:
        for _ in range(n):
            X.append(holo.features(one_window(resonant_tap(z))))
            y.append(z)
    if with_none:
        for _ in range(30):
            w = one_window(click())
            if holo.gate(w) is None:
                X.append(holo.features(w))
                y.append(holo.NONE)
    return X, y


class DetectorAndGate(unittest.TestCase):
    def test_finds_one_tap(self):
        self.assertEqual(len(windows(stream(resonant_tap("left_rear")))), 1)

    def test_noise_only_gives_nothing(self):
        self.assertEqual(len(windows(RNG.normal(0, 0.0005, holo.SR * 3))), 0)

    def test_sustained_tone_is_gated(self):
        t = np.arange(int(2 * holo.SR)) / holo.SR
        s = 0.2 * np.sin(2 * np.pi * 400 * t)
        s[:holo.SR] = 0
        s += RNG.normal(0, 0.0005, len(s))
        ws = windows(s.astype("float32"))
        self.assertTrue(ws)
        self.assertTrue(all(holo.gate(w) for w in ws))

    def test_timeout_returns_none(self):
        class Fake(holo.Listener):
            def __init__(self):
                import queue
                from collections import deque
                self.q = queue.Queue()
                self.ready_blocks = deque()
                self.detector = holo.Detector()
        self.assertEqual(Fake().next_window(timeout=0.3), (None, None))


class Classifier(unittest.TestCase):
    def test_zones_separate(self):
        X, y = dataset(15)
        m = holo.Model().fit(X, y)
        ok = 0
        for z in holo.ZONES:
            for _ in range(10):
                pred, _, _ = m.predict(holo.features(one_window(resonant_tap(z))))
                ok += pred == z
        self.assertGreaterEqual(ok, 38)

    def test_background_class_rejects_clicks_and_keeps_taps(self):
        X, y = dataset(15, with_none=True)
        self.assertIn(holo.NONE, y)
        m = holo.Model().fit(X, y)
        self.assertEqual(m.classes[-1], holo.NONE)
        rejected = kept = 0
        for _ in range(20):
            w = one_window(click())
            if holo.gate(w):
                rejected += 1
                continue
            pred, _, _ = m.predict(holo.features(w))
            rejected += pred is None
        for z in holo.ZONES:
            for _ in range(10):
                pred, _, _ = m.predict(holo.features(one_window(resonant_tap(z))))
                kept += pred == z
        self.assertGreaterEqual(rejected, 18)
        self.assertGreaterEqual(kept, 38)

    def test_profile_roundtrip(self):
        X, y = dataset(12, with_none=True)
        m = holo.Model().fit(X, y)
        m2 = holo.Model.from_json(m.to_json())
        x = holo.features(one_window(resonant_tap("right_rear")))
        self.assertEqual(m.predict(x)[0], m2.predict(x)[0])
        self.assertEqual(m2.classes, m.classes)

    def test_old_profile_without_classes_loads(self):
        X, y = dataset(12)
        d = holo.Model().fit(X, y).to_json()
        d.pop("classes")
        self.assertEqual(holo.Model.from_json(d).classes, holo.ZONES)

    def test_cap_negatives_keeps_newest(self):
        X = list(range(holo.NEG_MAX_KEEP + 5))
        y = [holo.NONE] * len(X)
        X2, y2 = holo.cap_negatives(X, y)
        self.assertEqual(len(X2), holo.NEG_MAX_KEEP)
        self.assertEqual(X2[-1], X[-1])


class Stereo(unittest.TestCase):
    def pair(self, delay):
        s = resonant_tap("left_front")
        left = np.concatenate([s, np.zeros(200)])
        right = np.concatenate([np.zeros(delay), s, np.zeros(200 - delay)])
        x = RNG.normal(0, 0.0005, (int(1.8 * holo.SR), 2))
        o = int(1.2 * holo.SR)
        x[o:o + len(left), 0] += left
        x[o:o + len(right), 1] += right
        det, out = holo.Detector(), []
        for i in range(0, len(x) - holo.BLOCK + 1, holo.BLOCK):
            w = det.feed(x[i:i + holo.BLOCK].astype("float32"))
            if w is not None:
                out.append(w)
        self.assertEqual(len(out), 1)
        return out[0]

    def test_shape_and_delay_sign(self):
        a = holo.features(self.pair(delay=10))
        b = holo.features(self.pair(delay=0))
        base = len(holo.features(np.zeros(holo.WIN) + 1e-3 * RNG.normal(size=holo.WIN)))
        self.assertEqual(len(a), base + 10)
        self.assertEqual(len(a), len(b))
        # delay feature is the second-to-last element
        self.assertGreater(abs(a[-2] - b[-2]), 0.1)

    def test_mono_features_unchanged_by_channel_layout(self):
        w = one_window(resonant_tap("left_rear"))
        self.assertEqual(len(holo.features(w)), 44)


if __name__ == "__main__":
    unittest.main()
