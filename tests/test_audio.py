"""Audio regressions with simulated input; no microphone or PortAudio required."""
from pathlib import Path
import queue
import sys
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import holo


class Capture(unittest.TestCase):
    def listener(self):
        stream = Mock()
        sd = types.SimpleNamespace(InputStream=Mock(return_value=stream))
        with patch.dict(sys.modules, {'sounddevice': sd}):
            listener = holo.Listener(sensitivity=1.5)
        return listener, stream

    def feed(self, listener, status=False):
        listener._cb(np.ones((holo.BLOCK, 1), dtype='float32') * .001,
                     holo.BLOCK, None, status)

    def test_callback_copies_input_memory(self):
        listener, _ = self.listener()
        block = np.ones((holo.BLOCK, 1), dtype='float32')
        listener._cb(block, holo.BLOCK, None, False)
        block[:] = 0
        _, saved = listener.read_block()
        self.assertTrue(np.all(saved == 1))

    def test_overloaded_consumer_keeps_new_audio_and_resets_detection(self):
        listener, _ = self.listener()
        for _ in range(51):
            self.feed(listener)
        self.assertEqual(listener.q.qsize(), 1)
        self.assertEqual(listener.dropped_blocks, 50)
        listener.detector.collecting = [np.ones(holo.BLOCK)]
        listener.read_block()
        self.assertIsNone(listener.detector.collecting)
        self.assertEqual(listener.detector.sensitivity, 1.5)

    def test_portaudio_overflow_discards_partial_tap(self):
        listener, _ = self.listener()
        listener.detector.collecting = [np.ones(holo.BLOCK)]
        self.feed(listener, True)
        listener.read_block()
        self.assertEqual(listener.input_overflows, 1)
        self.assertEqual(listener.detector.seen, 0)
        self.assertIsNone(listener.detector.collecting)

    def test_stale_audio_cannot_trigger_an_action(self):
        listener, _ = self.listener()
        listener.q.put((1.0, np.ones(holo.BLOCK), False))
        listener.detector.collecting = [np.ones(holo.BLOCK)]
        with patch.object(holo.time, 'monotonic', return_value=2.0):
            with self.assertRaises(queue.Empty):
                listener.read_block()
        self.assertIsNone(listener.detector.collecting)

    def test_start_failure_closes_stream(self):
        listener, stream = self.listener()
        stream.start.side_effect = RuntimeError('device gone')
        with self.assertRaisesRegex(RuntimeError, 'device gone'):
            listener.__enter__()
        stream.close.assert_called_once()

    def test_stop_failure_still_closes_stream(self):
        listener, stream = self.listener()
        stream.stop.side_effect = RuntimeError('driver failure')
        with self.assertRaisesRegex(RuntimeError, 'driver failure'):
            listener.__exit__()
        stream.close.assert_called_once()


class Hearing(unittest.TestCase):
    def detect(self, signal, sensitivity):
        detector = holo.Detector(sensitivity)
        windows = []
        for i in range(0, len(signal) - holo.BLOCK + 1, holo.BLOCK):
            window = detector.feed(signal[i:i + holo.BLOCK])
            if window is not None:
                windows.append(window)
        return windows

    def test_sensitive_setting_detects_a_quieter_synthetic_tap(self):
        signal = np.random.default_rng(41).normal(0, .0001, holo.SR * 2)
        t = np.arange(int(.08 * holo.SR)) / holo.SR
        signal[holo.SR:holo.SR + len(t)] += .005 * np.exp(-60 * t) * np.sin(2 * np.pi * 2300 * t)
        self.assertEqual(len(self.detect(signal, 1)), 0)
        windows = self.detect(signal, 2)
        self.assertEqual(len(windows), 1)
        self.assertIsNone(holo.gate(windows[0]))

    def test_sensitive_setting_still_ignores_steady_quiet_noise(self):
        signal = np.random.default_rng(4).normal(0, .0005, holo.SR * 3)
        self.assertEqual(self.detect(signal, 2), [])

    def test_invalid_sensitivity_is_rejected(self):
        for value in (0, -1, 3, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                holo.Detector(value)


if __name__ == '__main__':
    unittest.main()
