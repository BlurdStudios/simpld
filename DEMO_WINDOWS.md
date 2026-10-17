# Simpl'd — Windows demo build & production readiness

Separate build doc (does not replace README.md). For the client's demo TODAY.

## 1. What to hand the client

- `dist/Simpld-Setup-1.0.0.exe` (24M, + `.sha256`) — **this is the handoff.**
  Double-click → installs to `C:\Program Files\Simpld\` with Start Menu
  shortcut + uninstaller. Client never sees `_internal/` or DLLs.
- `dist/Simpld-windows-demo.zip` — fallback portable layout only (unzip,
  run `Simpld/Simpld.exe`). Prefer the installer.
- Real Windows PE executables (onedir + Inno Setup 6): `Simpld.exe` plus its
  support folder, installed under Program Files.
  Built with **Windows** Python + PyInstaller — not a renamed Linux binary.
- Fully offline. Installer needs admin (Program Files); portable zip needs
  no installer/admin rights.
- First launch creates per-user data at `%LOCALAPPDATA%\Simpld\`
  (e.g. `C:\Users\<name>\AppData\Local\Simpld\factory.db`, plus `backups\`).
  Nothing is written into Program Files or protected folders; uninstalling
  never deletes the factory database.

Build under test (this demo):

- artifact: **NOT YET PRODUCED — no `dist/` exists on the Linux host.**
- blocker (exact): isolated Wine prefix creation never completes on this host.
  `wineboot --init` (Wine 11.0 Staging, Fedora, `WINEPREFIX=~/.cache/simpld-wine`)
  wedges in `rundll32.exe setupapi,InstallHinfSection Wow64Install ... wine.inf`
  at ~100% CPU for 13+ minutes with no progress; the 64-bit pass behaved the
  same and only advanced after being killed. The prefix itself is alive
  (`wine64 cmd /c echo` returns 0), but Windows Python cannot be installed into
  a half-registered prefix, so no Windows PyInstaller run was possible here.
  Host notes: no `wineboot` wrapper binary (used builtin `wineboot.exe` via the
  loader); Wine refuses prefixes directly under `/tmp` (used `~/.cache`).
- **Primary route for TODAY: build natively on any Windows PC** (§5, 10–15 min):
  copy the project folder (without `data/`), `pip install -r requirements.txt`,
  `pip install -r build/windows/build_requirements.txt`,
  `python -m PyInstaller --noconfirm build/windows/simpld.spec` → real
  `dist\Simpld\Simpld.exe`. The spec, script (`tools/build_windows_wine.sh`,
  fixed + syntax-checked) and exclusions are ready; only a working Windows-side
  interpreter was missing.

## 2. Demo credentials (seeded defaults — CHANGE BEFORE PRODUCTION)

The demo ships with three seeded logins so every role can be shown:

| user       | password    | role       |
| ---------- | ----------- | ---------- |
| admin      | admin123    | admin      |
| packager   | pack123     | packager   |
| dispatcher | dispatch123 | dispatcher |

Seeded demo content: jobs 4167/4168-style sample jobs with reels, packed
cartons (Ready Stock) and one loading dispatch order, so Packaging / Ready
Stock / Dispatch / History / Summary all have something to show.

**Authentication policy was NOT changed** for this build: same PBKDF2-SHA256
local auth, same roles, same pages. Production risks before going live:

1. Default passwords are public-by-construction — change all three (or delete
   the demo users and create real ones) via the admin user dialog.
2. Minimum password length is 4 — set stronger passwords by policy, not code.
3. There is no lockout/audit on failed logins (offline single-PC app).
4. Demo seed data must be wiped for production (fresh `%LOCALAPPDATA%\Simpld`
   folder = fresh DB; do NOT copy the demo `factory.db` to the floor PC).

## 3. What's NOT in the .exe (production-data exclusion)

Verified at staging time (`tools/build_windows_wine.sh` aborts otherwise):

- `data/factory.db`, `data/hardware.json`, `data/print_log.txt`,
  `data/backups/`, `data/*.csv`, `tests/`, `.venv/`, `__pycache__` are all
  excluded. Only `run.py` + `factory/` source + UI png assets + installed
  package deps (PySide6, Pillow, qrcode, pyserial) are frozen in.

## 4. Hardware on the demo

- Scale (Essae DS-215, COM port) and label printer (Citizen CL-S621): the demo
  runs the **mock/file-log** path unless a real COM port / printer share is
  configured on the client PC (hardware task owns `factory/hardware/*` and the
  config dialog; env overrides `SIMPLD_SCALE_PORT`, `SIMPLD_PRINTER_NAME`).
- For native-serial proof on the real floor PC, plug the scale in, set the COM
  port in the app's hardware dialog, and weigh a reel live. That step cannot
  be proven under Wine/Linux — see §6.
- **Demo switch (nothing plugged in at all)**: the Packaging device strip now
  carries a **Demo** button. One click (plus a confirmation) switches to
  simulated hardware for the whole flow — made-up weights, labels written to
  the print log (`%LOCALAPPDATA%\Simpld\print_log.txt` when installed), and
  the device pills read `demo · simulated`. It is off by default and stored
  per machine (`hardware.json`); `SIMPLD_DEMO=1` forces it on at launch. The
  switched-on state is honored in the frozen build on purpose — that is the
  point: the demo runs from the installed `.exe`. Turn it off before real
  production (unlike `SIMPLD_ALLOW_MOCK`, which stays dead when frozen).

## 5. Reproduce the build

On any Linux host with Wine, or natively on Windows:

```bash
# Linux (isolated Wine prefix /tmp/simpld-wine — ~/.wine untouched):
tools/build_windows_wine.sh
# Native Windows (Windows Python 3.12 + pip install -r requirements.txt):
python -m pip install -r build/windows/build_requirements.txt
python -m PyInstaller --noconfirm build/windows/simpld.spec
# Installer (either host, ISCC on PATH / C:\InnoSetup\ISCC.exe under Wine):
iscc build/windows/simpld.iss   # → dist/Simpld-Setup-1.0.0.exe
```

## 6. Proof status (honest)

- [ ] Real Windows PE produced (`Simpld.exe`, `file` reports PE32+).
- [ ] Smoke test: app launches, login dialog appears, seeded jobs visible.
- [ ] Data written to user-data folder, not beside the .exe.
- [ ] sha256 recorded in `dist/*.sha256`.
- [ ] **Wine smoke test ≠ real Windows proof.** Qt GUI startup under Wine
      validates the bundle resolves its imports/DLLs, but COM ports, printer
      drivers and native rendering must be confirmed on a real Windows PC.
      Remaining native-Windows proof: run the .exe on the client's machine,
      log in, weigh/pack one reel, save a PDF slip, take an evening backup.

## 7. Frozen-asset / path notes for the UI & hardware owners (no collisions)

Checked read-only; NOT edited (owned by the parallel hardware/UI task):

- `factory/ui/styles.py`: QSS png paths derive from `__file__` — frozen-safe
  as-is because the spec bundles `factory/ui/assets/*.png` into
  `sys._MEIPASS` (read-only reads are fine). No change needed.
- `factory/ui/label_preview.py::_out_path` and `pages_misc.SummaryPage`
  export/backup dialog defaults derive from `<repo>/data` via `__file__`.
  Frozen, that resolves into read-only `sys._MEIPASS`. Qt file dialogs tolerate
  a missing default dir, but the owner should switch these defaults to
  `factory.db.app_data_dir()` (new shared helper, safe to import) when
  frozen. Same for `factory/hardware/config.py::DEFAULT_PATH` and
  `factory/hardware/mock.py` log path — `app_data_dir()` is exposed for
  exactly this reuse; I did not touch those files per task ownership.
