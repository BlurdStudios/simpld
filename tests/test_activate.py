"""Activation transport: key-string round-trip + dialog smoke. No network."""
import base64
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


def _signed_lic(priv, fp="AFP"):
    body = {"serial": "S-1", "customer": "C", "site_id": "s1",
            "edition": "pro", "packager_fp": fp, "max_stations": 2,
            "features": ["lan"], "issued": "2026-10-26",
            "expires": "2027-10-26", "max_version": "9.x"}
    msg = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["sig"] = base64.b64encode(priv.sign(msg)).decode()
    return body


def test_key_string_round_trip(monkeypatch):
    monkeypatch.setenv("SIMPLD_TEST_FP", "AFP")
    priv, pub = _keypair()
    lic = _signed_lic(priv, L.machine_fingerprint())
    key = L.encode_key_string(lic)
    assert key.startswith(L.KEY_PREFIX) and "\n" not in key and " " not in key
    back = L.decode_key_string(key)
    assert back == lic
    ok, _ = L.verify_license(back, pub)
    assert ok


def test_key_string_tamper(monkeypatch):
    monkeypatch.setenv("SIMPLD_TEST_FP", "AFP")
    priv, pub = _keypair()
    lic = _signed_lic(priv, L.machine_fingerprint())
    key = L.encode_key_string(lic)
    raw = json.loads(base64.urlsafe_b64decode(key[len(L.KEY_PREFIX):]
                                              + "==").decode())
    raw["customer"] = "Evil"
    bad = (L.KEY_PREFIX
           + base64.urlsafe_b64encode(json.dumps(raw).encode()).decode())
    ok, _ = L.verify_license(L.decode_key_string(bad), pub)
    assert not ok


def test_decode_rejects_garbage():
    import pytest
    for bad in ["", "hello", "SIMPLD1.!!!", '{"no": "sig"}']:
        with pytest.raises(ValueError):
            L.decode_key_string(bad)


def test_decode_accepts_raw_json_paste(monkeypatch):
    monkeypatch.setenv("SIMPLD_TEST_FP", "AFP")
    priv, _pub = _keypair()
    lic = _signed_lic(priv, L.machine_fingerprint())
    pasted = json.dumps(lic, indent=2)
    assert L.decode_key_string(pasted) == lic


def test_activate_dialog_builds():
    from PySide6.QtWidgets import QApplication
    from factory.ui.activate import ActivateDialog
    app = QApplication.instance() or QApplication([])
    dlg = ActivateDialog()
    assert "Machine ID" in dlg.mid_edit.text() or dlg.mid_edit.text()
    assert dlg.tabs.count() == 2
