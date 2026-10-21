"""TLS client for the packager-host LAN data service (factory/lan_server.py).

Stdlib only (urllib + ssl). The client holds NO database handle, imports no
factory.db/services modules, and NEVER falls back to a local DB: when the
server is unreachable it raises LanConnectionError and the caller must show
an error. Reads and writes are attempted exactly ONCE each (no auto-retry).

Uncertain-outcome rule for writes: if a mutating call raises
LanConnectionError (or times out), the write MAY have committed on the
server. The caller must refresh via a read op (ready_stock / list_jobs /
order_progress / ...) and only then decide whether a retry with a NEW
request_id is safe. Retrying blindly can create a duplicate reel/carton/
order. request_id is a caller-supplied tracing label only; the server does
not deduplicate it.

Typical use:
    cli = LanClient(host="192.168.1.10", port=8443, ca_cert="/path/simpld-ca.pem")
    cli.login("packager", "pack123")   # real server-side DB authentication
    jobs = cli.list_jobs()
    ...
    cli.logout()
"""
from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any


class LanError(Exception):
    """Base for all LAN client errors. Never carries tokens/passwords."""


class LanConnectionError(LanError):
    """Server unreachable / TLS failed / timed out. Outcome of a write that
    raised this is UNKNOWN: refresh via a read before deciding to retry."""


class LanAuthError(LanError):
    """HTTP 401: bad credentials, missing/expired/invalid session token."""


class LanPermissionError(LanError):
    """HTTP 403: authenticated role may not do this operation."""


class LanConflictError(LanError):
    """HTTP 409: duplicate / already-scanned / order-full type conflict."""


class LanValidationError(LanError):
    """HTTP 422: unknown op, bad args, or a business-rule rejection."""


class LanRateLimitedError(LanError):
    """HTTP 429: too many login attempts; wait and retry."""


class LanServerError(LanError):
    """HTTP 5xx / unexpected status from the server."""


_STATUS_ERRORS = {
    401: LanAuthError,
    403: LanPermissionError,
    409: LanConflictError,
    422: LanValidationError,
    429: LanRateLimitedError,
}


