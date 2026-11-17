import json
import tempfile
import unittest
from unittest.mock import Mock
from pathlib import Path

import numpy as np

from sound_trigger.core import Library, Player, decode, render
from sound_trigger.inputs import Inputs


class CoreTests(unittest.TestCase):
    def test_keyboard_repeat_release_and_close(self):
        hits = []
        inputs = Inputs(lambda: hits.append(1))
        inputs.listener = Mock()
        inputs.listener.canonical.side_effect = lambda key: 'canonical'
        inputs.hotkey = Mock()
        inputs.hotkey.press.side_effect = lambda key: inputs.trigger()
        raw = Mock(name='function_key')
        raw.name = 'f8'
        inputs.press(raw)
        inputs.press(raw)
        self.assertEqual(len(hits), 1)
        inputs.release(raw)
        inputs.press(raw)
        self.assertEqual(len(hits), 2)
        inputs.release(raw)
        inputs.close()
        inputs.press(raw)
        self.assertEqual(len(hits), 2)

    def test_persistence_copy_delete_and_original_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'test.wav'
            source.write_bytes(b'test fixture')
            library = Library(Path(tmp) / 'data')
            item = library.add(source)
            library.state['count'] = 9
            library.save()
            loaded = Library(library.root)
            self.assertEqual(loaded.state['count'], 9)
            self.assertEqual(loaded.file(item).read_bytes(), b'test fixture')
            loaded.remove(item['id'])
            self.assertTrue(source.exists())
            self.assertFalse(loaded.file(item).exists())
            self.assertIsNone(Library(library.root).state['selected'])

    def test_invalid_settings_preserved_and_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            original = json.dumps({'sounds': [{'file': '../outside.wav'}]})
            (path / 'settings.json').write_text(original)
            library = Library(path)
            self.assertTrue(library.warning)
            self.assertEqual(library.state['sounds'], [])
            self.assertEqual(next(path.glob('settings-invalid-*.json')).read_text(), original)
            with self.assertRaises(ValueError):
                library.file({'file': '../outside.wav'})

    def test_callback_retrigger_stop_and_count_without_audio(self):
        player = Player()
        self.assertFalse(player.trigger())
        player.stream = object()
        audio = np.arange(12, dtype=np.float32).reshape(6, 2)
        player.set_ready(audio)
        self.assertTrue(player.trigger())
        out = np.empty((4, 2), np.float32)
        player.callback(out, 4, None, False)
        np.testing.assert_array_equal(out, audio[:4])
        player.trigger()
        player.callback(out, 4, None, False)
        np.testing.assert_array_equal(out, audio[:4])
        player.callback(out, 4, None, False)
        np.testing.assert_array_equal(out[:2], audio[4:])
        self.assertTrue(np.all(out[2:] == 0))
        player.stop()
        player.callback(out, 4, None, True)
        self.assertTrue(np.all(out == 0))
        self.assertEqual(player.count, 3)
        self.assertEqual(player.underruns, 1)

    def test_dsp_pitch_and_speed_are_independent(self):
        rate = 24000
        t = np.arange(rate) / rate
        audio = np.column_stack([.2 * np.sin(2 * np.pi * 440 * t)] * 2).astype('float32')
        def frequency(y):
            return np.argmax(abs(np.fft.rfft(y[:, 0]))) * rate / len(y)
        fast = render(audio, rate, 0, 2.)
        self.assertAlmostEqual(len(fast), rate // 2, delta=2)
        self.assertAlmostEqual(frequency(fast), 440, delta=5)
        high = render(audio, rate, 12, 1.)
        self.assertEqual(len(high), rate)
        self.assertAlmostEqual(frequency(high), 880, delta=5)
        self.assertTrue(np.isfinite(high).all())

    def test_decode_wav_mp3_and_m4a(self):
        import av
        with tempfile.TemporaryDirectory() as tmp:
            rate = 48000
            wave = (.1 * np.sin(2 * np.pi * 440 * np.arange(4096) / rate)).astype('float32')
            for ext, codec in [('wav', 'pcm_s16le'), ('mp3', 'libmp3lame'), ('m4a', 'aac')]:
                with self.subTest(ext=ext):
                    path = Path(tmp) / ('synthetic.' + ext)
                    with av.open(str(path), 'w') as container:
                        stream = container.add_stream(codec, rate=rate)
                        stream.layout = 'stereo'
                        frame = av.AudioFrame.from_ndarray(np.stack([wave, wave]), format='fltp', layout='stereo')
                        frame.sample_rate = rate
                        for packet in stream.encode(frame):
                            container.mux(packet)
                        for packet in stream.encode(None):
                            container.mux(packet)
                    decoded = decode(path, 24000)
                    self.assertEqual(decoded.shape[1], 2)
                    self.assertGreater(len(decoded), 1000)
                    self.assertTrue(np.isfinite(decoded).all())




if __name__ == '__main__':
    unittest.main()
