#!/usr/bin/env python3
"""Run the test suite one file per process — the CI gate.

Why not one pytest process for everything? Qt/PySide6 can abort the whole
interpreter when many widget tests share a process; the fault site wanders
between unrelated tests (a Windows CI access violation, and a segfault on the
dev host — see plans/README.md). Every test file passes on its own, so this
runner gives each file a fresh interpreter and retries a crashed/failed file
once. A file that fails twice fails the run.

    python3 tools/run_tests.py                 # whole suite, CI mode
    python3 tools/run_tests.py --retries 2
    python3 tools/run_tests.py tests/test_ui_smoke.py tests/test_updater.py

Each file is run as::

    <python> -m pytest <file> -q -p no:cacheprovider

On failure the file names are written to `.pytest-failed-files` (one path per
line) so the CI "Diagnose" step can rerun just those with -x.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILED_FILE = ROOT / ".pytest-failed-files"


def _default_python() -> str:
    """The interpreter to run pytest with.

    Normally sys.executable. Some launchers (the T3 Code AppImage on the dev
    host) set sys.executable to the launcher instead of a Python binary;
    fall back to the python3 on PATH there.
    """
    name = Path(sys.executable).name.lower()
    if name.startswith("python"):
        return sys.executable
    return shutil.which("python3") or shutil.which("python") or sys.executable


def discover() -> list[Path]:
    return sorted((ROOT / "tests").glob("test_*.py"))


def run_one(path: Path, timeout: float, python: str) -> tuple[int, float]:
    cmd = [python, "-m", "pytest", str(path), "-q",
           "-p", "no:cacheprovider"]
    started = time.monotonic()
    try:
        code = subprocess.run(cmd, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"!! {path.name}: timed out after {timeout:.0f}s", flush=True)
        code = 124
    except KeyboardInterrupt:
        raise
    return code, time.monotonic() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*",
                        help="test files (default: tests/test_*.py)")
    parser.add_argument("--retries", type=int, default=1,
                        help="extra attempts for a failed/crashed file "
                             "(default: 1)")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds per attempt (default: 900)")
    parser.add_argument("--python", default=_default_python(),
                        help="interpreter for pytest (default: this one)")
    args = parser.parse_args(argv)

    files = [Path(f).resolve() for f in args.files] or discover()
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        print("no such test file: " + ", ".join(missing), file=sys.stderr)
        return 2

    attempts = max(1, args.retries + 1)
    failed: list[tuple[Path, int]] = []
    skipped = 0
    started_all = time.monotonic()

    for path in files:
        code = 1
        for attempt in range(1, attempts + 1):
            suffix = "" if attempt == 1 else f" (retry {attempt}/{attempts})"
            print(f"\n=== {path.name}{suffix} ===", flush=True)
            code, elapsed = run_one(path, args.timeout, args.python)
            if code == 0:
                print(f"PASS {path.name} ({elapsed:.1f}s)", flush=True)
                break
            if code == 5:
                # pytest: no tests collected (test_flow.py is a placeholder).
                print(f"SKIP {path.name} (no tests collected)", flush=True)
                skipped += 1
                break
            print(f"FAIL {path.name} (attempt {attempt}/{attempts}, "
                  f"exit {code}, {elapsed:.1f}s)", flush=True)
            if attempt < attempts:
                print(f"    retrying {path.name} - a fresh process often "
                      f"clears the Qt/PySide6 flake", flush=True)
        else:
            failed.append((path, code))

    total = time.monotonic() - started_all
    print("\n===== test summary =====", flush=True)
    for path, code in failed:
        rel = os.path.relpath(path, ROOT)
        print(f"FAIL {rel} (exit {code} after {attempts} attempts)", flush=True)
    print(f"{len(files)} files: {len(files) - len(failed) - skipped} passed, "
          f"{skipped} skipped, {len(failed)} failed in {total:.1f}s", flush=True)

    if failed:
        FAILED_FILE.write_text(
            "".join(os.path.relpath(p, ROOT) + "\n" for p, _ in failed),
            encoding="utf-8")
        return 1
    try:
        FAILED_FILE.unlink()
    except FileNotFoundError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
