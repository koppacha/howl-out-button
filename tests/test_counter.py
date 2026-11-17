import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from sound_trigger.app import Window
from sound_trigger.core import Library, Player, decode
from test_output import FakeBackend

class CounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_reset_hide_keep_counting_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            player = Player(audio_backend=FakeBackend())
            window = Window(Library(root), player, lambda *args: Mock(), monitor_outputs=False)
            window.show(); self.app.processEvents()
            player.trigger(); player.trigger(); window.tick()
            self.assertIn('2', window.counter.text())
            QTest.mouseClick(window.hide_count_button, Qt.MouseButton.LeftButton)
            self.assertTrue(window.counter.isHidden())
            self.assertEqual(window.hide_count_button.text(), '表示')
            player.trigger(); window.tick()
            self.assertEqual(player.count, 3)
            QTest.mouseClick(window.reset_count_button, Qt.MouseButton.LeftButton)
            self.assertEqual(player.count, 0)
            self.assertEqual(Library(root).state['count'], 0)
            self.assertTrue(Library(root).state['count_hidden'])
            QTest.mouseClick(window.hide_count_button, Qt.MouseButton.LeftButton)
            self.assertFalse(window.counter.isHidden())
            self.assertIn('0', window.counter.text())
            self.assertIn('Howl-Out-Button', window.windowTitle())
            window.close(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_library_rolls_back_failed_save_and_rejects_external_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source.wav'; source.write_bytes(b'fixture')
            library = Library(root/'library')
            with patch.object(library, 'save', side_effect=OSError('test full disk')):
                with self.assertRaises(OSError): library.add(source)
            self.assertEqual(library.state['sounds'], [])
            item = library.add(source)
            with patch.object(library, 'save', side_effect=OSError('test full disk')):
                with self.assertRaises(OSError): library.remove(item['id'])
            self.assertEqual(library.state['selected'], item['id'])
            self.assertTrue(library.file(item).exists())
            try: (library.audio/'escape.wav').symlink_to(source)
            except OSError: return  # Windows may require Developer Mode for symlinks.
            with self.assertRaises(ValueError): library.file({'file':'escape.wav'})

    def test_decode_disables_auto_demux_and_network_protocols(self):
        with patch('av.open', side_effect=RuntimeError('stop before parsing')) as opened:
            with self.assertRaises(RuntimeError): decode(Path('fixture.mp3'), 48000)
        self.assertEqual(opened.call_args.kwargs['format'], 'mp3')
        self.assertEqual(opened.call_args.kwargs['options']['protocol_whitelist'], 'file')
