"""Licence heartbeat: offer install is verified, acked, and never re-applied."""
import os
import tempfile
from unittest import mock

import pytest

from factory import sync as SYNC


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("SIMPLD_SYNC_URL", "https://dash.example")
    monkeypatch.setenv("SIMPLD_SYNC_SITE_ID", "site-1")
    monkeypatch.setenv("SIMPLD_SYNC_KEY", "k")


def test_not_configured_reports_cleanly(monkeypatch):
    monkeypatch.setattr(SYNC, "configured", lambda: False)
    res = SYNC.licence_heartbeat()
    assert res["ok"] is False and res["installed"] is None


def test_offer_verified_then_acked(cfg, monkeypatch):
    calls = []
    offer = {"serial": "S1", "licence": {"serial": "S1"}}
    replies = [{"ok": True, "offer": offer}, {"ok": True, "offer": None}]

    def fake_post(path, payload, timeout=SYNC.TIMEOUT):
        calls.append(payload)
        return replies.pop(0)

    from factory import license as LIC
    monkeypatch.setattr(SYNC, "_post_json", fake_post)
    monkeypatch.setattr(LIC, "load_license", lambda *a, **k: None)
    monkeypatch.setattr(LIC, "verify_license", lambda lic, *a, **k: (True, "ok"))
    saved = []
    monkeypatch.setattr(LIC, "save_license_file", lambda lic, *a, **k: saved.append(lic) or "p")
    res = SYNC.licence_heartbeat()
    assert res == {"ok": True, "installed": "S1", "detail": "ok"}
    assert saved == [{"serial": "S1"}]
    assert calls[-1].get("ack_serial") == "S1"


def test_bad_offer_is_not_installed(cfg, monkeypatch):
    from factory import license as LIC
    monkeypatch.setattr(SYNC, "_post_json",
                        lambda p, pl, timeout=0: {"offer": {"serial": "S2", "licence": {"serial": "S2"}}})
    monkeypatch.setattr(LIC, "load_license", lambda *a, **k: None)
    monkeypatch.setattr(LIC, "verify_license", lambda lic, *a, **k: (False, "bad sig"))
    saved = []
    monkeypatch.setattr(LIC, "save_license_file", lambda lic, *a, **k: saved.append(lic))
    res = SYNC.licence_heartbeat()
    assert res["ok"] is False and saved == []


def test_already_installed_offer_is_noop(cfg, monkeypatch):
    from factory import license as LIC
    monkeypatch.setattr(LIC, "load_license", lambda *a, **k: {"serial": "S3"})
    saved = []
    monkeypatch.setattr(LIC, "save_license_file", lambda lic, *a, **k: saved.append(lic))
    assert SYNC._install_offer({"serial": "S3", "licence": {"serial": "S3"}}) == "S3"
    assert saved == []


def test_request_trial_sends_days(cfg, monkeypatch):
    sent = {}

    def fake_post(path, payload, timeout=SYNC.TIMEOUT):
        sent.update(path=path, payload=payload)
        return {"ok": True, "status": "pending"}

    monkeypatch.setattr(SYNC, "_post_json", fake_post)
    res = SYNC.request_trial(14, "need more time")
    assert res["ok"] is True
    assert sent["path"] == "/api/trial-request"
    assert sent["payload"]["days"] == 14
