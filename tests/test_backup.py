"""Safe-backup tests. Temp DB/files only — never production data/factory.db."""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory import db as DB
from factory import backup as B
from factory import services as S
from tests._auth_helper import ensure_default_users


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-backup-"), "factory.db")
    DB.init_db(tmp)
    from factory import auth as _A
    ensure_default_users(tmp)
    assert _A.authenticate("admin", "admin123", tmp) is not None
    return tmp


def test_backup_copies_data_with_open_wal_writer():
    """Source stays open in WAL with uncheckpointed committed rows; backup sees them."""
    p = _fresh_db()
    job = S.create_job("BACKUP WAL JOB", core_wt=0.280, path=p)
    # Hold the source connection OPEN (WAL) while backing up.
    holder = DB.get_conn(p)
    try:
        holder.execute(
            "INSERT INTO jobs(job_no, job_name, size_mm, core_wt, status, created_at)"
            " VALUES(?,?,?,?, 'open', ?)",
            (999001, "OPEN-WAL-JOB", 0, 0.5, DB.now_iso()))
        holder.commit()
        dest = tempfile.mkdtemp(prefix="simpld-bak-")
        out = B.backup_db(dest, src=p)
        assert os.path.exists(out)
        c = sqlite3.connect(out)
        try:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            names = {r[0] for r in c.execute("SELECT job_name FROM jobs").fetchall()}
            assert "OPEN-WAL-JOB" in names, names
        finally:
            c.close()
    finally:
        holder.close()


def test_backup_never_overwrites_collision_gets_suffix():
    p = _fresh_db()
    S.create_job("COLLISION JOB", core_wt=0.280, path=p)
    dest = tempfile.mkdtemp(prefix="simpld-bak-")
    when = datetime(2026, 10, 10, 18, 30)
    first = B.backup_db(dest, src=p, when=when)
    second = B.backup_db(dest, src=p, when=when)
    assert first != second
    assert os.path.exists(first) and os.path.exists(second)
    assert second.endswith("-01.db"), second
    # Original untouched.
    assert os.path.getsize(first) > 0 and os.path.getsize(second) > 0


def test_backup_source_inside_dest_dir_never_clobbered():
    import hashlib
    import shutil
    p = _fresh_db()
    S.create_job("SELFDIR JOB", core_wt=0.280, path=p)
    d = tempfile.mkdtemp(prefix="simpld-bak-")
    # Source DB lives inside the destination dir under the exact name the
    # timestamp scheme would emit: backup must take the -01 suffix and leave
    # the source byte-identical (never truncated/overwritten mid-read).
    src_as_target = os.path.join(d, "simpld_2026-01-01_0000.db")
    shutil.copy2(p, src_as_target)
    before = hashlib.sha256(open(src_as_target, "rb").read()).digest()
    out = B.backup_db(d, src=src_as_target, when=datetime(2026, 1, 1, 0, 0))
    assert out.endswith("-01.db"), out
    assert hashlib.sha256(open(src_as_target, "rb").read()).digest() == before
    assert os.path.getsize(out) > 0


def test_backup_failure_leaves_no_partial(tmp_path=None):
    import pytest
    with pytest.raises(ValueError):
        B.backup_db(tempfile.mkdtemp(prefix="simpld-bak-"),
                     src="/nonexistent/path/factory.db")
    # No .part temp files left behind in a fresh dest.
    d = tempfile.mkdtemp(prefix="simpld-bak-")
    try:
        B.backup_db(d, src="/nonexistent/path/factory.db")
    except ValueError:
        pass
    assert [f for f in os.listdir(d) if ".part-" in f] == []


def test_backup_restores_and_opens():
    """Backup file is a usable DB: integrity ok + tables queryable."""
    p = _fresh_db()
    job = S.create_job("RESTORE JOB", core_wt=0.280, path=p)
    r = S.create_reel(job["id"], gross=11.0, core=0.280, path=p)
    dest = tempfile.mkdtemp(prefix="simpld-bak-")
    out = B.backup_db(dest, src=p)
    c = sqlite3.connect(out)
    try:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= 1
        assert c.execute("SELECT COUNT(*) FROM reels").fetchone()[0] >= 1
    finally:
        c.close()


def test_frozen_default_db_is_localappdata_style(monkeypatch, tmp_path):
    """app_data_dir() honors SIMPLD_DATA_DIR env (portable/test hook).

    The override is compared through the same abspath/expanduser
    normalization app_data_dir() applies, so the test holds on POSIX and
    Windows (a literal "/tmp/..." flips to "C:\\tmp\\..." on Windows).
    """
    target = str(tmp_path / "simpld-test-data")
    monkeypatch.setenv("SIMPLD_DATA_DIR", target)
    assert DB.app_data_dir() == os.path.abspath(os.path.expanduser(target))
