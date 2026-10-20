"""Updater tests: version logic, manifests, verification, LAN channel.

No Qt and no real internet. The LAN end-to-end tests run a real TLS server on
127.0.0.1 with a temp updates directory (same pattern as test_lan_transport.py)
and exercise updater.LanSource + download_and_verify against it. Everything
here is offline-safe and touches only temp files.
"""
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory import lan_server as LS
from factory import updater as U
from factory.db import init_db
from tests._auth_helper import ensure_default_users


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _write_installer(folder: str, version: str,
                     data: bytes = b"fake-installer") -> tuple[str, str]:
    """Create Simpld-Setup-<version>.exe + its .sha256 sidecar."""
    name = f"Simpld-Setup-{version}.exe"
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(data)
    digest = hashlib.sha256(data).hexdigest()
    with open(path + ".sha256", "w", encoding="ascii") as f:
        f.write(f"{digest}  {name}\n")
    return path, digest


def _info(version: str, url: str, sha: str, source: str = "folder") -> U.UpdateInfo:
    return U.UpdateInfo(version=version, filename=f"Simpld-Setup-{version}.exe",
                        url=url, sha256=sha, source=source)


class _Src:
    """Stand-in channel for check_for_update orchestration tests."""

    def __init__(self, name: str, info=None, exc=None):
        self.name = name
        self._info = info
        self._exc = exc

    def fetch(self):
        if self._exc is not None:
            raise self._exc
        return self._info


# --------------------------------------------------------------------------
# version logic
# --------------------------------------------------------------------------

def test_parse_and_compare_versions():
    assert U.parse_version("v1.2.3") == (1, 2, 3)
    assert U.parse_version(" 1.0 ") == (1, 0)
    assert U.parse_version("1.0.1.exe") is None
    assert U.parse_version("garbage") is None
    assert U.parse_version("") is None
    assert U.parse_version(None) is None
    assert U.normalize_version("v1.0.1") == "1.0.1"
    assert U.normalize_version("nope") is None

    assert U.is_newer("1.0.1", "1.0.0")
    assert U.is_newer("v1.2", "1.1.9")
    assert U.is_newer("1.0.1", "1.0")       # trailing segments count as newer
    assert not U.is_newer("1.0.0", "1.0.0")
    assert not U.is_newer("0.9", "1.0.0")
    assert not U.is_newer("not-a-version", "1.0.0")  # junk is never newer
    assert not U.is_newer("1.0.1", "junk-current")   # fail closed


# --------------------------------------------------------------------------
# checksum + manifest parsing
# --------------------------------------------------------------------------

def test_parse_sha256_file():
    hex64 = "ab" * 32
    assert U.parse_sha256_file(f"{hex64}  Simpld-Setup-1.0.1.exe\n") == hex64
    assert U.parse_sha256_file(("AB" * 32)) == hex64     # case normalized
    with pytest.raises(U.UpdateError):
        U.parse_sha256_file("")
    with pytest.raises(U.UpdateError):
        U.parse_sha256_file("de ad be ef\n")


def test_update_manifest_validation():
    hex64 = "cd" * 32
    good = json.dumps({"version": "1.0.2", "file": "Simpld-Setup-1.0.2.exe",
                       "sha256": hex64, "notes": "fix"})
    info = U.parse_update_manifest(good)
    assert (info.version, info.sha256, info.notes) == ("1.0.2", hex64, "fix")

    bad_cases = [
        "not json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"version": "abc", "file": "Simpld-Setup-1.0.2.exe",
                    "sha256": hex64}),
        json.dumps({"version": "1.0.2", "file": "../evil.exe",
                    "sha256": hex64}),
        json.dumps({"version": "1.0.2", "file": "Simpld-Setup-1.0.2.exe",
                    "sha256": "nope"}),
        json.dumps({"version": "1.0.2", "file": "Simpld-Setup-1.0.2.exe"}),
    ]
    for payload in bad_cases:
        with pytest.raises(U.UpdateError):
            U.parse_update_manifest(payload)


def test_github_release_info():
    hex64 = "ef" * 32
    payload = {
        "tag_name": "v1.0.1",
        "body": "notes here",
        "assets": [
            {"name": "Simpld-Setup-1.0.1.exe",
             "browser_download_url": "https://example.test/a.exe"},
            {"name": "Simpld-Setup-1.0.1.exe.sha256",
             "browser_download_url": "https://example.test/a.sha256"},
        ],
    }
    info = U.github_release_info(payload, f"{hex64}  a.exe\n")
    assert info.version == "1.0.1"
    assert info.filename == "Simpld-Setup-1.0.1.exe"
    assert info.url == "https://example.test/a.exe"
    assert info.sha256 == hex64
    assert info.source == "github"

    # No checksum asset -> refuse; no installer asset -> refuse; junk tag -> refuse.
    with pytest.raises(U.UpdateError):
        U.github_release_info(payload, "")
    with pytest.raises(U.UpdateError):
        U.github_release_info({"tag_name": "v1.0.1", "assets": []}, "")
    with pytest.raises(U.UpdateError):
        U.github_release_info({"tag_name": "not-a-version",
                               "assets": payload["assets"]}, f"{hex64}  a\n")


