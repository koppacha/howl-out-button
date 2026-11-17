"""Export public source only; excludes settings, audio, agent files and logs."""
from pathlib import Path
import argparse
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = ('README.md', 'LICENSE', 'THIRD_PARTY.md', 'requirements.txt', 'build.py',
         'run.py', 'package_source.py', '.gitignore')


def export(destination):
    paths = [ROOT / name for name in FILES]
    for folder, extensions in (('sound_trigger', {'.py'}), ('tests', {'.py'}),
                               ('hooks', {'.py'}), ('.github', {'.yml', '.yaml'})):
        paths += [p for p in (ROOT / folder).rglob('*') if p.is_file()
                  and p.suffix in extensions and '__pycache__' not in p.parts]
    paths += [ROOT / 'docs' / name for name in ('screenshot.png', 'hotkey-registration.png', 'output-selector.png')
              if (ROOT / 'docs' / name).is_file()]
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise ValueError('Source export refuses external or symlinked files')
            archive.write(path, 'Howl-Out-Button/' + str(path.relative_to(ROOT)))
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'Howl-Out-Button-source.zip')
    print(export(parser.parse_args().output))
