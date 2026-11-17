from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import threading
import uuid

import numpy as np

EXTENSIONS = {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.aiff', '.aif'}
MAX_SECONDS = 120  # Bound decoded memory and offline processing time.


class Library:
    def __init__(self, root: Path):
        self.root = root
        self.audio = root / 'audio'
        self.audio.mkdir(parents=True, exist_ok=True)
        self.path = root / 'settings.json'
        self.state = dict(sounds=[], selected=None, trigger=None, pitch=0., speed=1., count=0, output=None, count_hidden=False)
        self.warning = ''
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(state, dict):
                    raise ValueError('設定がオブジェクトではありません')
                self.state.update({k: state[k] for k in self.state if k in state})
                if not isinstance(self.state['sounds'], list):
                    raise ValueError('音源リストが不正です')
                for item in self.state['sounds']:
                    if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ('id', 'name', 'file')):
                        raise ValueError('音源情報が不正です')
                    self.file(item)
                binding = self.state['trigger']
                if binding is not None:
                    if not isinstance(binding, dict) or binding.get('kind') not in ('keyboard', 'midi'):
                        raise ValueError('トリガー情報が不正です')
                    if binding['kind'] == 'keyboard' and not isinstance(binding.get('key'), str):
                        raise ValueError('キー情報が不正です')
                    if binding['kind'] == 'midi' and (not isinstance(binding.get('port'), str)
                            or binding.get('event') not in ('note', 'cc')
                            or not isinstance(binding.get('number'), int) or not 0 <= binding['number'] <= 127
                            or not isinstance(binding.get('channel'), int) or not 0 <= binding['channel'] <= 15):
                        raise ValueError('MIDI情報が不正です')
                self.state['count'] = max(0, int(self.state['count']))
                if not isinstance(self.state['count_hidden'], bool):
                    raise ValueError('カウンター表示設定が不正です')
                if not all(math.isfinite(float(self.state[k])) for k in ('pitch', 'speed')):
                    raise ValueError('再生設定が有限数ではありません')
                self.state['pitch'] = min(12., max(-12., float(self.state['pitch'])))
                self.state['speed'] = min(2., max(.5, float(self.state['speed'])))
                output = self.state['output']
                if output is not None and (not isinstance(output, dict)
                        or not all(isinstance(output.get(k), str) for k in ('name', 'hostapi'))):
                    raise ValueError('音声出力情報が不正です')
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                # Preserve damaged settings for recovery; never silently overwrite them.
                backup = self.path.with_name(f'settings-invalid-{uuid.uuid4().hex[:8]}.json')
                shutil.copy2(self.path, backup)
                self.state = dict(sounds=[], selected=None, trigger=None, pitch=0., speed=1., count=0, output=None, count_hidden=False)
                self.warning = f'設定を読み込めませんでした。元の設定は {backup.name} に退避しました。'

    def file(self, item):
        name = item['file']
        if not isinstance(name, str) or Path(name).name != name or name in ('', '.', '..'):
            raise ValueError('保存音源のパスが不正です')
        path = self.audio / name
        if not path.resolve().is_relative_to(self.audio.resolve()):
            raise ValueError('保存フォルダ外の音源は参照できません')
        return path

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.path)

    def add(self, source: Path):
        if source.suffix.lower() not in EXTENSIONS:
            raise ValueError('非対応の拡張子です')
        token = uuid.uuid4().hex
        item = dict(id=token, name=source.name, file=token + source.suffix.lower())
        shutil.copy2(source, self.file(item))
        previous = self.state['selected']
        self.state['sounds'].append(item)
        self.state['selected'] = token
        try:
            self.save()
        except OSError:
            self.state['sounds'].remove(item)
            self.state['selected'] = previous
            self.file(item).unlink(missing_ok=True)
            raise
        return item

    def remove(self, token):
        item = next(x for x in self.state['sounds'] if x['id'] == token)
        previous, index = self.state['selected'], self.state['sounds'].index(item)
        self.state['sounds'].remove(item)
        if self.state['selected'] == token:
            self.state['selected'] = None
        try:
            self.save()
        except OSError:
            self.state['sounds'].insert(index, item)
            self.state['selected'] = previous
            raise
        self.file(item).unlink(missing_ok=True)