# --------------------------------------------------------------------------
# folder channel
# --------------------------------------------------------------------------

def test_folder_source_picks_highest_and_requires_hash(tmp_path):
    _write_installer(str(tmp_path), "1.0.1")
    _write_installer(str(tmp_path), "1.0.2")
    os.unlink(str(tmp_path / "Simpld-Setup-1.0.2.exe.sha256"))
    # 1.0.2 exists but has no checksum -> skipped, never offered.
    info = U.FolderSource(str(tmp_path)).fetch()
    assert info is not None and info.version == "1.0.1"
    assert info.sha256 == hashlib.sha256(b"fake-installer").hexdigest()


def test_folder_source_missing_dir_is_silent(tmp_path):
    assert U.FolderSource(str(tmp_path / "does-not-exist")).fetch() is None


def test_folder_source_manifest_is_authoritative(tmp_path):
    _write_installer(str(tmp_path), "1.0.1")
    _write_installer(str(tmp_path), "2.0.0")   # stray newer file
    (tmp_path / "latest.json").write_text(json.dumps({
        "version": "1.0.1", "file": "Simpld-Setup-1.0.1.exe",
        "sha256": hashlib.sha256(b"fake-installer").hexdigest(),
        "notes": ""}))
    info = U.FolderSource(str(tmp_path)).fetch()
    assert info.version == "1.0.1"  # the published manifest wins over scans

    (tmp_path / "latest.json").write_text(json.dumps({
        "version": "1.0.1", "file": "Simpld-Setup-9.9.9.exe",
        "sha256": hashlib.sha256(b"x").hexdigest(), "notes": ""}))
    with pytest.raises(U.UpdateError):
        U.FolderSource(str(tmp_path)).fetch()  # manifest names a missing file


# --------------------------------------------------------------------------
# download + verify
# --------------------------------------------------------------------------

def test_download_and_verify_ok_and_tamper(tmp_path):
    exe, digest = _write_installer(str(tmp_path), "1.0.1")
    dest = tmp_path / "dl"
    dest.mkdir()
    seen = []
    info = _info("1.0.1", exe, digest)
    got = U.download_and_verify(info, dest_dir=str(dest),
                                progress=lambda d, t: seen.append((d, t)))
    assert open(got, "rb").read() == b"fake-installer"
    assert seen and seen[-1][0] == seen[-1][1] == len(b"fake-installer")

    bad = _info("1.0.1", exe, "00" * 32)
    with pytest.raises(U.UpdateError):
        U.download_and_verify(bad, dest_dir=str(dest))
    assert not os.path.exists(os.path.join(str(dest), bad.filename))


def test_download_and_verify_in_place(tmp_path):
    # Folder channel pointed at the updates dir itself: verify, don't copy.
    exe, digest = _write_installer(str(tmp_path), "1.0.1")
    info = _info("1.0.1", exe, digest)
    got = U.download_and_verify(info, dest_dir=str(tmp_path))
    assert got == exe and open(exe, "rb").read() == b"fake-installer"

    tampered = _info("1.0.1", exe, "11" * 32)
    with pytest.raises(U.UpdateError):
        U.download_and_verify(tampered, dest_dir=str(tmp_path))
    assert os.path.isfile(exe)  # the published file is never deleted


def test_download_refuses_non_file_non_url(tmp_path):
    with pytest.raises(U.UpdateError):
        U.download_and_verify(_info("1.0.1", "ftp://x/y.exe", "aa" * 32),
                              dest_dir=str(tmp_path))


# --------------------------------------------------------------------------
# check_for_update orchestration
# --------------------------------------------------------------------------

def test_check_for_update_picks_newest_across_channels():
    def mk(v):
        return _info(v, "/x", "aa" * 32)
    best = U.check_for_update("1.0.0", sources=[
        _Src("lan", mk("1.0.1")),
        _Src("folder", mk("1.0.3")),
        _Src("github", exc=U.UpdateError("offline")),
    ])
    assert best.version == "1.0.3"


def test_check_for_update_none_when_not_newer_or_broken():
    mk = lambda v: _info(v, "/x", "aa" * 32)
    assert U.check_for_update("1.0.0", sources=[
        _Src("lan", mk("1.0.0")),
        _Src("github", exc=U.UpdateError("offline")),
    ]) is None
    assert U.check_for_update("1.0.0", sources=[]) is None
    assert U.check_for_update("1.0.0", sources=[
        _Src("crasher", exc=RuntimeError("bug")),
    ]) is None  # a broken channel can never break the app


