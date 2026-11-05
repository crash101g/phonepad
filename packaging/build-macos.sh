#!/bin/sh
# Builds dist/PhonePad-macos-arm64.zip on an Apple silicon Mac.
# Needs: pip install pyinstaller pyobjc-framework-Cocoa
set -eu
cd "$(dirname "$0")/.."
version=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" phonepad/__init__.py)
python3 -m PyInstaller --noconfirm --clean --windowed --name PhonePad --target-architecture arm64 \
  --osx-bundle-identifier io.github.crash101g.PhonePad \
  --add-data phonepad/phone.html:phonepad --hidden-import phonepad.app_macos --hidden-import phonepad.input_macos \
  packaging/macos_entry.py
plist=dist/PhonePad.app/Contents/Info.plist
/usr/bin/plutil -replace CFBundleShortVersionString -string "$version" "$plist"
/usr/bin/plutil -insert NSLocalNetworkUsageDescription -string 'PhonePad talks to your phone over your local network.' "$plist"
/usr/bin/codesign --force --deep --sign - dist/PhonePad.app
/usr/bin/ditto -c -k --keepParent dist/PhonePad.app dist/PhonePad-macos-arm64.zip
