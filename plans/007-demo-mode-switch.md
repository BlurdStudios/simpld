# Plan 007: Demo mode switch — simulate the weigh machine and label printer

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> - `git status --short` → expect empty (the repo was initialized at commit `85fb000`; uncommitted edits from another task would show here). If non-empty, report what shows before proceeding.
> - `grep -rn "demo_mode" factory tests` → expect NO matches.
> - `grep -n "def mock_allowed" factory/hardware/devices.py` → line ~135.
> - `grep -n "class MockEssaeDS215" factory/hardware/mock.py` → line ~29, and its `__init__` takes only `self`.
> - `grep -n "DeviceStrip(" factory/ui/page_packaging.py` → line ~291, `DeviceStrip("packaging", show_station_label=True)`.
> - `grep -n "DEV.mock_allowed()" factory/ui/page_packaging.py` → line ~806.
> If any of these differ, STOP and report.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW–MED (new explicit demo path around fail-closed hardware rules; no change to the default/fail-closed behavior)
- **Depends on**: none
- **Category**: feature / hardware
- **Planned at**: commit `85fb000`, 2026-10-17

## Why this matters

The operator (and the agency demo audience) needs to run the whole app on a machine with **no scale and no printer** — weighing a reel, saving it, and "printing" its label — without touching a single device. Today that is impossible in a frozen build: plan 004 made `mock_allowed()` return False whenever `_is_frozen()`, so `require_scale()`/`require_printer()` block weighing and printing with a "not configured" error even though `build_scale`/`build_printer` fall back to the mock drivers when no port/name is set.

The fix is an **explicit, user-visible demo switch** — not a silent fallback. Default OFF, so production stays fail-closed exactly as plan 004 left it. When ON: the mock scale produces plausible simulated weights, the mock printer logs labels to `print_log.txt`, both device pills read `demo · simulated`, and every toggle asks for confirmation once. The switch must work in the frozen Windows build (that is the point — the demo runs from the installed `.exe`), which is why it is a distinct setting from `SIMPLD_ALLOW_MOCK` and is documented as such.

## Current state

- `factory/hardware/devices.py:65-80` — `build_scale(cfg)` returns `_mock.MockEssaeDS215()` when `scale_port` is blank; `build_printer(cfg)` returns `_mock.MockCitizenCLS621()` when `printer_name` is blank.
- `factory/hardware/devices.py:135-162` — `mock_allowed()` (False when frozen), `require_scale()`, `require_printer()` raise unless a device is configured or the test override is set.
- `factory/hardware/mock.py:29-70` — `MockEssaeDS215` echoes a manual value (`read_weight` returns 0.0 when unset; `freeze` rounds the manual value). No `unfreeze()`. `MockCitizenCLS621` logs jobs to `print_log.txt`.
- `factory/hardware/config.py:36-48` — `DEFAULTS` (persisted by `save()`; only keys in `DEFAULTS` survive a save), `load()` applies `SIMPLD_SCALE_PORT` / `SIMPLD_PRINTER_NAME` env overrides.
- `factory/hardware/status.py:89-105` — `_detail_scale` / `_detail_printer` build the pill text; blank config → state `setup`, detail `not found`.
- `factory/ui/device_strip.py:28-35` — `_STATE_STYLE` maps state names to dot colours; `DeviceStrip.__init__` (line ~226) takes `(station, parent=None, show_station_label=False)` and renders pills + a Refresh button.
- `factory/ui/page_packaging.py:291` — `DeviceStrip("packaging", show_station_label=True)`; line ~806 — `if isinstance(HW.scale, HW.MockEssaeDS215) and DEV.mock_allowed():` in `read_scale()`.
- `factory/ui/page_packaging.py:855-875` — `refresh_hardware()` resets the weigh card when the scale switches mock↔real; `main_window._on_devices_detected` calls it when a strip emits `detected`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Compile check | `python -m py_compile factory/hardware/config.py factory/hardware/mock.py factory/hardware/devices.py factory/hardware/status.py factory/ui/device_strip.py factory/ui/page_packaging.py tests/test_demo_mode.py` | exit 0 |
| New + neighbor tests | `SIMPLD_DB=/tmp/opencode/p007.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_demo_mode.py tests/test_mock_scale.py tests/test_hardware_real.py -q -p no:cacheprovider` | 0 failed |
| Full suite | `SIMPLD_DB=/tmp/opencode/p007-full.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests -q -p no:cacheprovider` | 0 failed, 1 skipped |
| Proof the switch is explicit | `grep -n "demo_mode" factory/hardware/devices.py factory/hardware/config.py factory/hardware/status.py factory/ui/device_strip.py factory/ui/page_packaging.py` | hits in all five files |

