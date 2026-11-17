import argparse


def main():
    parser = argparse.ArgumentParser(description='Howl-Out-Button（わんコマンドボタン）')
    parser.add_argument('--diagnose', metavar='REPORT.json', help='合成音源でバンドルを自己診断（機器入力なし）')
    args = parser.parse_args()
    if args.diagnose:
        from sound_trigger.diagnostics import diagnose
        return diagnose(args.diagnose)
    from sound_trigger.app import main as start_gui
    return start_gui()


if __name__ == '__main__':
    raise SystemExit(main())
