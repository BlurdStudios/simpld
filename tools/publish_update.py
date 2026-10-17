#!/usr/bin/env python3
"""Publish a built installer to the LAN / folder update channel.

Run this on the packager computer after getting a `Simpld-Setup-X.Y.Z.exe`
there (from the GitHub release, a USB stick, the build output — any way):

    python3 tools/publish_update.py dist/Simpld-Setup-1.0.1.exe \
        --dir data/updates --notes "Carton sticker fix"

It writes into --dir (created if missing):

    Simpld-Setup-1.0.1.exe           copy of the installer
    Simpld-Setup-1.0.1.exe.sha256    sha256sum-style checksum
    latest.json                      manifest the app + LAN server serve

`factory/lan_server.py --updates-dir <dir>` then offers the manifest to every
LAN client (`GET /updates/latest.json`); the packager itself reads the folder
directly. Downgrades are refused unless --force, so the fleet can never be
silently rolled back. See UPDATES.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory import updater  # noqa: E402

CHUNK = 256 * 1024


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Publish an installer to the LAN update channel folder.")
    ap.add_argument("installer", help="path to Simpld-Setup-X.Y.Z.exe")
    ap.add_argument("--dir", required=True,
                    help="updates folder (served by lan_server --updates-dir)")
    ap.add_argument("--notes", default="", help="shown in the update prompt")
    ap.add_argument("--force", action="store_true",
                    help="allow publishing an older version than latest.json")
    ap.add_argument("--prune", action="store_true",
                    help="delete other Simpld-Setup-* files from the folder")
    ns = ap.parse_args(argv)

    name = os.path.basename(ns.installer)
    match = updater.INSTALLER_RE.match(name)
    if not match or not os.path.isfile(ns.installer):
        print(f"error: {ns.installer!r} is not a Simpld-Setup-X.Y.Z.exe file",
              file=sys.stderr)
        return 2
    version = updater.normalize_version(match.group(1))
    if version is None:
        print("error: installer name has no usable version", file=sys.stderr)
        return 2

    out_dir = ns.dir
    os.makedirs(out_dir, exist_ok=True)

    manifest_path = os.path.join(out_dir, updater.MANIFEST_NAME)
    if os.path.isfile(manifest_path) and not ns.force:
        try:
            with open(manifest_path, "rb") as f:
                current = updater.parse_update_manifest(f.read(64 * 1024))
        except updater.UpdateError as exc:
            print(f"error: existing {updater.MANIFEST_NAME} is invalid "
                  f"({exc}); fix it or pass --force", file=sys.stderr)
            return 2
        if updater.parse_version(version) < updater.parse_version(current.version):
            print(f"error: {updater.MANIFEST_NAME} already publishes "
                  f"{current.version} (> {version}); pass --force to "
                  "publish a downgrade anyway", file=sys.stderr)
            return 2

    digest = _sha256_of(ns.installer)
    dest_exe = os.path.join(out_dir, name)
    if os.path.abspath(ns.installer) != os.path.abspath(dest_exe):
        shutil.copy2(ns.installer, dest_exe)
    with open(dest_exe + ".sha256", "w", encoding="ascii") as f:
        f.write(f"{digest}  {name}\n")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"version": version, "file": name, "sha256": digest,
                   "notes": ns.notes}, f, indent=2)
        f.write("\n")

    if ns.prune:
        for entry in sorted(os.listdir(out_dir)):
            if entry == name or entry == name + ".sha256":
                continue
            if (updater.INSTALLER_RE.match(entry)
                    or (entry.endswith(".exe.sha256")
                        and updater.INSTALLER_RE.match(entry[:-7]))):
                os.unlink(os.path.join(out_dir, entry))
                print(f"pruned {entry}")

    print(f"published Simpl'd {version} to {out_dir}")
    print(f"  {name}")
    print(f"  {name}.sha256")
    print(f"  {updater.MANIFEST_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
