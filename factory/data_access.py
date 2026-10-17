"""App-facing data backend facade: local DB or packager-host LAN service.

One module for Qt pages to import instead of reaching into services,
slip_pdf, auth, or raw SQLite. The backend is chosen ONCE per process via
configure_local() / configure_lan() / configure_from_env() and then persists:
there is no dynamic auto-fallback, so a LAN disconnect surfaces as a
LanConnectionError the UI must show (never a silent local write).

Contract:
- Local mode calls the real services layer on the default DB, so real
  DB-bound auth (auth.require_permission via ContextVar session) is enforced.
- LAN mode calls LanClient's real methods with validated explicit args.
  It never imports factory.db/services, never initializes a DB, never seeds
  default users, and never writes a local file. No generic getattr/proxy
  dispatch: every operation is an explicit function below.
- Server-only arguments (path/actor/period) never cross this API, so a
  client cannot forge roles or pick DB files/periods over LAN.
- Errors are preserved, not wrapped: LanError subclasses propagate in LAN
  mode, ValueError/PermissionError propagate in local mode. Facade-level
  config/misuse errors raise DataAccessError subclasses (a controlled UI
  type the pages can catch alongside the backend errors).
- No printing or hardware here: clients print locally from real records
  fetched through this API and record printer-accepted output via
  mark_reel_printed()/mark_carton_printed() only.

UI integration: pages import this module instead of services/slip_pdf/
auth/get_conn; PackingSlipDialog takes slip_data(job_id) dict for rendering
instead of querying SQLite; users management stays local-admin-only
(remote raises RemoteUnsupportedError); printer dialogs keep using
factory.hardware directly.
"""

from __future__ import annotations

import os
import threading
from typing import Any

# --------------------------------------------------------------------------
# Facade-level errors (backend errors pass through unwrapped)
# --------------------------------------------------------------------------


class DataAccessError(Exception):
    """Base for facade config/misuse errors (not backend failures)."""


class NotConfiguredError(DataAccessError):
    """No backend configured or no LAN session signed in."""


class RemoteUnsupportedError(DataAccessError):
    """Operation has no server op (e.g. user management over LAN)."""


# --------------------------------------------------------------------------
# Single process-wide backend selection (simplest correct for one app)
# --------------------------------------------------------------------------

_LOCK = threading.Lock()
_MODE: str = "local"  # "local" | "lan"
_LAN_HOST: str | None = None
_LAN_PORT: int = 8443
_LAN_CA_CERT: str | None = None
_LAN_TIMEOUT: float = 10.0
_CLIENT: Any = None  # LanClient instance after authenticate(), else None

QR_PREFIX = "VASAVI"


def backend() -> str:
    """Current backend: "local" or "lan". Persists until reconfigured."""
    with _LOCK:
        return _MODE


def is_lan() -> bool:
    return backend() == "lan"


def configure_local() -> None:
    """Use the on-machine SQLite DB via the real services layer."""
    with _LOCK:
        global _MODE, _CLIENT
        _MODE = "local"
        _CLIENT = None


def configure_lan(host: str, port: int, ca_cert: str,
                  timeout: float = 10.0) -> None:
    """Use the packager-host LAN service. Validates; does no network I/O."""
    if not host or not str(host).strip():
        raise ValueError("LAN host required.")
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("LAN port must be 1-65535.")
    if not (1 <= port <= 65535):
        raise ValueError("LAN port must be 1-65535.")
    if not ca_cert:
        raise ValueError("LAN ca_cert required (server certificate trust).")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise ValueError("LAN timeout must be 0-300 seconds.")
    if not (0 < timeout <= 300):
        raise ValueError("LAN timeout must be 0-300 seconds.")
    with _LOCK:
        global _MODE, _LAN_HOST, _LAN_PORT, _LAN_CA_CERT, _LAN_TIMEOUT
        global _CLIENT
        _MODE = "lan"
        _LAN_HOST = str(host).strip()
        _LAN_PORT = port
        _LAN_CA_CERT = ca_cert
        _LAN_TIMEOUT = timeout
        _CLIENT = None  # re-authenticate after every (re)configuration


