#!/usr/bin/env bash
# package_deb.sh — wrap the PyInstaller Linux binary (dist/RichmorConfig) into a .deb so users
# double-click to install (Software installer pulls WebKitGTK, adds it to the apps menu). No chmod.
#
# Usage:  bash package_deb.sh <output.deb>
set -euo pipefail
OUT="${1:-dist/RichmorConfig-linux.deb}"
VER="1.0.0"

ROOT="$(mktemp -d)/richmorconfig"
mkdir -p "$ROOT/DEBIAN" \
         "$ROOT/opt/RichmorConfig" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/512x512/apps"

cp dist/RichmorConfig "$ROOT/opt/RichmorConfig/RichmorConfig"
chmod 755 "$ROOT/opt/RichmorConfig/RichmorConfig"
[ -f assets/favicon-512.png ] && cp assets/favicon-512.png \
    "$ROOT/usr/share/icons/hicolor/512x512/apps/richmorconfig.png" || true

cat > "$ROOT/DEBIAN/control" <<EOF
Package: richmorconfig
Version: $VER
Section: utils
Priority: optional
Architecture: amd64
Depends: libwebkit2gtk-4.1-0 | libwebkit2gtk-4.0-37, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0, gir1.2-gtk-3.0
Maintainer: Globo360 <support@globo360.com>
Description: Richmor MDVR Config
 Local configuration and monitoring tool for Richmor MDVR recorders.
 Connects to the recorder's Wi-Fi and opens a desktop window.
EOF

cat > "$ROOT/usr/share/applications/richmorconfig.desktop" <<EOF
[Desktop Entry]
Name=Richmor MDVR Config
Comment=Configure and monitor Richmor MDVR recorders
Exec=/opt/RichmorConfig/RichmorConfig
Icon=richmorconfig
Type=Application
Categories=Utility;Network;
Terminal=false
EOF

mkdir -p "$(dirname "$OUT")"
dpkg-deb --build --root-owner-group "$ROOT" "$OUT"
echo "Built: $OUT"
