#!/usr/bin/env python3
"""Simpl'd benchmark CLI.

Typical runs:

    # local services + concurrency + scale, safe temp DBs
    python3 tools/benchmark.py --quick

    # loopback LAN baseline (temp HTTPS server, safe writes)
    python3 tools/benchmark.py --mode lan --local-server --quick

    # real LAN write benchmark: start this on the packager ...
    python3 tools/benchmark.py --serve --bind 0.0.0.0 \\
        --cert-host 192.168.1.10 --port 9443 --seed-boxes 500

    # ... then on dispatch (copy the printed CA file):
    python3 tools/benchmark.py --mode lan --host 192.168.1.10 --port 9443 \\
        --ca ./bench-cert.pem --user bench-admin --password '...' \\
        --allow-writes --iterations 50

    # production server, read-only latency only
    python3 tools/benchmark.py --mode lan --host 192.168.1.10 --port 8443 \\
        --ca ./lan-cert.pem --user dispatcher --password '...'

See BENCHMARKING.md for interpretation, safety and sample results.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory import benchmark as B  # noqa: E402


def _parse_sizes(raw: str) -> list[int]:
    out: list[int] = []
    for part in str(raw or "").replace(" ", "").split(","):
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"scale size must be an integer, got {part!r}")
        if n <= 0:
            raise argparse.ArgumentTypeError("scale sizes must be > 0")
        out.append(n)
    return sorted(set(out))


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Measure Simpl'd local service and LAN inventory latency.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--mode", choices=("local", "lan", "both"), default="local",
                    help="what to measure (default: local)")
    ap.add_argument("--iterations", type=int, default=50,
                    help="timed calls per operation (default: 50)")
    ap.add_argument("--warmup", type=int, default=2,
                    help="read warmup calls excluded from samples (default: 2)")
    ap.add_argument("--workers", type=int, default=4,
                    help="concurrent clients for the write cycle (default: 4)")
    ap.add_argument("--cycles", type=int, default=5,
                    help="cycles per concurrent client (default: 5)")
    ap.add_argument("--scale-sizes", default="500,2000",
                    help="comma-separated local scale sizes in boxes "
                         "(default: 500,2000)")
    ap.add_argument("--scale-iterations", type=int, default=5,
                    help="read repetitions per scale size (default: 5)")
    ap.add_argument("--no-scale", action="store_true",
                    help="skip the local scale sweep")
    ap.add_argument("--sla-ms", type=float, default=200.0,
                    help="p95 comfort-limit threshold in ms (default: 200)")
    ap.add_argument("--quick", action="store_true",
                    help="small run for a smoke check (10 iterations, 2x2 "
                         "concurrency, 200-box scale)")
    ap.add_argument("--host", default="",
                    help="LAN server host/IP (external mode)")
    ap.add_argument("--port", type=int, default=8443,
                    help="LAN server port (default: 8443; serve default 9443)")
    ap.add_argument("--ca", default="",
                    help="CA/certificate PEM copied from the server")
    ap.add_argument("--user", default=B.BENCH_USER,
                    help=f"LAN username (default: {B.BENCH_USER})")
    ap.add_argument("--password", default="",
                    help="LAN password (prompted if omitted for external mode)")
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="LAN request timeout seconds (default: 10)")
    ap.add_argument("--local-server", action="store_true",
                    help="start a temporary localhost HTTPS server (safe writes)")
    ap.add_argument("--allow-writes", action="store_true",
                    help="allow benchmark writes on an EXTERNAL LAN server; "
                         "use only on a temp/benchmark server")
    ap.add_argument("--json", default="",
                    help="write the structured report to this path")
    ap.add_argument("--markdown", default="",
                    help="write the Markdown report to this path")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress progress messages on stderr")
    ap.add_argument("--debug", action="store_true",
                    help="show a traceback on failure")
    ap.add_argument("--serve", action="store_true",
                    help="run a temporary benchmark LAN server until Ctrl+C")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="serve bind address (default: 127.0.0.1; use "
                         "0.0.0.0 for LAN clients)")
    ap.add_argument("--cert-host", default="127.0.0.1",
                    help="host/IP clients use; goes into the cert SAN "
                         "(default: 127.0.0.1)")
    ap.add_argument("--seed-boxes", type=int, default=0,
                    help="serve mode: pre-seed this many stock boxes")
    ap.add_argument("--seed-orders", type=int, default=20,
                    help="serve mode: pre-seed this many empty orders")
    ap.add_argument("--cert", default="",
                    help="serve mode: existing certificate PEM (skip openssl)")
    ap.add_argument("--key", default="",
                    help="serve mode: existing private key PEM")
    return ap


def _validate(ns: argparse.Namespace, ap: argparse.ArgumentParser) -> None:
    if ns.serve:
        if (ns.cert and not ns.key) or (ns.key and not ns.cert):
            ap.error("--cert and --key must be provided together")
        if not (0 <= ns.port <= 65535):
            ap.error("--port must be 0-65535")
        return
    if ns.iterations < 1:
        ap.error("--iterations must be >= 1")
    if ns.warmup < 0:
        ap.error("--warmup must be >= 0")
    if not (1 <= ns.workers <= 64):
        ap.error("--workers must be 1-64")
    if not (1 <= ns.cycles <= 1000):
        ap.error("--cycles must be 1-1000")
    if ns.scale_iterations < 1:
        ap.error("--scale-iterations must be >= 1")
    if ns.sla_ms <= 0:
        ap.error("--sla-ms must be > 0")
    if not (1 <= ns.port <= 65535):
        ap.error("--port must be 1-65535")
    if ns.mode in ("lan", "both"):
        if ns.local_server and ns.host:
            ap.error("use either --local-server or --host, not both")
        if not ns.local_server and not ns.host:
            ap.error("LAN mode needs --host (or --local-server)")
        if not ns.local_server and not ns.ca:
            ap.error("LAN mode needs --ca (server certificate/CA PEM)")
    elif ns.local_server or ns.allow_writes:
        ap.error("--local-server/--allow-writes are only for LAN mode")


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    ns = ap.parse_args(argv)
    _validate(ns, ap)

    if ns.serve:
        try:
            return B.serve_benchmark(
                bind=ns.bind, port=ns.port, cert_host=ns.cert_host,
                seed_boxes=max(0, ns.seed_boxes),
                cert=ns.cert or None, key=ns.key or None,
                seed_orders=max(0, ns.seed_orders))
        except KeyboardInterrupt:
            return 130
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            print(f"error: {exc}", file=sys.stderr)
            if ns.debug:
                traceback.print_exc()
            return 2

    if ns.quick:
        iterations = 10
        warmup = 1
        workers = 2
        cycles = 2
        scale_sizes = [] if ns.no_scale else [200]
        scale_iterations = 3
    else:
        iterations = ns.iterations
        warmup = ns.warmup
        workers = ns.workers
        cycles = ns.cycles
        try:
            scale_sizes = [] if ns.no_scale else _parse_sizes(ns.scale_sizes)
        except argparse.ArgumentTypeError as exc:
            ap.error(str(exc))
        scale_iterations = ns.scale_iterations

    if ns.mode in ("lan", "both") and not ns.local_server and not ns.password:
        try:
            ns.password = getpass.getpass("LAN password: ")
        except (EOFError, KeyboardInterrupt):
            print("error: password required for external LAN mode", file=sys.stderr)
            return 2

    def progress(msg: str) -> None:
        if not ns.quiet:
            print(f"[bench] {msg}", file=sys.stderr, flush=True)

    config = {
        "mode": ns.mode,
        "iterations": iterations,
        "warmup": warmup,
        "workers": workers,
        "cycles": cycles,
        "scale_sizes": scale_sizes,
        "scale_iterations": scale_iterations,
        "sla_ms": ns.sla_ms,
        "local_server": bool(ns.local_server),
        "allow_writes": bool(ns.allow_writes),
        "host": ns.host,
        "port": ns.port,
        "ca": ns.ca,
        "user": ns.user,
        "password": ns.password,
        "timeout": ns.timeout,
    }

    try:
        report = B.run_benchmark(config, progress=progress)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        if ns.debug:
            traceback.print_exc()
        return 2

    markdown = B.render_markdown(report)
    print(markdown)
    try:
        if ns.markdown:
            path = Path(ns.markdown).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown + "\n", encoding="utf-8")
        if ns.json:
            path = Path(ns.json).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2, default=str) + "\n",
                            encoding="utf-8")
    except OSError as exc:
        print(f"error: could not write report: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
