"""Outbound-only dashboard sync (Plan 009 Phase 3, factory side).

The factory never serves inbound traffic. This module POSTs cached payloads
to the vendor-hosted dashboard (see SYNC_CONTRACT.md + dashboard/):

    POST <SYNC_URL>/api/push-day    {site_id, day_report}
    POST <SYNC_URL>/api/push-orders {site_id, orders, stock_counts}

Config (env, read per call so key rotation needs no restart):
    SIMPLD_SYNC_URL      e.g. https://simpld-dashboard.workers.dev
    SIMPLD_SYNC_SITE_ID  e.g. vasavi-f1
    SIMPLD_SYNC_KEY      Bearer API key for this site (never logged)

Behavior: timeout + key from env, tokens redacted on error. Airplane mode /
no internet: queue payloads to <app_data>/sync_queue.json, log only, floor
unaffected. Manual `Sync now` button (SummaryPage) + reuse of the updater
trigger point call `push_now()` best-effort on internet-available.

Stdlib only (urllib). No Qt. LAN-safe: reads go through data_access only.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime

TIMEOUT = 15.0


def _cfg() -> tuple[str, str, str]:
    url = (os.environ.get("SIMPLD_SYNC_URL") or "").strip().rstrip("/")
    site = (os.environ.get("SIMPLD_SYNC_SITE_ID") or "").strip()
    key = (os.environ.get("SIMPLD_SYNC_KEY") or "").strip()
    return url, site, key


def configured() -> bool:
    url, site, key = _cfg()
    return bool(url and site and key)


def _app_dir() -> str:
    try:
        from .db import app_data_dir
        return app_data_dir()
    except Exception:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "data")


def _state_path() -> str:
    return os.path.join(_app_dir(), "sync_state.json")


def _queue_path() -> str:
    return os.path.join(_app_dir(), "sync_queue.json")


def last_sync() -> dict:
    """Last-sync label data: {at, ok, detail}. Empty when never synced."""
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(ok: bool, detail: str = "") -> None:
    try:
        os.makedirs(_app_dir(), exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "ok": ok, "detail": detail[:200]}, f)
    except OSError:
        pass


def adapt_day_report(rep: dict) -> dict:
    """Map services.day_report() (rich) to dashboard DayReport (contract)."""
    reels = []
    for r in rep.get("reel_rows") or []:
        reels.append({
            "roll_code": str(r.get("roll_code") or ""),
            "roll_label": str(r.get("roll_label") or ""),
            "job_id": str(r.get("job_no") or ""),
            "net_kg": round(float(r.get("net") or 0), 3),
        })
    boxes = []
    for b in rep.get("box_rows") or []:
        rolls = str(b.get("rolls") or "")
        linked = [x.strip() for x in rolls.split(",") if x.strip()]
        boxes.append({
            "carton_no": str(b.get("carton_no") or ""),
            "bundle_no": (str(b.get("bundle_no"))
                          if b.get("bundle_no") is not None else None),
            "job_id": str(b.get("job_no") or ""),
            "num_reels": int(b.get("num_reels") or 0),
            "total_gross_kg": round(float(b.get("gross") or 0), 3),
            "linked_rolls": linked,
        })
    t = rep.get("totals") or {}
    by_job: dict = {}
    for j in rep.get("by_job") or []:
        by_job[str(j.get("job_no"))] = {
            "reels": int(j.get("reels") or 0),
            "reel_net_kg": round(float(j.get("reel_net") or 0), 3),
            "boxes": int(j.get("boxes") or 0),
            "box_gross_kg": round(float(j.get("box_gross") or 0), 3),
        }
    return {
        "day": str(rep.get("day") or ""),
        "reel_rows": reels,
        "box_rows": boxes,
        "totals": {
            "reel_count": int(t.get("reels") or 0),
            "reel_net_kg": round(float(t.get("reel_net") or 0), 3),
            "box_count": int(t.get("boxes") or 0),
            "box_gross_kg": round(float(t.get("box_gross") or 0), 3),
        },
        "by_job": by_job,
    }


def build_push(day: str | None = None) -> dict:
    """Assemble both push payloads via the DA facade (LAN-safe)."""
    from . import data_access as DA
    from .db import today_iso
    day = day or today_iso()
    rep = DA.day_report(day)
    orders_raw = DA.list_orders()
    orders = []
    for o in orders_raw:
        if o.get("required_pieces") is not None:
            target = int(o.get("required_pieces") or 0)
            loaded = int(o.get("loaded_pieces") or 0)
        else:
            target = int(o.get("required_bundles") or 0)
            loaded = int(o.get("loaded") or 0)
        orders.append({
            "order_id": str(o.get("order_no") or o.get("id")),
            "buyer": str(o.get("customer") or ""),
            "target_qty": target,
            "loaded_qty": loaded,
            "status": str(o.get("status") or ""),
        })
    summary = DA.ready_stock_summary()
    packed = sum(int(v.get("boxes") or 0) for v in summary.values())
    dispatched = sum(int(o.get("loaded") or 0) for o in orders_raw)
    raw_rolls = 0
    try:
        for j in DA.list_jobs(status="open"):
            try:
                raw_rolls += len(DA.loose_reels(int(j.get("id"))))
            except Exception:
                pass
    except Exception:
        pass
    _, site, _ = _cfg()
    try:
        from . import license as _LIC
        reporter = {"station_id": _LIC.station_id(),
                    "packager_fp": _LIC.machine_fingerprint()}
    except Exception:
        reporter = {}
    # Licence snapshot for vendor tracking (trial countdowns, renewals,
    # upgrades). Optional: old factories omit it and the server tolerates
    # its absence. Never blocks the push when licensing itself errors.
    try:
        from . import license as _LIC2
        from .version import VERSION as _APP_VER
        _st = _LIC2.license_status()
        _lic = _st.get("lic") or {}
        licence = {
            "edition": str(_lic.get("edition") or ""),
            "expires": str(_lic.get("expires") or _st.get("expires") or ""),
            "state": str(_st.get("state") or ""),
            "days_left": _LIC2.license_days_left(_lic if _lic else None),
            "packager_fp": str(_lic.get("packager_fp") or reporter.get("packager_fp") or ""),
            "app_version": str(_APP_VER or ""),
        }
        if not licence["edition"] and licence["state"] in ("none", "invalid"):
            licence = {"edition": "", "expires": "", "state": licence["state"],
                       "days_left": None, "packager_fp": licence["packager_fp"],
                       "app_version": licence["app_version"]}
    except Exception:
        licence = {}
    return {
        "site_id": site,
        "reporter": reporter,
        "licence": licence,
        "day_report": adapt_day_report(rep),
        "orders": orders,
        "stock_counts": {"raw_rolls": raw_rolls,
                         "packed_cartons": packed,
                         "dispatched_cartons": dispatched},
    }


def _post(path: str, payload: dict, timeout: float = TIMEOUT) -> None:
    url, _site, key = _cfg()
    req = urllib.request.Request(
        url + path, data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json",
                                "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(64 * 1024).decode("utf-8", "replace")
        if resp.status >= 300:
            raise OSError(f"HTTP {resp.status}: {body[:120]}")
    except urllib.error.HTTPError as exc:
        # Key material redacted: report status only.
        raise OSError(f"HTTP {exc.code} from dashboard push") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError,
            OSError) as exc:
        raise OSError(f"dashboard unreachable ({exc.__class__.__name__})") \
            from None


def _queue(payload: dict) -> None:
    try:
        os.makedirs(_app_dir(), exist_ok=True)
        q: list = []
        try:
            with open(_queue_path(), "r", encoding="utf-8") as f:
                q = json.load(f)
            if not isinstance(q, list):
                q = []
        except (OSError, ValueError):
            q = []
        q.append({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "payload": payload})
        with open(_queue_path(), "w", encoding="utf-8") as f:
            json.dump(q[-20:], f)  # bounded: keep the last 20 pushes
    except OSError:
        pass


def push_now(day: str | None = None,
             timeout: float = TIMEOUT) -> dict:
    """POST both payloads. Never raises: failure queues + logs, floor first.

    Returns {ok, queued, detail}. ok=False with sync disabled is normal
    (factory works fully offline).
    """
    if not configured():
        return {"ok": False, "queued": False,
                "detail": "sync disabled (set SIMPLD_SYNC_URL/_SITE_ID/_KEY)"}
    try:
        push = build_push(day)
    except Exception as exc:
        return {"ok": False, "queued": False,
                "detail": f"build failed: {exc}"}
    day_payload = {"site_id": push["site_id"],
                   "reporter": push.get("reporter") or {},
                   "day_report": push["day_report"]}
    orders_payload = {"site_id": push["site_id"],
                      "reporter": push.get("reporter") or {},
                      "orders": push["orders"],
                      "stock_counts": push["stock_counts"]}
    try:
        _post("/api/push-day", day_payload, timeout)
        _post("/api/push-orders", orders_payload, timeout)
    except Exception as exc:
        _queue(push)  # airplane-mode: retry next window
        _save_state(False, str(exc))
        return {"ok": False, "queued": True, "detail": str(exc)}
    _save_state(True, "pushed")
    return {"ok": True, "queued": False, "detail": "pushed"}
