"""Sync buttons: zero-config messaging and visible push failures."""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import sync as S
from factory.ui import pages_misc as PM
from factory.ui import settings_dialog as SD


def _unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMPLD_SYNC_URL", raising=False)
    monkeypatch.delenv("SIMPLD_SYNC_SITE_ID", raising=False)
    monkeypatch.delenv("SIMPLD_SYNC_KEY", raising=False)
    monkeypatch.setenv("SIMPLD_DB", str(tmp_path / "empty.db"))
    monkeypatch.setenv("SIMPLD_DATA_DIR", str(tmp_path / "appdata"))


def test_settings_sync_now_shows_setup_hint_when_unconfigured(tmp_path, monkeypatch):
    _unconfigured(tmp_path, monkeypatch)
    dlg = mock.Mock()
    dlg._refresh_sync_status = mock.Mock()
    with mock.patch.object(SD.QMessageBox, "information") as info, \
         mock.patch.object(S, "push_now") as push:
        SD.SettingsDialog._sync_now(dlg)
    push.assert_not_called()
    dlg._refresh_sync_status.assert_called_once_with()
    msg = info.call_args.args[2]
    assert "vendor" in msg.lower()
    assert "nothing to enter" in msg.lower()
    assert "Settings" not in msg


def test_summary_sync_now_uses_same_setup_hint(tmp_path, monkeypatch):
    _unconfigured(tmp_path, monkeypatch)
    page = mock.Mock()
    with mock.patch.object(PM.QMessageBox, "information") as info:
        PM.SummaryPage.sync_now(page)
    msg = info.call_args.args[2]
    assert msg == S.setup_hint()
    assert "Settings" not in msg


def test_settings_sync_now_surfaces_push_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLD_SYNC_URL", "https://dash.example")
    monkeypatch.setenv("SIMPLD_SYNC_SITE_ID", "site")
    monkeypatch.setenv("SIMPLD_SYNC_KEY", "k")
    dlg = mock.Mock()
    dlg._refresh_sync_status = mock.Mock()
    res = {"ok": False, "queued": False, "detail": "HTTP 401 from dashboard push"}
    with mock.patch.object(S, "push_now", return_value=res), \
         mock.patch.object(SD.QMessageBox, "warning") as warn:
        SD.SettingsDialog._sync_now(dlg)
    assert "HTTP 401" in warn.call_args.args[2]
    dlg._refresh_sync_status.assert_called_once_with()
