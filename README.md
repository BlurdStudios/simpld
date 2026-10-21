# Simpl'd — inventory and dispatch software (native PySide6 desktop, offline-first)

> Display name is **Simpl'd** (no v2 suffix). Simpl'd is its own brand and a
> product of **Blur'd**; Blur'd appears only as parent attribution (About
> dialog, installer publisher), never in the app chrome. The company name
> prints only where the old paper did — stickers and packing slips.

Fast industrial desktop utility. Replaces "Fortune Labels N Systems".

Native Python + Qt only (`python3 run.py` launches the desktop app).
No Reflex/web framework, no Electron, no webview. The visual language recreates
Buridan UI (https://buridan-ui.reflex.run/ button/input/select/tabs/dialog/frame/
typography/message + theming tokens) with native Qt widgets — not actual Reflex
components. v1 is preserved untouched in `../simpld-old-ui-rollback`.

## Run (Linux factory PC)

```
pip install -r requirements.txt
python3 run.py
```

No internet needed to run (the update check in UPDATES.md is optional and
stays silent when offline). Data in `data/factory.db` (SQLite). Print log in
`data/print_log.txt`.

## Updates (automatic, WiFi / LAN)

The app updates itself: it checks silently a few seconds after the window
opens (the public release mirror where there is internet, the packager's LAN
channel where there is none, a local folder on the packager) and offers
verified installs via **About → Check for updates**. Every installer is
SHA-256 verified before it runs; a failure never blocks the floor.

Releasing a version = bump `factory/version.py`, then
`git tag v1.0.1 && git push origin v1.0.1` — CI runs the tests, builds the
installer, writes its checksum and publishes it to the public release mirror
the fleet updates from. The source repo stays private; the mirror carries
binaries only. Full procedures (mirror setup, LAN publishing, security
properties, troubleshooting): **UPDATES.md**.

## Logins (offline, same software, different doors)

Production startup creates the database schema only — no demo data, no
pre-made users. On the very first launch (empty users table) the login
dialog switches to **administrator setup**: choose the administrator
username and password and click **Create administrator**. Sign in as that
admin afterwards and create the packager/dispatcher logins (👥 Users →
+ ADD); password resets (RESET PASSWORD) work the same way. Users, roles
and PBKDF2 password hashes live in the local `users` table — no internet,
nothing leaves the PC.

## Dev hot-reload (no more stop/start while iterating)

```
python3 run.py --dev                  # auto-restart on any .py change
SIMPLD_DEV_USER=admin python3 run.py --dev   # + skip login (dev only)
```

Stdlib-only file watcher (1s poll, `*.py` only — the DB in `data/` is ignored).
Restarts the process so QSS/theme edits apply too; DB persists, unsaved UI
state resets. Factory floor: plain `python3 run.py` (no watcher).

## Workflow (matches old system + new decoupling)

