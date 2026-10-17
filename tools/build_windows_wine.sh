#!/usr/bin/env bash
# Build the real Windows Simpld.exe using an ISOLATED Wine prefix + official
# Windows Python + PyInstaller. Never touches ~/.wine, global Wine config, or
# existing apps. Official downloads only (python.org, PyPI).
#
# Usage:  tools/build_windows_wine.sh [--skip-install]
# Output: dist/Simpld/Simpld.exe  +  dist/Simpld-windows-demo.zip (+ .sha256)
#
# Requirements on the Linux host: wine (>=8), curl, unzip, zip.
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$PROJ/build/windows"
DIST_DIR="$PROJ/dist"
STAGE="/tmp/simpld-winsrc"
# Isolated prefix under $HOME (NOT ~/.wine, NOT /tmp root: Wine refuses to
# create a prefix directly under a directory not owned by the user).
export WINEPREFIX="${WINEPREFIX:-$HOME/.cache/simpld-wine}"
export WINEARCH=win64
export WINEDEBUG=-all

PY_VER="3.12.10"
PY_EXE="python-${PY_VER}-amd64.exe"
PY_URL="https://www.python.org/ftp/python/${PY_VER}/${PY_EXE}"
PY_CACHE="$HOME/.cache/simpld-dl/${PY_EXE}"
WINPY="C:\\Python312\\python.exe"

log() { echo "[wine-build] $*"; }

# 0. Sanity: production data must never be staged into the installer.
for f in "$PROJ/data/factory.db" "$PROJ/data/hardware.json" "$PROJ/data/print_log.txt"; do
  if [ ! -f "$f" ]; then log "note: $f absent (fine)"; fi
done

# 1. Isolated prefix (does NOT use ~/.wine). NOTE: this host's Wine ships
# without the `wineboot` wrapper, so invoke the builtin via the loader.
if [ ! -f "$WINEPREFIX/system.reg" ]; then
  log "creating isolated prefix $WINEPREFIX"
  wine wineboot --init
fi

# 2. Official Windows Python (python.org only).
if [ "${1:-}" != "--skip-install" ] || ! wine "$WINPY" --version >/dev/null 2>&1; then
  if [ ! -f "$PY_CACHE" ]; then
    log "downloading $PY_URL"
    curl -fL --retry 3 -o "$PY_CACHE" "$PY_URL"
  fi
  log "installing Windows Python $PY_VER into prefix"
  wine "$PY_CACHE" /quiet InstallAllUsers=0 PrependPath=1 TargetDir="C:\\Python312"
  wine "$WINPY" --version
fi

# 3. Windows-side deps (pinned app reqs + isolated build req).
log "installing Windows deps"
wine "$WINPY" -m pip install --upgrade pip
wine "$WINPY" -m pip install -r "Z:/$(echo "$PROJ" | sed 's#^/##')/requirements.txt"
wine "$WINPY" -m pip install -r "Z:/$(echo "$BUILD_DIR" | sed 's#^/##')/build_requirements.txt"

# 4. Stage clean source (no data/, tests/, venv, caches, build outputs).
log "staging clean source to $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp "$PROJ/run.py" "$STAGE/"
cp -r "$PROJ/factory" "$STAGE/"
cp -r "$PROJ/build" "$STAGE/"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name "*.pyc" -delete 2>/dev/null || true
if [ -f "$STAGE/factory.db" ] || [ -d "$STAGE/data" ]; then
  echo "FATAL: production data leaked into stage" >&2; exit 1
fi

# 5. Freeze with Windows PyInstaller (real PE .exe, onedir).
# NOTE: workpath MUST NOT be $STAGE/build — the spec lives at
# $STAGE/build/windows/simpld.spec and PyInstaller wipes its workpath.
log "running PyInstaller (Windows)"
rm -rf "$STAGE/dist" "$STAGE/winbuild"
(cd "$STAGE" && wine "$WINPY" -m PyInstaller --noconfirm --distpath dist --workpath winbuild "build\\windows\\simpld.spec")

# 6. Collect + zip + checksum on the Linux side.
log "collecting artifacts"
if [ ! -f "$STAGE/dist/Simpld/_internal/PySide6/plugins/platforms/qwindows.dll" ] && [ ! -f "$STAGE/dist/Simpld/PySide6/plugins/platforms/qwindows.dll" ]; then
  echo "FATAL: qwindows.dll missing in staged build — Qt plugins were not bundled (would fail with 'no Qt platform plugin could be initialized'). Aborting." >&2
  echo "Check Windows PySide6 install and build/windows/simpld.spec plugin section." >&2
  exit 1
fi
mkdir -p "$DIST_DIR"
rm -rf "$DIST_DIR/Simpld" "$DIST_DIR/Simpld-windows-demo.zip" "$DIST_DIR/Simpld-windows-demo.zip.sha256"
cp -r "$STAGE/dist/Simpld" "$DIST_DIR/"
(cd "$DIST_DIR" && zip -qr "Simpld-windows-demo.zip" Simpld)
(cd "$DIST_DIR" && sha256sum "Simpld-windows-demo.zip" > "Simpld-windows-demo.zip.sha256")
wine --version > "$DIST_DIR/BUILD-INFO.txt"
wine "$WINPY" --version >> "$DIST_DIR/BUILD-INFO.txt" 2>&1 || true
wine "$WINPY" -m PyInstaller --version >> "$DIST_DIR/BUILD-INFO.txt" 2>&1 || true
log "done: $DIST_DIR/Simpld/Simpld.exe"
ls -la "$DIST_DIR" | head

# 7. Installer (Inno Setup 6 under the same prefix; skipped if not installed).
# Install once: wine innosetup-6.7.3.exe /VERYSILENT /DIR="C:\\InnoSetup"
# (https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe)
if [ -f "$WINEPREFIX/drive_c/InnoSetup/ISCC.exe" ]; then
  log "compiling Inno Setup installer"
  wine "C:\\InnoSetup\\ISCC.exe" "Z:/$(echo "$PROJ" | sed 's#^/##')/build/windows/simpld.iss"
  (cd "$DIST_DIR" && sha256sum "Simpld-Setup-1.0.0.exe" > "Simpld-Setup-1.0.0.exe.sha256")
  log "done: $DIST_DIR/Simpld-Setup-1.0.0.exe"
else
  log "Inno Setup not installed in prefix — installer step skipped"
fi
