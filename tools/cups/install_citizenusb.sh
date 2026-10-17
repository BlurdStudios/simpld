#!/bin/bash
# Install the isolated 'citizenusb' CUPS backend + Citizen_CL_S621 queue.
# Safe by design: never touches /usr/lib/cups/backend/usb, never changes
# USB permissions, never disables SELinux, never overwrites queues.
#
# Review this file, then run exactly:
#   sudo ./tools/cups/install_citizenusb.sh
# from the repo root. No print happens during install (discovery only).
set -euo pipefail

QUEUE="Citizen_CL_S621"
BACKEND_SRC="$(cd "$(dirname "$0")" && pwd)/citizenusb"
BACKEND_DST="/usr/lib/cups/backend/citizenusb"
PPD="/usr/share/cups/model/ctzcls.ppd"
FILTER_SRC="/usr/lib64/cups/filter/rastertocls"
FILTER_DST="/usr/lib/cups/filter/rastertocls"

fail() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "INFO: $*"; }

[ "$(id -u)" -eq 0 ] || fail "run as root: sudo ./tools/cups/install_citizenusb.sh"
[ -f "$BACKEND_SRC" ] || fail "backend source missing: $BACKEND_SRC"
[ -f "$PPD" ] || fail "Citizen PPD missing: $PPD (reinstall ctzcls-cups rpm)"
[ -x "$FILTER_SRC" ] || fail "Citizen filter missing: $FILTER_SRC (reinstall ctzcls-cups rpm)"
command -v python3 >/dev/null || fail "python3 missing"
command -v lpadmin >/dev/null || fail "cups-client missing (lpadmin not found)"
python3 -c "import usb.core, usb.util" 2>/dev/null \
  || { info "installing python3-pyusb dependency..."; dnf install -y python3-pyusb || fail "dnf install python3-pyusb failed"; }
python3 -c "import usb.core" || fail "PyUSB still unavailable after install"

# 1) Isolated backend, root-owned 0700 so CUPS runs it as root (backend(7)).
install -m 0700 -o root -g root "$BACKEND_SRC" "$BACKEND_DST"
info "installed $BACKEND_DST (0700 root)"

# 2) Make the vendor filter visible at the distro filter path (non-overwriting).
if [ -e "$FILTER_DST" ] && [ ! -L "$FILTER_DST" ]; then
  info "filter already exists at $FILTER_DST; leaving it untouched"
elif [ -L "$FILTER_DST" ]; then
  info "filter symlink already present: $(readlink "$FILTER_DST")"
else
  ln -s "$FILTER_SRC" "$FILTER_DST"
  info "symlinked $FILTER_DST -> $FILTER_SRC"
fi
[ -x "$FILTER_DST" ] || fail "filter not executable at $FILTER_DST"

# 3) SELinux labels (best effort; never disabled).
if command -v restorecon >/dev/null; then
  restorecon -v "$BACKEND_DST" "$FILTER_DST" 2>/dev/null || info "restorecon skipped/failed (non-fatal)"
fi

# 4) System usb backend untouched — verify it still exists.
[ -x /usr/lib/cups/backend/usb ] || info "WARNING: system usb backend missing (left as-is)"

# 5) Discovery must list the Citizen unit (read-only, no print).
DISC="$("$BACKEND_DST" 2>/dev/null || true)"
echo "$DISC"
URI="$(echo "$DISC" | grep -o 'citizenusb://[^ ]*' | head -n 1 || true)"
[ -n "${URI:-}" ] || fail "backend discovery found no Citizen USB 2730:0fff unit. Check cable/power, then re-run."
info "discovered $URI"

systemctl try-restart cups 2>/dev/null || service cups restart 2>/dev/null || info "cupsd restart skipped (non-fatal)"

# 6) Create queue only if it does not already exist (never overwrite).
if lpstat -p "$QUEUE" >/dev/null 2>&1; then
  info "queue $QUEUE already exists; leaving it untouched"
else
  lpadmin -p "$QUEUE" -E -v "$URI" -P "$PPD" \
    -o PageSize=w288h216 -o Resolution=203dpi \
    -o printer-error-policy=stop-printer \
    -D "Citizen CL-S621 via citizenusb" \
    || fail "lpadmin failed"
  cupsenable "$QUEUE" || true
  cupsaccept "$QUEUE" || true
  info "created queue $QUEUE on $URI (4x3in w288h216 @203dpi)"
fi

echo "OK: backend + queue ready. Physical test (prints one label) is NOT run automatically."
echo "When ready: lp -d $QUEUE -o PageSize=w288h216 -o Resolution=203dpi /usr/share/cups/data/default-testpage.pdf"
