# Device status (3 devices, 2 stations, auto-detected)

| Station | Device | Model | Detected? | Status states |
|---------|--------|-------|-----------|---------------|
| Packaging | Weigh machine | Essae DS-215 (RS232 8N1) | yes — probes every serial port | `ready` / `setup` / `error` / `detecting` |
| Packaging | Label printer | Citizen CL-S621II (installed driver) | yes — matches the OS printer list | `ready` / `setup` / `error` / `detecting` |
| Dispatch | QR scanner | USB keyboard wedge | n/a — nothing to detect | `idle` / `active` |

**There is no Devices dialog and no port dropdown.** Hardware is detected
automatically; the pills at the top of each station page are the whole
hardware surface.

## How detection works

- **Weigh machine** — opens each serial port at 9600 8N1 and keeps the one that
  answers with a *parseable weight frame*, judged by the same
  `parse_weight_line()` the app uses to read weights. That is a real handshake,
  not a name guess, so a wrong port stays silent and a false positive is not
  possible. Other baud rates are only tried if nothing answers.
- **Label printer** — reads the installed printer list and picks the name
  matching the Citizen CL-S621 family.
- **QR scanner** — a keyboard wedge; the OS types into the focused box. Its
  pill tracks whether a scan has landed this session (`idle` → `active`).

Detection runs on a worker thread (`DetectionController`), so probing ports
never blocks the UI. It fires once at startup (`MainWindow.detect_devices()`).

## Interacting with the pills

- **Click a pill** — re-detects that one device.
- **Click Refresh** — re-detects everything.
- The Refresh button disables while a probe is in flight, and only the device
  being probed shows the violet "detecting" state.

Detected values are written to `hardware.json`, so the env overrides
(`SIMPLD_SCALE_PORT` / `SIMPLD_PRINTER_NAME`) still win, and `devices.py` keeps
failing closed — an undetected scale still blocks weighing rather than quietly
using the mock.

## When two devices answer / none do

- **Several scales answer** — the already-configured port wins, so a restart
  never silently migrates to a different machine.
- **Nothing answers** — the pill reads `not found` and the app stays
  unconfigured (fail-closed). Fix the cable/power and click the pill again.

## Two layout rules that are load-bearing

1. **The strip lives in the page ROOT layout**, above the splitter, not inside
   a pane. The Packaging left pane is ~360px and two pills need ~560px; in
   there, Qt squeezed it until "Weigh machine" rendered on top of "not found".
2. **Every pill child is pinned to `QSizePolicy.Minimum`** with each label's
   minimum width set to its full text width, so a too-narrow pane *overflows*
   instead of overlapping, and names are never truncated to "Weigh mac…".

Verified at the app's 1080x680 minimum and in a deliberately broken 60px
sidebar.

## Files

| file | what it is |
|------|-----------|
| `factory/hardware/autodetect.py` | the probes. **No Qt** — runs on a worker thread |
| `factory/hardware/status.py` | device registry, state resolution, scan tracking, pure logic |
| `factory/ui/device_strip.py` | `DeviceStrip` / `DevicePill` / `DetectionController` |

## Concurrency (regression-tested)

Every strip shares **one app-wide `DetectionController`**, so two pages asking
to detect at once cannot probe the same COM ports simultaneously. `detect_all`
also merges both results into a **single read-modify-write** of the config.

Both exist because of a real bug: detection originally ran once per strip, and
`detect_printer` re-read and rewrote the whole config — so a concurrent scale
save got overwritten back to empty and the weigh machine silently "forgot" its
port between runs. `test_autodetect_concurrent_runs_do_not_clobber` pins it.