def configure_from_env() -> str:
    """Explicit env wiring: SIMPLD_SERVER_HOST / _PORT / _CA_CERT / _TIMEOUT.

    HOST set -> configure_lan (PORT defaults 8443, TIMEOUT defaults 10;
    CA_CERT required). HOST unset -> configure_local(). Returns backend().
    Reads env only; never stores tokens (the session token lives in the
    LanClient instance memory after authenticate()).
    """
    host = (os.environ.get("SIMPLD_SERVER_HOST") or "").strip()
    if not host:
        configure_local()
        return "local"
    port_raw = (os.environ.get("SIMPLD_SERVER_PORT") or "8443").strip()
    ca_cert = (os.environ.get("SIMPLD_SERVER_CA_CERT") or "").strip()
    timeout_raw = (os.environ.get("SIMPLD_SERVER_TIMEOUT") or "10").strip()
    try:
        port = int(port_raw)
    except ValueError:
        raise ValueError("SIMPLD_SERVER_PORT must be 1-65535.")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        raise ValueError("SIMPLD_SERVER_TIMEOUT must be 0-300 seconds.")
    configure_lan(host, port, ca_cert, timeout)
    return "lan"


def _lan_client() -> Any:
    """Configured client or raise NotConfiguredError (no auto-fallback)."""
    with _LOCK:
        if _MODE != "lan":
            raise NotConfiguredError(
                "LAN backend not configured (call configure_lan() first).")
        cli = _CLIENT
    if cli is None:
        raise NotConfiguredError("Not signed in (call authenticate() first).")
    return cli


# --------------------------------------------------------------------------
# Session (UI session shape: {"username", "role"} / None)
# --------------------------------------------------------------------------

def authenticate(username: str, password: str) -> dict | None:
    """Sign in on the configured backend.

    Local: real DB authentication; returns {"username", "role"} or None on
    bad credentials (mirrors auth.authenticate, session held per-context).
    LAN: real server-side DB authentication; returns {"username", "role"};
    bad credentials raise LanAuthError. No token is written anywhere: it
    stays in the LanClient instance memory.
    """
    if backend() == "lan":
        from .lan_client import LanClient
        with _LOCK:
            host, port, ca_cert, timeout = (
                _LAN_HOST, _LAN_PORT, _LAN_CA_CERT, _LAN_TIMEOUT)
        cli = LanClient(host, port, ca_cert, timeout=timeout)
        # Raises LanAuthError / LanConnectionError on failure; the dead
        # instance is discarded so no stale session lingers.
        try:
            who = cli.login(username, password)
        except Exception:
            raise
        with _LOCK:
            global _CLIENT
            _CLIENT = cli
        return {"username": who.get("username"), "role": who.get("role")}
    from . import auth as _auth
    rec = _auth.authenticate(username, password)
    if not rec:
        return None
    return {"username": rec["username"], "role": rec["role"]}


def current_user() -> dict | None:
    """Current session as {"username", "role"}, or None when signed out."""
    if backend() == "lan":
        with _LOCK:
            cli = _CLIENT
        if cli is None:
            return None
        from .lan_client import LanAuthError
        try:
            who = cli.whoami()
        except LanAuthError:
            return None
        return {"username": who.get("username"), "role": who.get("role")}
    from . import auth as _auth
    return _auth.current_user()


def logout() -> None:
    """Sign out on the configured backend (server token invalidated remotely
    in LAN mode; local session cleared). Never raises on an expired token."""
    if backend() == "lan":
        with _LOCK:
            global _CLIENT
            cli = _CLIENT
            _CLIENT = None
        if cli is not None:
            try:
                cli.logout()
            except Exception:
                pass
        return
    from . import auth as _auth
    _auth.logout_current()


def pages_for(role: str) -> tuple:
    """Role -> visible pages. Pure mapping; identical on both backends."""
    from . import auth as _auth
    return _auth.pages_for(role)


# --------------------------------------------------------------------------
# Pure QR helpers (no backend; same payload the labels print)
# --------------------------------------------------------------------------

def qr_payload(carton_no: str) -> str:
    return f"{QR_PREFIX}:{carton_no}"


def parse_scan(text: str) -> str:
    """Accept 'C0007' or 'VASAVI:C0007' from the keyboard-wedge scanner."""
    t = (text or "").strip().upper()
    if ":" in t:
        t = t.split(":")[-1].strip()
    return t


# --------------------------------------------------------------------------
# Small input normalizer (UI passes raw text; server needs real ints)
# --------------------------------------------------------------------------

