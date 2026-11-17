"""Keep HIToolbox input-source calls on the macOS main thread.

pynput 1.x normally prepares its keyboard layout inside Listener._run().
macOS can terminate the process when that worker calls TIS APIs. Prepare a
self-contained (keyboard type, layout bytes) snapshot on the main thread,
then use pynput's event-tap loop without its worker-side layout setup.
"""
import threading

PERMISSION_MESSAGE = (
    'macOSがキーボード監視の開始を許可しませんでした。'
    'システム設定 → プライバシーとセキュリティ → 入力監視でHowl-Out-Buttonを許可し、'
    'アプリを再起動してください。ONでも更新前のアプリへの許可が残ることがあります。'
    'その場合は、いま起動しているアプリを一覧に追加し直してください。'
)


def require_permission():
    """Advisory only: preflight can disagree with actual event-tap access.

    Do not refuse a listener or show an OS prompt during application startup.
    The listen-only event tap itself is the authoritative access check.
    """
    import Quartz
    return bool(Quartz.CGPreflightListenEventAccess())


def _listener_type(base, mixin, context_factory, invalidate_tap=None, character_map=None):
    class MainThreadLayoutListener(base):
        def __init__(self, *args, **kwargs):
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError('macOSのキーボード初期化はメインスレッドで実行してください')
            with context_factory() as context:
                self._layout_snapshot = context
                self._character_keys = character_map(context) if character_map else {}
            super().__init__(*args, **kwargs)
            self._startup_ready = threading.Event()
            self._startup_error = None
            self._event_tap = None

        def canonical(self, key):
            # Raw HID events may carry no Unicode text; IMEs may carry a
            # different character. Keep their physical code before the base
            # canonicalizer discards it from character-bearing events.
            vk = getattr(key, 'vk', None)
            char = getattr(key, 'char', None)
            if vk is not None:
                return type(key).from_vk(vk)
            if char and char.lower() in self._character_keys:
                return type(key).from_vk(self._character_keys[char.lower()])
            return super().canonical(key)

        def _mark_ready(self):
            super()._mark_ready()
            self._startup_ready.set()

        def _create_event_tap(self):
            tap = super()._create_event_tap()
            self._event_tap = tap
            if tap is None:
                self._startup_error = PermissionError(PERMISSION_MESSAGE)
            return tap

        def _run(self):
            self._context = self._layout_snapshot
            try:
                # Deliberately bypass keyboard.Listener._run(), which calls
                # keycode_context() on this worker and can cause SIGTRAP.
                mixin._run(self)
            except Exception as exc:
                self._startup_error = exc
            finally:
                self._context = None
                self._startup_ready.set()
                if self._event_tap is not None and invalidate_tap is not None:
                    invalidate_tap(self._event_tap)
                    self._event_tap = None

        def wait(self):
            if not self._startup_ready.wait(3):
                self.stop()
                raise RuntimeError('キーボード監視の開始がタイムアウトしました')
            if self._startup_error is not None:
                self.stop()
                raise self._startup_error

    return MainThreadLayoutListener


def create_listener(keyboard, check_access=False, **kwargs):
    from pynput._util.darwin import ListenerMixin, keycode_context, keycode_to_string
    import Quartz
    key_mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
    if check_access:
        # A mixed mask can succeed after macOS silently strips key events.
        # Probe key-down/up ONLY, without adding it to a run loop or reading input.
        probe = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly, key_mask, lambda proxy, kind, event, ref: event, None)
        if probe is None:
            raise PermissionError(PERMISSION_MESSAGE)
        Quartz.CFMachPortInvalidate(probe)

    def character_map(context):
        result = {}
        for vk in range(128):
            char = keycode_to_string(context, vk).lower()
            if len(char) == 1 and char.isprintable():
                result.setdefault(char, vk)
        return result

    cls = _listener_type(keyboard.Listener, ListenerMixin, keycode_context, Quartz.CFMachPortInvalidate, character_map)
    cls._EVENTS = key_mask | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
    return cls(**kwargs)
