"""Repeatable performance benchmarks for Simpl'd.

This module answers two questions with measurements rather than guesses:

1. How long does one inventory write take on this machine (SQLite + the real
   service rules), and how does that change with several stations writing at
   once?
2. How much of the dispatcher's scan latency is service work and how much is
   the current LAN transport (HTTPS, one fresh TLS connection per request)?

It is deliberately a developer/owner tool, not part of the floor UI:

* Every local run creates a fresh temporary DB. It never reads or writes
  ``data/factory.db``.
* ``temp_lan_server()`` starts a real HTTPS LAN server on a temp DB. This is
  safe for write benchmarks and is the recommended way to measure the real
  network between the packager and dispatch PCs.
* An external LAN server is **read-only by default**. Write benchmarks against
  an external server require ``allow_writes=True`` and should only be pointed
  at a benchmark/temp server, never production.

No Qt imports here. ``factory.lan_server`` is imported lazily so percentile
tests and local-only use stay lightweight.
"""
from __future__ import annotations

import contextlib
import ipaddress
import json
import math
import os
import platform
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Iterable

from . import auth as A
from . import db as DB
from . import services as S
from .version import VERSION

BENCH_USER = "bench-admin"
DEFAULT_WPR = 1000
DEFAULT_REELS_PER_BOX = 2
REEL_GROSS = 11.38
REEL_CORE = 0.28
BOX_GROSS = 22.60
BOX_TARE = 0.300
# LAN validation caps required_pieces at 100,000,000. Keep benchmark orders
# inside the public API rather than bypassing server validation.
HUGE_PIECES = 100_000_000

# Ordered so reports read like the floor flow.
WRITE_OPS = (
    "create_reel",
    "pack_next_box",
    "create_dispatch_order",
    "scan_into_order",
    "order_progress",
    "complete_dispatch",
)
READ_OPS = (
    "ready_stock",
    "order_progress",
    "client_label_detail",
    "list_orders",
    "job_stats",
    "production_history",
    "packing_slip_rows",
    "daily_summary",
)
SCALE_READ_OPS = (
    "ready_stock",
    "order_progress",
    "list_orders",
    "job_stats",
    "production_history",
    "packing_slip_rows",
    "daily_summary",
)


# ---------------------------------------------------------------------------
# Small statistics helpers
# ---------------------------------------------------------------------------