def test_default_sources_env(monkeypatch, tmp_path):
    for var in ("SIMPLD_UPDATE_DISABLE", "SIMPLD_UPDATE_GITHUB",
                "SIMPLD_SERVER_HOST", "SIMPLD_SERVER_PORT",
                "SIMPLD_SERVER_CA_CERT", "SIMPLD_UPDATE_DIR",
                "SIMPLD_UPDATE_REPO"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("SIMPLD_UPDATE_DISABLE", "1")
    assert U.default_sources() == []

    monkeypatch.delenv("SIMPLD_UPDATE_DISABLE")
    monkeypatch.setenv("SIMPLD_SERVER_HOST", "192.168.1.10")
    monkeypatch.setenv("SIMPLD_SERVER_PORT", "8443")
    monkeypatch.setenv("SIMPLD_SERVER_CA_CERT", "/tmp/ca.pem")
    monkeypatch.setenv("SIMPLD_UPDATE_DIR", str(tmp_path))
    names = [s.name for s in U.default_sources()]
    assert names[0] == "lan" and names[-1] == "github" and "folder" in names
    lan = U.default_sources()[0]
    assert (lan.host, lan.port, lan.ca_cert) == ("192.168.1.10", 8443,
                                                 "/tmp/ca.pem")
    # The default GitHub channel is the public release mirror: the source
    # repo is private, so installed copies cannot read its releases.
    assert U.default_sources()[-1].repo == "BlurdStudios/simpld-releases"

    monkeypatch.setenv("SIMPLD_UPDATE_REPO", "example/fork")
    assert U.default_sources()[-1].repo == "example/fork"

    monkeypatch.setenv("SIMPLD_UPDATE_GITHUB", "0")
    assert "github" not in [s.name for s in U.default_sources()]


def test_install_refuses_non_windows(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX-only expectation")
    p = tmp_path / "Simpld-Setup-1.0.1.exe"
    p.write_bytes(b"x")
    with pytest.raises(U.UpdateError):
        U.install_and_relaunch(str(p))


# --------------------------------------------------------------------------
# LAN channel end-to-end (real TLS server + real updates dir)
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tls_pair():
    """Local cert+key for 127.0.0.1 (same recipe as test_lan_transport.py)."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI not available")
    d = tempfile.mkdtemp(prefix="simpld-upd-cert-")
    cert = os.path.join(d, "cert.pem")
    key = os.path.join(d, "key.pem")
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "2", "-nodes",
         "-subj", "/CN=simpld-upd-test",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    yield cert, key
    shutil.rmtree(d, ignore_errors=True)


def _run_server(cert, key, updates_dir):
    d = tempfile.mkdtemp(prefix="simpld-upd-db-")
    dbp = os.path.join(d, "factory.db")
    init_db(dbp)
    ensure_default_users(dbp)
    httpd = LS.create_server(dbp, "127.0.0.1", 0, cert, key,
                             updates_dir=updates_dir)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    return httpd, th, d


@pytest.fixture()
def lan_updates(tls_pair, tmp_path):
    cert, key = tls_pair
    upd = tmp_path / "updates"
    upd.mkdir()
    exe, digest = _write_installer(str(upd), "9.9.9")
    (upd / "latest.json").write_text(json.dumps({
        "version": "9.9.9", "file": os.path.basename(exe),
        "sha256": digest, "notes": "lan e2e"}))
    httpd, th, db_dir = _run_server(cert, key, str(upd))
    try:
        yield {"port": httpd.server_address[1], "ca": cert, "dir": str(upd)}
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=10)
        shutil.rmtree(db_dir, ignore_errors=True)


def test_lan_channel_end_to_end(lan_updates):
    src = U.LanSource("127.0.0.1", lan_updates["port"],
                      ca_cert=lan_updates["ca"])
    info = src.fetch()
    assert info is not None
    assert info.version == "9.9.9"
    assert info.source == "lan" and info.notes == "lan e2e"

    dest = tempfile.mkdtemp(prefix="simpld-upd-dl-")
    try:
        path = U.download_and_verify(info, dest_dir=dest)
        assert open(path, "rb").read() == b"fake-installer"
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_lan_update_endpoint_refuses_unlisted(lan_updates):
    ctx = ssl.create_default_context(cafile=lan_updates["ca"])
    base = f"https://127.0.0.1:{lan_updates['port']}"
    for bad in ("/updates/other.txt",
                "/updates/..%2Ffactory.db",
                "/updates/Simpld-Setup-..%2F..%2Ffactory.db.exe",
                "/updates/"):
        req = urllib.request.Request(base + bad)
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=10, context=ctx)
        assert ei.value.code == 404


def test_lan_endpoint_404_without_updates_dir(tls_pair):
    cert, key = tls_pair
    d = tempfile.mkdtemp(prefix="simpld-upd-nodir-")
    try:
        dbp = os.path.join(d, "factory.db")
        init_db(dbp)
        ensure_default_users(dbp)
        httpd = LS.create_server(dbp, "127.0.0.1", 0, cert, key)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            ctx = ssl.create_default_context(cafile=cert)
            req = urllib.request.Request(
                f"https://127.0.0.1:{httpd.server_address[1]}/updates/latest.json")
            with pytest.raises(urllib.error.HTTPError) as ei:
                urllib.request.urlopen(req, timeout=10, context=ctx)
            assert ei.value.code == 404
        finally:
            httpd.shutdown()
            httpd.server_close()
            th.join(timeout=10)
    finally:
        shutil.rmtree(d, ignore_errors=True)
