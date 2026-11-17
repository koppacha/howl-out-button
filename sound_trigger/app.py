from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sys

from platformdirs import user_data_path
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QLockFile
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QFileDialog,
    QMessageBox, QDoubleSpinBox, QKeySequenceEdit, QComboBox, QGroupBox, QDialog, QDialogButtonBox)

from .core import Library, Player, decode, render, EXTENSIONS
from .inputs import Inputs
from .hotkeys import from_sequence, to_sequence
from . import __version__, APP_NAME, DATA_NAME


class Bridge(QObject):
    result = Signal(int, object, str)
    error = Signal(str)


class OutputCombo(QComboBox):
    """Keep the popup model stable until the user finishes choosing."""
    popupClosed = Signal()

    def __init__(self):
        super().__init__()
        self.popup_open = False
        self.popup_dialog = None
        self.popup_list = None

    def showPopup(self):
        if self.popup_dialog is not None:
            self.popup_dialog.raise_()
            return
        self.popup_open = True
        dialog = QDialog(self.window())
        self.popup_dialog = dialog
        dialog.setWindowTitle('音声出力を選択')
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.resize(560, 320)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('出力先を選び、「決定」を押してください。'))
        choices = QListWidget()
        self.popup_list = choices
        choices.setAccessibleName('音声出力の候補')
        choices.addItems([self.itemText(i) for i in range(self.count())])
        choices.setCurrentRow(self.currentIndex())
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('決定')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('キャンセル')
        layout.addWidget(buttons)

        def choose():
            if self.popup_dialog is not dialog:
                return
            index = choices.currentRow()
            if index >= 0:
                self.setCurrentIndex(index)
                self.activated.emit(index)
                dialog.accept()

        def finished():
            super(OutputCombo, self).hidePopup()
            self.popup_open = False
            self.popup_dialog = self.popup_list = None
            self.popupClosed.emit()

        buttons.accepted.connect(choose)
        buttons.rejected.connect(dialog.reject)
        choices.itemActivated.connect(lambda item: choose())
        dialog.finished.connect(finished)
        dialog.open()
        choices.setFocus()

    def hidePopup(self):
        super().hidePopup()
        if self.popup_dialog is not None:
            self.popup_dialog.reject()


