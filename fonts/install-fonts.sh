#!/usr/bin/env bash
# Interim font installer for pyEfis displays.
#
# Installs the configuration-manager editor's clean sans-serif set -- B612
# (the Airbus cockpit font), Inter, Roboto, Open Sans -- into the user font
# directory on the Pi, NO sudo required: it apt-get-downloads the Debian font
# packages and extracts the TTFs. pyEfis renders with whatever fonts are
# installed on the device, so these must be present for a design to render in
# them (otherwise Qt falls back to DejaVu, which always ships).
#
# This is the interim seed for a proper signed "fonts" pack delivered by the
# pyefis-data on-Pi updater -- see README.md.
#
# Usage (on the Pi):  bash install-fonts.sh
set -u

PKGS="fonts-b612 fonts-inter fonts-roboto-unhinted fonts-open-sans"
DEST="$HOME/.local/share/fonts"
TMP="$(mktemp -d)"

mkdir -p "$DEST"
cd "$TMP" || exit 1
echo "Downloading: $PKGS"
apt-get download $PKGS || { echo "apt-get download failed"; exit 1; }

for deb in *.deb; do dpkg-deb -x "$deb" ext; done
# Regular/bold sans faces only (skip italics and the Noto bulk).
find ext -type f \( -iname "*.ttf" -o -iname "*.otf" \) \
  ! -iname "*Italic*" -exec cp {} "$DEST/" \;
fc-cache -f "$DEST" >/dev/null 2>&1

cd - >/dev/null || true
rm -rf "$TMP"

echo "Installed font families:"
fc-list : family | tr "," "\n" | grep -iE "^(B612|Inter|Roboto|Open Sans)$" | sort -u
