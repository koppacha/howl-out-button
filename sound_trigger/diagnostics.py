"""Offline bundle self-check using generated audio; never opens input hooks."""
import json
import os
import sys
from pathlib import Path
import tempfile
import traceback


def diagnose(report):
    result = {}
    try:
        import av
        import numpy as np
        import sounddevice
        from PySide6.QtCore import qVersion
        from PySide6.QtMultimedia import QMediaDevices
        result['output_observer_available'] = QMediaDevices is not None
        from .core import decode, render
        if sys.platform == 'darwin':
            from pynput import keyboard
            from Quartz import CGEventCreateKeyboardEvent, CGEventKeyboardSetUnicodeString
            from .mac_keyboard import create_listener
            from .inputs import Inputs
            hits = []
            inputs = Inputs(lambda: hits.append(1))
            inputs.listener = create_listener(keyboard, on_press=inputs.press, on_release=inputs.release)
            inputs.keyboard = lambda: None  # Do not start a real input monitor.
            inputs.configure(dict(kind='keyboard', key='c'))
            for text in ('', 'C', 'そ'):
                event = CGEventCreateKeyboardEvent(None, 8, True)
                CGEventKeyboardSetUnicodeString(event, len(text), text)
                key = inputs.listener._event_to_key(event)
                inputs.press(key)
                inputs.press(key)
                inputs.release(key)
            if len(hits) != 3:
                raise RuntimeError('Physical C key normalization check failed')
            inputs.set_foreground(True)
            inputs.global_activate()
            inputs.local_activate()
            if len(hits) != 4:
                raise RuntimeError('Foreground/background routing check failed')
            result['keyboard'] = dict(c_variants=3, single_foreground_press=1, event_tap_started=False)
        rate = 24000
        wave = (.1 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)).astype('float32')
        with tempfile.TemporaryDirectory(prefix='soundtrigger-check-') as tmp:
            os.environ.setdefault('NUMBA_CACHE_DIR', str(Path(tmp) / 'cache'))
            for extension, codec in [('wav', 'pcm_s16le'), ('mp3', 'libmp3lame'), ('m4a', 'aac')]:
                source = Path(tmp) / f'synthetic.{extension}'
                with av.open(str(source), 'w') as container:
                    stream = container.add_stream(codec, rate=rate)
                    stream.layout = 'stereo'
                    frame = av.AudioFrame.from_ndarray(np.stack([wave, wave]), format='fltp', layout='stereo')
                    frame.sample_rate = rate
                    for packet in stream.encode(frame):
                        container.mux(packet)
                    for packet in stream.encode(None):
                        container.mux(packet)
                data = decode(source, rate)
                processed = render(data, rate, 3, 1.25)
                if not len(processed) or not np.isfinite(processed).all():
                    raise RuntimeError('Invalid processed audio')
                result[extension] = dict(decoded_frames=len(data), processed_frames=len(processed))
        result.update(ok=True, qt=qVersion(), av=av.__version__)
    except Exception:
        result.update(ok=False, error=traceback.format_exc())
    Path(report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1
