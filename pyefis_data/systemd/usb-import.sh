#!/bin/sh
# installs to /usr/local/lib/makerplane/usb-import.sh  (chmod +x)
# Mounts a USB partition read-only, and if it carries a makerplane-data/
# directory with a manifest, runs `pyefis-data import` against it as the
# pyefis user. All outcomes are logged to the journal; failures are never
# fatal. The import itself signature-verifies the stick's manifest, so a
# malicious stick cannot install anything.
set -eu

DEV="/dev/${1:?partition name required}"
USER_NAME="${PYEFIS_USER:-pyefis}"
MNT="$(mktemp -d /run/makerplane-usb.XXXXXX)"

cleanup() { umount "$MNT" 2>/dev/null || true; rmdir "$MNT" 2>/dev/null || true; }
trap cleanup EXIT

mount -o ro,nosuid,nodev,noexec "$DEV" "$MNT" 2>/dev/null || {
    echo "makerplane-usb-import: could not mount $DEV"; exit 0; }

if [ -f "$MNT/makerplane-data/manifest.json" ]; then
    echo "makerplane-usb-import: importing from $DEV"
    # Run as the pyefis user so files land under that user's ~/makerplane-data.
    su -s /bin/sh -c "pyefis-data import '$MNT/makerplane-data'" "$USER_NAME" \
        || echo "makerplane-usb-import: import reported errors (data left untouched)"
else
    echo "makerplane-usb-import: $DEV has no makerplane-data/ — ignoring"
fi
