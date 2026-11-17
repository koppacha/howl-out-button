import tempfile
import unittest
from pathlib import Path

import numpy as np

from sound_trigger.core import Library, Player


class FakeStream:
    latency = .01
    def __init__(self, backend, **kwargs):
        self.backend = backend
        self.kwargs = kwargs
    def start(self):
        self.backend.calls.append(('start', self.kwargs['device']))
        if self.backend.fail_start:
            raise RuntimeError('device unavailable')
    def stop(self):
        self.backend.calls.append(('stop', self.kwargs['device']))
    def close(self):
        self.backend.calls.append(('close', self.kwargs['device']))


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.default = 0
        self.fail_start = False
        self.devices = [
            dict(index=0, name='Speakers', hostapi=0, max_output_channels=2, default_samplerate=48000),
            dict(index=1, name='Headphones', hostapi=0, max_output_channels=2, default_samplerate=44100),
        ]
    def query_hostapis(self):
        return [dict(name='Core Audio')]
    def query_devices(self, device=None, kind=None):
        if kind == 'output':
            return self.devices[self.default if device is None else device]
        return self.devices
    def OutputStream(self, **kwargs):
        return FakeStream(self, **kwargs)
    def _terminate(self):
        self.calls.append(('terminate',))
    def _initialize(self):
        self.calls.append(('initialize',))


class OutputTests(unittest.TestCase):
    def test_reconnect_closes_old_stream_before_refresh_and_follows_default(self):
        backend = FakeBackend()
        player = Player(audio_backend=backend)
        player.open()
        player.set_ready(np.ones((16, 2), dtype=np.float32))
        player.trigger()
        backend.default = 1
        devices = player.list_outputs(refresh=True)
        self.assertIsNone(player.ready)
        self.assertIsNone(player.stream)
        self.assertEqual(backend.calls[-4:], [('stop', 0), ('close', 0), ('terminate',), ('initialize',)])
        self.assertEqual(devices[1]['name'], 'Headphones')
        player.open()
        self.assertEqual(player.device_name, 'Headphones')
        self.assertEqual(player.rate, 44100)
        self.assertEqual(player.stream.kwargs['device'], 1)
        self.assertFalse(player.trigger())  # Old-rate buffer cannot play after reconnect.
        player.close()

    def test_explicit_output_overrides_default_and_failure_stays_silent(self):
        backend = FakeBackend()
        player = Player(audio_backend=backend)
        player.open(1)
        self.assertEqual(player.device_name, 'Headphones')
        player.set_ready(np.ones((16, 2), dtype=np.float32))
        backend.fail_start = True
        with self.assertRaises(RuntimeError):
            player.open(1)
        self.assertIsNone(player.stream)
        self.assertIsNone(player.ready)
        self.assertFalse(player.trigger())
        self.assertEqual(backend.calls[-1], ('close', 1))

    def test_mono_output_downmix(self):
        backend = FakeBackend()
        backend.devices[1]['max_output_channels'] = 1
        player = Player(audio_backend=backend)
        player.open(1)
        self.assertEqual(player.stream.kwargs['channels'], 1)
        player.set_ready(np.array([[.2, .6], [.8, -.4]], dtype=np.float32))
        player.trigger()
        output = np.empty((3, 1), dtype=np.float32)
        player.callback(output, 3, None, False)
        np.testing.assert_allclose(output[:, 0], [.4, .2, 0], atol=1e-6)
        player.close()

    def test_output_selection_persists_and_old_settings_default_to_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Library(Path(tmp))
            self.assertIsNone(library.state['output'])
            choice = dict(name='Headphones', hostapi='Core Audio')
            library.state['output'] = choice
            library.save()
            self.assertEqual(Library(Path(tmp)).state['output'], choice)