**Known host flake**: the full suite occasionally segfaults (exit 139, no summary) on this machine — it has happened in previous plans, at different test files. If that happens: rerun the full suite once. If it crashes twice in a row, report it and stop; do NOT try to fix it (it is a known, documented Qt-teardown issue on this host, not this plan's problem).

## Scope

**In scope**:
- `factory/hardware/config.py`
- `factory/hardware/mock.py`
- `factory/hardware/devices.py`
- `factory/hardware/status.py`
- `factory/ui/device_strip.py`
- `factory/ui/page_packaging.py`
- `tests/test_demo_mode.py` (new)
- `README.md` and `DEMO_WINDOWS.md` (short notes)
- `plans/README.md` (status row)

**Out of scope**:
- `mock_allowed()` / `SIMPLD_ALLOW_MOCK` semantics — unchanged, still dead when frozen.
- `factory/ui/page_dispatch.py` — the scanner is a keyboard wedge and needs no demo path. Do NOT add the switch to the dispatch strip.
- The DB, LAN server, print flows' data shape, `slip_pdf.py`.
- Any file not listed above.

## Steps

### Step 1 — `factory/hardware/config.py`: persist the setting + env override

1. Add to `DEFAULTS` (after `printer_name`):
   ```python
   "demo_mode": False,        # explicit demo switch: simulate scale+printer (no hardware)
   ```
   This is required for `save()` to persist it — `save()` only writes keys present in `DEFAULTS`.
2. In `load()`, after the existing env overrides, add:
   ```python
   if os.environ.get("SIMPLD_DEMO") == "1":
       cfg["demo_mode"] = True
   ```

**Verify**: `python - <<'EOF'` snippet: save `{"demo_mode": True}` to a temp path, `load(path)["demo_mode"] is True`, and a fresh `DEFAULTS` copy has `demo_mode` False. Or rely on the new tests in Step 7.

### Step 2 — `factory/hardware/mock.py`: simulate a plausible weight in demo mode

Add a `demo` flag to `MockEssaeDS215`. Default behavior (constructor with no argument) must stay byte-for-byte equivalent for the existing tests: manual entry, jitter ±0.005, `read_weight()`/`freeze()` returning 0.0 without a manual value.

```python
class MockEssaeDS215(ScaleInterface):
    """Pretends to be the Essae DS-215. Real version opens serial.Serial('/dev/ttyS0', 9600).

    demo=True (set only by devices.build_scale when the demo switch is on)
    simulates plausible reel/carton weights so the app can be shown with no
    hardware. Default instances keep the old manual-entry behavior tests use.
    """

    def __init__(self, demo: bool = False):
        self._manual = 0.0
        self._frozen: float | None = None
        self._demo = bool(demo)
        self._demo_base: float | None = None

    def set_manual(self, kg: float):
        self._manual = float(kg or 0)
        self._frozen = None
        self._demo_base = None

    def _demo_read_base(self) -> float:
        if self._demo_base is None:
            self._demo_base = random.uniform(4.0, 24.0)
        return self._demo_base

    def read_weight(self) -> float:
        if self._manual:
            base = self._frozen if self._frozen is not None else self._manual
            jitter = random.uniform(-0.005, 0.005)
            return round(max(0.0, base + jitter), 3)
        if self._demo:
            if self._frozen is not None:
                return round(self._frozen, 3)
            return round(self._demo_read_base() + random.uniform(-0.02, 0.02), 3)
        return 0.0

    def freeze(self) -> float:
        if self._manual:
            self._frozen = round(self._manual, 3)
            return self._frozen
        if self._demo:
            self._frozen = round(self._demo_read_base(), 3)
            return self._frozen
        self._frozen = round(self._manual, 3)   # old behavior: 0.0
        return self._frozen

    def unfreeze(self):
        self._frozen = None
        if self._demo and not self._manual:
            self._demo_base = None   # next read = a new simulated reel
```

**Verify**: `python -m py_compile factory/hardware/mock.py`, then a quick REPL check: `MockEssaeDS215(demo=True).read_weight()` is in 3.9–24.1, and `MockEssaeDS215().read_weight() == 0.0`.

### Step 3 — `factory/hardware/devices.py`: honor the switch

1. Add the helper next to `mock_allowed()`:
   ```python
   def demo_mode(cfg: dict | None = None) -> bool:
       """True when the explicit demo switch is on (simulated hardware).

       Unlike SIMPLD_ALLOW_MOCK this IS honored in the frozen build on
       purpose: the switched-on demo runs on a machine with no scale or
       printer. It is never silent — the user turns it on from the device
       strip, a confirmation is shown, and the pills read "demo".
       """
       cfg = cfg if cfg is not None else _config.load()
       return bool(cfg.get("demo_mode"))
   ```
2. `build_scale`: demo first —
   ```python
   if demo_mode(cfg):
       return _mock.MockEssaeDS215(demo=True)
   port = str(cfg.get("scale_port") or "").strip()
   ...
   ```
3. `build_printer`: `if demo_mode(cfg): return _mock.MockCitizenCLS621()` before the name check.
4. `require_scale` / `require_printer`: add `or demo_mode(cfg)` to the pass condition and extend the docstring by one line ("The explicit demo switch also passes — see demo_mode()").
5. `mode_text`: add a branch before the existing `is_mock_scale() and is_mock_printer()` check:
   ```python
   if demo_mode(cfg):
       base = "DEMO — simulated scale + printer (no hardware)"
   elif is_mock_scale() and is_mock_printer():
       base = "Demo/mock — no device selected"
   ```
   The existing blank-config output `Demo/mock — no device selected` must stay identical (a test asserts it).
6. `_override_suffix()`: add `"SIMPLD_DEMO"` to the tuple of env names.
7. Update the module docstring: mention the explicit demo switch as the one path that intentionally reaches the mock drivers in a frozen build (contrast with `SIMPLD_ALLOW_MOCK`).

**Verify**: `grep -n "demo_mode" factory/hardware/devices.py` shows the helper + all five call sites. `python -m py_compile`.

### Step 4 — `factory/hardware/status.py`: pills read "demo"

1. In `_detail_scale` and `_detail_printer`, first line:
   ```python
   if _devices.demo_mode(cfg):
       return "demo", "simulated"
   ```
2. In `device_status`, extend the hint block:
   ```python
   if state == "setup":
       out["hint"] = "Click to detect again."
   elif state == "demo":
       out["hint"] = "Demo mode is on — this device is simulated."
   ```
3. Module docstring: add `demo  the explicit demo switch is on — simulated, not real` to the state list.

`needs_setup()` stays as is: `demo` is not a blocker state.

**Verify**: `python -m py_compile factory/hardware/status.py`.

### Step 5 — `factory/ui/device_strip.py`: the switch itself

1. Add to `_STATE_STYLE`: `"demo": "#BE185D",`
2. Imports: add `QMessageBox` to the `PySide6.QtWidgets` import; add
   ```python
   from factory.hardware import config as HWConfig
   from factory.hardware import devices as HWDevices
   ```
3. `DeviceStrip.__init__` signature becomes
   `def __init__(self, station, parent=None, show_station_label=False, demo_switch=False):`
4. After the pills loop and **before** the Refresh button, add:
   ```python
   if demo_switch:
       self.btn_demo = QPushButton("Demo")
       self.btn_demo.setCheckable(True)
       self.btn_demo.setMinimumHeight(28)
       style_button(self.btn_demo, "ghost")
       try:
           self.btn_demo.setChecked(HWDevices.demo_mode())
       except Exception:
           self.btn_demo.setChecked(False)
       self._demo_label(self.btn_demo.isChecked())
       self.btn_demo.toggled.connect(self._toggle_demo)   # connect AFTER setChecked
       outer.addWidget(self.btn_demo, 0, Qt.AlignmentFlag.AlignVCenter)
   ```
5. New methods on `DeviceStrip`:
   ```python
   def _demo_label(self, on: bool) -> None:
       self.btn_demo.setText("Demo ON" if on else "Demo")
       self.btn_demo.setToolTip(
           "Demo mode is ON — the weigh machine and label printer are simulated "
           "(labels go to the print log, not paper). Click to turn it off."
           if on else
           "Turn on demo mode: simulate the weigh machine and label printer "
           "so the app can run with no hardware.")

   def _toggle_demo(self, on: bool) -> None:
       on = bool(on)
       if on:
           ans = QMessageBox.question(
               self, "Turn on demo mode?",
               "Demo mode simulates the weigh machine and label printer.\n\n"
               "Weights are made up and labels are written to the print log "
               "instead of being printed. Turn it off before real production.",
               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
           if ans != QMessageBox.StandardButton.Yes:
               self.btn_demo.blockSignals(True)
               self.btn_demo.setChecked(False)
               self.btn_demo.blockSignals(False)
               self._demo_label(False)
               return
       try:
           cfg = HWConfig.load()
           cfg["demo_mode"] = on
           HWConfig.save(cfg)
           HWDevices.reload_hardware(cfg)
       except Exception as e:
           self.btn_demo.blockSignals(True)
           self.btn_demo.setChecked(not on)
           self.btn_demo.blockSignals(False)
           self._demo_label(not on)
           try:
               QMessageBox.warning(self, "Demo mode",
                                   f"Could not change demo mode: {e}")
           except Exception:
               pass
           return
       self._demo_label(on)
       self.refresh()
       self.detected.emit({"demo": on})
   ```
   Emitting `detected` is deliberate: `main_window` already wires every strip's `detected` to the packaging page's `refresh_hardware()`, which re-arms the weigh/print buttons and resets a stale weigh card.
6. Update the module docstring: the strip now also carries an optional Demo switch (Packaging only).

**Verify**: `python -m py_compile factory/ui/device_strip.py`.

### Step 6 — `factory/ui/page_packaging.py`: enable the switch + make demo reads fresh

1. Line ~291: `self.devices = DeviceStrip("packaging", show_station_label=True, demo_switch=True)`
2. In `read_scale()`, the branch at line ~806 becomes:
   ```python
   if isinstance(HW.scale, HW.MockEssaeDS215) and (DEV.mock_allowed() or DEV.demo_mode()):
       # Test-compat (SIMPLD_ALLOW_MOCK, source checkout only) or the explicit
       # demo switch: echo the manual entry or a fresh simulated reading. Demo
       # unfreezes first so every Read starts a new reel, never a stale freeze.
       try:
           if DEV.demo_mode() and hasattr(HW.scale, "unfreeze"):
               HW.scale.unfreeze()
           self.gross.setValue(HW.scale.read_weight())
       except Exception as e:
           self.gross.setValue(0)
           self._refresh_state()
           return self.say(f"Scale error: {e}", ok=False)
   ```
   Leave the `else` real-scale branch untouched.
3. Update the comment block at lines ~329-333 to mention the demo switch (the "no mock/test-data helpers in production" rule still holds; the demo switch is explicit and user-visible).

`stop_wt()` needs no change — `mock.freeze()` now returns the simulated value.

**Verify**: `python -m py_compile factory/ui/page_packaging.py`.

### Step 7 — `tests/test_demo_mode.py` (new)

Follow the isolation patterns already used in `tests/test_mock_scale.py` (`_fresh_db`, `_page`, `_select_job`) and `tests/test_hardware_real.py` (temp `SIMPLD_HW_JSON`, restored in `finally`). Required tests:

1. `test_config_demo_mode_defaults_off_and_round_trips` — `config.DEFAULTS["demo_mode"] is False`; `save({"demo_mode": True}, tmp)` then `load(tmp)["demo_mode"] is True`.
2. `test_simpld_demo_env_forces_on` — `monkeypatch.setenv("SIMPLD_DEMO", "1")` → `config.load(tmp)["demo_mode"] is True`.
3. `test_demo_mode_honored_even_when_frozen` — `monkeypatch.setattr("factory.db._is_frozen", lambda: True)`; `cfg = {"demo_mode": True}`: `build_scale(cfg)` is `MockEssaeDS215`, `build_printer(cfg)` is `MockCitizenCLS621`, `require_scale(cfg)` and `require_printer(cfg)` do not raise. Then `monkeypatch.setenv("SIMPLD_ALLOW_MOCK", "1")` and assert `mock_allowed() is False` (the test override stays dead when frozen — this plan must not regress plan 004).
4. `test_demo_mode_off_keeps_fail_closed` — same monkeypatch but `cfg = {"demo_mode": False}`: `require_scale(cfg)` raises `RuntimeError`.
5. `test_mock_scale_demo_simulation` — `MockEssaeDS215(demo=True)`: first `read_weight()` in (3.9, 24.1); a second read differs by < 0.05; `freeze()` within 0.05 of the last read; three reads after freeze are all exactly the frozen value; `unfreeze()` then read is again in range. Also the regression: `MockEssaeDS215().read_weight() == 0.0` and `.freeze() == 0.0`.
6. `test_status_pills_read_demo` — `cfg = {"demo_mode": True, "scale_port": "", "printer_name": ""}`; call `DEV.reload_hardware(cfg)`; `status.device_status("scale", cfg)` → `state == "demo"` and `detail == "simulated"`; same for printer; `status.needs_setup("packaging", cfg) is False`. In `finally`, restore with `DEV.reload_hardware({"scale_port": "", "printer_name": ""})`.
7. `test_packaging_demo_switch_toggles_and_weighs` — UI test:
   - point `SIMPLD_HW_JSON` at a temp file (save/restore, pattern from `test_hardware_real.py` around line 534);
   - `monkeypatch.delenv("SIMPLD_ALLOW_MOCK", raising=False)` — demo alone must pass the gates;
   - fresh temp DB + `PackagingPage()`;
   - `assert not pg.devices.btn_demo.isChecked()`;
   - patch `QMessageBox.question` → `Yes`, `pg.devices.btn_demo.click()`;
   - assert checked, text `"Demo ON"`, `config.load()["demo_mode"] is True`, `isinstance(factory.hardware.mock.scale, MockEssaeDS215)`, and `factory.hardware.devices.demo_mode() is True`;
   - create a job, `_select_job(pg, job["id"])`, `pg.read_scale()` → `pg.gross.value() > 0` and `"not configured" not in pg.msg.casefold()`;
   - click again (no question patch needed on disable; assert `question` was NOT called a second time by re-patching with a `side_effect=AssertionError` variant or by counting calls) → `config.load()["demo_mode"] is False`;
   - `pg.close()`, restore env.

**Verify**: run the "New + neighbor tests" command → 0 failed.

### Step 8 — docs

- `README.md`: add a short "Demo mode" note near the hardware section (find the right spot; keep it 2–4 lines): the Demo switch on the Packaging device strip simulates the weigh machine and label printer for training/demos (no hardware needed); labels go to `print_log.txt`; `SIMPLD_DEMO=1` forces it on at launch; it is off by default and real production should leave it off.
- `DEMO_WINDOWS.md`: add a short subsection pointing at the same switch for the demo build.
- `plans/README.md`: set row 007 to `DONE` (or `BLOCKED` with a one-line reason).

## Test plan

- New file `tests/test_demo_mode.py` (7 tests above). The suite's existing hardware tests (`test_mock_scale.py`, `test_hardware_real.py`) must stay green unmodified — they encode the fail-closed rules this plan must not weaken.
- Nothing in the tests touches `data/` — always temp `SIMPLD_DB` and temp `SIMPLD_HW_JSON`.

## Done criteria

- [ ] `grep -rn "demo_mode" factory | grep -v __pycache__` shows the setting in config, devices, status, device_strip, page_packaging
- [ ] `tests/test_demo_mode.py` exists and all 7 tests pass
- [ ] `tests/test_mock_scale.py` and `tests/test_hardware_real.py` still pass unmodified
- [ ] Full suite: 0 failed, 1 skipped (one rerun allowed for the known segfault flake)
- [ ] Default behavior unchanged: blank config without the switch still blocks weighing/printing with "not configured"
- [ ] `plans/README.md` row 007 updated
- [ ] No file outside Scope modified

## STOP conditions

- `mock_allowed()` or `SIMPLD_ALLOW_MOCK` behavior changes in any way — plan 004's guarantee must stay intact (the new tests check it; do not "fix" a failure by weakening the old tests).
- `test_blank_config_selects_mock` (asserts `mode_text` starts with `Demo/mock` for blank config) fails after your edit — your `mode_text` branch order is wrong; fix the branch, do not touch the test.
- The full suite segfaults twice in a row (known host flake) — report and stop.
- Any test writes `data/hardware.json` or `data/factory.db` in the repo (check `git status --short` before committing nothing — you must not run commit at all; still, a modified `data/` file means an env leak in a test) — STOP and report.
- You need a decision the plan does not cover (e.g. a different switch location): report it instead of improvising.

## Maintenance notes

- Demo mode is per machine, stored in `hardware.json` (`demo_mode: true`) — so a demo laptop can be left in demo mode, and a factory PC never is.
- Demo mode does NOT change the database; demo records land wherever the app is pointed. The confirmation dialog and the `demo · simulated` pills are the honest-markers-by-design; keep them if the switch is ever moved.
- The switch is deliberately NOT `SIMPLD_ALLOW_MOCK`: that override stays test-only and frozen-dead.
- If the strip later grows more toggles, the Demo button pattern (checkable `ghost` button + `_demo_label` + `detected.emit`) is the template.