def _wrappers_int(value: Any) -> int:
    """Strict positive int for wrappers/reel, mirroring services' message."""
    if isinstance(value, bool) or value is None:
        raise ValueError(
            "Wrappers/reel must be a positive whole number (e.g. 16500)")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(
                "Wrappers/reel must be a positive whole number (e.g. 16500)")
        value = int(value)
    if isinstance(value, str):
        t = value.strip()
        if not t.isdigit():
            raise ValueError(
                "Wrappers/reel must be a positive whole number (e.g. 16500)")
        value = int(t)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(
            "Wrappers/reel must be a positive whole number (e.g. 16500)")
    return value


# --------------------------------------------------------------------------
# Products / jobs (explicit ops matching existing UI calls)
# --------------------------------------------------------------------------

def list_products() -> list:
    if backend() == "lan":
        return _lan_client().list_products()
    from . import services as _svc
    return _svc.list_products()


def get_product(product_id: int) -> dict | None:
    if backend() == "lan":
        return _lan_client().get_product(product_id)
    from . import services as _svc
    return _svc.get_product(product_id)


def create_product(name: str, wrappers_per_reel: Any) -> dict:
    w = _wrappers_int(wrappers_per_reel)
    if backend() == "lan":
        return _lan_client().create_product(name, w)
    from . import services as _svc
    return _svc.create_product(name, w)


def set_product_wrappers(product_id: int, wrappers_per_reel: Any) -> dict:
    w = _wrappers_int(wrappers_per_reel)
    if backend() == "lan":
        return _lan_client().set_product_wrappers(product_id, w)
    from . import services as _svc
    return _svc.set_product_wrappers(product_id, w)


def list_jobs(status: str | None = None) -> list:
    if backend() == "lan":
        return _lan_client().list_jobs(status)
    from . import services as _svc
    return _svc.list_jobs(status)


def get_job_by_no(job_no: int) -> dict | None:
    if backend() == "lan":
        return _lan_client().get_job(job_no)
    from . import services as _svc
    return _svc.get_job_by_no(job_no)


def get_job_by_id(job_id: int) -> dict | None:
    """Job row by row id (replaces the UI's raw SELECT * FROM jobs)."""
    if backend() == "lan":
        for j in _lan_client().list_jobs():
            if j.get("id") == job_id:
                return j
        return None
    from .db import get_conn
    c = get_conn()
    try:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def create_job_for_product(product_id: int, core_wt: float = 0.5) -> dict:
    if backend() == "lan":
        return _lan_client().create_job_for_product(product_id,
                                                    core_wt=core_wt)
    from . import services as _svc
    return _svc.create_job_for_product(product_id, core_wt=core_wt)


def set_job_status(job_id: int, status: str) -> None:
    if backend() == "lan":
        _lan_client().set_job_status(job_id, status)
        return
    from . import services as _svc
    _svc.set_job_status(job_id, status)


def delete_job(job_id: int) -> None:
    if backend() == "lan":
        _lan_client().delete_job(job_id)
        return
    from . import services as _svc
    _svc.delete_job(job_id)


def job_stats(job_id: int) -> dict:
    if backend() == "lan":
        return _lan_client().job_stats(job_id)
    from . import services as _svc
    return _svc.job_stats(job_id)


# --------------------------------------------------------------------------
# Reels / cartons
# --------------------------------------------------------------------------

def loose_reels(job_id: int) -> list:
    if backend() == "lan":
        return _lan_client().loose_reels(job_id)
    from . import services as _svc
    return _svc.loose_reels(job_id)


def job_reels(job_id: int) -> list:
    if backend() == "lan":
        return _lan_client().job_reels(job_id)
    from . import services as _svc
    return _svc.job_reels(job_id)


def create_reel(job_id: int, gross: float, core: float | None = None,
                shift: str = "DAY", supervisor: str = "",
                customer: str = "") -> dict:
    if backend() == "lan":
        return _lan_client().create_reel(
            job_id, gross, core=core, shift=shift,
            supervisor=supervisor, customer=customer)
    from . import services as _svc
    return _svc.create_reel(job_id, gross, core=core, shift=shift,
                            supervisor=supervisor, customer=customer)


def reweigh_reel(reel_id: int, new_gross: float,
                 new_core: float | None = None) -> dict:
    if backend() == "lan":
        return _lan_client().reweigh_reel(reel_id, new_gross, new_core)
    from . import services as _svc
    return _svc.reweigh_reel(reel_id, new_gross, new_core=new_core)


