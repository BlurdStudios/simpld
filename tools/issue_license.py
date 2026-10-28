#!/usr/bin/env python3
"""Offline vendor tool: issue one `license.lic` per site.

Private key NEVER lives in the repo. Provide it via:
    SIMPLD_LICENSE_PRIVKEY=<base64 32-byte seed or 64-byte raw>
or --privkey-file <path> (raw bytes or base64 text).

Usage:
    python3 tools/issue_license.py --gen-key
        # prints new private seed + public key (run once, store offline)
    python3 tools/issue_license.py --show-pubkey --privkey-file vendor.key
    python3 tools/issue_license.py --customer "Vasavi" --site-id vasavi-f1 \\
        --edition pro --packager-fp <board-hash> --max-stations 2 \\
        --issued 2026-10-26 --expires 2027-10-26 --max-version 2.x \\
        --privkey-file vendor.key --out license.lic

Logs serial/fp/expiry to stdout as the vendor registry line (paste into the
50-site support sheet). Public key goes into factory/license.py PUBLIC_KEY_B64.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_privkey(args) -> bytes:
    raw = ""
    if args.privkey_file:
        with open(args.privkey_file, "r", errors="replace") as f:
            raw = f.read().strip()
    else:
        raw = (os.environ.get("SIMPLD_LICENSE_PRIVKEY") or "").strip()
    if not raw:
        raise SystemExit("no private key: set SIMPLD_LICENSE_PRIVKEY or --privkey-file")
    try:
        seed = base64.b64decode(raw)
    except Exception:
        raise SystemExit("private key is not valid base64")
    if len(seed) == 64:
        seed = seed[:32]
    if len(seed) != 32:
        raise SystemExit(f"private key must decode to 32 bytes (got {len(seed)})")
    return seed


def cmd_gen_key(_args) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes_raw() if hasattr(priv, "private_bytes_raw") else None
    if seed is None:  # older cryptography: encode via _raw fallback
        from cryptography.hazmat.primitives import serialization
        seed = priv.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
    pub = priv.public_key().public_bytes_raw() if hasattr(
        priv.public_key(), "public_bytes_raw") else priv.public_key().public_bytes(
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.Raw,
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"]).PrivateFormat.Raw,
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"]).NoEncryption())
    print(f"PRIVKEY (store offline, never commit): {base64.b64encode(seed).decode()}")
    print(f"PUBKEY  (paste into factory/license.py): {base64.b64encode(pub).decode()}")
    return 0


def cmd_show_pubkey(args) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    seed = _load_privkey(args)
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    try:
        pub = priv.public_key().public_bytes_raw()
    except AttributeError:
        from cryptography.hazmat.primitives import serialization
        pub = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
    print(base64.b64encode(pub).decode())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Issue Simpl'd site licences (offline)")
    ap.add_argument("--gen-key", action="store_true")
    ap.add_argument("--show-pubkey", action="store_true")
    ap.add_argument("--privkey-file", default="")
    ap.add_argument("--customer", default="")
    ap.add_argument("--site-id", default="")
    ap.add_argument("--serial", default="")
    ap.add_argument("--edition", default="pro",
                    choices=["simple", "pro", "premium", "trial"])
    ap.add_argument("--trial-days", type=int, default=0,
                    help="Trial length in days (sets expires=issued+N; implies --edition trial unless edition given)")
    ap.add_argument("--packager-fp", default="")
    ap.add_argument("--max-stations", type=int, default=2)
    ap.add_argument("--issued", default="")
    ap.add_argument("--expires", default="")
    ap.add_argument("--max-version", default="2.x")
    ap.add_argument("--out", default="license.lic")
    args = ap.parse_args(argv)

    if args.gen_key:
        return cmd_gen_key(args)
    if args.show_pubkey:
        return cmd_show_pubkey(args)

    for req in ("customer", "site_id", "packager_fp", "issued"):
        if not getattr(args, req):
            raise SystemExit(f"--{req.replace('_','-')} required")
    # Trial: --trial-days N sets expires=issued+N (edition defaults to trial).
    if getattr(args, "trial_days", 0):
        try:
            td = int(args.trial_days)
        except (TypeError, ValueError):
            raise SystemExit("--trial-days must be a positive whole number")
        if td <= 0 or td > 365:
            raise SystemExit("--trial-days must be 1..365")
        if args.edition not in ("trial", "pro"):
            print("warning: --trial-days implies trial timing; keeping edition"
                  f" '{args.edition}'", file=sys.stderr)
        if args.edition == "pro" and getattr(args, "trial_days", 0):
            # Allow pro-timed trials, but default to trial edition when the
            # caller did not pick an edition explicitly.
            pass
        try:
            base = datetime.strptime(args.issued[:10], "%Y-%m-%d")
        except ValueError:
            raise SystemExit("issued must be YYYY-MM-DD")
        from datetime import timedelta as _td
        args.expires = (base + _td(days=td)).strftime("%Y-%m-%d")
        if args.edition == "pro":
            # Caller asked for N-day timing on pro: keep pro (vendor choice).
            pass
        else:
            args.edition = "trial"
    if not getattr(args, "expires", ""):
        raise SystemExit("--expires required (or --trial-days N)")
    try:
        datetime.strptime(args.issued[:10], "%Y-%m-%d")
        datetime.strptime(args.expires[:10], "%Y-%m-%d")
    except ValueError:
        raise SystemExit("issued/expires must be YYYY-MM-DD")

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    seed = _load_privkey(args)
    priv = Ed25519PrivateKey.from_private_bytes(seed)

    from factory.license import EDITION_DEFAULT_FEATURES
    serial = args.serial or f"SIMPLD-{datetime.now().strftime('%Y%m%d')}-001"
    body = {
        "serial": serial,
        "customer": args.customer,
        "site_id": args.site_id,
        "edition": args.edition,
        "packager_fp": args.packager_fp,
        "max_stations": int(args.max_stations),
        "features": EDITION_DEFAULT_FEATURES.get(args.edition, []),
        "issued": args.issued[:10],
        "expires": args.expires[:10],
        "max_version": args.max_version,
    }
    if args.edition == "simple":
        body["features"] = []
        if int(args.max_stations) > 1:
            print("warning: simple edition forced to max_stations=1",
                  file=sys.stderr)
            body["max_stations"] = 1
    if args.edition == "trial":
        if int(args.max_stations) != 1:
            print("warning: trial edition forced to max_stations=1",
                  file=sys.stderr)
            body["max_stations"] = 1
    msg = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    sig = priv.sign(msg)
    body["sig"] = base64.b64encode(sig).decode()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {args.out}")
    print(f"REGISTRY {serial} | {args.site_id} | {args.customer} | "
          f"{args.edition} | fp={args.packager_fp} | exp={body['expires']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
