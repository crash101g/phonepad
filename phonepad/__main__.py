import argparse
import sys

from phonepad import __version__


def main():
    parser = argparse.ArgumentParser(prog='phonepad', description='Use your phone as a wireless keyboard and mouse.')
    parser.add_argument('--terminal', action='store_true', help='run without a window, showing the QR code here')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args()
    if args.terminal:
        from phonepad.service import run_in_terminal
        return run_in_terminal()
    if sys.platform == 'darwin':
        from phonepad.app_macos import run
    else:
        from phonepad.app import run
    return run()


if __name__ == '__main__':
    sys.exit(main())
