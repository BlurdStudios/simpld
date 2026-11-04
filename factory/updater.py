"""Auto-update: GitHub Releases + LAN + local folder. Stdlib only, no Qt.

Three "release channels" deliver the same contract — an Inno Setup installer
plus a SHA-256 for it, published together:

- **github** — the public release mirror (default
  `BlurdStudios/simpld-releases`) publishes one GitHub Release per `vX.Y.Z`
  tag; the assets are `Simpld-Setup-X.Y.Z.exe` and its `.sha256`. The source
  repo is private; the mirror carries installers only, so this channel needs
  no token and works wherever the machine has internet.
- **lan** — the packager computer serves its updates folder over the existing
  HTTPS LAN service (`GET /updates/latest.json`, `GET /updates/<file>`, see
  factory/lan_server.py). Works with no internet at all.
- **folder** — a plain directory (default `<data dir>/updates`, override with
  `SIMPLD_UPDATE_DIR`) an admin can fill from USB / a share. The packager
  machine itself updates from here.

Safety contract — never weakened:
- An update is only ever offered when the channel also provides a SHA-256 and
  the downloaded bytes match it exactly. A missing or mismatched hash refuses
  the update (fail closed, same philosophy as the hardware layer).
- Every failure (offline, 404, malformed manifest, hash mismatch) is
  non-fatal: the app keeps running and the reason lands in simpld.log.
- Installing spawns the real installer silently; the caller then quits so
  Inno can replace files, and the installer relaunches the app (see the
  `[Run]` entry in build/windows/simpld.iss).

Wire formats
------------
GitHub  `https://api.github.com/repos/<owner>/<repo>/releases/latest`
        assets: `Simpld-Setup-<version>.exe` + `Simpld-Setup-<version>.exe.sha256`

LAN / folder `latest.json` next to the files::

        {"version": "1.0.1",
         "file": "Simpld-Setup-1.0.1.exe",
         "sha256": "<64 lowercase hex>",
         "notes": "optional one-liner"}

Environment switches (see UPDATES.md):
  SIMPLD_UPDATE_DISABLE=1   no checks at all (per-machine kill switch)
  SIMPLD_UPDATE_GITHUB=0    skip the GitHub channel (offline fleet)
  SIMPLD_UPDATE_REPO=...    override the repo (fork/testing)
  SIMPLD_UPDATE_DIR=...     override the local updates folder
  SSL_CERT_FILE=...         trust this CA bundle instead of the PC's store
                            (honoured for update downloads; explicit LAN
                            anchors still win)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import tempfile
import contextlib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace

log = logging.getLogger("simpld.updater")

DEFAULT_REPO = "BlurdStudios/simpld-releases"
GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"

USER_AGENT = "Simpld-Updater/1 (+https://github.com/BlurdStudios/simpld)"
HTTP_TIMEOUT = 6.0          # per request; the check runs off the GUI thread
CHUNK = 256 * 1024
MANIFEST_NAME = "latest.json"

# Installer names are the allowlist everywhere (channels, LAN server, tools):
# the strict pattern is what keeps paths/URLs from ever being attacker-shaped.
INSTALLER_RE = re.compile(r"^Simpld-Setup-(\d+(?:\.\d+)*)\.exe$")
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SOURCE_LABELS = {
    "github": "GitHub Releases",
    "lan": "LAN packager channel",
    "folder": "local updates folder",
}


class UpdateError(Exception):
    """A channel, download or verification step failed. Never fatal."""


@dataclass(frozen=True)
class UpdateInfo:
    """One offer from one channel. `sha256` is mandatory by construction."""

    version: str          # normalized, no leading v: "1.0.1"
    filename: str         # must match INSTALLER_RE
    url: str              # https://... or a local filesystem path
    sha256: str           # lowercase hex, 64 chars
    source: str           # "github" | "lan" | "folder"
    notes: str = ""
    ca_cert: str = ""     # TLS trust anchor for LAN downloads ("" = system)


# --------------------------------------------------------------------------
# Version logic
# --------------------------------------------------------------------------

def parse_version(text) -> tuple[int, ...] | None:
    """'v1.2.3' -> (1, 2, 3); anything unparseable -> None."""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def normalize_version(text) -> str | None:
    """'v1.2.3' -> '1.2.3'; None when not a version."""
    parsed = parse_version(text)
    return ".".join(str(p) for p in parsed) if parsed else None


def is_newer(candidate: str, current: str) -> bool:
    """True only when both parse and candidate > current (fail closed)."""
    cand = parse_version(candidate)
    cur = parse_version(current)
    if cand is None or cur is None:
        return False
    return cand > cur


# --------------------------------------------------------------------------
# Small shared parsers (pure — the test surface)
# --------------------------------------------------------------------------

def parse_sha256_file(text: str) -> str:
    """Read a sha256sum-style file ('<hex>  <name>' or a bare hex line)."""
    token = (text or "").strip().split()[0].lower() if (text or "").strip() else ""
    if not _SHA256_RE.match(token):
        raise UpdateError("checksum file does not contain a SHA-256")
    return token


def parse_update_manifest(raw: str | bytes) -> UpdateInfo:
    """Validate a LAN/folder `latest.json` into an UpdateInfo (url added later)."""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise UpdateError("update manifest is not valid JSON")
    if not isinstance(payload, dict):
        raise UpdateError("update manifest must be a JSON object")
    version = normalize_version(payload.get("version"))
    if version is None:
        raise UpdateError("update manifest has no valid version")
    filename = payload.get("file")
    if not isinstance(filename, str) or not INSTALLER_RE.match(filename):
        raise UpdateError("update manifest names an unexpected file")
    sha = payload.get("sha256")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha.strip().lower()):
        raise UpdateError("update manifest has no valid SHA-256")
    notes = payload.get("notes")
    if not isinstance(notes, str):
        notes = ""
    return UpdateInfo(version=version, filename=filename, url="",
                      sha256=sha.strip().lower(), source="folder",
                      notes=notes)


def github_release_info(payload: dict, sha_text: str) -> UpdateInfo:
    """Build an UpdateInfo from a GitHub release JSON + its .sha256 asset text."""
    if not isinstance(payload, dict):
        raise UpdateError("GitHub release payload is not an object")
    version = normalize_version(payload.get("tag_name"))
    if version is None:
        raise UpdateError("GitHub release tag is not a version")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    exe = None
    for asset in assets:
        if isinstance(asset, dict) and INSTALLER_RE.match(
                str(asset.get("name") or "")):
            exe = asset
            break
    if exe is None:
        raise UpdateError("GitHub release has no Simpld-Setup installer asset")
    wanted = str(exe.get("name")) + ".sha256"
    sha_asset = next((a for a in assets if isinstance(a, dict)
                      and a.get("name") == wanted), None)
    if sha_asset is None:
        raise UpdateError("GitHub release has no .sha256 asset")
    notes = payload.get("body")
    return UpdateInfo(
        version=version,
        filename=str(exe.get("name")),
        url=str(exe.get("browser_download_url") or ""),
        sha256=parse_sha256_file(sha_text),
        source="github",
        notes=notes if isinstance(notes, str) else "",
    )


def read_sha256_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return parse_sha256_file(f.read(4096))
    except OSError as exc:
        raise UpdateError(f"cannot read checksum: {exc}")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _tls_context(explicit_ca: str = "") -> "ssl.SSLContext | None":
    """Primary TLS trust for update downloads (the fallback lives in
    _urlopen_resilient, not here).

    Floor PCs have broken Windows certificate stores (stale roots) while
    their browsers work fine — so GitHub downloads primarily pin to a CA
    bundle shipped with the app instead:

    1. an explicitly passed anchor (the LAN channel's packager cert) wins;
    2. else `SSL_CERT_FILE` (OpenSSL convention) when it names a real file;
    3. else the bundled `certifi` Mozilla roots;
    4. else None (system defaults — same as before, never a crash).

    Verification is always on; only the *source* of trust changes. When the
    pinned bundle rejects a GitHub handshake (typically antivirus HTTPS
    scanning, whose proxy CA is in the Windows store but not in the
    bundle), _urlopen_resilient retries once with system defaults before
    giving up with an actionable error.
    """
    cafile = (explicit_ca or "").strip()
    kind = "LAN trust anchor"
    if not cafile:
        env = os.environ.get("SSL_CERT_FILE", "").strip()
        if env and os.path.isfile(env):
            cafile, kind = env, "SSL_CERT_FILE bundle"
    if not cafile:
        try:
            import certifi
            cafile, kind = certifi.where(), "bundled CA bundle"
        except Exception:
            cafile = ""
    if not cafile:
        return None
    try:
        return ssl.create_default_context(cafile=cafile)
    except (OSError, ssl.SSLError) as exc:
        raise UpdateError(f"cannot load {kind}: {exc}")


def _is_cert_failure(exc: BaseException) -> bool:
    """True when this transport error is a certificate-verification failure.

    urllib surfaces TLS handshake failures as URLError wrapping the real
    ssl error in `.reason`, so both shapes are recognised.
    """
    cur: BaseException | None = exc
    for _ in range(3):
        if cur is None:
            return False
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        cur = getattr(cur, "reason", None)
    return "certificate verify failed" in str(exc).lower()


def _cert_hint(host: str, exc: Exception) -> str:
    """Actionable message for a certificate failure (never fatal)."""
    return (f"cannot verify the certificate of {host} ({exc}). "
            "Check the PC date/time, pause antivirus HTTPS scanning, or set "
            "SSL_CERT_FILE to a valid CA bundle file. "
            "Simpl'd keeps running the current version.")


@contextlib.contextmanager
def _urlopen_resilient(url_or_req, *, timeout: float,
                       ca_cert: str = ""):
    """Open a URL, retrying once with system certs on pinned-bundle rejection.

    Primary trust is the pinned bundle (bundled CAs / SSL_CERT_FILE / LAN
    anchor). If THAT handshake fails certificate verification and no
    explicit anchor was given (the GitHub channel), retry once with the
    system store: factory PCs often run antivirus HTTPS scanning whose
    proxy CA lives in the Windows store but not in the pinned bundle —
    the browser works there while pinned verification fails with
    "certificate verify failed". Verification stays ON in both attempts,
    and the SHA-256 check on the downloaded installer is the integrity
    anchor either way, so a network attacker gains nothing. LAN downloads
    keep their explicit packager anchor with no fallback (fail closed).
    """
    first = _tls_context(ca_cert)
    try:
        with urllib.request.urlopen(url_or_req, timeout=timeout,
                                    context=first) as resp:
            yield resp
            return
    except urllib.error.HTTPError:
        raise
    except Exception as exc:
        if ((ca_cert or "").strip() or first is None
                or not _is_cert_failure(exc)):
            raise
        try:
            host = urllib.parse.urlparse(
                url_or_req.full_url
                if hasattr(url_or_req, "full_url")
                else str(url_or_req)).hostname or "the update server"
        except Exception:
            host = "the update server"
        log.warning("update download: pinned CA bundle rejected %s (%s); "
                    "retrying once with system certificates "
                    "(antivirus HTTPS scanning?)", host, exc)
    try:
        with urllib.request.urlopen(url_or_req, timeout=timeout,
                                    context=None) as resp:
            yield resp
    except Exception as exc2:
        if _is_cert_failure(exc2):
            try:
                host = urllib.parse.urlparse(
                    url_or_req.full_url
                    if hasattr(url_or_req, "full_url")
                    else str(url_or_req)).hostname or "the update server"
            except Exception:
                host = "the update server"
            raise UpdateError(_cert_hint(host, exc2)) from exc2
        raise


def _http_get(url: str, *, timeout: float = HTTP_TIMEOUT,
              ca_cert: str = "", max_bytes: int = 1024 * 1024) -> bytes:
    """GET a URL; every transport problem becomes UpdateError (safe to log)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, */*",
    })
    try:
        with _urlopen_resilient(req, timeout=timeout,
                                ca_cert=ca_cert) as resp:
            return resp.read(max_bytes)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"HTTP {exc.code} from {url.split('?')[0]}")
    except (urllib.error.URLError, ssl.SSLError, TimeoutError,
            ConnectionError, OSError) as exc:
        raise UpdateError(f"cannot reach {url.split('?')[0]}: {exc}")


# --------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------

class GitHubSource:
    """Latest GitHub Release of the company repo (installer + .sha256)."""

    name = "github"

    def __init__(self, repo: str = DEFAULT_REPO, timeout: float = HTTP_TIMEOUT):
        self.repo = repo
        self.timeout = timeout

    def fetch(self) -> UpdateInfo | None:
        payload = _http_get(GITHUB_API.format(repo=self.repo),
                            timeout=self.timeout, max_bytes=512 * 1024)
        try:
            data = json.loads(payload)
        except ValueError:
            raise UpdateError("GitHub response is not JSON")
        sha_text = ""
        assets = data.get("assets") if isinstance(data, dict) else None
        for asset in assets or []:
            if (isinstance(asset, dict)
                    and str(asset.get("name") or "").startswith(
                        "Simpld-Setup-")
                    and str(asset.get("name") or "").endswith(".exe.sha256")):
                sha_text = _http_get(
                    str(asset.get("browser_download_url") or ""),
                    timeout=self.timeout, max_bytes=4096).decode(
                        "utf-8", "replace")
                break
        return github_release_info(data, sha_text)


class LanSource:
    """The packager computer's /updates channel over the LAN HTTPS service."""

    name = "lan"

    def __init__(self, host: str, port: int, ca_cert: str = "",
                 timeout: float = HTTP_TIMEOUT):
        self.host = host
        self.port = port
        self.ca_cert = ca_cert
        self.timeout = timeout

    def fetch(self) -> UpdateInfo | None:
        base = f"https://{self.host}:{self.port}"
        raw = _http_get(f"{base}/updates/{MANIFEST_NAME}",
                        timeout=self.timeout, ca_cert=self.ca_cert,
                        max_bytes=64 * 1024)
        info = parse_update_manifest(raw)
        url = f"{base}/updates/{urllib.parse.quote(info.filename)}"
        return replace(info, url=url, source="lan", ca_cert=self.ca_cert)


class FolderSource:
    """A local directory with installer + .sha256 files (manifest optional)."""

    name = "folder"

    def __init__(self, directory: str):
        self.directory = directory

    def fetch(self) -> UpdateInfo | None:
        if not os.path.isdir(self.directory):
            return None
        # A published latest.json is authoritative: it pins exactly which
        # installer IT published, even when other files sit in the folder.
        manifest_path = os.path.join(self.directory, MANIFEST_NAME)
        if os.path.isfile(manifest_path):
            with open(manifest_path, "rb") as f:
                info = parse_update_manifest(f.read(64 * 1024))
            path = os.path.join(self.directory, info.filename)
            if not os.path.isfile(path):
                raise UpdateError(
                    f"manifest names {info.filename!r} but it is not in "
                    f"{self.directory}")
            return replace(info, url=path, source="folder")
        # No manifest: scan installer + checksum pairs, newest wins. A file
        # without a valid .sha256 is skipped, never offered.
        best: UpdateInfo | None = None
        for name in sorted(os.listdir(self.directory)):
            if not INSTALLER_RE.match(name):
                continue
            try:
                digest = read_sha256_file(
                    os.path.join(self.directory, name + ".sha256"))
            except UpdateError as exc:
                log.warning("updates folder: ignoring %s (%s)", name, exc)
                continue
            version = normalize_version(INSTALLER_RE.match(name).group(1))
            if version is None:
                continue
            cand = UpdateInfo(version=version, filename=name,
                              url=os.path.join(self.directory, name),
                              sha256=digest, source="folder")
            if best is None or parse_version(cand.version) > parse_version(best.version):
                best = cand
        return best


def default_sources() -> list:
    """Channels for this machine, cheapest first (LAN before GitHub)."""
    if os.environ.get("SIMPLD_UPDATE_DISABLE") == "1":
        return []
    sources: list = []
    # LAN client wiring is the documented env interface (LAN_SETUP.md).
    host = (os.environ.get("SIMPLD_SERVER_HOST") or "").strip()
    if host:
        try:
            port = int((os.environ.get("SIMPLD_SERVER_PORT") or "8443").strip())
        except ValueError:
            port = 8443
        sources.append(LanSource(host, port,
                                 ca_cert=(os.environ.get(
                                     "SIMPLD_SERVER_CA_CERT") or "").strip()))
    # Local updates folder (the packager hosts this one).
    directory = (os.environ.get("SIMPLD_UPDATE_DIR") or "").strip()
    if not directory:
        try:
            from factory.db import app_data_dir
            directory = os.path.join(app_data_dir(), "updates")
        except Exception:
            directory = ""
    if directory:
        sources.append(FolderSource(directory))
    if os.environ.get("SIMPLD_UPDATE_GITHUB", "1") != "0":
        sources.append(GitHubSource(
            (os.environ.get("SIMPLD_UPDATE_REPO") or DEFAULT_REPO).strip()))
    return sources


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def check_for_update(current: str | None = None,
                     sources=None) -> UpdateInfo | None:
    """Query every channel; return the newest offer strictly newer than us.

    Never raises for channel trouble — a broken/offline channel is logged
    and skipped. When several channels carry the same version the earliest
    configured channel wins (LAN/folder before GitHub on the factory floor).
    """
    if current is None:
        from factory.version import VERSION as current
    chosen = list(sources) if sources is not None else default_sources()
    best: UpdateInfo | None = None
    best_key: tuple[int, ...] | None = None
    for source in chosen:
        try:
            info = source.fetch()
        except UpdateError as exc:
            log.info("update check (%s): %s", source.name, exc)
            continue
        except Exception:
            log.exception("update check (%s) crashed", source.name)
            continue
        if info is None:
            log.info("update check (%s): nothing published", source.name)
            continue
        if not is_newer(info.version, current):
            log.info("update check (%s): %s is not newer than %s",
                     source.name, info.version, current)
            continue
        key = parse_version(info.version)
        if best is None or key > best_key:
            best, best_key = info, key
    return best


# --------------------------------------------------------------------------
# Download + install
# --------------------------------------------------------------------------

def download_and_verify(info: UpdateInfo, dest_dir: str | None = None,
                        progress=None) -> str:
    """Fetch the installer and verify SHA-256; returns the local path.

    On ANY mismatch the partial file is deleted and UpdateError is raised —
    an unverified installer can never be handed to the OS.
    """
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="simpld-update-")
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, info.filename)
    digest = hashlib.sha256()
    source_is_file = os.path.isfile(info.url)
    if not source_is_file and not info.url.lower().startswith(
            ("http://", "https://")):
        raise UpdateError("update source is neither a file nor a URL")
    if source_is_file and os.path.abspath(info.url) == os.path.abspath(target):
        # Folder channel with dest_dir pointing at the updates folder itself:
        # verify the published bytes in place rather than copying the file
        # onto itself (which would truncate it). A mismatch never deletes
        # the published file — it refuses to install it.
        with open(target, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
        if digest.hexdigest() != info.sha256:
            raise UpdateError(
                "published installer does not match its SHA-256 — update "
                "refused (corrupted or tampered file)")
        return target
    try:
        if source_is_file:
            total = os.path.getsize(info.url)
            with open(info.url, "rb") as src, open(target, "wb") as dst:
                done = 0
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        else:
            req = urllib.request.Request(info.url, headers={
                "User-Agent": USER_AGENT})
            with _urlopen_resilient(req, timeout=30,
                                   ca_cert=info.ca_cert) as resp, \
                    open(target, "wb") as dst:
                try:
                    total = int(resp.headers.get("Content-Length") or -1)
                except (TypeError, ValueError):
                    total = -1
                done = 0
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except (urllib.error.URLError, ssl.SSLError, TimeoutError,
            ConnectionError, OSError) as exc:
        _unlink_quiet(target)
        raise UpdateError(f"download failed: {exc}")
    if digest.hexdigest() != info.sha256:
        _unlink_quiet(target)
        raise UpdateError(
            "downloaded installer does not match its SHA-256 — update "
            "refused (corrupted download or tampered release)")
    return target


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _install_block_reason(major: int | None = None,
                          is_64bit: bool | None = None) -> str | None:
    """Why this PC cannot run the installer, or None when it can.

    Pure (takes the OS facts as arguments) so tests cover it without
    mocking the platform. The live values come from sys.getwindowsversion
    and sys.maxsize in install_and_relaunch.

    The whole stack — PySide6 6.x, Python 3.12, the Inno installer
    (MinVersion=10.0, x64) — needs 64-bit Windows 10 or newer. Older
    Windows must be told so UP FRONT with a readable error: with
    /SILENT + /SUPPRESSMSGBOXES Inno would otherwise exit silently and
    the app has already quit for the install, which reads as "the
    update does nothing".
    """
    try:
        bits64 = bool(sys.maxsize > 2**32) if is_64bit is None else bool(is_64bit)
    except Exception:
        bits64 = True
    if major is None:
        try:
            major = int(sys.getwindowsversion().major)
        except Exception:
            return ("could not determine the Windows version — install "
                    "Simpld-Setup manually to stay safe")
    try:
        major = int(major)
    except (TypeError, ValueError):
        return ("could not determine the Windows version — install "
                "Simpld-Setup manually to stay safe")
    if major < 10:
        return ("this PC runs Windows %d — Simpl'd updates need 64-bit "
                "Windows 10 or newer (the app itself needs it too). "
                "Install manually on a supported PC instead" % major)
    if not bits64:
        return ("this is a 32-bit Windows install — Simpl'd ships 64-bit "
                "only. Reinstall Windows 64-bit or stay on the current "
                "version")
    return None


def install_and_relaunch(installer_path: str) -> None:
    """Start the Inno Setup installer silently. Windows only.

    The caller must quit the app right after: Inno closes running instances
    (`CloseApplications`), replaces the files, and its `[Run]` entry — which
    runs as the original (non-elevated) user — relaunches Simpl'd.
    """
    if os.name != "nt":
        raise UpdateError(
            "automatic install is Windows-only — run the installer manually")
    block = _install_block_reason()
    if block is not None:
        raise UpdateError(block)
    if not os.path.isfile(installer_path):
        raise UpdateError("the downloaded installer is missing")
    args = [installer_path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/CLOSEAPPLICATIONS"]
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, name, 0)
    try:
        subprocess.Popen(args, close_fds=True, creationflags=flags)
    except OSError as exc:
        raise UpdateError(f"could not start the installer: {exc}")
