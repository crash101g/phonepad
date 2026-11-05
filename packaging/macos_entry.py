"""The program PyInstaller wraps into PhonePad.app."""
import sys

from phonepad.__main__ import main

sys.exit(main())