def percentile(values: Iterable[float], pct: float) -> float:
    """Linear-interpolated percentile. Returns 0.0 for an empty sequence."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    pct = max(0.0, min(100.0, float(pct)))
    rank = (len(vals) - 1) * pct / 100.0
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (rank - lo)


def summarize(samples_ms: Iterable[float], errors: int = 0) -> dict:
    """Summarise a list of millisecond samples for the report."""
    vals = [float(v) for v in samples_ms]
    n = len(vals)
    if not n:
        return {
            "n": 0, "mean_ms": 0.0, "min_ms": 0.0, "p50_ms": 0.0,
            "p90_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0,
            "ops_per_s": 0.0, "errors": int(errors),
        }
    mean = sum(vals) / n
    return {
        "n": n,
        "mean_ms": round(mean, 3),
        "min_ms": round(min(vals), 3),
        "p50_ms": round(percentile(vals, 50), 3),
        "p90_ms": round(percentile(vals, 90), 3),
        "p95_ms": round(percentile(vals, 95), 3),
        "p99_ms": round(percentile(vals, 99), 3),
        "max_ms": round(max(vals), 3),
        "ops_per_s": round(1000.0 / mean, 2) if mean > 0 else 0.0,
        "errors": int(errors),
    }


def measure(backend: "Backend", op: str, fn: Callable[[], Any],
            iterations: int, warmup: int = 0,
            stop_on_error: bool = False) -> dict:
    """Time ``fn`` and return one report row.

    Warmup calls are excluded from the samples. Writes normally use
    ``warmup=0`` because each call mutates data; reads use a small warmup.
    """
    iterations = max(0, int(iterations))
    for _ in range(max(0, int(warmup))):
        try:
            fn()
        except Exception:
            break
    samples: list[float] = []
    errors: list[str] = []
    req_bytes: list[int] = []
    resp_bytes: list[int] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report, don't hide a failed op
            errors.append(f"{type(exc).__name__}: {exc}")
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        samples.append(elapsed_ms)
        if backend.kind == "lan":
            req_bytes.append(backend.last_req_bytes)
            resp_bytes.append(backend.last_resp_bytes)
        if errors and stop_on_error:
            break
    row = {
        "op": op,
        "stats": summarize(samples, len(errors)),
        "errors": errors[:5],
        "error_count": len(errors),
    }
    if req_bytes:
        row["request_bytes_avg"] = round(sum(req_bytes) / len(req_bytes), 1)
        row["response_bytes_avg"] = round(sum(resp_bytes) / len(resp_bytes), 1)
    return row


# ---------------------------------------------------------------------------
# Temporary DB / TLS / server helpers
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def temp_db(prefix: str = "simpld-bench-"):
    """Fresh schema + one random admin, in a throwaway directory.

    Yields ``(directory, db_path, password)``. The random password is only
    printed for the explicit ``serve`` command; it is never persisted.
    """
    directory = tempfile.mkdtemp(prefix=prefix)
    db_path = os.path.join(directory, "factory.db")
    DB.init_db(db_path)
    password = secrets.token_urlsafe(12)
    c = DB.get_conn(db_path)
    try:
        c.execute(
            "INSERT INTO users(username, pass_hash, role, created_at)"
            " VALUES(?,?,?,?)",
            (BENCH_USER, A.hash_password(password), "admin", DB.now_iso()))
        c.commit()
    finally:
        c.close()
    try:
        yield directory, db_path, password
    finally:
        A.set_session(None)
        with A._SESSIONS_LOCK:
            A._SESSIONS.clear()
        shutil.rmtree(directory, ignore_errors=True)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def make_cert(directory: str, host: str = "127.0.0.1") -> tuple[str, str]:
    """Create a short-lived self-signed cert for the benchmark server."""
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError(
            "openssl CLI not found. Generate a cert per LAN_SETUP.md and pass "
            "--cert/--key to the serve command.")
    host = (host or "127.0.0.1").strip()
    if host in ("0.0.0.0", "::", "[::]"):
        raise RuntimeError(
            "--cert-host must be the address clients use (e.g. the packager "
            "LAN IP), not a wildcard bind address.")
    cert = os.path.join(directory, "bench-cert.pem")
    key = os.path.join(directory, "bench-key.pem")
    san: list[str] = []
    if _is_ip(host):
        san.append(f"IP:{host}")
    else:
        san.append(f"DNS:{host}")
    for fallback in ("127.0.0.1", "localhost"):
        item = f"IP:{fallback}" if _is_ip(fallback) else f"DNS:{fallback}"
        if item not in san:
            san.append(item)
    proc = subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "2", "-nodes",
         "-subj", "/CN=simpld-benchmark",
         "-addext", "subjectAltName=" + ",".join(san)],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            "openssl could not create the benchmark certificate: "
            + (proc.stderr or proc.stdout or "unknown error")[-1500:])
    if not (os.path.getsize(cert) and os.path.getsize(key)):
        raise RuntimeError("openssl produced empty certificate/key files.")
    return cert, key


def _start_server(db_path: str, bind: str, port: int,
                  cert: str, key: str):
    from . import lan_server as LS
    httpd = LS.create_server(db_path, bind, port, cert, key)
    # The normal server logs every request to stderr; a benchmark should not
    # drown its own report in access logs.
    httpd.RequestHandlerClass.log_message = lambda *a, **k: None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop_server(httpd, thread) -> None:
    try:
        httpd.shutdown()
    except Exception:
        pass
    try:
        httpd.server_close()
    except Exception:
        pass
    try:
        thread.join(timeout=10)
    except Exception:
        pass


@contextlib.contextmanager
def temp_lan_server(host: str = "127.0.0.1"):
    """Temp DB + real TLS LAN server on 127.0.0.1 for a loopback baseline."""
    with temp_db() as (directory, db_path, password):
        cert, key = make_cert(directory, host)
        httpd, thread = _start_server(db_path, "127.0.0.1", 0, cert, key)
        port = int(httpd.server_address[1])
        try:
            yield {
                "host": "127.0.0.1", "port": port, "ca": cert,
                "user": BENCH_USER, "password": password,
                "db": db_path, "dir": directory,
            }
        finally:
            _stop_server(httpd, thread)


# ---------------------------------------------------------------------------
# Backends: identical method surface for direct and LAN calls
# ---------------------------------------------------------------------------

def _traffic_client_class(base):
    class _Traffic(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.last_req_bytes = 0
            self.last_resp_bytes = 0

        def call(self, op, args=None, request_id=None):
            self.last_req_bytes = len(json.dumps(
                {"op": op, "args": args or {}}).encode("utf-8"))
            self.last_resp_bytes = 0
            data = super().call(op, args, request_id=request_id)
            try:
                self.last_resp_bytes = len(json.dumps(
                    data, default=str).encode("utf-8"))
            except Exception:
                self.last_resp_bytes = 0
            return data

    return _Traffic


class Backend:
    kind = "abstract"
    role = ""

    def close(self) -> None:
        pass

    @property
    def last_req_bytes(self) -> int:
        return 0

    @property
    def last_resp_bytes(self) -> int:
        return 0


class LocalBackend(Backend):
    """In-process service calls against one temp DB (admin session)."""

    kind = "local"
    role = "admin"

    def __init__(self, path: str, user: str = BENCH_USER, password: str = ""):
        self.path = path
        self.user = user
        self.password = password
        if A.authenticate(user, password, path) is None:
            raise RuntimeError("Benchmark login failed on the temp DB.")
        who = A.current_user(path) or {}
        self.role = str(who.get("role") or "")

    def close(self) -> None:
        A.set_session(None)

    # -- writes --
    def create_product(self, name: str, wrappers: int = DEFAULT_WPR) -> dict:
        return S.create_product(name, wrappers, path=self.path)

    def create_job_for_product(self, product_id: int) -> dict:
        return S.create_job_for_product(product_id, path=self.path)

    def create_reel(self, job_id: int) -> dict:
        return S.create_reel(job_id, gross=REEL_GROSS, core=REEL_CORE,
                             path=self.path)

    def pack_next_box(self, job_id: int,
                      reels_per_box: int = DEFAULT_REELS_PER_BOX) -> dict:
        return S.pack_next_box(job_id, BOX_GROSS, BOX_TARE,
                               reels_per_box=reels_per_box, path=self.path)

    def pack_carton(self, job_id: int, reel_ids: list[int]) -> dict:
        return S.pack_carton(job_id, reel_ids, path=self.path)

    def create_order(self, job_id: int, required_pieces: int,
                     customer_po: str = "BENCH") -> dict:
        return S.create_dispatch_order(
            "BENCHMARK CUSTOMER", job_id, required_pieces=required_pieces,
            customer_po=customer_po, piece_unit="wrapper", path=self.path)

    def scan(self, order_id: int, scan_text: str) -> dict:
        return S.scan_into_order(order_id, scan_text, path=self.path)

    def complete_dispatch(self, order_id: int) -> dict:
        return S.complete_dispatch(order_id, path=self.path)

    # -- reads --
    def whoami(self) -> dict:
        return A.current_user(self.path) or {}

    def list_jobs(self, status: str | None = None) -> list[dict]:
        return S.list_jobs(status=status, path=self.path)

    def job_stats(self, job_id: int) -> dict:
        return S.job_stats(job_id, path=self.path)

    def ready_stock(self, job_id: int | None = None) -> list[dict]:
        return S.ready_stock(job_id=job_id, path=self.path)

    def find_carton(self, scan_text: str) -> dict | None:
        return S.find_carton(scan_text, path=self.path)

    def carton_detail(self, carton_id: int) -> dict:
        return S.carton_detail(carton_id, path=self.path)

    def client_label_detail(self, order_id: int, carton_id: int) -> dict:
        return S.client_label_detail(order_id, carton_id, path=self.path)

    def list_orders(self, status: str | None = None) -> list[dict]:
        return S.list_orders(status=status, path=self.path)

    def order_progress(self, order_id: int) -> dict:
        return S.order_progress(order_id, path=self.path)

    def production_history(self, limit: int | None = None) -> list[dict]:
        rows = S.production_history(path=self.path)
        return rows if limit is None else rows[:int(limit)]

    def packing_slip_rows(self, job_id: int):
        return S.packing_slip_rows(job_id, path=self.path)

    def daily_summary(self, day: str | None = None) -> dict:
        return S.daily_summary(day=day, path=self.path)


class LanBackend(Backend):
    """One authenticated HTTPS client session against a LAN server."""

    kind = "lan"

    def __init__(self, host: str, port: int, ca: str,
                 user: str, password: str, timeout: float = 10.0):
        from . import lan_client as LC
        self.client = _traffic_client_class(LC.LanClient)(
            host, int(port), ca, timeout=float(timeout))
        who = self.client.login(user, password)
        self.role = str((who or {}).get("role") or "")
        self.host = host
        self.port = int(port)

    @property
    def last_req_bytes(self) -> int:
        return int(getattr(self.client, "last_req_bytes", 0) or 0)

    @property
    def last_resp_bytes(self) -> int:
        return int(getattr(self.client, "last_resp_bytes", 0) or 0)

    def close(self) -> None:
        try:
            self.client.logout()
        except Exception:
            pass

    def create_product(self, name: str, wrappers: int = DEFAULT_WPR) -> dict:
        return self.client.create_product(name, wrappers)

    def create_job_for_product(self, product_id: int) -> dict:
        return self.client.create_job_for_product(product_id)

    def create_reel(self, job_id: int) -> dict:
        return self.client.create_reel(job_id, REEL_GROSS, core=REEL_CORE)

    def pack_next_box(self, job_id: int,
                      reels_per_box: int = DEFAULT_REELS_PER_BOX) -> dict:
        return self.client.pack_next_box(
            job_id, BOX_GROSS, BOX_TARE, reels_per_box=reels_per_box)

    def pack_carton(self, job_id: int, reel_ids: list[int]) -> dict:
        return self.client.pack_carton(job_id, reel_ids)

    def create_order(self, job_id: int, required_pieces: int,
                     customer_po: str = "BENCH") -> dict:
        return self.client.create_dispatch_order(
            "BENCHMARK CUSTOMER", job_id, required_pieces=required_pieces,
            customer_po=customer_po, piece_unit="wrapper")

    def scan(self, order_id: int, scan_text: str) -> dict:
        return self.client.scan_into_order(order_id, scan_text)

    def complete_dispatch(self, order_id: int) -> dict:
        return self.client.complete_dispatch(order_id)

    def whoami(self) -> dict:
        return self.client.whoami()

    def list_jobs(self, status: str | None = None) -> list[dict]:
        return self.client.list_jobs(status)

    def job_stats(self, job_id: int) -> dict:
        return self.client.job_stats(job_id)

    def ready_stock(self, job_id: int | None = None) -> list[dict]:
        return self.client.ready_stock(job_id)

    def find_carton(self, scan_text: str) -> dict | None:
        return self.client.find_carton(scan_text)

    def carton_detail(self, carton_id: int) -> dict:
        return self.client.carton_detail(carton_id)

    def client_label_detail(self, order_id: int, carton_id: int) -> dict:
        return self.client.client_label_detail(order_id, carton_id)

    def list_orders(self, status: str | None = None) -> list[dict]:
        return self.client.list_orders(status)

    def order_progress(self, order_id: int) -> dict:
        return self.client.order_progress(order_id)

    def production_history(self, limit: int | None = 200) -> list[dict]:
        return self.client.production_history(200 if limit is None else int(limit))

    def daily_summary(self, day: str | None = None) -> dict:
        return self.client.daily_summary(day)


# ---------------------------------------------------------------------------
# Data setup helpers
# ---------------------------------------------------------------------------

def _new_job(backend: Backend, label: str) -> dict:
    name = f"BENCH {label} {secrets.token_hex(3).upper()}"
    product = backend.create_product(name, DEFAULT_WPR)
    return backend.create_job_for_product(int(product["id"]))


def _make_box(backend: Backend, job_id: int,
              reels: int = DEFAULT_REELS_PER_BOX) -> dict:
    reel_ids = [int(backend.create_reel(job_id)["id"]) for _ in range(reels)]
    return backend.pack_carton(job_id, reel_ids)


def _require_admin(backend: Backend) -> None:
    if backend.role != "admin":
        raise RuntimeError(
            f"Write benchmark needs an admin account; this session is "
            f"'{backend.role or 'unknown'}'. Use the temporary benchmark "
            "server or --read-only.")


# ---------------------------------------------------------------------------
# Scenario: single-client service path
# ---------------------------------------------------------------------------

def run_path(backend: Backend, iterations: int, warmup: int = 2,
             progress: Callable[[str], None] | None = None) -> dict:
    """Measure the packaging -> scan -> progress path on one client."""
    _require_admin(backend)
    iterations = max(1, int(iterations))

    def note(msg: str) -> None:
        if progress:
            progress(msg)

    started = time.perf_counter()
    note("setting up benchmark jobs and boxes")
    job_reel = _new_job(backend, "REEL")
    job_pack = _new_job(backend, "PACK")
    for _ in range(iterations * DEFAULT_REELS_PER_BOX):
        backend.create_reel(int(job_pack["id"]))
    job_scan = _new_job(backend, "SCAN")
    scan_boxes = [_make_box(backend, int(job_scan["id"]))
                  for _ in range(iterations)]
    job_stock = _new_job(backend, "STOCK")
    for _ in range(iterations):
        _make_box(backend, int(job_stock["id"]))
    job_orders = _new_job(backend, "ORDERS")
    scan_order = backend.create_order(
        int(job_scan["id"]),
        min(HUGE_PIECES,
            iterations * DEFAULT_REELS_PER_BOX * DEFAULT_WPR),
        "SCAN")
    setup_s = time.perf_counter() - started
    note(f"setup done in {setup_s:.2f}s; measuring")

    ops: list[dict] = []
    ops.append(measure(
        backend, "create_reel",
        lambda: backend.create_reel(int(job_reel["id"])),
        iterations, 0, True))
    ops.append(measure(
        backend, "pack_next_box",
        lambda: backend.pack_next_box(int(job_pack["id"])),
        iterations, 0, True))
    ops.append(measure(
        backend, "create_dispatch_order",
        lambda: backend.create_order(int(job_orders["id"]), HUGE_PIECES, "BENCH"),
        iterations, 0, True))

    box_iter = iter(scan_boxes)

    def next_scan() -> dict:
        box = next(box_iter)
        return backend.scan(int(scan_order["id"]), str(box["carton_no"]))

    ops.append(measure(backend, "scan_into_order", next_scan,
                       iterations, 0, True))
    ops.append(measure(
        backend, "order_progress",
        lambda: backend.order_progress(int(scan_order["id"])),
        iterations, max(0, warmup), False))
    ops.append(measure(
        backend, "ready_stock",
        lambda: backend.ready_stock(int(job_stock["id"])),
        iterations, max(0, warmup), False))
    ops.append(measure(
        backend, "job_stats",
        lambda: backend.job_stats(int(job_stock["id"])),
        iterations, max(0, warmup), False))

    if scan_boxes:
        first = scan_boxes[0]
        ops.append(measure(
            backend, "client_label_detail",
            lambda: backend.client_label_detail(
                int(scan_order["id"]), int(first["id"])),
            min(iterations, 50), max(0, warmup), False))
    ops.append(measure(
        backend, "list_orders", lambda: backend.list_orders(),
        min(iterations, 50), 1, False))
    if backend.role == "admin":
        ops.append(measure(
            backend, "daily_summary", lambda: backend.daily_summary(),
            min(iterations, 50), 1, False))

    return {
        "setup_s": round(setup_s, 3),
        "iterations": iterations,
        # measured pack boxes + scan boxes + stock boxes
        "boxes": iterations * 3,
        # measured reels + pack reels + scan-box reels + stock-box reels
        "reels": iterations * (1 + 3 * DEFAULT_REELS_PER_BOX),
        "ops": ops,
    }


# ---------------------------------------------------------------------------
# Scenario: read-only external server
# ---------------------------------------------------------------------------

def run_read_only(backend: Backend, iterations: int,
                  warmup: int = 1,
                  progress: Callable[[str], None] | None = None) -> dict:
    """Safe measurements against an external/production LAN server."""
    role = (backend.whoami() or {}).get("role") or backend.role
    backend.role = str(role)
    jobs = backend.list_jobs() or []
    job_id = int(jobs[0]["id"]) if jobs else None
    ops: list[dict] = []
    skipped: list[str] = []

    if progress:
        progress(f"read-only mode ({role}); no writes will be sent")

    ops.append(measure(backend, "whoami", backend.whoami,
                       iterations, warmup, False))
    ops.append(measure(backend, "list_jobs", backend.list_jobs,
                       iterations, warmup, False))
    ops.append(measure(backend, "ready_stock",
                       lambda: backend.ready_stock(None),
                       iterations, warmup, False))
    if job_id is not None:
        ops.append(measure(backend, "job_stats",
                           lambda: backend.job_stats(job_id),
                           iterations, warmup, False))
    else:
        skipped.append("job_stats (server has no jobs)")
    if role in ("dispatcher", "admin"):
        ops.append(measure(
            backend, "production_history",
            lambda: backend.production_history(200), iterations, warmup, False))
        ops.append(measure(backend, "list_orders", backend.list_orders,
                           iterations, warmup, False))
    else:
        skipped.append(f"production_history (role '{role}' lacks history.read)")
        skipped.append(f"list_orders (role '{role}' lacks report.read)")
    if role == "admin":
        ops.append(measure(backend, "daily_summary", backend.daily_summary,
                           iterations, warmup, False))
    else:
        skipped.append(f"daily_summary (role '{role}'; admin only)")

    return {"role": role, "iterations": iterations,
            "skipped": skipped, "ops": ops}


# ---------------------------------------------------------------------------
# Scenario: concurrent inventory updates
# ---------------------------------------------------------------------------

def _concurrency_worker(factory: Callable[[], Backend], worker_id: int,
                        cycles: int) -> dict:
    backend = factory()
    try:
        _require_admin(backend)
        job = _new_job(backend, f"CONC{worker_id}")
        samples: dict[str, list[float]] = {}
        op_errors: dict[str, int] = {}
        errors: list[str] = []
        completed = 0

        def timed(op: str, fn: Callable[[], Any]) -> Any:
            t0 = time.perf_counter_ns()
            try:
                return fn()
            except Exception:
                op_errors[op] = op_errors.get(op, 0) + 1
                raise
            finally:
                samples.setdefault(op, []).append(
                    (time.perf_counter_ns() - t0) / 1_000_000.0)

        started = time.perf_counter()
        for _ in range(cycles):
            try:
                timed("create_reel",
                      lambda: backend.create_reel(int(job["id"])))
                timed("create_reel",
                      lambda: backend.create_reel(int(job["id"])))
                box = timed("pack_next_box",
                            lambda: backend.pack_next_box(int(job["id"])))
                order = timed(
                    "create_dispatch_order",
                    lambda: backend.create_order(
                        int(job["id"]), 2 * DEFAULT_WPR, "CONC"))
                timed("scan_into_order",
                      lambda: backend.scan(
                          int(order["id"]), str(box["carton_no"])))
                timed("order_progress",
                      lambda: backend.order_progress(int(order["id"])))
                timed("complete_dispatch",
                      lambda: backend.complete_dispatch(int(order["id"])))
                completed += 1
            except Exception as exc:  # noqa: BLE001 - keep the report useful
                errors.append(f"worker {worker_id}: {type(exc).__name__}: {exc}")
                break
        ended = time.perf_counter()
        return {
            "worker": worker_id, "samples": samples, "op_errors": op_errors,
            "errors": errors, "completed": completed,
            "started": started, "ended": ended,
        }
    finally:
        backend.close()


def run_concurrency(backend_factory: Callable[[], Backend], workers: int,
                    cycles: int,
                    progress: Callable[[str], None] | None = None) -> dict:
    """Run independent inventory-update cycles from several clients."""
    workers = max(1, int(workers))
    cycles = max(1, int(cycles))
    if progress:
        progress(f"starting {workers} concurrent clients x {cycles} cycles")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_concurrency_worker, backend_factory, wid, cycles)
                   for wid in range(workers)]
        for fut in as_completed(futures):
            results.append(fut.result())
            if progress:
                progress(f"client {len(results)}/{workers} finished")

    samples: dict[str, list[float]] = {}
    op_errors: dict[str, int] = {}
    errors: list[str] = []
    for result in results:
        for op, vals in result["samples"].items():
            samples.setdefault(op, []).extend(vals)
        for op, count in result.get("op_errors", {}).items():
            op_errors[op] = op_errors.get(op, 0) + count
        errors.extend(result["errors"])
    started = min((r["started"] for r in results), default=time.perf_counter())
    ended = max((r["ended"] for r in results), default=started)
    elapsed = max(ended - started, 1e-9)
    ops_total = sum(len(vals) for vals in samples.values())
    cycles_done = sum(int(r["completed"]) for r in results)
    rows = []
    for op in WRITE_OPS:
        if op not in samples:
            continue
        rows.append({
            "op": op,
            "stats": summarize(samples[op], op_errors.get(op, 0)),
            "error_count": op_errors.get(op, 0),
        })
    return {
        "workers": workers,
        "cycles": cycles,
        "attempted_cycles": workers * cycles,
        "completed_cycles": cycles_done,
        "elapsed_s": round(elapsed, 3),
        "cycles_per_s": round(cycles_done / elapsed, 2),
        "ops_per_s": round(ops_total / elapsed, 2),
        "ops": rows,
        "errors": errors[:20],
        "error_count": len(errors),
    }


# ---------------------------------------------------------------------------
# Scenario: local scale sweep
# ---------------------------------------------------------------------------

def _db_size_mb(path: str) -> float:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(path + suffix)
        except OSError:
            pass
    return round(total / (1024 * 1024), 2)


def run_scale(sizes: Iterable[int], iterations: int = 5,
              progress: Callable[[str], None] | None = None) -> list[dict]:
    """Seed N boxes with the real services, then measure reads at that size."""
    cleaned = sorted({int(n) for n in sizes if int(n) > 0})
    out: list[dict] = []
    for n in cleaned:
        if progress:
            progress(f"scale {n:,} boxes: seeding")
        with temp_db(prefix="simpld-bench-scale-") as (_d, path, password):
            backend = LocalBackend(path, BENCH_USER, password)
            try:
                seed_started = time.perf_counter()
                stock_job = _new_job(backend, "SCALE-STOCK")
                for i in range(n):
                    _make_box(backend, int(stock_job["id"]))
                    if progress and (i + 1) % 250 == 0:
                        progress(f"scale {n:,}: stock {i + 1:,}/{n:,}")
                dispatch_job = _new_job(backend, "SCALE-DISPATCH")
                boxes = []
                for i in range(n):
                    boxes.append(_make_box(backend, int(dispatch_job["id"])))
                    if progress and (i + 1) % 250 == 0:
                        progress(f"scale {n:,}: dispatch {i + 1:,}/{n:,}")
                order = backend.create_order(
                    int(dispatch_job["id"]),
                    min(HUGE_PIECES,
                        n * DEFAULT_REELS_PER_BOX * DEFAULT_WPR),
                    "SCALE")
                seed_s = time.perf_counter() - seed_started

                if progress:
                    progress(f"scale {n:,}: scanning {n:,} boxes into one order")
                scan_started = time.perf_counter()
                for box in boxes:
                    backend.scan(int(order["id"]), str(box["carton_no"]))
                scan_s = time.perf_counter() - scan_started

                if progress:
                    progress(f"scale {n:,}: measuring reads")
                reads = [
                    measure(backend, "ready_stock",
                            lambda: backend.ready_stock(int(stock_job["id"])),
                            iterations, 1, False),
                    measure(backend, "order_progress",
                            lambda: backend.order_progress(int(order["id"])),
                            iterations, 1, False),
                    measure(backend, "list_orders", backend.list_orders,
                            iterations, 1, False),
                    measure(backend, "job_stats",
                            lambda: backend.job_stats(int(stock_job["id"])),
                            iterations, 1, False),
                    measure(backend, "production_history",
                            backend.production_history,
                            iterations, 1, False),
                    measure(backend, "packing_slip_rows",
                            lambda: backend.packing_slip_rows(
                                int(stock_job["id"])),
                            iterations, 1, False),
                    measure(backend, "daily_summary", backend.daily_summary,
                            iterations, 1, False),
                ]
                out.append({
                    "boxes": n,
                    "reels": n * 2 * 2,
                    "db_mb": _db_size_mb(path),
                    "seed_s": round(seed_s, 3),
                    "seed_boxes_per_s": round((2 * n) / seed_s, 2)
                    if seed_s > 0 else 0.0,
                    "scan_s": round(scan_s, 3),
                    "scan_avg_ms": round(scan_s * 1000.0 / n, 3) if n else 0.0,
                    "reads": reads,
                })
            finally:
                backend.close()
        if progress:
            progress(f"scale {n:,} boxes: done")
    return out


def measured_limits(scale_rows: list[dict], sla_ms: float = 200.0) -> dict:
    """Largest tested size whose p95 is under the SLA, per read op."""
    limits: dict[str, str] = {}
    if not scale_rows:
        return limits
    for op in SCALE_READ_OPS:
        passing = []
        for row in scale_rows:
            match = next((r for r in row.get("reads", [])
                          if r.get("op") == op), None)
            if match and match["stats"]["p95_ms"] <= sla_ms:
                passing.append(int(row["boxes"]))
        if not passing:
            limits[op] = f"below {min(r['boxes'] for r in scale_rows):,} boxes"
        elif len(passing) == len(scale_rows):
            limits[op] = f">= {max(passing):,} boxes (largest tested)"
        else:
            limits[op] = f"{max(passing):,} boxes"
    return limits


# ---------------------------------------------------------------------------
# Environment / report
# ---------------------------------------------------------------------------

def environment() -> dict:
    return {
        "app_version": VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "sqlite": sqlite3.sqlite_version,
        "openssl": ssl.OPENSSL_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _stats_by_op(ops: list[dict]) -> dict[str, dict]:
    return {row["op"]: row for row in ops or []}


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    return "\n".join(out)


def _path_table(ops: list[dict]) -> str:
    rows = []
    for row in ops or []:
        s = row["stats"]
        rows.append([
            row["op"], s["n"], s["p50_ms"], s["p95_ms"], s["p99_ms"],
            s["max_ms"], s["ops_per_s"], row.get("error_count", 0),
        ])
    return _md_table(
        ["Operation", "n", "p50 ms", "p95 ms", "p99 ms", "max ms",
         "ops/s", "errors"], rows)


def _payload_table(ops: list[dict]) -> str:
    rows = []
    for row in ops or []:
        if "request_bytes_avg" not in row:
            continue
        rows.append([row["op"], row["request_bytes_avg"],
                     row["response_bytes_avg"]])
    if not rows:
        return ""
    return _md_table(["Operation", "request B (approx)",
                      "response B (approx)"], rows)


def _concurrency_markdown(title: str, result: dict) -> str:
    head = (
        f"### {title}\n\n"
        f"{result['completed_cycles']:,}/{result['attempted_cycles']:,} cycles "
        f"completed in {result['elapsed_s']:.2f}s - "
        f"**{result['cycles_per_s']:.2f} cycles/s**, "
        f"**{result['ops_per_s']:.2f} calls/s**, "
        f"{result['error_count']} error(s).\n\n"
    )
    rows = []
    for row in result.get("ops", []):
        s = row["stats"]
        rows.append([row["op"], s["n"], s["p50_ms"], s["p95_ms"],
                     s["p99_ms"], s["max_ms"], row.get("error_count", 0)])
    if rows:
        head += _md_table(
            ["Operation", "n", "p50 ms", "p95 ms", "p99 ms", "max ms",
             "errors"], rows)
    if result.get("errors"):
        head += "\n\nErrors:\n" + "\n".join(
            f"- `{e}`" for e in result["errors"])
    return head


def render_markdown(report: dict) -> str:
    env = report.get("environment", {})
    cfg = report.get("config", {})
    local = report.get("local") or {}
    lan = report.get("lan") or {}
    out: list[str] = ["# Simpl'd benchmark report", ""]
    out.append(f"Generated: {env.get('generated_at', '')}")
    out.append("")
    out.append("## Environment")
    out.append("")
    out.append(
        f"- Simpl'd **{env.get('app_version', '?')}** on {env.get('platform', '?')} "
        f"({env.get('machine', '?')}, {env.get('cpu_count', '?')} CPUs)")
    out.append(
        f"- Python {env.get('python', '?')} | SQLite {env.get('sqlite', '?')} | "
        f"{env.get('openssl', '?')}")
    if lan:
        endpoint = lan.get("endpoint", {})
        label = "temporary loopback server" if endpoint.get("local_server") \
            else "external LAN server"
        out.append(
            f"- LAN: `https://{endpoint.get('host', '?')}:{endpoint.get('port', '?')}` "
            f"({label})")
    out.append("")
    out.append("## Configuration")
    out.append("")
    out.append(
        f"- iterations per operation: {cfg.get('iterations', '?')}; "
        f"read warmup: {cfg.get('warmup', '?')}; "
        f"concurrency: {cfg.get('workers', '?')} clients x "
        f"{cfg.get('cycles', '?')} cycles")
    out.append(
        f"- scale sweep: {cfg.get('scale_sizes') or 'off'}; "
        f"scale read iterations: {cfg.get('scale_iterations', '?')}")
    out.append("")

    if local:
        out.append("## Local service latency (in-process SQLite)")
        out.append("")
        out.append(f"Setup: {local.get('path', {}).get('setup_s', 0):.2f}s. "
                   "One process, real services and audits, no network.")
        out.append("")
        out.append(_path_table(local.get("path", {}).get("ops", [])))
        out.append("")
        conc = local.get("concurrency")
        if conc:
            out.append(_concurrency_markdown("Local concurrent writes", conc))
            out.append("")
        scale = local.get("scale") or []
        if scale:
            out.append("## Local scale sweep")
            out.append("")
            out.append(
                "Real service writes seeded both stock and dispatch boxes; "
                "scan time grows with the order because each scan reads the "
                "loaded items. DB size includes WAL and audit rows.")
            out.append("")
            rows = []
            for row in scale:
                reads = _stats_by_op(row.get("reads", []))
                def p95(op: str) -> float:
                    return reads.get(op, {}).get("stats", {}).get("p95_ms", 0.0)
                rows.append([
                    f"{row['boxes']:,}", f"{row['reels']:,}", row["db_mb"],
                    row["seed_s"], row["seed_boxes_per_s"], row["scan_avg_ms"],
                    p95("ready_stock"), p95("order_progress"),
                    p95("list_orders"), p95("production_history"),
                    p95("packing_slip_rows"), p95("daily_summary"),
                ])
            out.append(_md_table(
                ["Boxes", "Coils", "DB MB", "Seed s", "Boxes/s", "Scan avg ms",
                 "stock p95", "progress p95", "orders p95", "history p95",
                 "slip p95", "summary p95"], rows))
            out.append("")
            limits = report.get("limits") or {}
            if limits:
                out.append(f"### Measured comfort limits (p95 <= "
                           f"{cfg.get('sla_ms', 200)} ms)")
                out.append("")
                for op in SCALE_READ_OPS:
                    if op in limits:
                        out.append(f"- `{op}`: {limits[op]}")
                out.append("")

    if lan:
        endpoint = lan.get("endpoint", {})
        out.append("## LAN latency (HTTPS, one connection per call)")
        out.append("")
        out.append(
            f"Endpoint `https://{endpoint.get('host', '?')}:{endpoint.get('port', '?')}`; "
            f"role `{lan.get('role', '?')}`.")
        out.append("")
        read_only = lan.get("read_only")
        if read_only is not None:
            out.append(
                "**Read-only run** - no writes were sent to this server.")
            if read_only.get("skipped"):
                out.append("")
                out.append("Skipped: " + "; ".join(read_only["skipped"]))
            out.append("")
            out.append(_path_table(read_only.get("ops", [])))
            payload = _payload_table(read_only.get("ops", []))
            if payload:
                out.append("")
                out.append("Approximate JSON payload sizes (TLS/HTTP overhead "
                           "not included):")
                out.append("")
                out.append(payload)
        else:
            out.append(f"Setup: {lan.get('path', {}).get('setup_s', 0):.2f}s.")
            out.append("")
            out.append(_path_table(lan.get("path", {}).get("ops", [])))
            payload = _payload_table(lan.get("path", {}).get("ops", []))
            if payload:
                out.append("")
                out.append("Approximate JSON payload sizes (TLS/HTTP overhead "
                           "not included):")
                out.append("")
                out.append(payload)
            conc = lan.get("concurrency")
            if conc:
                out.append("")
                out.append(_concurrency_markdown("LAN concurrent writes", conc))
        out.append("")

    comparison = report.get("comparison") or []
    if comparison:
        out.append("## LAN overhead vs local (same operation counts)")
        out.append("")
        out.append(_md_table(
            ["Operation", "local p50 ms", "LAN p50 ms", "delta p50 ms",
             "local p95 ms", "LAN p95 ms"], comparison))
        out.append("")

    out.append("## How to read this")
    out.append("")
    out.append(
        "- `p50` is typical, `p95` is the slow one in twenty, `p99` is the "
        "tail. A scan that occasionally takes seconds is a floor problem even "
        "if its median looks fine.")
    out.append(
        "- The current LAN client opens a fresh TLS connection for every call. "
        "LAN p50 therefore includes a handshake; the `LAN minus local` table "
        "shows that transport cost. This is the baseline, not a keep-alive "
        "client.")
    out.append(
        "- Local runs and the automatic LAN server use throwaway DBs. "
        "External LAN runs are read-only unless `--allow-writes` is explicit; "
        "write benchmarks belong on the temporary server.")
    out.append(
        "- The benchmark exercises services, SQLite, auth and HTTPS. It does "
        "not exercise Qt repainting, the weigh indicator, or a physical "
        "printer.")
    out.append(
        "- Scale seeding uses the real services (including audit rows), so "
        "the seed rate is itself a single-writer write benchmark. The "
        "\"comfort limit\" is just the largest tested size under the SLA, not "
        "a hard maximum.")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_benchmark(config: dict,
                  progress: Callable[[str], None] | None = None) -> dict:
    """Run the requested sections and return a JSON-serialisable report."""
    mode = str(config.get("mode") or "local").lower()
    if mode not in ("local", "lan", "both"):
        raise ValueError("mode must be local, lan or both")
    iterations = max(1, int(config.get("iterations", 50)))
    warmup = max(0, int(config.get("warmup", 2)))
    workers = max(1, int(config.get("workers", 4)))
    cycles = max(1, int(config.get("cycles", 5)))
    scale_sizes = [int(n) for n in (config.get("scale_sizes") or []) if int(n) > 0]
    if mode == "lan":
        # The scale sweep seeds a local temp DB; a network-only run should not
        # report a scale configuration it never executed.
        scale_sizes = []
    scale_iterations = max(1, int(config.get("scale_iterations", 5)))
    sla_ms = float(config.get("sla_ms", 200.0))
    timeout = float(config.get("timeout", 10.0))
    local_server = bool(config.get("local_server"))
    allow_writes = bool(config.get("allow_writes"))
    host = str(config.get("host") or "").strip()
    port = int(config.get("port") or 8443)
    ca = str(config.get("ca") or "").strip()
    user = str(config.get("user") or BENCH_USER).strip()
    password = str(config.get("password") or "")
    debug_progress = progress or (lambda _m: None)

    public_cfg = {
        "mode": mode, "iterations": iterations, "warmup": warmup,
        "workers": workers, "cycles": cycles,
        "scale_sizes": scale_sizes, "scale_iterations": scale_iterations,
        "sla_ms": sla_ms, "local_server": local_server,
        "allow_writes": allow_writes,
        "endpoint": (None if local_server or mode == "local"
                     else f"{host}:{port}"),
    }
    report: dict = {
        "schema": 1,
        "tool": "simpld-benchmark",
        "environment": environment(),
        "config": public_cfg,
        "local": None,
        "lan": None,
        "comparison": [],
        "limits": {},
    }

    if mode in ("local", "both"):
        debug_progress("running local service benchmark")
        with temp_db() as (_d, path, pw):
            backend = LocalBackend(path, BENCH_USER, pw)
            try:
                path_result = run_path(backend, iterations, warmup, progress)
                conc_result = run_concurrency(
                    lambda: LocalBackend(path, BENCH_USER, pw),
                    workers, cycles, progress)
            finally:
                backend.close()
        report["local"] = {"path": path_result, "concurrency": conc_result}
        if scale_sizes:
            debug_progress("running local scale sweep")
            report["local"]["scale"] = run_scale(
                scale_sizes, scale_iterations, progress)
            report["limits"] = measured_limits(
                report["local"]["scale"], sla_ms)

    if mode in ("lan", "both"):
        lan: dict = {}
        if local_server:
            debug_progress("starting temporary LAN server")
            with temp_lan_server() as srv:
                backend = LanBackend(
                    srv["host"], srv["port"], srv["ca"],
                    BENCH_USER, srv["password"], timeout)
                try:
                    lan["endpoint"] = {
                        "host": srv["host"], "port": srv["port"],
                        "local_server": True,
                    }
                    lan["role"] = backend.role
                    lan["path"] = run_path(
                        backend, iterations, warmup, progress)
                    lan["concurrency"] = run_concurrency(
                        lambda: LanBackend(
                            srv["host"], srv["port"], srv["ca"],
                            BENCH_USER, srv["password"], timeout),
                        workers, cycles, progress)
                finally:
                    backend.close()
        else:
            if not host or not ca or not password:
                raise ValueError(
                    "LAN mode needs --host, --ca and --password (or "
                    "--local-server).")
            debug_progress(f"connecting to LAN server {host}:{port}")
            backend = LanBackend(host, port, ca, user, password, timeout)
            try:
                lan["endpoint"] = {
                    "host": host, "port": port, "local_server": False,
                }
                lan["role"] = backend.role
                if allow_writes:
                    lan["path"] = run_path(
                        backend, iterations, warmup, progress)
                    lan["concurrency"] = run_concurrency(
                        lambda: LanBackend(host, port, ca, user, password,
                                           timeout),
                        workers, cycles, progress)
                else:
                    lan["read_only"] = run_read_only(
                        backend, iterations, warmup, progress)
            finally:
                backend.close()
        report["lan"] = lan

    if report["local"] and report["lan"] and report["lan"].get("path"):
        local_ops = _stats_by_op(
            report["local"]["path"].get("ops", []))
        lan_ops = _stats_by_op(
            report["lan"]["path"].get("ops", []))
        rows = []
        # order_progress appears in both tuples; keep one row per operation.
        for op in dict.fromkeys(WRITE_OPS + READ_OPS):
            if op not in local_ops or op not in lan_ops:
                continue
            l = local_ops[op]["stats"]
            n = lan_ops[op]["stats"]
            rows.append([
                op, l["p50_ms"], n["p50_ms"],
                round(n["p50_ms"] - l["p50_ms"], 3),
                l["p95_ms"], n["p95_ms"],
            ])
        report["comparison"] = rows

    return report


# ---------------------------------------------------------------------------
# Standalone temporary server
# ---------------------------------------------------------------------------

def serve_benchmark(bind: str = "127.0.0.1", port: int = 9443,
                    cert_host: str = "127.0.0.1",
                    seed_boxes: int = 0,
                    cert: str | None = None,
                    key: str | None = None,
                    seed_orders: int = 20) -> int:
    """Run a temporary HTTPS benchmark server until Ctrl+C.

    The DB and certificate live in a temp directory and are removed on exit.
    Prints the CA path, random admin password and ready-to-copy client
    commands. This is the safe way to benchmark real LAN write latency.
    """
    with temp_db(prefix="simpld-bench-serve-") as (directory, db_path, password):
        if seed_boxes > 0:
            print(f"[bench] seeding {seed_boxes:,} boxes in the temp DB ...",
                  file=sys.stderr)
            backend = LocalBackend(db_path, BENCH_USER, password)
            try:
                job = _new_job(backend, "SERVER")
                started = time.perf_counter()
                for i in range(seed_boxes):
                    _make_box(backend, int(job["id"]))
                    if (i + 1) % 250 == 0:
                        print(f"[bench] seeded {i + 1:,}/{seed_boxes:,}",
                              file=sys.stderr)
                for i in range(max(0, int(seed_orders))):
                    backend.create_order(int(job["id"]), HUGE_PIECES,
                                         f"SEED-{i + 1}")
                print(f"[bench] seed done in {time.perf_counter() - started:.1f}s",
                      file=sys.stderr)
            finally:
                backend.close()

        if cert and key:
            certfile, keyfile = cert, key
        else:
            print("[bench] generating a short-lived self-signed certificate ...",
                  file=sys.stderr)
            certfile, keyfile = make_cert(directory, cert_host)

        httpd, thread = _start_server(db_path, bind, int(port),
                                      certfile, keyfile)
        actual_port = int(httpd.server_address[1])
        if bind in ("0.0.0.0", "::", "[::]") and cert_host in (
                "127.0.0.1", "localhost"):
            print("[bench] WARNING: wildcard bind with a loopback cert host; "
                  "remote clients will fail TLS verification. Use "
                  "--cert-host <packager-LAN-IP>.", file=sys.stderr)
        print("", file=sys.stderr)
        print("[bench] temporary benchmark server is running", file=sys.stderr)
        print(f"[bench] DB (throwaway): {db_path}", file=sys.stderr)
        print(f"[bench] CA certificate: {certfile}", file=sys.stderr)
        print(f"[bench] endpoint: https://{cert_host}:{actual_port}",
              file=sys.stderr)
        print(f"[bench] user: {BENCH_USER}", file=sys.stderr)
        print(f"[bench] password: {password}", file=sys.stderr)
        print("", file=sys.stderr)
        print("[bench] On the dispatcher PC, copy the CA file and run:",
              file=sys.stderr)
        print(
            f"  python3 tools/benchmark.py --mode lan --host {cert_host} "
            f"--port {actual_port} --ca <copied-cert.pem> "
            f"--user {BENCH_USER} --password '{password}' --allow-writes",
            file=sys.stderr)
        print("[bench] Read-only (no benchmark data written):", file=sys.stderr)
        print(
            f"  python3 tools/benchmark.py --mode lan --host {cert_host} "
            f"--port {actual_port} --ca <copied-cert.pem> "
            f"--user {BENCH_USER} --password '{password}' --iterations 30",
            file=sys.stderr)
        print("[bench] Ctrl+C stops the server and deletes the temp DB.",
              file=sys.stderr)
        print("", file=sys.stderr)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[bench] stopping ...", file=sys.stderr)
        finally:
            _stop_server(httpd, thread)
    return 0
