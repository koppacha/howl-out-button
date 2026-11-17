"""Run `python build.py` from a clone. No global package installation."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import venv
from sound_trigger import BINARY_NAME

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-install', action='store_true', help='既存の.venvでオフライン再ビルド')
    parser.add_argument('--codesign-identity', help='macOSの署名ID（更新後の権限維持には同じ正式な署名IDが必要）')
    options = parser.parse_args()
    if not (3, 11) <= sys.version_info[:2] <= (3, 13):
        raise SystemExit('Python 3.11–3.13 を使用してください。例: python3.13 build.py')
    os.chdir(ROOT)
    os.environ.setdefault('PYINSTALLER_CONFIG_DIR', str(ROOT / 'build' / '.pyinstaller-cache'))
    env = ROOT / '.venv'
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(env)
    def run(*args, env=None):
        subprocess.run([str(python), *args], check=True, env=env)
    if not options.no_install:
        run('-m', 'pip', 'install', '--upgrade', 'pip')
        run('-m', 'pip', 'install', '-r', 'requirements.txt')
    test_env = os.environ.copy()
    test_env['QT_QPA_PLATFORM'] = 'offscreen'
    run('-m', 'unittest', 'discover', '-s', 'tests', '-v', env=test_env)
    signing = ['--codesign-identity', options.codesign_identity] if options.codesign_identity else []
    run('-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
        '--name', BINARY_NAME, '--additional-hooks-dir', 'hooks', '--collect-all', 'numba',
        '--collect-all', 'llvmlite', '--collect-all', 'av', '--collect-all', 'sounddevice',
        '--add-data', 'LICENSE:.', '--add-data', 'THIRD_PARTY.md:.',
        '--hidden-import', 'pynput.keyboard._darwin' if sys.platform == 'darwin' else 'pynput.keyboard._win32',
        '--osx-bundle-identifier', 'io.soundtrigger.desktop', *signing, 'run.py')
    binary = ROOT / 'dist' / (f'{BINARY_NAME}.app/Contents/MacOS/{BINARY_NAME}'
                             if sys.platform == 'darwin' else f'{BINARY_NAME}/{BINARY_NAME}.exe')
    report = ROOT / 'build' / 'bundle-report.json'
    report.unlink(missing_ok=True)
    subprocess.run([str(binary), '--diagnose', str(report)], check=True)
    if not json.loads(report.read_text(encoding='utf-8')).get('ok'):
        raise SystemExit('生成アプリの自己診断が失敗しました。build/bundle-report.json を確認してください。')
    print('ビルド完了:', ROOT / 'dist' / ('Howl-Out-Button.app' if sys.platform == 'darwin' else 'Howl-Out-Button'))


if __name__ == '__main__':
    main()