1. **Packaging — nested Reel packing / Box packing workspaces (no weight auto-detect)**
   - **Reel packing** — set the session core weight once, weigh one reel → SAVE + PRINT **Type-1 reel sticker (NO QR)** — bordered table (Company + J:no, Job Name, Date/No of Reels, No of Pieces, Gross/Core/Net, big bare Roll No#). No customer, shift or supervisor on the sticker. The preview under the weigh card shows reel art here (box art in Box packing). Stick it on the reel, then the reel waits loose for Box packing.
   - **Box packing** — set the session box/tare weight once (it applies to every box). Weigh the packed box: the next loose reels in production order are consumed into it, and PACK creates the box **without printing**. The Type-2 box sticker (WITH QR) is queued and printed one-by-one from the sticker queue (reprint available); it shows `Bundle:n · Rolls:24,25` and the bare box number once under the QR. QR = `VASAVI:<box code>`; the scanner accepts the QR or the plain code + Enter.
   - Fixed stock for Type-1/Type-2: 4 x 3 in Citizen media (101.6 x 76.2 mm @203dpi). No size options.
   - Note: the box layout is inferred from the reel-sticker photo — send a box-sticker photo to correct it.
   - The loose-reel list is display-only (no checkboxes / Select all / Clear): Box packing always consumes the next loose reels in production order. PACK validates the gross weight, the session box/tare and the reels-per-box limit before writing anything.
2. **Ready Stock** — boxes with status Ready, listed by bare box number. No customer shown. Search by number, code or job; reprint sticker, Packing Slip (A4 match of the old paper slip: left data table, right print preview, office-printer box + Save PDF). **Scan QR to verify**: scan any box (Ready or dispatched) to see its batch + box details and dispatch status.
3. **Dispatch** — New order (customer + product job + **Customer PO** + required **pieces/wrappers**; a 3-coil box is 3 × pieces-per-coil). Scan each box QR: boxes are numbered **1..N for that client order** (never the scanned carton code), progress shows loaded/required wrappers with 75% / 90% / target / over-target alerts, and the crossing box is accepted whole. The scan opens the **Type-3 wide client sticker** preview (A4 width, content height) showing logo, Pack. Date, Customer, Product, Batch No., **Customer PO**, No of Coils, one row per coil (S.no, pieces, gross/tare/net, mfg date), totals and `BOX NO: n`. Batch is `SVSF/MON-DD` from the coil mfg date unless the order overrides it. COMPLETE once the target is reached; legacy bundle/reel orders stay exact. Mismatch + duplicates blocked. **On completion the dispatch slip opens automatically** — the whole order details (customer, PO, batch, every box with weights/pieces, totals) on the office printer, or Save PDF — and the same record is **frozen in the database** in the same transaction, so the **Slip** button reprints it identically any time later. The company logo is uploaded in **About → Client sticker logo** (admin) and stored in the shared DB, so every PC prints the same logo.

   **Testing the sticker without a QR scanner (test PCs only):** sign in as admin, open **About → Dispatch test aid**, enable **Show Test scan in Dispatch**, then in Dispatch select an order and press **Test scan**. It loads the next Ready box through the same `VASAVI:<box>` scan path and opens the client-sticker preview. It does **not** change the printer or scale, so turn demo mode off to test the real printer. Turn the aid off after testing; it can also be forced with `SIMPLD_MOCK_QR=1` or `"mock_qr": true` in `hardware.json`.
4. **Jobs** — reusable **products master** (product name + pieces/reel, a
    positive whole number). New packaging jobs pick an existing product from
    the dropdown; add a product once in the same New-job area, then reuse it
    across sessions. Each job snapshots the product name + pieces count, so
    later catalog edits cannot rewrite history. Legacy job names were
    backfilled as products with pieces *unknown* (never invented). Size /
    Core fields no longer appear in the new-job dialog; legacy size/core data
    stays in the DB and reel core behavior is unchanged. Customer lives on the
    **dispatch order** only — never on stickers.
   - QR stays an opaque ID (`VASAVI:Cxxxx`). No public lookup: distributor/software side stays protected; public data needs a client decision first. In-app scanner verification covers the floor need.
5. **History / Summary** — dispatch log + daily totals (reels/boxes/stock/dispatched/weights) + CSV export for future dashboard upload + **Evening backup** (timestamped DB copy — run before closing the computer).

## Branding (Simpl'd identity, parent Blur'd)

The mark and icon set are **authored as vector art**, not AI-generated. AI
image models produce soft photographic shapes that turn to mush at a 16px
taskbar icon and cannot emit a multi-frame Windows `.ico`; vector masters can.
Rendered deterministically with `PySide6.QtSvg` + Pillow — both already
requirements, nothing new to install.

| file | what it is |
|------|-----------|
| `tools/build_brand_assets.py` | mark geometry + renderer (run this after any design change) |
| `tools/import_hugeicons.py` | icon importer/previewer — the only place to swap a glyph |
| `brand/README.md` | the mark: variants, geometry, why the numbers are what they are |
| `brand/svg/mark*.svg` | the logomark masters — **bare glyph is primary**, no tile |
| `brand/svg/icons/*.svg` | vendored HugeIcons glyphs + LICENSE.txt (MIT) |
| `factory/ui/brand.py` | tokens, cached icon loading, wordmark lockup |
| `factory/ui/splash.py` | branded splash + About dialog |
| `factory/ui/assets/brand/` | generated: mark ladder + icon set (2 variants x 4 sizes) |
| `factory/ui/assets/simpld.ico` | generated: 9-frame Windows icon (16…256) |

Icons come from **HugeIcons free** (6,000+ stroke-rounded glyphs, MIT, no
attribution required) — same 24px grid, stroke-width 1.5, round caps/joins, so
they slot into the existing renderer with no style drift. They are **vendored**
into `brand/svg/icons/`, so the app build and the factory PC need no npm and no
network.

Rebuild everything:

```
python3 tools/build_brand_assets.py     # brand/svg -> PNG + .ico
python3 tools/preview_brand.py          # contact sheet -> brand/preview.png
python3 tools/shoot_ui.py               # offscreen screenshots -> brand/shots/
```

Swap or add an icon (dev machine, needs internet once):

```
npm pack @hugeicons/core-free-icons@4.3.5
tar xzf hugeicons-core-free-icons-4.3.5.tgz
python3 tools/import_hugeicons.py --package package --preview
# edit CHOSEN in that file, then:
python3 tools/import_hugeicons.py --package package --import
python3 tools/build_brand_assets.py
```

**Re-branding** = change the hexes at the top of `tools/build_brand_assets.py`
*and* in `factory/ui/brand.py`, re-run the build script. The app chrome reads
the accent through `APP_QSS` placeholders (`__ACCENT__`, `__ACCENT_HOVER__`,
`__ACCENT_PRESSED__`) substituted in `styles.py`, so no page hardcodes a brand
colour. The accent is applied to: focus rings, the active nav pill, progress
bars, and a new `accent` button kind (`style_button(btn, "accent")`).

Design constraints that are deliberate — do not "fix" them without re-checking:

- The wordmark is **native Qt text**, not a PNG, so it stays sharp at 150%/200%
  Windows scaling. The accent apostrophe is a literal `’`; Qt's rich-text
  parser does not decode `&#8217;` and prints the entity verbatim as `|`.
- Icons ship in a white `-on` variant because the active nav pill is dark.
- `simpld.ico` base frame must stay the **largest** (256px) or Pillow drops
  every smaller frame and ships a 1-frame icon.

## Numbering (continues old series)

- Job No: integer from 4169 (old was at 4168)
- Roll No: monthly global 1,2,3… (resets each month across all jobs)
- Bundle No: per-job 1,2,3… (packing-slip row)
- Box No: monthly global — stored/QR as `CYYYYMM-NNNN`; stickers and tables show the bare number (`1`, `24`)
- Client box No: per dispatch order 1..N in scan order (renumbered on removal); this is the `BOX NO` on the wide client sticker
- Order No: `DO-0001…`

## Devices (3 devices, 2 stations, auto-detected)

No Devices dialog, no port dropdown. Simpl'd works out the hardware itself: it
probes every serial port for a weigh machine that answers with a parseable
weight frame, and picks the configured label printer out of the OS printer list
(the 4x3 Citizen on packaging; the wide client-sticker printer on dispatch).
The QR scanner is a keyboard wedge and needs no configuration. Each station page
shows clickable device pills; click one to re-detect it, or press Refresh.

If nothing answers, the pill reads `not found` and the app stays fail-closed —
it never silently falls back to a mock scale. Full detail in
**DEVICE_STATUS.md**.

## Hardware (real drivers + explicit demo/mock)

- `factory/hardware/base.py` — Scale/Printer/Scanner interfaces
- `factory/hardware/essae.py` — real Essae DS-215 over RS232 (pyserial;
  fresh reading per Read, errors on timeout/unstable/disconnect — never a
  stale or 0.000 kg value presented as measured)
- `factory/hardware/qtprint.py` — real label printers through their installed
  OS driver via Qt (QPrinter/QPainter, zero margins). Type-1/Type-2 use the
  fixed 101.6 x 76.2 mm Citizen stock; Type-3 renders a 210 mm-wide sticker
  whose height follows the coil count and requires that exact custom paper
  size on the dispatch printer. No EPL/ZPL commands.
- `factory/hardware/mock.py` — MockEssaeDS215, MockCitizenCLS621 (file log),
  MockScanner (keyboard wedge, nothing to do)
- `factory/hardware/devices.py` — shared instances from `config.load`
  (file `data/hardware.json`, env overrides). Blank port/name = demo/mock.
- The scanner is a USB keyboard wedge: click a scan box and scan (Enter).
- Weighing follows the selected workspace (Reel packing / Box packing) — the
  scale never auto-detects the object type from weight.
- While the mock scale is active, Packaging shows small Mock rows — Reel/Box on scale (weigh simulation) and Test data (+3 test reels, Pack test box for dummy data). They hide themselves on real hardware.
- **Demo mode (training / no-hardware demos)** — demo installers
  (`Simpld-Setup-X.Y.Z-demo.exe`, USB handout only) carry a **Demo** button
  in the device strip: one click (plus a confirm) simulates the weigh
  machine and label printer on a PC with nothing plugged in. Weights are
  made up, labels land in the print log, the device pills read
  `demo · simulated`, and it is off by default. **Prod installers have no
  Demo button at all** — simulation can never be switched on on the floor.
  Demo builds are never published to the update mirror, so they never
  auto-update (and the fleet never auto-updates into one). `SIMPLD_DEMO=1`
  forces demo on at launch in demo builds / source runs only (per-machine
  setting in `hardware.json`).
- Fresh databases start **empty** (schema only — production bootstrap never
  creates jobs, reels, boxes, orders or logins; the first-run admin is set
  up in the login dialog). Test fixtures live in `seed_sample_data()`
  (test-only) and demo data is never written by production startup.

## Hardware setup (demo day)

1. `pip install -r requirements.txt` (includes pyserial for the scale).
   If the project `.venv` has no working interpreter, use system python3 —
   the `.venv` as shipped contains package copies but no `bin/python`.
2. Install the Citizen CL-S621II with the manufacturer's driver for the demo
   OS (Linux factory PC or Windows — use the platform's normal Add Printer
   flow) and print its Windows/Linux test page first.
3. Plug in the Essae and power it on **before** opening the app. Detection runs
   automatically at startup: the device pills above the Packaging page probe
   every serial port and show `ready · COM3 · 9600` once the weigh machine
   answers. The label printer pill resolves from the installed printer list.
   No port entry, no dropdown, no Save.
   - Nothing detected → the pill reads `not found`. Check the cable/power,
     then click the pill (or **Refresh**) to probe again.
   - Want to pin a port by hand (two identical scales, say)? Set
     `SIMPLD_SCALE_PORT=COM3` / `SIMPLD_PRINTER_NAME="Citizen CL-S621"`, or
     edit `hardware.json` in the user data dir. Env still wins over the file.
4. Packaging gates on real hardware: with nothing detected, Read scale and
   Print are blocked with an error instead of writing fake records.
   A failed real read/print shows an error and blocks the action — nothing
   silently falls back to fake weights or file-log prints.

## Benchmarking (local limits + LAN latency)

`tools/benchmark.py` is a safe, repeatable way to answer "how fast is this PC
and this LAN?" without touching production data. It measures real service
writes (reel → box → order → scan), concurrent inventory updates, large local
datasets, and the actual HTTPS round trip to the packager. Local runs and the
built-in LAN server use throwaway DBs; a remote server is read-only unless
writes are explicitly enabled.

```
python3 tools/benchmark.py --quick
python3 tools/benchmark.py --mode lan --local-server --quick
```

For a real packager → dispatcher measurement, start `--serve` on the packager
(temp DB/cert), then run the printed client command on dispatch. Full safe
procedure, interpretation, and a baseline table: **BENCHMARKING.md**.

## Test the 8-step flow without UI

```
python3 -m tests.test_flow
python3 -m tests.test_history
python3 -m tests.test_packaging_detect
python3 -m tests.test_ui_smoke
python3 -m tests.test_sticker_preview
python3 -m tests.test_mock_scale
python3 -m tests.test_slip_pdf
python3 -m tests.test_verify
python3 -m tests.test_toast
python3 -m tests.test_products
```

UI smoke checks run offscreen (`QT_QPA_PLATFORM=offscreen`) against a temporary
`SIMPLD_DB` — no production DB writes.
