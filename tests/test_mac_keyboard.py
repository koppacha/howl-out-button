from contextlib import contextmanager
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from sound_trigger.inputs import Inputs
from sound_trigger.mac_keyboard import _listener_type, require_permission


class FakeBase(threading.Thread):
    def __init__(self):
        super().__init__()
        self.stopped = False

    def run(self):
        self._run()

    def _run(self):
        raise AssertionError('Unsafe keyboard.Listener._run must be bypassed')

    def _mark_ready(self):
        pass

    def _create_event_tap(self):
        return object()

    def stop(self):
        self.stopped = True


class MacKeyboardTests(unittest.TestCase):
    def test_layout_on_main_thread_event_loop_on_worker(self):
        calls = []
        @contextmanager
        def context():
            calls.append(('layout', threading.current_thread()))
            yield ('keyboard-type', b'layout snapshot')
        class Mixin:
            def _run(self):
                calls.append(('events', threading.current_thread(), self._context))
                self._mark_ready()
        listener = _listener_type(FakeBase, Mixin, context)()
        listener.start()
        listener.wait()
        listener.join(2)
        self.assertFalse(listener.is_alive())
        self.assertIs(calls[0][1], threading.main_thread())
        self.assertIsNot(calls[1][1], threading.main_thread())
        self.assertEqual(calls[1][2], ('keyboard-type', b'layout snapshot'))

    def test_event_tap_failure_is_reported_without_hanging(self):
        @contextmanager
        def context():
            yield None
        class DeniedBase(FakeBase):
            def _create_event_tap(self):
                return None
        class Mixin:
            def _run(self):
                self._create_event_tap()
                self._mark_ready()
        listener = _listener_type(DeniedBase, Mixin, context)()
        listener.start()
        with self.assertRaises(PermissionError):
            listener.wait()
        listener.join(2)
        self.assertTrue(listener.stopped)

    def test_worker_initialization_failure_is_reported(self):
        @contextmanager
        def context():
            yield None
        class Mixin:
            def _run(self):
                raise RuntimeError('test startup error')
        listener = _listener_type(FakeBase, Mixin, context)()
        listener.start()
        with self.assertRaisesRegex(RuntimeError, 'test startup error'):
            listener.wait()
        listener.join(2)

    def test_layout_creation_off_main_thread_is_rejected(self):
        context = Mock()
        cls = _listener_type(FakeBase, object, context)
        errors = []
        def worker():
            try:
                cls()
            except RuntimeError as exc:
                errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)
        self.assertEqual(len(errors), 1)
        context.assert_not_called()

    def test_preflight_is_advisory_and_never_requests_access_at_startup(self):
        quartz = Mock()
        quartz.CGPreflightListenEventAccess.return_value = False
        quartz.CGRequestListenEventAccess.return_value = False
        with patch.dict(sys.modules, Quartz=quartz):
            self.assertFalse(require_permission())
        quartz.CGRequestListenEventAccess.assert_not_called()

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS backend')
    def test_false_preflight_does_not_block_a_working_event_tap(self):
        listener = Mock()
        inputs = Inputs(lambda: None)
        with patch('sound_trigger.mac_keyboard.require_permission', return_value=False) as preflight:
            with patch('sound_trigger.mac_keyboard.create_listener', return_value=listener):
                inputs.configure(dict(kind='keyboard', key='c'))
        preflight.assert_not_called()
        listener.start.assert_called_once()
        listener.wait.assert_called_once()
        self.assertEqual(inputs.binding['key'], 'c')

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS backend')
    def test_dead_listener_is_replaced_on_retry(self):
        old, new = Mock(), Mock()
        old.is_alive.return_value = False
        inputs = Inputs(lambda: None)
        inputs.listener = old
        with patch('sound_trigger.mac_keyboard.create_listener', return_value=new):
            inputs.configure(dict(kind='keyboard', key='c'))
        old.stop.assert_called_once()
        new.start.assert_called_once()
        self.assertIs(inputs.listener, new)

    def test_event_tap_is_invalidated_on_shutdown(self):
        @contextmanager
        def context():
            yield None
        class Mixin:
            def _run(self):
                self._create_event_tap()
                self._mark_ready()
        invalidate = Mock()
        listener = _listener_type(FakeBase, Mixin, context, invalidate)()
        listener.start()
        listener.wait()
        listener.join(2)
        invalidate.assert_called_once()

    @unittest.skipUnless(sys.platform in ('darwin', 'win32'), 'Supported desktop OS only')
    def test_c_registration_repeat_with_real_key_parser(self):
        # No hooks are installed; use the real parser/canonicalizer and synthetic keys.
        from pynput import keyboard
        hits = []
        inputs = Inputs(lambda: hits.append(1))
        inputs.listener = keyboard.Listener(on_press=lambda key: None)
        inputs.keyboard = Mock()  # Parser-only test; never start an OS hook.
        inputs.configure(dict(kind='keyboard', key='c'))
        key = keyboard.KeyCode.from_char('C')
        inputs.press(key)
        inputs.press(key)
        inputs.release(key)
        inputs.press(key)
        inputs.release(key)
        self.assertEqual(len(hits), 2)
        inputs.close()
        self.assertEqual(len(hits), 2)