def decode(path: Path, rate: int):
    """Decode locally with bundled FFmpeg libraries; no external executable."""
    import av
    chunks = []
    total = 0
    formats = {'.wav': 'wav', '.mp3': 'mp3', '.m4a': 'mov', '.aac': 'aac', '.flac': 'flac',
               '.ogg': 'ogg', '.aiff': 'aiff', '.aif': 'aiff'}
    if path.suffix.lower() not in formats:
        raise ValueError('非対応の拡張子です')
    with av.open(str(path), format=formats[path.suffix.lower()], options={'protocol_whitelist': 'file'}) as container:
        if not container.streams.audio:
            raise ValueError('音声トラックがありません')
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=rate)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                data = converted.to_ndarray().T.copy()
                chunks.append(data)
                total += len(data)
                if total > MAX_SECONDS * rate:
                    raise ValueError(f'音源は {MAX_SECONDS} 秒以内にしてください')
        for converted in resampler.resample(None):
            chunks.append(converted.to_ndarray().T.copy())
    if not chunks:
        raise ValueError('音源が空です')
    result = np.concatenate(chunks)
    if len(result) > MAX_SECONDS * rate:
        raise ValueError(f'音源は {MAX_SECONDS} 秒以内にしてください')
    return result


def render(data, rate, pitch, speed):
    if pitch or speed != 1.:
        import librosa
        y = data.T
        if pitch:
            y = librosa.effects.pitch_shift(y, sr=rate, n_steps=pitch)
        if speed != 1.:
            y = librosa.effects.time_stretch(y, rate=speed)
        data = y.T
    return np.ascontiguousarray(np.clip(data, -1, 1), dtype=np.float32)


class Player:
    """One persistent output stream. Retriggers restart the current sound."""
    def __init__(self, stream_factory=None, audio_backend=None):
        self.lock = threading.Lock()
        self.ready = None
        self.active = None
        self.position = 0
        self.count = 0
        self.underruns = 0
        self.stream = None
        self.rate = 48000
        self.stream_factory = stream_factory
        self.audio_backend = audio_backend
        self.device_name = '未接続'
        self.channels = 2

    def backend(self):
        if self.audio_backend is None:
            import sounddevice
            self.audio_backend = sounddevice
        return self.audio_backend

    def list_outputs(self, refresh=False):
        sd = self.backend()
        if refresh:
            # PortAudio caches device IDs/defaults. All our streams must be
            # closed before its documented expert-mode reinitialization.
            self.close()
            sd._terminate()
            sd._initialize()
        apis = sd.query_hostapis()
        return [dict(index=i, name=d['name'], hostapi=apis[d['hostapi']]['name'])
                for i, d in enumerate(sd.query_devices()) if d['max_output_channels'] > 0]

    def open(self, device=None):
        self.close()
        sd = self.backend()
        factory = self.stream_factory or sd.OutputStream
        info = sd.query_devices(device, kind='output')
        self.rate = int(info['default_samplerate'])
        self.channels = min(2, int(info['max_output_channels']))
        if self.channels < 1:
            raise ValueError('このデバイスには音声出力がありません')
        stream = factory(device=info['index'], samplerate=self.rate, channels=self.channels,
                         dtype='float32', blocksize=0, latency='low', callback=self.callback)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self.stream = stream
        self.device_name = info['name']

    def set_ready(self, audio):
        with self.lock:
            self.ready = audio
            self.active = None
            self.position = 0

    def trigger(self):
        with self.lock:
            self.count += 1
            if self.ready is not None and self.stream is not None:
                self.active = self.ready
                self.position = 0
                return True
            return False

    def stop(self):
        with self.lock:
            self.active = None

    def reset_count(self):
        with self.lock:
            self.count = 0

    def callback(self, outdata, frames, timing, status):
        outdata.fill(0)
        with self.lock:
            if status:
                self.underruns += 1
            if self.active is None:
                return
            n = min(frames, len(self.active) - self.position)
            block = self.active[self.position:self.position + n]
            if outdata.shape[1] == 1:
                outdata[:n, 0] = (block[:, 0] + block[:, 1]) * .5
            else:
                outdata[:n] = block
            self.position += n
            if self.position == len(self.active):
                self.active = None

    def close(self):
        with self.lock:
            stream = self.stream
            self.stream = None
            self.active = None
            self.ready = None
            self.position = 0
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
