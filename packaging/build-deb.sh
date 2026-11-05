#!/bin/sh
# Builds dist/phonepad_<version>_all.deb. Needs no root.
set -eu
cd "$(dirname "$0")/.."
version=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" phonepad/__init__.py)
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT

install -d "$root/DEBIAN" "$root/usr/bin" "$root/usr/lib/phonepad/phonepad" "$root/usr/lib/udev/rules.d" \
  "$root/usr/share/applications" "$root/usr/share/icons/hicolor/scalable/apps" "$root/usr/share/doc/phonepad"
install -m 644 phonepad/*.py phonepad/phone.html "$root/usr/lib/phonepad/phonepad/"
install -m 644 packaging/60-phonepad-uinput.rules "$root/usr/lib/udev/rules.d/"
install -m 644 packaging/io.github.crash101g.PhonePad.desktop "$root/usr/share/applications/"
install -m 644 packaging/phonepad.svg "$root/usr/share/icons/hicolor/scalable/apps/"
install -m 644 LICENSE NOTICE README.md "$root/usr/share/doc/phonepad/"
cat > "$root/usr/bin/phonepad" <<'SH'
#!/bin/sh
PYTHONPATH=/usr/lib/phonepad exec python3 -m phonepad "$@"
SH
chmod 755 "$root/usr/bin/phonepad"
cat > "$root/DEBIAN/control" <<CONTROL
Package: phonepad
Version: $version
Architecture: all
Maintainer: crash101g <152836374+crash101g@users.noreply.github.com>
Homepage: https://github.com/crash101g/phonepad
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, iproute2
Section: utils
Priority: optional
Description: Use your phone as a wireless keyboard and mouse
 Scan a QR code and a page opens on your phone with a trackpad and keyboard
 that control this computer over the local network. Nothing to install on
 the phone.
CONTROL
cat > "$root/DEBIAN/postinst" <<'SH'
#!/bin/sh
set -e
if [ "$1" = configure ] && command -v udevadm >/dev/null; then
  udevadm control --reload-rules || true
  udevadm trigger --name-match=uinput || true
fi
SH
chmod 755 "$root/DEBIAN/postinst"
mkdir -p dist
dpkg-deb --root-owner-group --build "$root" "dist/phonepad_${version}_all.deb"
