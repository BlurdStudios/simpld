"""Evening backup: copy the local SQLite DB to a timestamped file.

The data lives on this PC only, so before closing for the day the operator
takes one backup (default data/backups/, or a USB stick via the folder
picker). The file is a plain SQLite DB — it opens in the app by pointing
SIMPLD_DB at it, and it is also the vehicle if a second PC ever needs data.
Stdlib only.
"""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime

from .db import DB_PATH, app_data_dir


def _default_dest_dir() -> str:
    """Frozen (.exe): <LOCALAPPDATA>/Simpld/backups. Source: <repo>/data/backups."""
    try:
        root = app_data_dir()
    except Exception:
        root = None
    if root:
        return os.path.join(root, "backups")
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(base), "data", "backups")


def _unique_out(dest_dir: str, ts: str) -> str:
    """simpld_YYYY-MM-DD_HHMM.db; never overwrite: add -01, -02, ... on collision."""
    cand = os.path.join(dest_dir, f"simpld_{ts}.db")
    if not os.path.exists(cand):
        return cand
    i = 1
    while True:
        cand = os.path.join(dest_dir, f"simpld_{ts}-{i:02d}.db")
        if not os.path.exists(cand):
            return cand
        i += 1


def backup_db(dest_dir: str | None = None, src: str | None = None,
              when: datetime | None = None) -> str:
    """Online-safe backup via sqlite3.Connection.backup (WAL-safe, open-conn safe).

    Writes to a temp file in dest_dir then atomically os.replace()s it, so a
    failure never leaves a partial .db behind. Runs PRAGMA integrity_check on
    the copy before publishing. Refuses src==dest (same file) and never
    overwrites an existing backup (timestamp collision -> -01/-02 suffix).
    Returns the final path.
    """
    s = src or DB_PATH
    if not os.path.exists(s):
        raise ValueError(f"No database to back up at {s}")
    d = dest_dir or os.environ.get("SIMPLD_BACKUP_DIR") or _default_dest_dir()
    os.makedirs(d, exist_ok=True)
    ts = (when or datetime.now()).strftime("%Y-%m-%d_%H%M")
    out = _unique_out(d, ts)
    # Same-file / self-backup guard (covers symlinks, case, relative paths).
    try:
        if os.path.samefile(s, out):
            raise ValueError("Backup destination is the same file as the source DB")
    except FileNotFoundError:
        pass
    if os.path.abspath(os.path.realpath(s)) == os.path.abspath(os.path.realpath(out)):
        raise ValueError("Backup destination is the same file as the source DB")
    if os.path.isdir(out):
        raise ValueError(f"Backup destination is a directory: {out}")
    tmp = out + f".part-{os.getpid()}.tmp"
    src_con = None
    dst_con = None
    try:
        # Read-only open: never mutates/WAL-checkpoints the live production DB.
        src_con = sqlite3.connect(f"file:{os.path.abspath(s)}?mode=ro", uri=True)
        # Isolate destination: temp file published atomically at the end.
        if os.path.exists(tmp):
            os.remove(tmp)
        dst_con = sqlite3.connect(tmp)
        with dst_con:
            dst_con.execute("PRAGMA journal_mode=DELETE")
        src_con.backup(dst_con)
        # Verify the copy before publishing (corrupt/incomplete -> abort).
        row = dst_con.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise OSError(f"Backup integrity check failed: {row[0] if row else 'unknown'}")
        dst_con.commit()
        dst_con.close()
        dst_con = None
        src_con.close()
        src_con = None
        os.replace(tmp, out)
    except Exception:
        try:
            if dst_con is not None:
                dst_con.close()
        except Exception:
            pass
        try:
            if src_con is not None:
                src_con.close()
        except Exception:
            pass
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise
    return out