class Window(QMainWindow):
    def __init__(self, library=None, player=None, inputs_factory=Inputs, monitor_outputs=True):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} {__version__}')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(760, 720)
        self.setAcceptDrops(True)
        self.library = library or Library(user_data_path(DATA_NAME, appauthor=False))
        self.player = player or Player()
        self.player.count = self.library.state['count']
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='audio-prepare')
        self.pending = None
        self.generation = 0
        self.closing = False
        self.refreshing_outputs = False
        self.pending_output_refresh = False
        self.pending_output_reinitialize = False
        self.trigger_active = False
        self.bridge = Bridge()
        self.bridge.result.connect(self.prepared)
        self.bridge.error.connect(self.show_error)
        self.inputs = inputs_factory(self.player.trigger)
        self.hotkey_dialog = None
        self.local_shortcut = QShortcut(self)
        self.local_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.local_shortcut.setAutoRepeat(False)
        self.local_shortcut.activated.connect(self.inputs.local_activate)
        QApplication.instance().applicationStateChanged.connect(self.application_state_changed)
        self.application_state_changed(QApplication.instance().applicationState())
        self.media_devices = QMediaDevices(self) if monitor_outputs else None
        host = QWidget()
        self.setCentralWidget(host)
        layout = QVBoxLayout(host)
        title = QLabel(APP_NAME)
        title.setStyleSheet('font-size: 30px; font-weight: 700;')
        layout.addWidget(title)
        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.button(row, '音源を追加', self.choose_files)
        self.button(row, '選択音源を削除', self.remove)
        layout.addLayout(row)
        group = QGroupBox('再生設定（選択中の音源に共通）')
        fx = QHBoxLayout(group)
        fx.addWidget(QLabel('ピッチ'))
        self.pitch = QDoubleSpinBox()
        self.pitch.setRange(-12, 12)
        self.pitch.setSingleStep(1)
        self.pitch.setSuffix(' 半音')
        self.pitch.setValue(self.library.state['pitch'])
        fx.addWidget(self.pitch)
        fx.addWidget(QLabel('速度'))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(.5, 2)
        self.speed.setSingleStep(.05)
        self.speed.setSuffix(' ×')
        self.speed.setValue(self.library.state['speed'])
        fx.addWidget(self.speed)
        layout.addWidget(group)
        output_group = QGroupBox('音声出力')
        output_row = QHBoxLayout(output_group)
        self.output_choice = OutputCombo()
        output_row.addWidget(self.output_choice, 1)
        self.button(output_row, '出力を更新／再接続', lambda: self.refresh_outputs())
        layout.addWidget(output_group)
        binding = QGroupBox('ホットキー')
        keys = QVBoxLayout(binding)
        self.binding_label = QLabel()
        self.binding_label.setWordWrap(True)
        keys.addWidget(self.binding_label)
        row = QHBoxLayout()
        self.register_button = self.button(row, 'ホットキーを登録', self.register_hotkey)
        self.delete_button = self.button(row, 'ホットキーを削除', lambda: self.set_binding(None))
        keys.addLayout(row)
        layout.addWidget(binding)
        r = QHBoxLayout()
        self.play_button = self.button(r, '▶ 再生', self.player.trigger)
        self.button(r, '■ 停止', self.player.stop)
        layout.addLayout(r)
        self.counter = QLabel()
        self.counter.setStyleSheet('font-size: 24px; font-weight: 600;')
        counter_row = QHBoxLayout()
        counter_row.addWidget(self.counter, 1)
        self.reset_count_button = self.button(counter_row, 'リセット', self.reset_count)
        self.hide_count_button = self.button(counter_row, '非表示', self.toggle_counter)
        layout.addLayout(counter_row)
        self.update_counter_visibility()
        self.status = QLabel('音源を追加してください')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.device_status = QLabel()
        self.device_status.setWordWrap(True)
        layout.addWidget(self.device_status)
        layout.addWidget(QLabel('音源は120秒まで。再生中に押すと先頭から再生。長押しリピートは無効。'))
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(350)
        self.debounce.timeout.connect(self.prepare)
        self.pitch.valueChanged.connect(self.effects_changed)
        self.speed.valueChanged.connect(self.effects_changed)
        self.list.currentItemChanged.connect(self.selection_changed)
        self.output_choice.activated.connect(self.output_changed)
        self.output_choice.popupClosed.connect(self.flush_pending_output_refresh)
        self.output_debounce = QTimer(self)
        self.output_debounce.setSingleShot(True)
        self.output_debounce.setInterval(350)
        self.output_debounce.timeout.connect(self.refresh_outputs)
        if self.media_devices is not None:
            self.media_devices.audioOutputsChanged.connect(self.schedule_output_refresh)
        self.refresh_list()
        self.output_signature = self.default_output_signature()
        self.refresh_outputs(reinitialize=False, prepare=False)
        self.update_binding_label()
        # A permission failure must not block startup behind a modal dialog.
        QTimer.singleShot(0, self.activate_saved_trigger)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.tick()
        self.prepare()
        if self.library.warning:
            self.bridge.error.emit(self.library.warning)

    @staticmethod
    def button(layout, text, action):
        button = QPushButton(text)
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def show_error(self, text):
        if not self.closing:
            QMessageBox.warning(self, APP_NAME, text)

    def persist(self):
        try:
            self.library.save()
        except OSError as exc:
            self.status.setText(f'設定を保存できません: {exc}')

    def default_output_signature(self):
        if self.media_devices is None:
            return None
        device = self.media_devices.defaultAudioOutput()
        devices = tuple(sorted((bytes(d.id()), d.description())
                               for d in self.media_devices.audioOutputs()))
        return bytes(device.id()), device.description(), devices

    def schedule_output_refresh(self):
        if self.closing or self.refreshing_outputs:
            return
        signature = self.default_output_signature()
        if signature != self.output_signature:
            self.output_signature = signature
            self.output_debounce.start()

    def output_changed(self, index):
        choice = self.output_choice.itemData(index)
        # QComboBox emits activated while its popup may still be closing.
        def apply():
            if self.closing:
                return
            self.library.state['output'] = choice
            self.persist()
            self.refresh_outputs(reinitialize=False)
        QTimer.singleShot(0, apply)

    def flush_pending_output_refresh(self):
        if self.pending_output_refresh and not self.closing:
            reset = self.pending_output_reinitialize
            self.pending_output_refresh = self.pending_output_reinitialize = False
            QTimer.singleShot(0, lambda: None if self.closing else self.refresh_outputs(reinitialize=reset))

    def refresh_outputs(self, reinitialize=True, prepare=True):
        if self.closing:
            return
        if self.output_choice.popup_open or self.refreshing_outputs:
            self.pending_output_refresh = True
            self.pending_output_reinitialize |= reinitialize
            return
        self.refreshing_outputs = True
        self.output_debounce.stop()
        self.debounce.stop()
        self.generation += 1
        self.player.set_ready(None)
        self.play_button.setEnabled(False)
        selected = self.library.state['output']
        self.output_choice.blockSignals(True)
        target = None
        found = selected is None
        rows = [('OSの既定出力に自動追従', None)]
        selected_index = 0
        try:
            for device in self.player.list_outputs(refresh=reinitialize):
                identity = dict(name=device['name'], hostapi=device['hostapi'])
                rows.append((f"{device['name']} ({device['hostapi']})", identity))
                if not found and identity == selected:
                    found = True
                    target = device['index']
                    selected_index = len(rows) - 1
            if not found:
                rows.append((f"未接続: {selected['name']}", selected))
                selected_index = len(rows) - 1
                self.player.close()
                raise RuntimeError('選択した出力が見つかりません。機器を接続するか、別の出力を選択してください')
            self.player.open(target)
            self.device_status.setText(f'出力: {self.player.device_name} / {self.player.rate} Hz / デバイス報告レイテンシ {self.player.stream.latency * 1000:.1f} ms')
        except Exception as exc:
            if selected is not None and not found:
                label = f"未接続: {selected['name']}"
                if not any(data == selected for _, data in rows):
                    rows.append((label, selected))
                    selected_index = len(rows) - 1
            self.device_status.setText(f'音声出力を開けません: {exc}。「出力を更新／再接続」で再試行できます。')
        finally:
            old_rows = [(self.output_choice.itemText(i), self.output_choice.itemData(i))
                        for i in range(self.output_choice.count())]
            if rows != old_rows:
                self.output_choice.clear()
                for text, data in rows:
                    self.output_choice.addItem(text, data)
            self.output_choice.setCurrentIndex(selected_index)
            self.output_choice.blockSignals(False)
            self.refreshing_outputs = False
        self.output_signature = self.default_output_signature()
        if prepare:
            self.prepare()

    def refresh_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for sound in self.library.state['sounds']:
            item = QListWidgetItem(sound['name'])
            item.setData(Qt.ItemDataRole.UserRole, sound['id'])
            self.list.addItem(item)
            if sound['id'] == self.library.state['selected']:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '音源を追加', '', 'Audio (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.aiff *.aif)')
        self.add_files(paths)

    def add_files(self, paths):
        for path in paths:
            try:
                # Decode on the worker after copying; malformed files remain removable.
                self.library.add(Path(path))
            except (OSError, ValueError) as exc:
                self.show_error(f'{Path(path).name}: {exc}')
        self.refresh_list()
        self.prepare()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in EXTENSIONS for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def remove(self):
        item = self.list.currentItem()
        if item is None:
            return
        self.player.set_ready(None)
        try:
            self.library.remove(item.data(Qt.ItemDataRole.UserRole))
        except OSError as exc:
            self.show_error(str(exc))
        self.refresh_list()
        self.prepare()

    def selection_changed(self, item, previous):
        self.library.state['selected'] = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.persist()
        self.prepare()

    def effects_changed(self):
        self.library.state.update(pitch=self.pitch.value(), speed=self.speed.value())
        self.persist()
        # Invalidate immediately, including results arriving during the debounce.
        self.generation += 1
        self.player.set_ready(None)
        self.play_button.setEnabled(False)
        self.status.setText('設定変更を反映しています…')
        self.debounce.start()

    def prepare(self):
        self.generation += 1
        version = self.generation
        self.player.set_ready(None)
        self.play_button.setEnabled(False)
        if self.pending:
            self.pending.cancel()
        sound = next((x for x in self.library.state['sounds'] if x['id'] == self.library.state['selected']), None)
        if not sound:
            self.status.setText('再生する音源を選択してください')
            return
        path = self.library.file(sound)
        rate, pitch, speed = self.player.rate, self.pitch.value(), self.speed.value()
        self.status.setText('音源を準備中… 初回のピッチ／速度変換には時間がかかることがあります')
        self.pending = self.pool.submit(lambda: render(decode(path, rate), rate, pitch, speed))
        def done(future):
            if future.cancelled() or self.closing:
                return
            try:
                data = future.result()
            except Exception as exc:
                self.bridge.result.emit(version, None, str(exc))
            else:
                self.bridge.result.emit(version, data, '')
        self.pending.add_done_callback(done)

    def prepared(self, version, data, error):
        if version != self.generation or self.closing:
            return
        if error:
            self.status.setText(f'音源を準備できません: {error}')
        else:
            self.player.set_ready(data)
            self.play_button.setEnabled(self.player.stream is not None)
            if self.player.stream is None:
                self.status.setText('音源は準備済みですが、音声出力が停止しています。出力先を確認してください。')
            else:
                self.status.setText(f'準備完了 · {len(data) / self.player.rate:.2f} 秒 · 押すと即時再生')

    def application_state_changed(self, state):
        self.inputs.set_foreground(state == Qt.ApplicationState.ApplicationActive)

    def register_hotkey(self):
        if self.hotkey_dialog is not None:
            self.hotkey_dialog.raise_()
            return
        dialog = QDialog(self)
        self.hotkey_dialog = dialog
        dialog.setWindowTitle('ホットキーを登録')
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('登録するキー、またはキーの組み合わせを押してください。'))
        editor = QKeySequenceEdit()
        editor.setMaximumSequenceLength(1)
        saved = self.library.state['trigger']
        if saved and saved.get('kind') == 'keyboard':
            editor.setKeySequence(to_sequence(saved['key']))
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('登録')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('キャンセル')
        layout.addWidget(buttons)
        self.inputs.suspended = True
        self.local_shortcut.setEnabled(False)

        def save():
            try:
                key = from_sequence(editor.keySequence())
            except ValueError as exc:
                self.show_error(str(exc))
                return
            if self.set_binding(dict(kind='keyboard', key=key)):
                dialog.accept()

        def finished():
            self.hotkey_dialog = None
            self.inputs.suspended = False
            self.local_shortcut.setEnabled(self.library.state['trigger'] is not None)

        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        dialog.finished.connect(finished)
        dialog.open()
        editor.setFocus()

    def set_binding(self, binding):
        try:
            global_active = self.inputs.configure(binding)
        except Exception as exc:
            self.show_error(f'ホットキーを登録できません: {exc}')
            return False
        self.library.state['trigger'] = binding
        self.trigger_active = binding is not None and global_active is not False
        self.persist()
        sequences = [to_sequence(binding['key'])] if binding else []
        if binding and len(binding['key']) == 1 and binding['key'].isalpha():
            sequences.append(to_sequence('<shift>+' + binding['key']))
        self.local_shortcut.setKeys(sequences)
        self.local_shortcut.setEnabled(binding is not None and self.hotkey_dialog is None)
        self.update_binding_label()
        return True

    def activate_saved_trigger(self):
        if self.closing:
            return
        saved = self.library.state['trigger']
        if saved and saved.get('kind') != 'keyboard':
            saved = None  # MIDI registration was removed by the simplified UI.
        self.set_binding(saved)

    def update_binding_label(self):
        binding = self.library.state['trigger']
        if not binding or binding.get('kind') != 'keyboard':
            text = '未登録'
            detail = ''
        else:
            text = to_sequence(binding['key']).toString(QKeySequence.SequenceFormat.NativeText)
            error = self.inputs.health_error()
            detail = error if isinstance(error, str) else ''
            if detail:
                text += '（アプリ内のみ有効）'
        self.binding_label.setText(f'保存済みホットキー: {text}')
        self.binding_label.setToolTip(detail)
        self.delete_button.setEnabled(binding is not None)

    def reset_count(self):
        self.player.reset_count()
        self.library.state['count'] = 0
        self.counter.setText('押下回数  0')
        self.persist()

    def update_counter_visibility(self):
        hidden = self.library.state['count_hidden']
        self.counter.setVisible(not hidden)
        self.hide_count_button.setText('表示' if hidden else '非表示')

    def toggle_counter(self):
        self.library.state['count_hidden'] = not self.library.state['count_hidden']
        self.update_counter_visibility()
        self.persist()

    def tick(self):
        self.schedule_output_refresh()
        error = self.inputs.health_error()
        if self.trigger_active and isinstance(error, str) and error:
            self.trigger_active = False
            self.update_binding_label()
        count = self.player.count
        self.counter.setText(f'押下回数  {count:,}')
        if count != self.library.state['count']:
            self.library.state['count'] = count
            self.persist()
        if self.player.underruns:
            self.status.setText(f'音声バッファ警告 {self.player.underruns} 回。負荷や出力デバイスを確認してください。')

    def closeEvent(self, event):
        self.closing = True
        self.output_choice.hidePopup()
        if self.hotkey_dialog is not None:
            self.hotkey_dialog.reject()
        self.timer.stop()
        self.debounce.stop()
        self.output_debounce.stop()
        self.shutdown_errors = []
        # A failed device cleanup must not skip persistence or other cleanup.
        for action in (self.inputs.close, self.player.close, self.tick, self.persist,
                       lambda: self.pool.shutdown(wait=False, cancel_futures=True)):
            try:
                action()
            except Exception as exc:
                self.shutdown_errors.append(type(exc).__name__)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    root = user_data_path(DATA_NAME, appauthor=False)
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('NUMBA_CACHE_DIR', str(root / 'cache'))
    lock = QLockFile(str(root / 'app.lock'))
    if not lock.tryLock(0):
        QMessageBox.information(None, APP_NAME, 'すでに起動しています。既存のウィンドウを確認してください。')
        return 1
    window = Window()
    window.show()
    return app.exec()