def delete_reel(reel_id: int) -> None:
    if backend() == "lan":
        _lan_client().delete_reel(reel_id)
        return
    from . import services as _svc
    _svc.delete_reel(reel_id)


def mark_reel_printed(reel_id: int) -> dict:
    """Record printer-accepted reel output. Call ONLY after the local
    printer driver returned True; failures must leave the record pending."""
    if backend() == "lan":
        return _lan_client().mark_reel_printed(reel_id)
    from . import services as _svc
    return _svc.mark_reel_printed(reel_id)


def pack_weight_carton(job_id: int, gross_weight: float, shift: str = "DAY",
                       customer: str = "") -> dict:
    if backend() == "lan":
        return _lan_client().pack_weight_carton(
            job_id, gross_weight, shift=shift, customer=customer)
    from . import services as _svc
    return _svc.pack_weight_carton(job_id, gross_weight, shift=shift,
                                   customer=customer)


def pack_carton(job_id: int, reel_ids: list, shift: str = "DAY",
                customer: str | None = None) -> dict:
    """Legacy reel-linked pack (kept for tests/history; floor uses boxes)."""
    if backend() == "lan":
        return _lan_client().pack_carton(job_id, list(reel_ids),
                                         shift=shift, customer=customer)
    from . import services as _svc
    return _svc.pack_carton(job_id, list(reel_ids), shift=shift,
                            customer=customer)


def delete_carton(carton_id: int) -> None:
    if backend() == "lan":
        _lan_client().delete_carton(carton_id)
        return
    from . import services as _svc
    _svc.delete_carton(carton_id)


def mark_carton_printed(carton_id: int) -> dict:
    """Record printer-accepted carton output (printer-accepted only)."""
    if backend() == "lan":
        return _lan_client().mark_carton_printed(carton_id)
    from . import services as _svc
    return _svc.mark_carton_printed(carton_id)


def carton_detail(carton_id: int) -> dict:
    if backend() == "lan":
        return _lan_client().carton_detail(carton_id)
    from . import services as _svc
    return _svc.carton_detail(carton_id)


def ready_stock(job_id: int | None = None) -> list:
    if backend() == "lan":
        return _lan_client().ready_stock(job_id)
    from . import services as _svc
    return _svc.ready_stock(job_id)


def carton_verify(scan_text: str) -> dict | None:
    if backend() == "lan":
        return _lan_client().carton_verify(scan_text)
    from . import services as _svc
    return _svc.carton_verify(scan_text)


# --------------------------------------------------------------------------
# Read-only predictions / history / summary (actual backend formats)
# --------------------------------------------------------------------------

def predict_next_reel_seq() -> int:
    """Next reel sequence number (int on both backends)."""
    if backend() == "lan":
        return int(_lan_client().predict_next_reel_seq()["next_seq"])
    from . import services as _svc
    return int(_svc.predict_next_reel_seq())


def predict_next_box_seq() -> int:
    if backend() == "lan":
        return int(_lan_client().predict_next_box_seq()["next_seq"])
    from . import services as _svc
    return int(_svc.predict_next_box_seq())


def production_history(limit: int = 200) -> list:
    if backend() == "lan":
        return _lan_client().production_history(limit)
    from . import services as _svc
    return _svc.production_history()[:limit]


def list_orders(status: str | None = None) -> list:
    if backend() == "lan":
        return _lan_client().list_orders(status)
    from . import services as _svc
    return _svc.list_orders(status)


def order_progress(order_id: int) -> dict:
    if backend() == "lan":
        return _lan_client().order_progress(order_id)
    from . import services as _svc
    return _svc.order_progress(order_id)


def create_dispatch_order(customer: str, job_id: int, required_bundles: int,
                          order_date: str | None = None) -> dict:
    if backend() == "lan":
        return _lan_client().create_dispatch_order(
            customer, job_id, required_bundles, order_date=order_date)
    from . import services as _svc
    return _svc.create_dispatch_order(customer, job_id, required_bundles,
                                      order_date=order_date)


def scan_into_order(order_id: int, scan_text: str) -> dict:
    if backend() == "lan":
        return _lan_client().scan_into_order(order_id, scan_text)
    from . import services as _svc
    return _svc.scan_into_order(order_id, scan_text)


