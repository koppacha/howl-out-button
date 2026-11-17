"""One keyboard binding, with separate foreground and global routes."""
import sys
import threading


class Inputs:
    def __init__(self, trigger):
        self.trigger = trigger
        self.lock = threading.RLock()
        self.binding = None
        self.hotkey = None
        self.listener = None
        self.pressed = set()
        self.closed = False
        self.suspended = False
        self.foreground = False
        self.last_error = ''
        self.modifier_keys = set()
        self.allowed_modifiers = set()

    def set_foreground(self, foreground):
        self.foreground = bool(foreground)

    def global_activate(self):
        if (self.binding and not self.closed and not self.suspended and not self.foreground
                and not ((self.pressed & self.modifier_keys) - self.allowed_modifiers)):
            self.trigger()

    def local_activate(self):
        # Qt emits this only inside this application. Global callbacks are
        # suppressed while the app is foreground, so one press counts once.
        if self.binding and not self.closed and not self.suspended:
            self.trigger()

    def keyboard(self):
        if self.closed:
            raise RuntimeError('入力処理は終了しています')
        if self.listener and self.listener.is_alive():
            return
        self.stop_listener()
        from pynput import keyboard
        if sys.platform == 'darwin':
            from .mac_keyboard import create_listener
            listener = create_listener(keyboard, on_press=self.press, on_release=self.release, check_access=True)
        else:
            listener = keyboard.Listener(on_press=self.press, on_release=self.release)
        self.listener = listener
        try:
            listener.start()
            listener.wait()
        except Exception:
            self.stop_listener()
            raise

    def configure(self, binding):
        if self.closed:
            raise RuntimeError('入力処理は終了しています')
        if binding is not None and binding.get('kind') != 'keyboard':
            raise ValueError('登録できるのはキーボードのホットキー1件です')
        if binding is None:
            with self.lock:
                self.binding = self.hotkey = None
                self.last_error = ''
            self.stop_listener()
            return True
        from pynput import keyboard
        keys = keyboard.HotKey.parse(binding['key'])
        with self.lock:
            self.binding = binding
            self.hotkey = None
        try:
            self.keyboard()
            # Bindings and incoming events must use the same representation.
            # On macOS this is the physical virtual-key code, not IME text.
            normalized = [self.listener.canonical(key) for key in keys]
            with self.lock:
                self.modifier_keys = {keyboard.Key.ctrl, keyboard.Key.alt, keyboard.Key.shift, keyboard.Key.cmd}
                self.allowed_modifiers = set(normalized) & self.modifier_keys
                if len(binding['key']) == 1 and binding['key'].isalpha():
                    self.allowed_modifiers.add(keyboard.Key.shift)
                self.hotkey = keyboard.HotKey(normalized, self.global_activate)
                self.last_error = ''
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False  # In-app Qt shortcuts remain usable without OS access.

    def press(self, key):
        with self.lock:
            if self.closed or key is None or self.listener is None:
                return
            key = self.listener.canonical(key)
            if key in self.pressed:
                return
            self.pressed.add(key)
            if not self.suspended and self.hotkey:
                self.hotkey.press(key)

    def release(self, key):
        with self.lock:
            if self.closed or key is None or self.listener is None:
                return
            key = self.listener.canonical(key)
            self.pressed.discard(key)
            if self.hotkey:
                self.hotkey.release(key)

    def stop_listener(self):
        listener, self.listener = self.listener, None
        self.pressed.clear()
        if listener:
            listener.stop()
            if listener.is_alive():
                listener.join(timeout=.5)

    def close(self):
        self.closed = True
        self.stop_listener()

    def health_error(self):
        if self.closed or self.binding is None:
            return ''
        if self.last_error:
            return self.last_error
        if self.listener is None or not self.listener.is_alive():
            return 'アプリ外のホットキー監視が停止しています。登録し直すと再接続できます。'
        return ''
