"""Site licensing (offline-first, fail-closed).

One `license.lic` per site on the packager. Dispatcher inherits (server-side
check). Floor never stops on router failure: no internet required for launch
or checks. No per-PC key files, no 50 installers: generic installer + 1 site
file.

Format (frozen):
    {"serial","customer","site_id","edition","packager_fp","max_stations",
     "features","issued","expires","max_version","sig"}

- `sig` is Ed25519-base64 over the canonical JSON of all other keys
  (sorted keys, compact separators, utf-8). Private key stays offline with
  the vendor; the public key ships in this module (never the private key).
- `packager_fp` binds the file to one board. A copy on another board fails
  closed (invalid).
- `max_stations` = 1 packager + N dispatchers. Server counts distinct
  `station_id` seen in 30d vs this limit (+1 overdraft for 72h, once).
- Expiry +30d grace = write-allow with banner; after that write-block,
  read + backup/export still allowed. Never deletes data.

Stdlib + `cryptography` only. No Qt, no network.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import date, datetime, timedelta

# Vendor Ed25519 public key (base64 raw 32 bytes). Generated 2026-10-26 via
# tools/issue_license.py --gen-key; private key stays offline with the vendor
# (env SIMPLD_LICENSE_PRIVKEY or --privkey-file, never in the repo).
# Env override SIMPLD_LICENSE_PUBKEY wins (lets the floor rotate without code).
PUBLIC_KEY_B64 = "wLurqkogkywImSm2rKQ+i6RD3vG9cRD4ccyGQ+lX2lk="

EDITIONS = ("simple", "pro", "premium", "trial")
EDITION_DEFAULT_STATIONS = {"simple": 1, "pro": 3, "premium": 6, "trial": 1}
EDITION_DEFAULT_FEATURES = {
    "simple": [],
    "pro": ["lan", "history", "summary"],
    "premium": ["lan", "history", "summary", "api"],
    "trial": ["lan", "history", "summary"],
}

GRACE_DAYS = 30
EXPIRING_WARN_DAYS = 14
OVERDRAFT_EXTRA = 1
OVERDRAFT_HOURS = 72


def _pubkey_b64() -> str:
    env = (os.environ.get("SIMPLD_LICENSE_PUBKEY") or "").strip()
    return env or PUBLIC_KEY_B64


def _test_fp() -> str | None:
    v = (os.environ.get("SIMPLD_TEST_FP") or "").strip()
    return v or None


def machine_fingerprint() -> str:
    """Stable board ID: board UUID + MAC, hashed. Reinstall = same ID.

    Order: SIMPLD_TEST_FP override (tests) → /sys/class/dmi UUID (linux) →
    wmic UUID (windows) → MAC fallback. Always returns a hex string.
    """
    override = _test_fp()
    if override:
        return hashlib.sha256(f"test:{override}".encode()).hexdigest()[:32]
    raw = ""
    try:
        for p in ("/sys/class/dmi/id/product_uuid",
                  "/sys/class/dmi/product_uuid"):
            if os.path.isfile(p):
                with open(p, "r", errors="replace") as f:
                    v = f.read().strip()
                if v and "00000000" not in v:
                    raw = f"dmi:{v}"
                    break
    except OSError:
        pass
    if not raw and os.name == "nt":
        try:
            import subprocess
            out = subprocess.run(
                ["wmic", "csproduct", "get", "UUID"],
                capture_output=True, text=True, timeout=5).stdout or ""
            for line in out.splitlines():
                t = line.strip()
                if t and t.upper() != "UUID" and "00000000" not in t:
                    raw = f"wmic:{t}"
                    break
        except Exception:
            pass
    try:
        mac = uuid.getnode()
        mac_s = f"{mac:012x}"
    except Exception:
        mac_s = "nomac"
    seed = raw or f"mac:{mac_s}"
    return hashlib.sha256(f"{seed}|{mac_s}".encode()).hexdigest()[:32]


def _app_dir() -> str:
    try:
        from .db import app_data_dir
        return app_data_dir()
    except Exception:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "data")


def license_path() -> str:
    env = (os.environ.get("SIMPLD_LICENSE_PATH") or "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if os.name == "nt":
        base = (os.environ.get("LOCALAPPDATA")
                or os.environ.get("APPDATA")
                or os.environ.get("USERPROFILE")
                or os.path.expanduser("~"))
        return os.path.join(base, "Simpld", "license.lic")
    return os.path.join(_app_dir(), "license.lic")


def station_file_path() -> str:
    if os.name == "nt":
        base = (os.environ.get("LOCALAPPDATA")
                or os.environ.get("APPDATA")
                or os.environ.get("USERPROFILE")
                or os.path.expanduser("~"))
        return os.path.join(base, "Simpld", "station.id")
    return os.path.join(_app_dir(), "station.id")


def station_id() -> str:
    """Persistent per-machine station UUID (dispatcher identity)."""
    p = station_file_path()
    try:
        if os.path.isfile(p):
            with open(p, "r", errors="replace") as f:
                v = f.read().strip()
            if v:
                return v
    except OSError:
        pass
    v = uuid.uuid4().hex
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(v)
    except OSError:
        pass
    return v


def _canonical_bytes(payload: dict) -> bytes:
    body = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(body, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def verify_signature(payload: dict, pubkey_b64: str | None = None) -> bool:
    """Ed25519 verify only. False on any problem (never raises for bad sig)."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError:
        return False
    try:
        pk_b64 = (pubkey_b64 if pubkey_b64 is not None
                  else _pubkey_b64()).strip()
        raw = base64.b64decode(pk_b64)
        pub = Ed25519PublicKey.from_public_bytes(raw)
        sig = base64.b64decode(str(payload.get("sig") or ""))
        pub.verify(sig, _canonical_bytes(payload))
        return True
    except Exception:
        return False


