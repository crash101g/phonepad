"""Opens the Mac window with a demo link, screenshots it, and quits. Usage: mac_window_demo.py out.png"""
import subprocess
import sys

from AppKit import NSApp, NSScreen
from PyObjCTools import AppHelper

from phonepad import app_macos


class Demo:
    def __init__(self, on_status):
        pass

    def start(self, new_code=False):
        return 'http://192.168.1.50:8765/?t=0123456789abcdef0123456789abcdef'

    def stop(self):
        pass


def shoot():
    print('screens', len(NSScreen.screens()))
    print('capture', subprocess.run(['/usr/sbin/screencapture', '-x', sys.argv[1]]).returncode)
    NSApp.terminate_(None)


app_macos.Service = Demo
AppHelper.callLater(3, shoot)
app_macos.run()