class LanClient:
    """Authenticated HTTPS client. One instance = one user session."""

    def __init__(self, host: str, port: int, ca_cert: str,
                 timeout: float = 10.0) -> None:
        if not host or not host.strip():
            raise ValueError("host required")
        if not (1 <= int(port) <= 65535):
            raise ValueError("port must be 1-65535")
        if not ca_cert:
            raise ValueError("ca_cert required (server certificate trust)")
        if timeout <= 0 or timeout > 300:
            raise ValueError("timeout must be 0-300 seconds")
        self._host = host.strip()
        self._port = int(port)
        self._ca_cert = ca_cert
        self._timeout = float(timeout)
        self._token: str | None = None
        self._ctx = ssl.create_default_context(cafile=ca_cert)
        # Hostname/IP verification stays ON: the server cert must carry a
        # SAN matching the host/IP used here (see LAN_SETUP.md).

    # -- session ---------------------------------------------------------
    @property
    def token(self) -> str | None:
        return self._token

    @property
    def logged_in(self) -> bool:
        return self._token is not None

    @property
    def base_url(self) -> str:
        return f"https://{self._host}:{self._port}"

    def login(self, username: str, password: str) -> dict:
        """Authenticate against the server DB. Stores the session token."""
        self._token = None
        data = self._raw("login", {"username": username, "password": password},
                         with_token=False)
        tok = data.get("session_token")
        if not tok:
            raise LanAuthError("Login did not return a session.")
        self._token = tok
        return {"username": data.get("username"),
                "role": data.get("role")}

    def logout(self) -> None:
        """Invalidate the server-side token; always clears the local copy."""
        try:
            if self._token:
                self._raw("logout", {}, with_token=True)
        except LanAuthError:
            pass  # already invalid server-side; still clear locally
        finally:
            self._token = None

    # -- transport -------------------------------------------------------
    def _raw(self, op: str, args: dict, *, with_token: bool = True,
             request_id: str | None = None) -> Any:
        body: dict = {"op": op, "args": args or {}}
        if request_id is not None:
            body["request_id"] = request_id
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api", data=payload, method="POST",
            headers={"Content-Type": "application/json"})
        if with_token:
            if not self._token:
                raise LanAuthError("Not logged in.")
            req.add_header("X-Session-Token", self._token)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout,
                                        context=self._ctx) as resp:
                msg = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ssl.SSLError, ConnectionError, OSError) as exc:
            raise LanConnectionError(
                f"Cannot reach LAN server at {self._host}:{self._port} "
                f"({exc.__class__.__name__}). Check the packager computer, "
                "host/port, network, and CA trust. No local fallback: "
                "data entry must wait for the connection.") from None
        if not isinstance(msg, dict) or not msg.get("ok"):
            raise LanServerError("Malformed server response.")
        return msg.get("data")

    def _http_error(self, exc: urllib.error.HTTPError) -> LanError:
        try:
            msg = json.loads(exc.read().decode("utf-8"))
            err = (msg.get("error") or {})
            detail = str(err.get("message") or "Request failed.")
        except Exception:
            detail = f"Request failed (HTTP {exc.code})."
        cls = _STATUS_ERRORS.get(exc.code, LanServerError)
        if cls is LanAuthError and self._token and exc.code == 401:
            pass  # token went stale server-side; keep it for logout() to clear
        return cls(f"{detail} (HTTP {exc.code})")

    def health(self) -> dict:
        """Unauthenticated liveness probe. Raises LanConnectionError offline."""
        req = urllib.request.Request(self.base_url + "/health", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout,
                                        context=self._ctx) as resp:
                msg = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ssl.SSLError, ConnectionError, OSError) as exc:
            raise LanConnectionError(
                f"Cannot reach LAN server at {self._host}:{self._port} "
                f"({exc.__class__.__name__}).") from None
        except urllib.error.HTTPError as exc:
            raise LanServerError(f"Health check failed (HTTP {exc.code}).")
        if not isinstance(msg, dict) or not msg.get("ok"):
            raise LanServerError("Malformed health response.")
        return msg

    def call(self, op: str, args: dict | None = None,
             request_id: str | None = None) -> Any:
        """Single attempt at any allowlisted op. Writes are never retried."""
        if not op or not isinstance(op, str):
            raise ValueError("op required")
        if args is not None and not isinstance(args, dict):
            raise ValueError("args must be an object")
        return self._raw(op, args or {}, with_token=True,
                         request_id=request_id)

    # -- reads -----------------------------------------------------------
    def whoami(self) -> dict:
        return self.call("whoami")

    def list_products(self) -> list:
        return self.call("list_products")

    def get_product(self, product_id: int) -> dict | None:
        return self.call("get_product", {"product_id": product_id})

    def list_jobs(self, status: str | None = None) -> list:
        args: dict = {}
        if status:
            args["status"] = status
        return self.call("list_jobs", args)

    def get_job(self, job_no: int) -> dict | None:
        return self.call("get_job", {"job_no": job_no})

    def job_stats(self, job_id: int) -> dict:
        return self.call("job_stats", {"job_id": job_id})

    def loose_reels(self, job_id: int) -> list:
        return self.call("loose_reels", {"job_id": job_id})

    def job_reels(self, job_id: int) -> list:
        return self.call("job_reels", {"job_id": job_id})

    def job_boxes(self, job_id: int) -> list:
        return self.call("job_boxes", {"job_id": job_id})

    def pending_box_stickers(self, job_id: int | None = None) -> list:
        args: dict = {}
        if job_id is not None:
            args["job_id"] = job_id
        return self.call("pending_box_stickers", args)

    def ready_stock(self, job_id: int | None = None) -> list:
        args: dict = {}
        if job_id is not None:
            args["job_id"] = job_id
        return self.call("ready_stock", args)

    def ready_stock_summary(self) -> dict:
        return self.call("ready_stock_summary", {})

    def find_carton(self, scan_text: str) -> dict | None:
        return self.call("find_carton", {"scan_text": scan_text})

    def carton_detail(self, carton_id: int) -> dict:
        return self.call("carton_detail", {"carton_id": carton_id})

    def carton_verify(self, scan_text: str) -> dict | None:
        return self.call("carton_verify", {"scan_text": scan_text})

    def predict_next_reel_seq(self) -> dict:
        return self.call("predict_next_reel_seq")

    def predict_next_box_seq(self) -> dict:
        return self.call("predict_next_box_seq")

    def todays_reels(self) -> list:
        return self.call("todays_reels")

    def production_history(self, limit: int = 200) -> list:
        return self.call("production_history", {"limit": limit})

    def list_audit(self, entity: str | None = None,
                   entity_id: int | None = None, limit: int = 200) -> list:
        args: dict = {"limit": limit}
        if entity:
            args["entity"] = entity
        if entity_id is not None:
            args["entity_id"] = entity_id
        return self.call("list_audit", args)

    def list_orders(self, status: str | None = None) -> list:
        args: dict = {}
        if status:
            args["status"] = status
        return self.call("list_orders", args)

    def order_progress(self, order_id: int) -> dict:
        return self.call("order_progress", {"order_id": order_id})

    def daily_summary(self, day: str | None = None) -> dict:
        args: dict = {}
        if day:
            args["day"] = day
        return self.call("daily_summary", args)

    def get_slip(self, job_id: int) -> dict:
        return self.call("get_slip", {"job_id": job_id})

    # -- writes (single attempt; see uncertain-outcome rule above) --------
    def create_product(self, name: str, wrappers_per_reel: int,
                       request_id: str | None = None) -> dict:
        return self.call("create_product",
                         {"name": name, "wrappers_per_reel": wrappers_per_reel},
                         request_id=request_id)

    def set_product_wrappers(self, product_id: int, wrappers_per_reel: int,
                             request_id: str | None = None) -> dict:
        return self.call("set_product_wrappers",
                         {"product_id": product_id,
                          "wrappers_per_reel": wrappers_per_reel},
                         request_id=request_id)

    def create_job(self, job_name: str, size_mm: float = 0,
                   core_wt: float = 0.5,
                   request_id: str | None = None) -> dict:
        return self.call("create_job",
                         {"job_name": job_name, "size_mm": size_mm,
                          "core_wt": core_wt}, request_id=request_id)

    def create_job_for_product(self, product_id: int, core_wt: float = 0.5,
                               request_id: str | None = None) -> dict:
        return self.call("create_job_for_product",
                         {"product_id": product_id, "core_wt": core_wt},
                         request_id=request_id)

    def set_job_status(self, job_id: int, status: str,
                       request_id: str | None = None) -> dict:
        return self.call("set_job_status", {"job_id": job_id, "status": status},
                         request_id=request_id)

    def delete_job(self, job_id: int,
                   request_id: str | None = None) -> dict:
        return self.call("delete_job", {"job_id": job_id},
                         request_id=request_id)

    def create_reel(self, job_id: int, gross: float, core: float | None = None,
                    shift: str = "DAY", supervisor: str = "",
                    customer: str = "",
                    request_id: str | None = None) -> dict:
        return self.call("create_reel",
                         {"job_id": job_id, "gross": gross, "core": core,
                          "shift": shift, "supervisor": supervisor,
                          "customer": customer}, request_id=request_id)

    def reweigh_reel(self, reel_id: int, new_gross: float,
                     new_core: float | None = None,
                     request_id: str | None = None) -> dict:
        return self.call("reweigh_reel",
                         {"reel_id": reel_id, "new_gross": new_gross,
                          "new_core": new_core}, request_id=request_id)

    def delete_reel(self, reel_id: int,
                    request_id: str | None = None) -> dict:
        return self.call("delete_reel", {"reel_id": reel_id},
                         request_id=request_id)

    def pack_weight_carton(self, job_id: int, gross_weight: float,
                           shift: str = "DAY", customer: str = "",
                           request_id: str | None = None) -> dict:
        return self.call("pack_weight_carton",
                         {"job_id": job_id, "gross_weight": gross_weight,
                          "shift": shift, "customer": customer},
                         request_id=request_id)

    def pack_next_box(self, job_id: int, gross_weight: float, box_weight: float,
                      reels_per_box: int = 2, shift: str = "DAY",
                      customer: str = "",
                      request_id: str | None = None) -> dict:
        return self.call("pack_next_box",
                         {"job_id": job_id, "gross_weight": gross_weight,
                          "box_weight": box_weight, "reels_per_box": reels_per_box,
                          "shift": shift, "customer": customer},
                         request_id=request_id)

    def pack_carton(self, job_id: int, reel_ids: list[int], shift: str = "DAY",
                    customer: str | None = None,
                    request_id: str | None = None) -> dict:
        return self.call("pack_carton",
                         {"job_id": job_id, "reel_ids": reel_ids,
                          "shift": shift, "customer": customer},
                         request_id=request_id)

    def delete_carton(self, carton_id: int,
                      request_id: str | None = None) -> dict:
        return self.call("delete_carton", {"carton_id": carton_id},
                         request_id=request_id)

    def mark_reel_printed(self, reel_id: int) -> dict:
        return self.call("mark_reel_printed", {"reel_id": reel_id})

    def mark_carton_printed(self, carton_id: int) -> dict:
        return self.call("mark_carton_printed", {"carton_id": carton_id})

    def create_dispatch_order(self, customer: str, job_id: int,
                              required_bundles: int | None = None,
                              order_date: str | None = None,
                              required_pieces: int | None = None,
                              customer_po: str = "", batch_no: str = "",
                              piece_unit: str = "wrapper",
                              request_id: str | None = None) -> dict:
        args: dict = {"customer": customer, "job_id": job_id,
                      "piece_unit": piece_unit}
        if required_bundles is not None:
            args["required_bundles"] = required_bundles
        if required_pieces is not None:
            args["required_pieces"] = required_pieces
        if order_date:
            args["order_date"] = order_date
        if customer_po:
            args["customer_po"] = customer_po
        if batch_no:
            args["batch_no"] = batch_no
        return self.call("create_dispatch_order", args,
                         request_id=request_id)

    def client_label_detail(self, order_id: int, carton_id: int) -> dict:
        return self.call("client_label_detail",
                         {"order_id": order_id, "carton_id": carton_id})

    def get_company_logo(self) -> str:
        return self.call("get_company_logo")

    def set_company_logo(self, logo_b64: str,
                         request_id: str | None = None) -> dict:
        return self.call("set_company_logo", {"logo": logo_b64},
                         request_id=request_id)

    def scan_into_order(self, order_id: int, scan_text: str,
                        request_id: str | None = None) -> dict:
        return self.call("scan_into_order",
                         {"order_id": order_id, "scan_text": scan_text},
                         request_id=request_id)

    def remove_from_order(self, order_id: int, carton_id: int,
                          request_id: str | None = None) -> dict:
        return self.call("remove_from_order",
                         {"order_id": order_id, "carton_id": carton_id},
                         request_id=request_id)

    def complete_dispatch(self, order_id: int,
                          request_id: str | None = None) -> dict:
        return self.call("complete_dispatch", {"order_id": order_id},
                         request_id=request_id)

    def dispatch_slip(self, order_id: int,
                      request_id: str | None = None) -> dict:
        return self.call("dispatch_slip", {"order_id": order_id},
                         request_id=request_id)

    def mark_dispatch_slip_printed(self, order_id: int,
                                   request_id: str | None = None) -> dict:
        return self.call("mark_dispatch_slip_printed", {"order_id": order_id},
                         request_id=request_id)

    def cancel_order(self, order_id: int,
                     request_id: str | None = None) -> dict:
        return self.call("cancel_order", {"order_id": order_id},
                         request_id=request_id)
