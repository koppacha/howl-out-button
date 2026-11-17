import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton, QKeySequenceEdit, QDialogButtonBox

from sound_trigger.app import Window
from sound_trigger.core import Library, Player
from sound_trigger.hotkeys import from_sequence, to_sequence
from sound_trigger.inputs import Inputs
from test_output import FakeBackend


class HotkeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sequence_roundtrip_single_chord(self):
        for value in ('c', '<ctrl>+c', '<alt>+<f8>', '<shift>+<cmd>+c', '<space>'):
            with self.subTest(value=value):
                self.assertEqual(from_sequence(to_sequence(value)), value)
        with self.assertRaises(ValueError):
            from_sequence(QKeySequence())
        self.assertEqual(from_sequence(QKeySequence('Ctrl+C')), '<cmd>+c' if sys.platform == 'darwin' else '<ctrl>+c')

    def create_window(self, root, global_allowed=False):
        from pynput import keyboard
        player = Player(audio_backend=FakeBackend())
        inputs = Inputs(player.trigger)
        if global_allowed:
            inputs.listener = keyboard.Listener(on_press=lambda key: None)
            inputs.keyboard = Mock()
        else:
            inputs.keyboard = Mock(side_effect=PermissionError('test denied'))
        library = Library(root)
        library.state['trigger'] = dict(kind='keyboard', key='c')
        window = Window(library, player, lambda *args: inputs, monitor_outputs=False)
        window.show()
        window.activateWindow()
        window.setFocus()
        self.app.processEvents()
        # Offscreen platforms need an explicit foreground notification.
        window.application_state_changed(Qt.ApplicationState.ApplicationActive)
        player.set_ready(np.full((16, 2), .25, np.float32))
        return window, inputs, player

    def close_window(self, window):
        window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_local_c_starts_playback_even_if_global_access_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            window, inputs, player = self.create_window(Path(tmp))
            self.assertFalse(window.trigger_active)
            self.assertIn('アプリ内のみ', window.binding_label.text())
            QTest.keyClick(window, Qt.Key.Key_C)
            self.app.processEvents()
            self.assertEqual(player.count, 1)
            self.assertIsNotNone(player.active)
            out = np.zeros((8, 2), np.float32)
            player.callback(out, 8, None, False)
            np.testing.assert_allclose(out, .25)
            QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(player.count, 2)
            self.close_window(window)

    def test_global_and_local_routes_do_not_double_count(self):
        from pynput import keyboard
        with tempfile.TemporaryDirectory() as tmp:
            window, inputs, player = self.create_window(Path(tmp), global_allowed=True)
            key = keyboard.KeyCode.from_char('C')
            inputs.press(key)
            QTest.keyClick(window, Qt.Key.Key_C)
            inputs.release(key)
            self.assertEqual(player.count, 1)
            inputs.set_foreground(False)
            inputs.press(key)
            inputs.press(key)
            inputs.release(key)
            self.assertEqual(player.count, 2)
            inputs.press(keyboard.Key.ctrl)
            inputs.press(key)
            inputs.release(key)
            inputs.release(keyboard.Key.ctrl)
            self.assertEqual(player.count, 2)  # Copy must not trigger plain C.
            inputs.press(keyboard.Key.shift)
            inputs.press(key)
            inputs.release(key)
            inputs.release(keyboard.Key.shift)
            self.assertEqual(player.count, 3)
            self.close_window(window)

    def test_main_trigger_group_has_only_label_and_two_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            window, inputs, player = self.create_window(Path(tmp))
            group = next(g for g in window.findChildren(QGroupBox) if g.title() == 'ホットキー')
            self.assertEqual(len(group.findChildren(QLabel)), 1)
            self.assertEqual({b.text() for b in group.findChildren(QPushButton)}, {'ホットキーを登録', 'ホットキーを削除'})
            self.assertEqual(group.findChildren(QKeySequenceEdit), [])
            self.close_window(window)

    def test_registration_cancel_save_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            window, inputs, player = self.create_window(root)
            QTest.mouseClick(window.register_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            dialog = window.hotkey_dialog
            self.assertTrue(dialog.isVisible())
            editor = dialog.findChild(QKeySequenceEdit)
            editor.setKeySequence(QKeySequence('D'))
            inputs.local_activate()
            inputs.global_activate()
            self.assertEqual(player.count, 0)
            box = dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(box.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(window.library.state['trigger']['key'], 'c')
            QTest.mouseClick(window.register_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            dialog = window.hotkey_dialog
            editor = dialog.findChild(QKeySequenceEdit)
            editor.setKeySequence(QKeySequence('D'))
            box = dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(box.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(Library(root).state['trigger']['key'], 'd')
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            window.activateWindow()
            window.setFocus()
            self.app.processEvents()
            QTest.keyClick(window, Qt.Key.Key_D)
            self.assertEqual(player.count, 1)
            QTest.mouseClick(window.delete_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertIsNone(Library(root).state['trigger'])
            QTest.keyClick(window, Qt.Key.Key_D)
            inputs.local_activate()
            self.assertEqual(player.count, 1)
            self.close_window(window)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS virtual key normalization')
    def test_physical_c_with_empty_uppercase_and_ime_text(self):
        from pynput import keyboard
        from sound_trigger.mac_keyboard import create_listener
        from Quartz import CGEventCreateKeyboardEvent, CGEventKeyboardSetUnicodeString
        player = Player(audio_backend=FakeBackend())
        player.open()
        player.set_ready(np.ones((16, 2), np.float32))
        inputs = Inputs(player.trigger)
        inputs.listener = create_listener(keyboard, on_press=inputs.press, on_release=inputs.release)
        inputs.keyboard = Mock()  # Do not install an OS monitor in tests.
        inputs.configure(dict(kind='keyboard', key='c'))
        for expected, text in enumerate(('', 'C', 'そ'), 1):
            event = CGEventCreateKeyboardEvent(None, 8, True)
            CGEventKeyboardSetUnicodeString(event, len(text), text)
            raw = inputs.listener._event_to_key(event)
            inputs.press(raw)
            inputs.press(raw)
            # Different key-up text must still release the same physical key.
            inputs.release(keyboard.KeyCode.from_vk(8))
            self.assertEqual(player.count, expected)
            self.assertIsNotNone(player.active)
        inputs.configure(None)
        inputs.press(keyboard.KeyCode.from_vk(8))
        self.assertEqual(player.count, 3)
        player.close()
