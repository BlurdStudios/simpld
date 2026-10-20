# LAN Setup — packager computer hosts the shared DB

Topology (confirmed): the **packager computer** and the **dispatcher computer**
are on the same LAN. SQLite stays a **single local file on the packager
computer**. The packager runs `factory/lan_server.py` (one authenticated
HTTPS data service); both computers run the Qt app, and the dispatcher-side
app talks to the service via `factory/lan_client.py`. There is no
shared-file SQLite access, no DB copy/sync, and no offline local-write mode.

## 1. Packager computer (server host)

1. Run the main app once. It creates the local DB (`init_db`) and the
   first-run admin account. The LAN service will **refuse to start** against
   a missing DB or a DB with zero users — user setup stays in the main app.
2. Note the packager's LAN IP (must be stable; prefer a static DHCP lease):
   `ip addr` (Linux) / `ipconfig` (Windows). Example: `192.168.1.10`.
3. Generate a certificate covering that IP (self-signed LAN cert is fine):
   ```
   openssl req -x509 -newkey rsa:2048 -keyout lan-key.pem -out lan-cert.pem \
     -days 825 -nodes -subj "/CN=simpld-packager" \
     -addext "subjectAltName=IP:192.168.1.10,DNS:packager-lan"
   ```
   Keep `lan-key.pem` on the packager only. Copy `lan-cert.pem` (public part)
   to each client computer as its trust anchor (`ca_cert`).
4. Open the port in the host firewall (example 8443):
   `sudo ufw allow 8443/tcp` (or the Windows Defender Firewall equivalent).
5. Start the service (all flags required; TLS is mandatory, no plaintext):
   ```
   python -m factory.lan_server --bind 192.168.1.10 --port 8443 \
     --db /path/to/factory.db --cert /path/to/lan-cert.pem --key /path/to/lan-key.pem
   ```
   `--bind 0.0.0.0` listens on every interface; prefer the explicit LAN IP.
   No server is ever started automatically by app code.

## 2. Client computer(s)

- Install the same app code, plus the packager's `lan-cert.pem`.
- The app picks its backend at startup from the environment, BEFORE any
  local database is touched:
  - `SIMPLD_SERVER_HOST` unset -> local mode (packager computer itself:
    its own SQLite file, first-run admin setup in the login dialog).
  - `SIMPLD_SERVER_HOST` set -> LAN client mode (dispatcher computer):
    no local database is created, no local users are seeded, and the
    login dialog authenticates against the packager over HTTPS. First-run
    admin setup is refused in this mode — create logins on the packager
    computer itself (user management is local-admin only, never over LAN).
  ```
  # dispatcher computer (bash):
  export SIMPLD_SERVER_HOST=192.168.1.10
  export SIMPLD_SERVER_PORT=8443            # optional, default 8443
  export SIMPLD_SERVER_CA_CERT=/path/to/lan-cert.pem   # required: packager cert
  export SIMPLD_SERVER_TIMEOUT=10           # optional, seconds, default 10
  python3 run.py
  ```
  (Windows: set the same four values as user environment variables, then
  start the app. The host/IP used must match a cert SAN entry from step 3.)
- Or wire the same values in code before startup:
  ```python
  from factory import data_access as DA
  DA.configure_lan(host="192.168.1.10", port=8443, ca_cert="lan-cert.pem")
  who = DA.authenticate("dispatcher", "dispatch123")  # real server-DB auth
  ```
- In the running app, LAN mode shows "LAN: <host> (shared DB)" in the
  sidebar, auto-refreshes Ready Stock / Jobs / Dispatch every 15 seconds
  (dispatcher sees packager changes without restarting), and surfaces
  failures explicitly:
  - `Connection issue` (unreachable/TLS/timeout): actions are disabled,
    nothing is written anywhere — reconnect, press Refresh, confirm the
    record is (not) there, then retry. Writes are attempted exactly once;
    an unknown-outcome write is never auto-retried (retrying blindly can
    create a duplicate reel/box/order — always refresh first).
  - `Session expired` (401): sign out and sign in again; stale tokens are
    never silently re-used.
- Hostname/IP verification is always on. Login failures raise `LanAuthError`;
  unreachable server raises `LanConnectionError` — the app must show an
  error, never write to a local DB copy.

## 3. Security properties

- TLS everywhere; the server has no plaintext mode. Plain HTTP gets no API.
- Passwords travel only inside the TLS login call and are never logged.
- Sessions are opaque server-side tokens (`X-Session-Token` header), bound to
  the server DB file + user id + credential hash, expiring after one shift.
  Logout invalidates immediately; demote/delete/password-change kills stale
  tokens on next use.
- The operation allowlist is fixed (`login/logout/whoami`, product/job/reel/
  carton/stock/dispatch/history/summary/slip). No SQL, file paths, periods,
  actor dicts, or roles are accepted from clients — the server re-resolves
  the actor from its DB and enforces per-op permissions on every call.
- Login is rate-limited per source IP (10 failures / 5 min -> HTTP 429).
- Requests are size-bounded (2 MB, to allow base64 logo uploads); errors are
  generic (401/403/409/413/422/429/500) and never leak tokens, paths, or
  tracebacks.
- Not exposed: user management (do it in the main app on the packager),
  files/backups, hardware/scale/printer access (each machine's own).

## 4. Write-safety rule (uncertain outcomes)

The client never auto-retries writes, and `request_id` is a tracing label
only — the server does **not** deduplicate it. If a write raises
`LanConnectionError`/timeout, its outcome is **UNKNOWN** (it may have
committed). The caller must refresh via a read (`ready_stock`, `list_jobs`,
`order_progress`, …) and only then decide whether a retry with a **new**
`request_id` is safe. Retrying blindly can create a duplicate.

## 5. Measuring LAN latency

`tools/benchmark.py` can measure the real packager → dispatcher HTTPS round
trip without touching production data: run `--serve` on the packager (temp DB
and cert), then run the printed read/write client command on dispatch. A
production server can be measured read-only with a normal dispatcher login.
See **BENCHMARKING.md** for safety, commands and interpretation.

## 6. Troubleshooting

- `LanConnectionError`: server process down, wrong host/port, firewall, or
  the client clock/CA mismatch (TLS). Check `GET https://host:port/health`.
- `LanAuthError` (401): bad credentials or stale token — log in again.
- `LanPermissionError` (403): that role may not do this op (e.g. dispatcher
  creating reels, packager touching dispatch orders).
- `certificate verify failed`: the `ca_cert` is not the packager's cert, or
  the host/IP used is missing from the cert SAN — regenerate with the right
  `-addext` and reconnect using that exact host/IP.
