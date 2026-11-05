import argparse
import sys

from . import __version__


def main():
    parser = argparse.ArgumentParser(prog='phonepad', description='Use your phone as a wireless keyboard and mouse.')
    parser.add_argument('--terminal', action='store_true', help='run without a window, showing the QR code here')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args()
    if args.terminal:
        from .service import run_in_terminal
        return run_in_terminal()
    from .app import run
    return run()


sys.exit(main())
