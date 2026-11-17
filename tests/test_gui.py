import os
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import wave
import unittest
import numpy as np
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from sound_trigger.app import Window
from sound_trigger.core import Library, Player
from sound_trigger.inputs import Inputs


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_and_settings_persist(self):
        app = self.app
        with tempfile.TemporaryDirectory() as tmp:
            player = Player()
            player.open = Mock()
            player.list_outputs = Mock(return_value=[])
            player.stream = Mock(latency=.01)
            inputs = Mock()
            window = Window(Library(Path(tmp)), player, lambda *args: inputs, monitor_outputs=False)
            window.show()
            app.processEvents()
            window.pitch.setValue(3)
            window.speed.setValue(1.5)
            window.player.trigger()
            window.tick()
            self.assertIn('1', window.counter.text())
            window.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            saved = Library(Path(tmp)).state
            self.assertEqual(saved['pitch'], 3)
            self.assertEqual(saved['speed'], 1.5)
            self.assertEqual(saved['count'], 1)
            inputs.close.assert_called_once()


    def test_popup_keeps_its_model_until_closed_and_ignores_duplicate_notifications(self):
        from test_output import FakeBackend
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend()
            inputs = Mock()
            window = Window(Library(Path(tmp)), Player(audio_backend=backend), lambda *args: inputs, monitor_outputs=False)
            window.show()
            self.app.processEvents()
            combo = window.output_choice
            QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertTrue(combo.popup_open)
            self.assertTrue(combo.popup_dialog.isVisible())
            before = list(backend.calls)
            before_generation = window.generation
            window.refresh_outputs()
            QTest.qWait(1100)  # Covers the application's one-second tick.
            self.assertTrue(combo.popup_open)
            self.assertTrue(combo.popup_dialog.isVisible())
            self.assertEqual(before, backend.calls)
            self.assertEqual(before_generation, window.generation)
            combo.hidePopup()
            self.app.processEvents()
            self.assertFalse(window.pending_output_refresh)
            self.assertIn(('terminate',), backend.calls)
            watcher = Mock()
            watcher.defaultAudioOutput.return_value.id.return_value = b'speakers'
            watcher.defaultAudioOutput.return_value.description.return_value = 'Speakers'
            watcher.audioOutputs.return_value = []
            window.media_devices = watcher
            window.output_signature = window.default_output_signature()
            before = list(backend.calls)
            for _ in range(5):
                window.schedule_output_refresh()
            QTest.qWait(500)
            self.assertEqual(before, backend.calls)
            QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
            self.assertTrue(combo.popup_dialog.isVisible())
            combo.popup_list.setCurrentRow(2)
            QTest.keyClick(combo.popup_list, Qt.Key.Key_Return)
            self.app.processEvents()
            self.assertIsNone(combo.popup_dialog)
            self.assertEqual(window.player.device_name, 'Headphones')
            QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
            self.assertTrue(combo.popup_dialog.isVisible())
            combo.hidePopup()
            self.app.processEvents()
            self.assertEqual(window.player.device_name, 'Headphones')
            window.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


    def test_audio_import_background_prepare_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'synthetic.wav'
            with wave.open(str(source), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes((np.sin(np.arange(2400) * 2 * np.pi * 440 / 24000) * 4000).astype('<i2').tobytes())
            player = Player()
            player.open = Mock()
            player.list_outputs = Mock(return_value=[])
            player.stream = Mock(latency=.01)
            inputs = Mock()
            window = Window(Library(root / 'data'), player, lambda *args: inputs, monitor_outputs=False)
            window.add_files([str(source)])
            deadline = time.monotonic() + 20
            while player.ready is None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNotNone(player.ready)
            self.assertTrue(window.play_button.isEnabled())
            self.assertTrue(player.trigger())
            window.remove()
            self.assertIsNone(player.ready)
            self.assertEqual(window.library.state['sounds'], [])
            self.assertTrue(source.exists())
            window.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_output_selection_disconnect_and_default_change(self):
        from test_output import FakeBackend
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend()
            player = Player(audio_backend=backend)
            inputs = Mock()
            window = Window(Library(Path(tmp)), player, lambda *args: inputs, monitor_outputs=False)
            self.assertEqual(player.device_name, 'Speakers')
            old_generation = window.generation
            window.output_choice.setCurrentIndex(2)
            window.output_choice.activated.emit(2)
            self.app.processEvents()
            self.assertEqual(player.device_name, 'Headphones')
            self.assertEqual(Library(Path(tmp)).state['output']['name'], 'Headphones')
            window.prepared(old_generation, np.ones((16, 2), dtype=np.float32), '')
            self.assertIsNone(player.ready)
            headphones = backend.devices.pop()
            window.refresh_outputs()
            self.assertIsNone(player.stream)
            self.assertIn('見つかりません', window.device_status.text())
            self.assertEqual(window.library.state['output']['name'], 'Headphones')
            backend.devices.append(headphones)
            window.refresh_outputs()
            self.assertEqual(player.device_name, 'Headphones')
            window.output_choice.setCurrentIndex(0)
            window.output_choice.activated.emit(0)
            self.app.processEvents()
            self.assertIsNone(Library(Path(tmp)).state['output'])
            self.assertEqual(player.device_name, 'Speakers')
            watcher = Mock()
            watcher.defaultAudioOutput.return_value.id.return_value = b'headphones'
            watcher.defaultAudioOutput.return_value.description.return_value = 'Headphones'
            watcher.audioOutputs.return_value = []
            window.media_devices = watcher
            backend.default = 1
            window.tick()
            self.assertTrue(window.output_debounce.isActive())
            window.refresh_outputs()
            self.assertEqual(player.device_name, 'Headphones')
            self.assertEqual(player.rate, 44100)
            window.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