def remove_from_order(order_id: int, carton_id: int) -> None:
    if backend() == "lan":
        _lan_client().remove_from_order(order_id, carton_id)
        return
    from . import services as _svc
    _svc.remove_from_order(order_id, carton_id)


def complete_dispatch(order_id: int) -> dict:
    if backend() == "lan":
        return _lan_client().complete_dispatch(order_id)
    from . import services as _svc
    return _svc.complete_dispatch(order_id)


def cancel_order(order_id: int) -> None:
    if backend() == "lan":
        _lan_client().cancel_order(order_id)
        return
    from . import services as _svc
    _svc.cancel_order(order_id)


def daily_summary(day: str | None = None) -> dict:
    if backend() == "lan":
        return _lan_client().daily_summary(day)
    from . import services as _svc
    return _svc.daily_summary(day)


def slip_data(job_id: int) -> dict:
    """Full packing-slip evidence dict for rendering (both backends).

    Renderers must accept this dict and never open SQLite themselves, so
    the slip preview/print works unchanged over LAN.
    """
    if backend() == "lan":
        return _lan_client().get_slip(job_id)
    from . import slip_pdf as _slip
    return _slip.get_slip(job_id)


def next_bundle_no(job_id: int) -> int:
    """Next per-job bundle number preview from real backend data.

    Preview only: the authoritative bundle_no is allocated server-side
    inside the pack transaction. Local mode reads MAX(bundle_no)+1 on the
    default DB; LAN mode derives it from the server slip rows (all cartons
    of the job, Ready or dispatched). Never invents beyond backend rows.
    """
    try:
        jid = int(job_id)
    except (TypeError, ValueError):
        raise ValueError("Job required.")
    if jid <= 0:
        raise ValueError("Job required.")
    if backend() == "lan":
        try:
            slip = slip_data(jid)
        except Exception:
            return 1
        mx = 0
        for r in slip.get("rows", []) or []:
            try:
                mx = max(mx, int(r.get("bundle_no") or 0))
            except (TypeError, ValueError):
                pass
        return mx + 1
    from .db import get_conn
    c = get_conn()
    try:
        r = c.execute("SELECT MAX(bundle_no) m FROM cartons WHERE job_id=?",
                      (jid,)).fetchone()
        return int(r["m"] or 0) + 1
    finally:
        c.close()


def describe_error(exc: Exception) -> tuple[str, str]:
    """UI-facing (message, kind) for a backend failure. No fallback, no
    retry: kind "auth" means re-sign-in, "connection" means show the
    connection issue and disable the action, "remote" means not available
    over LAN, "other" is the backend's own message (validation/permission).
    """
    from .lan_client import LanAuthError, LanConnectionError
    if isinstance(exc, LanAuthError):
        return ("Session expired — sign out and sign in again.", "auth")
    if isinstance(exc, LanConnectionError):
        return (f"Connection issue: {exc}", "connection")
    if isinstance(exc, RemoteUnsupportedError):
        return (str(exc), "remote")
    if isinstance(exc, NotConfiguredError):
        return ("Not signed in — sign out and sign in again.", "auth")
    return (str(exc) or exc.__class__.__name__, "other")


# --------------------------------------------------------------------------
# User management: local admin only (no server op exists)
# --------------------------------------------------------------------------

_REMOTE_USERS_MSG = (
    "User management is local-admin only: sign in as admin on the packager "
    "computer itself to manage logins. It is not available over LAN.")


def list_users() -> list:
    if backend() == "lan":
        raise RemoteUnsupportedError(_REMOTE_USERS_MSG)
    from . import auth as _auth
    return _auth.list_users()


def create_user(username: str, password: str, role: str) -> dict:
    if backend() == "lan":
        raise RemoteUnsupportedError(_REMOTE_USERS_MSG)
    from . import auth as _auth
    return _auth.create_user(username, password, role)


def set_password(username: str, new_password: str) -> None:
    if backend() == "lan":
        raise RemoteUnsupportedError(_REMOTE_USERS_MSG)
    from . import auth as _auth
    _auth.set_password(username, new_password)


def set_role(username: str, role: str) -> None:
    if backend() == "lan":
        raise RemoteUnsupportedError(_REMOTE_USERS_MSG)
    from . import auth as _auth
    _auth.set_role(username, role)


def delete_user(username: str) -> None:
    if backend() == "lan":
        raise RemoteUnsupportedError(_REMOTE_USERS_MSG)
    from . import auth as _auth
    _auth.delete_user(username)