def _parse_day(s: str | None) -> date | None:
    try:
        return datetime.strptime(str(s or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def load_license(path: str | None = None) -> dict | None:
    """Read the licence file. None when missing/unreadable (not an error)."""
    p = path or license_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def verify_license(lic: dict | None,
                   pubkey_b64: str | None = None) -> tuple[bool, str]:
    """Full offline check: shape + sig + board binding + version gate."""
    if not isinstance(lic, dict):
        return False, "no licence file — contact admin"
    required = ("serial", "customer", "site_id", "edition", "packager_fp",
                "max_stations", "features", "issued", "expires",
                "max_version", "sig")
    for k in required:
        if lic.get(k) in (None, ""):
            return False, f"licence missing '{k}' — contact admin"
    if str(lic.get("edition")) not in EDITIONS:
        return False, "licence edition unknown — contact admin"
    try:
        ms = int(lic.get("max_stations"))
        if ms < 1 or ms > 20:
            raise ValueError
    except (TypeError, ValueError):
        return False, "licence station count invalid — contact admin"
    if not isinstance(lic.get("features"), list):
        return False, "licence features invalid — contact admin"
    if _parse_day(lic.get("issued")) is None:
        return False, "licence issue date invalid — contact admin"
    if _parse_day(lic.get("expires")) is None:
        return False, "licence expiry invalid — contact admin"
    if not verify_signature(lic, pubkey_b64):
        return False, "licence signature invalid — contact admin"
    try:
        fp = str(lic.get("packager_fp") or "").strip()
        if fp and fp != machine_fingerprint():
            return False, ("licence is bound to another computer"
                           " (packager_fp mismatch) — contact admin")
    except Exception:
        return False, "licence machine check failed — contact admin"
    try:
        from .version import VERSION as _cur
        mv = str(lic.get("max_version") or "").strip()  # e.g. "2.x"
        major = mv.split(".")[0].lstrip("v")
        cur_major = str(_cur or "").split(".")[0]
        if major and major.isdigit() and cur_major.isdigit():
            if int(cur_major) > int(major):
                return False, (f"licence covers v{major}.x but app is v{_cur}"
                               " — contact admin")
    except Exception:
        pass
    if str(lic.get("edition")) == "simple" and "lan" in list(
            lic.get("features") or []):
        return False, "simple edition has no LAN — contact admin"
    if str(lic.get("edition")) == "trial":
        try:
            if int(lic.get("max_stations") or 0) != 1:
                return False, "trial licence is single-station — contact admin"
        except (TypeError, ValueError):
            return False, "licence station count invalid — contact admin"
    return True, "ok"


def is_demo_bypass() -> bool:
    """Demo builds skip licence; prod never does."""
    try:
        from .version import DEMO_ENABLED
        return bool(DEMO_ENABLED)
    except Exception:
        return False


def license_status(lic: dict | None = None, today: date | None = None,
                   pubkey_b64: str | None = None) -> dict:
    """State machine: ok / expiring / grace / blocked / invalid / none."""
    if is_demo_bypass():
        return {"state": "ok", "demo": True, "reason": "demo build"}
    if lic is None:
        lic = load_license()
    if lic is None:
        return {"state": "none", "reason": "no licence file — contact admin"}
    ok, reason = verify_license(lic, pubkey_b64)
    if not ok:
        return {"state": "invalid", "reason": reason}
    exp = _parse_day(lic.get("expires"))
    assert exp is not None
    now = today or date.today()
    if now <= exp:
        left = (exp - now).days
        if left <= EXPIRING_WARN_DAYS:
            return {"state": "expiring", "reason": f"expires in {left}d",
                    "expires": str(exp), "lic": lic}
        return {"state": "ok", "reason": "ok",
                "expires": str(exp), "lic": lic}
    over = (now - exp).days
    if over <= GRACE_DAYS:
        return {"state": "grace",
                "reason": (f"expired {over}d ago — {GRACE_DAYS - over}d grace"
                           " left, contact admin"),
                "expires": str(exp), "lic": lic}
    return {"state": "blocked",
            "reason": (f"expired {over}d ago — writes blocked,"
                       " backup/export still allowed"),
            "expires": str(exp), "lic": lic}


def license_display(st: dict | None = None) -> str:
    """'Licensed to X · pro · stations a/b · expires d' for About/title."""
    st = st if st is not None else license_status()
    lic = st.get("lic") or {}
    if st.get("state") in ("none", "invalid"):
        return f"Unlicensed — {st.get('reason', '')}"
    cust = str(lic.get("customer") or "?")
    ed = str(lic.get("edition") or "?")
    exp = str(lic.get("expires") or st.get("expires") or "?")
    base = f"Licensed to {cust} · {ed} · expires {exp} [{st.get('state')}]"
    # Trial timer: show days left vividly (e.g. "Trial · 12 days left").
    try:
        if ed == "trial":
            left = license_days_left(lic if lic else None)
            if left is not None:
                if left < 0:
                    base += f" · trial expired {-left}d ago"
                elif left == 0:
                    base += " · trial expires today"
                else:
                    base += f" · trial {left}d left"
    except Exception:
        pass
    return base


def license_days_left(lic: dict | None = None, today: date | None = None) -> int | None:
    """Days until expiry (negative when expired). None when no licence/expiry."""
    try:
        if lic is None:
            lic = load_license()
        if not lic:
            return None
        exp = _parse_day(lic.get("expires"))
        if exp is None:
            return None
        now = today or date.today()
        return (exp - now).days
    except Exception:
        return None


def is_trial_license(lic: dict | None = None) -> bool:
    """True when the loaded (or given) licence is a trial."""
    try:
        if lic is None:
            lic = load_license()
        return bool(lic) and str((lic or {}).get("edition") or "") == "trial"
    except Exception:
        return False


# ---------- station registry (packager DB; dispatcher inherits) ----------

def record_station(db_path: str | None = None, sid: str | None = None,
                   fp: str | None = None, now: str | None = None) -> dict:
    """Upsert one station heartbeat. No limit enforced here (see below)."""
    from .db import get_conn, now_iso
    sid = (sid or station_id()).strip()
    fp = (fp or machine_fingerprint()).strip()
    ts = now or now_iso()
    c = get_conn(db_path)
    try:
        r = c.execute("SELECT * FROM license_clients WHERE station_id=?",
                      (sid,)).fetchone()
        if r is None:
            c.execute("INSERT INTO license_clients(station_id, fp, first_seen,"
                      " last_seen, overdraft_until) VALUES(?,?,?,?,?)",
                      (sid, fp, ts, ts, ""))
        else:
            c.execute("UPDATE license_clients SET fp=?, last_seen=? WHERE"
                      " station_id=?", (fp, ts, sid))
        c.commit()
        row = c.execute("SELECT * FROM license_clients WHERE station_id=?",
                        (sid,)).fetchone()
        return dict(row)
    finally:
        c.close()


def active_station_count(db_path: str | None = None, days: int = 30) -> int:
    """Distinct stations seen in the last N days."""
    from .db import get_conn
    cutoff = (datetime.now() - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S")
    c = get_conn(db_path)
    try:
        r = c.execute("SELECT COUNT(*) n FROM license_clients WHERE"
                      " last_seen >= ?", (cutoff,)).fetchone()
        return int(r["n"] or 0)
    finally:
        c.close()


def station_allowed(max_stations: int, db_path: str | None = None,
                    sid: str | None = None, fp: str | None = None,
                    now_dt: datetime | None = None) -> tuple[bool, str]:
    """Enforce max_stations with a one-time +1 overdraft for 72h.

    Returns (allowed, reason). Known stations always allowed. New station
    over the limit: first time consumes the overdraft (72h banner+log),
    further N+1 rejected with 'license_invalid'. Admin release frees a slot.
    """
    from .db import get_conn, now_iso
    now_dt = now_dt or datetime.now()
    now_s = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    sid = (sid or station_id()).strip()
    c = get_conn(db_path)
    try:
        r = c.execute("SELECT * FROM license_clients WHERE station_id=?",
                      (sid,)).fetchone()
        if r is not None:
            return True, "known station"
        cutoff = (now_dt - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        n = c.execute("SELECT COUNT(*) n FROM license_clients WHERE"
                      " last_seen >= ?", (cutoff,)).fetchone()["n"]
        n = int(n or 0)
        if n < int(max_stations):
            return True, "slot available"
        # Over the limit: consume a global one-time overdraft if none active.
        # Overdraft grants exactly +1 (max+1 total): further N+2 rejected even
        # while the window is active.
        od = c.execute("SELECT overdraft_until FROM license_clients WHERE"
                       " overdraft_until != '' ORDER BY overdraft_until DESC"
                       " LIMIT 1").fetchone()
        od_active = bool(od and str(od["overdraft_until"]) >= now_s)
        if od_active:
            if n < int(max_stations) + OVERDRAFT_EXTRA:
                return True, "overdraft active"
            return False, ("station limit reached — release an old station"
                           " (admin) or contact vendor")
        if n >= int(max_stations) + OVERDRAFT_EXTRA:
            # overdraft already consumed once and still full
            return False, ("station limit reached — release an old station"
                           " (admin) or contact vendor")
        until = (now_dt + timedelta(hours=OVERDRAFT_HOURS)).strftime(
            "%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO license_clients(station_id, fp, first_seen,"
                  " last_seen, overdraft_until) VALUES(?,?,?,?,?)",
                  (sid, (fp or machine_fingerprint()).strip(),
                   now_s, now_s, until))
        c.commit()
        return True, f"overdraft until {until} — release a station soon"
    finally:
        try:
            c.close()
        except Exception:
            pass


def list_stations(db_path: str | None = None) -> list[dict]:
    from .db import get_conn
    c = get_conn(db_path)
    try:
        rows = c.execute("SELECT * FROM license_clients ORDER BY last_seen"
                         " DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def release_station(sid: str, db_path: str | None = None) -> None:
    """Admin self-serve: drop 1 old station to free a slot. Never deletes data."""
    from .db import get_conn
    c = get_conn(db_path)
    try:
        c.execute("DELETE FROM license_clients WHERE station_id=?",
                  ((sid or "").strip(),))
        c.commit()
    finally:
        c.close()


# ---------- key-string transport (paste-able licence, same signed bytes) ----------

KEY_PREFIX = "SIMPLD1."

def encode_key_string(lic: dict) -> str:
    """Licence as one paste-able line: SIMPLD1.<base64url(canonical JSON)>.

    Same signed payload as the .lic file — paste and file import verify
    through the identical path. Raises ValueError on bad input.
    """
    if not isinstance(lic, dict) or not lic.get("sig"):
        raise ValueError("Nothing to encode (not a signed licence).")
    raw = json.dumps(lic, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return KEY_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_key_string(s: str) -> dict:
    """Accept a SIMPLD1.* key, a raw .lic JSON paste, or a bare base64 blob.

    Returns the licence dict (UNVERIFIED — caller must verify_license()).
    Raises ValueError on garbage.
    """
    t = (s or "").strip()
    if not t:
        raise ValueError("Paste the licence key first.")
    if t.startswith("{"):
        try:
            d = json.loads(t)
        except ValueError:
            raise ValueError("That is not a valid licence (bad JSON).")
        if not isinstance(d, dict) or not d.get("sig"):
            raise ValueError("That is not a valid licence.")
        return d
    if t.startswith(KEY_PREFIX):
        t = t[len(KEY_PREFIX):]
    t += "=" * (-len(t) % 4)
    try:
        raw = base64.urlsafe_b64decode(t.encode("ascii"))
        d = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("That is not a valid licence key.")
    if not isinstance(d, dict) or not d.get("sig"):
        raise ValueError("That is not a valid licence key.")
    return d
