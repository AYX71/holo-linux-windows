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


class HeadsetCapture(unittest.TestCase):
    def make_listener(self):
        stream = Mock()
        sd = types.SimpleNamespace(InputStream=Mock(side_effect=[ValueError('48 kHz unsupported'), stream]),
                                   query_devices=Mock(return_value={'default_samplerate': 16000}))
        with patch.dict(sys.modules, {'sounddevice': sd}):
            listener = holo.Listener(device=3, headset=True)
        return listener, sd

    def test_headset_falls_back_to_native_rate(self):
        listener, sd = self.make_listener()
        self.assertEqual(listener.sample_rate, 16000)
        self.assertEqual(sd.InputStream.call_args.kwargs['samplerate'], 16000)
        self.assertEqual(sd.InputStream.call_args.kwargs['blocksize'], 160)
        self.assertEqual(sd.InputStream.call_args.kwargs['device'], 3)

    def test_laptop_does_not_silently_change_sample_rate(self):
        sd = types.SimpleNamespace(InputStream=Mock(side_effect=ValueError('unsupported')))
        with patch.dict(sys.modules, {'sounddevice': sd}), self.assertRaises(ValueError):
            holo.Listener()
        sd.InputStream.assert_called_once()

    def test_native_audio_produces_full_detector_blocks(self):
        listener, _ = self.make_listener()
        for _ in range(2):
            listener._cb(np.ones((160, 1), dtype='float32'), 160, None, False)
        _, block = listener.read_block()
        self.assertEqual(block.shape, (holo.BLOCK,))
        self.assertTrue(np.all(block == 1))

    def test_gap_discards_resampler_tail(self):
        listener, _ = self.make_listener()
        for _ in range(2):
            listener._cb(np.ones((160, 1), dtype='float32'), 160, None, False)
        listener.read_block()
        for gap in (True, False):
            listener._cb(np.zeros((160, 1), dtype='float32'), 160, None, gap)
        _, block = listener.read_block()
        self.assertTrue(np.all(block == 0))
        self.assertEqual(listener.detector.seen, 0)

    def test_streaming_interpolation_is_continuous_across_irregular_chunks(self):
        for rate in (8000, 16000, 22050, 44100):
            with self.subTest(rate=rate):
                adapter = holo.NativeRateAdapter(rate)
                t = np.arange(rate) / rate
                signal = np.sin(2 * np.pi * 700 * t).astype('float32')
                blocks = []
                for start in range(0, len(signal), 137):
                    blocks.extend(adapter.feed(signal[start:start + 137]))
                actual = np.concatenate(blocks)
                expected = np.interp(np.arange(len(actual)) * rate / holo.SR,
                                     np.arange(len(signal)), signal)
                np.testing.assert_allclose(actual, expected, atol=1e-6)
                self.assertGreaterEqual(len(actual), holo.SR - holo.BLOCK)
                self.assertLess(len(adapter.pending), holo.BLOCK)
                self.assertLessEqual(len(adapter.source), 2)

    def test_stereo_channels_are_kept_separate(self):
        adapter = holo.NativeRateAdapter(16000, 2)
        signal = np.column_stack([np.ones(1000), -np.ones(1000)])
        blocks = adapter.feed(signal)
        self.assertEqual(blocks[0].shape, (holo.BLOCK, 2))
        np.testing.assert_array_equal(blocks[0][:, 0], 1)
        np.testing.assert_array_equal(blocks[0][:, 1], -1)

    def test_synthetic_native_rate_tap_reaches_detector_and_gate(self):
        rate = 16000
        adapter = holo.NativeRateAdapter(rate)
        signal = np.random.default_rng(5).normal(0, .0002, rate * 2)
        t = np.arange(int(.08 * rate)) / rate
        signal[rate:rate + len(t)] += .15 * np.exp(-60 * t) * np.sin(2 * np.pi * 2300 * t)
        detector = holo.Detector(1.5)
        windows = []
        for start in range(0, len(signal), 160):
            for block in adapter.feed(signal[start:start + 160]):
                window = detector.feed(block)
                if window is not None:
                    windows.append(window)
        self.assertEqual(len(windows), 1)
        self.assertIsNone(holo.gate(windows[0]))


if __name__ == '__main__':
    unittest.main()
