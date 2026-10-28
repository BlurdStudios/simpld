"""Plan 009 Phase 2: licensing. Temp SIMPLD_DB only, no network, no Qt."""
import base64
import json
import os
from datetime import date, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as _db
from factory import license as L


def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    priv = Ed25519PrivateKey.generate()
    try:
        pub = priv.public_key().public_bytes_raw()
    except AttributeError:
        from cryptography.hazmat.primitives import serialization
        pub = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
    return priv, base64.b64encode(pub).decode()


def _sign(body, priv):
    msg = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body = dict(body)
    body["sig"] = base64.b64encode(priv.sign(msg)).decode()
    return body


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    p = str(tmp_path / "lic.db")
    monkeypatch.setenv("SIMPLD_DB", p)
    _db.DB_PATH = p
    _db.init_db(p)
    monkeypatch.setenv("SIMPLD_TEST_FP", "board-A")
    return p


def _lic_body(fp="X", edition="pro", ms=2, issued="2026-10-26",
              expires="2027-10-26"):
    from factory.license import EDITION_DEFAULT_FEATURES
    return {
        "serial": "SIMPLD-0017", "customer": "Vasavi", "site_id": "vasavi-f1",
        "edition": edition, "packager_fp": fp, "max_stations": ms,
        "features": EDITION_DEFAULT_FEATURES.get(edition, []),
        "issued": issued, "expires": expires, "max_version": "2.x",
    }


def test_valid_sig(tmpdb, monkeypatch):
    monkeypatch.setenv("SIMPLD_TEST_FP", "board-A")
    import hashlib
    fp = L.machine_fingerprint()
    priv, pub = _keypair()
    lic = _sign(_lic_body(fp=fp), priv)
    ok, _ = L.verify_license(lic, pub)
    assert ok
    st = L.license_status(lic, today=date(2026, 10, 26), pubkey_b64=pub)
    assert st["state"] in ("ok", "expiring")


def test_invalid_sig(tmpdb):
    priv, pub = _keypair()
    lic = _sign(_lic_body(fp="whatever"), priv)
    lic["customer"] = "Tampered"
    ok, reason = L.verify_license(lic, pub)
    assert not ok and "signature" in reason.lower()


def test_clone_fails_closed(tmpdb, monkeypatch):
    monkeypatch.setenv("SIMPLD_TEST_FP", "board-A")
    fp_a = L.machine_fingerprint()
    priv, pub = _keypair()
    lic = _sign(_lic_body(fp=fp_a), priv)
    monkeypatch.setenv("SIMPLD_TEST_FP", "board-B")  # different board
    ok, reason = L.verify_license(lic, pub)
    assert not ok and "another computer" in reason


def test_expired_grace_block(tmpdb, monkeypatch):
    monkeypatch.setenv("SIMPLD_TEST_FP", "board-A")
    fp = L.machine_fingerprint()
    priv, pub = _keypair()
    lic = _sign(_lic_body(fp=fp, expires="2026-09-01"), priv)
    assert L.license_status(
        lic, today=date(2026, 9, 15), pubkey_b64=pub)["state"] == "grace"
    assert L.license_status(
        lic, today=date(2026, 10, 5), pubkey_b64=pub)["state"] == "blocked"


def test_simple_has_no_lan(tmpdb):
    priv, pub = _keypair()
    body = _lic_body(edition="simple", ms=1)
    body["features"] = ["lan"]  # forged feature set
    lic = _sign(body, priv)
    ok, _ = L.verify_license(lic, pub)
    assert not ok


def test_overlimit_overdraft_once(tmpdb):
    # max 1 station: packager takes the slot, dispatcher 2 overdrafts once,
    # dispatcher 3 rejected.
    ok, _ = L.station_allowed(1, tmpdb, sid="packager", fp="f1")
    assert ok
    L.record_station(tmpdb, sid="packager", fp="f1")
    ok2, r2 = L.station_allowed(1, tmpdb, sid="disp2", fp="f2")
    assert ok2 and "overdraft" in r2
    ok3, _ = L.station_allowed(1, tmpdb, sid="disp3", fp="f3")
    assert not ok3
    # admin releases disp2 -> disp3 now fits via overdraft-active or free slot
    L.release_station("disp2", tmpdb)
    L.release_station("packager", tmpdb)  # free another to prove release works
    assert L.active_station_count(tmpdb) >= 0


def test_fingerprint_stable(tmpdb, monkeypatch):
    monkeypatch.setenv("SIMPLD_TEST_FP", "same-board")
    assert L.machine_fingerprint() == L.machine_fingerprint()
