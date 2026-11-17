"""Translate one Qt shortcut chord to/from the saved portable key string."""
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

SPECIAL = {
    Qt.Key.Key_Space: 'space', Qt.Key.Key_Tab: 'tab', Qt.Key.Key_Escape: 'esc',
    Qt.Key.Key_Return: 'enter', Qt.Key.Key_Enter: 'enter', Qt.Key.Key_Backspace: 'backspace',
    Qt.Key.Key_Delete: 'delete', Qt.Key.Key_Up: 'up', Qt.Key.Key_Down: 'down',
    Qt.Key.Key_Left: 'left', Qt.Key.Key_Right: 'right', Qt.Key.Key_Home: 'home',
    Qt.Key.Key_End: 'end', Qt.Key.Key_PageUp: 'page_up', Qt.Key.Key_PageDown: 'page_down',
    Qt.Key.Key_Insert: 'insert',
}
QT_NAMES = dict(space='Space', tab='Tab', esc='Esc', enter='Return', backspace='Backspace',
                delete='Del', up='Up', down='Down', left='Left', right='Right', home='Home',
                end='End', page_up='PgUp', page_down='PgDown', insert='Ins')


def from_sequence(sequence):
    if sequence.isEmpty() or sequence.count() != 1:
        raise ValueError('キーまたはキーの組み合わせを1つ入力してください')
    chord = sequence[0]
    key = chord.key()
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        name = chr(int(key)).lower()
    elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F20:
        name = f'<f{int(key) - int(Qt.Key.Key_F1) + 1}>'
    elif key in SPECIAL:
        name = f'<{SPECIAL[key]}>'
    else:
        raise ValueError('英数字・ファンクションキー・方向キーなどを指定してください')
    modifiers = chord.keyboardModifiers()
    parts = []
    control = Qt.KeyboardModifier.MetaModifier if sys.platform == 'darwin' else Qt.KeyboardModifier.ControlModifier
    command = Qt.KeyboardModifier.ControlModifier if sys.platform == 'darwin' else Qt.KeyboardModifier.MetaModifier
    for flag, label in [(control, 'ctrl'),
                        (Qt.KeyboardModifier.AltModifier, 'alt'),
                        (Qt.KeyboardModifier.ShiftModifier, 'shift'),
                        (command, 'cmd')]:
        if modifiers & flag:
            parts.append(f'<{label}>')
    return '+'.join([*parts, name])


def to_sequence(key):
    modifier_names = dict(ctrl='Meta' if sys.platform == 'darwin' else 'Ctrl', alt='Alt', shift='Shift',
                          cmd='Ctrl' if sys.platform == 'darwin' else 'Meta')
    parts = []
    for part in key.split('+'):
        if part.startswith('<') and part.endswith('>'):
            name = part[1:-1]
            parts.append(modifier_names.get(name, QT_NAMES.get(name, name.upper())))
        else:
            parts.append(part.upper())
    return QKeySequence.fromString('+'.join(parts), QKeySequence.SequenceFormat.PortableText)
