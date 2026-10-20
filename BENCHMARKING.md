# Benchmarking — local logic and LAN inventory latency

Simpl'd is offline-first and single-writer by design: SQLite on the packager
PC, with the dispatch PC calling the real service over authenticated HTTPS.
`tools/benchmark.py` measures that path instead of guessing:

- **Local service latency** — create a reel, pack a box, create/scan an order,
  read progress/stock/history, all in-process against a throwaway DB.
- **LAN inventory latency** — the same operations through
  `factory/lan_client.py`, including the current client's fresh TLS
  connection per request.
- **Concurrency** — several independent clients doing
  `weigh → pack → order → scan → complete` cycles at once, with throughput,
  p50/p95/p99 and lock/permission errors.
- **Local scale** — seed 500, 2,000 (or more) boxes with the real services,
  then measure reads and the growing cost of scanning into one order.
- **LAN payload sizes** — approximate JSON request/response bytes per call.

It is a developer/owner tool, not a floor page. It does not print, read the
scale, or repaint the UI.

## Safety first

- **Local runs** create a fresh temporary DB. They never touch
  `data/factory.db`.
- **`--local-server`** starts a temporary HTTPS server with a fresh DB and a
  random admin password. Writes are safe here.
- **A remote server is read-only by default.** Without `--allow-writes` the
  LAN run sends only reads (`whoami`, jobs, stock, orders, history, summary).
- `--allow-writes` against an external server writes benchmark products, jobs,
  boxes and orders. Use it **only** with the temporary `--serve` server, never
  on the production packager DB.
- The temporary server's DB and certificate are deleted on Ctrl+C. The
  private key never leaves the packager PC; only the public certificate is
  copied to the dispatcher PC.
- Run load tests off-shift; even a read-only benchmark adds requests to the
  packager's SQLite file.

## Quick start

```bash
# Local services + 2-client concurrency + 200-box scale (safe, ~2 s)
python3 tools/benchmark.py --quick

# Loopback LAN baseline (temp HTTPS server; includes TLS overhead)
python3 tools/benchmark.py --mode lan --local-server --quick

# Local + loopback LAN in one report with a LAN-minus-local table
python3 tools/benchmark.py --mode both --local-server --quick
```

On Windows use `python` or `py -3` in place of `python3`. The script uses the
same `.venv`/system Python as the app; no extra package is needed.

A full default local run (50 iterations, 4×5 concurrency, 500/2,000-box scale)
is roughly 30–60 s on the dev host. The scale sweep is the slow part; skip it
with `--no-scale` when you only care about per-operation latency.

## Real LAN latency — the important measurement

The loopback run proves the code path, not your Wi-Fi/Ethernet. To measure the
actual packager → dispatch link, run a **temporary server on the packager** and
the client on the dispatcher. This is safe because the server uses a throwaway
DB, not production.

### 1. On the packager PC

```bash
python3 tools/benchmark.py --serve \
  --bind 0.0.0.0 \
  --cert-host 192.168.1.10 \
  --port 9443 \
  --seed-boxes 500
```

Replace `192.168.1.10` with the packager's **stable LAN IP**. The certificate
is generated with that IP in its SAN. The command prints:

- the CA certificate path (copy this file to the dispatcher),
- a random `bench-admin` password,
- the exact client commands.

If `openssl` is not installed on Windows, generate a cert per
[`LAN_SETUP.md`](LAN_SETUP.md) and pass `--cert lan-cert.pem --key lan-key.pem`;
`--cert-host` must still match the address the dispatcher uses.

Allow Python/port 9443 through the Windows firewall. The temp server prints
its activity and stays in the foreground; Ctrl+C stops it and deletes the temp
DB.

### 2. On the dispatcher PC

Copy the printed CA file (for example `bench-cert.pem`) to the dispatcher, then:

```bash
python3 tools/benchmark.py --mode lan \
  --host 192.168.1.10 --port 9443 \
  --ca ./bench-cert.pem \
  --user bench-admin --password 'the-printed-password' \
  --allow-writes --iterations 50
```

This measures **real write latency** (reel, box, order, scan, progress) plus
4 concurrent clients by default. Save a report with:

```bash
python3 tools/benchmark.py ... --markdown lan-bench.md --json lan-bench.json
```

The report's `LAN overhead vs local` table is the network/TLS cost: run
`--mode both` (local first, then the LAN server) if you want both sections in
one file.

### 3. Production server — reads only

To check the live packager without writing anything:

```bash
python3 tools/benchmark.py --mode lan \
  --host 192.168.1.10 --port 8443 \
  --ca ./lan-cert.pem \
  --user dispatcher --password '...' \
  --iterations 30
```

Use a real production login. The client skips operations the role cannot read
(for example `daily_summary` for a non-admin), and never writes. Reads still
add load, so run this off-shift. For write latency on the same network, use
the temporary server above — it exercises the same HTTPS server and SQLite
stack with a safe DB.

## Reading the numbers

- **p50** — typical; **p95** — the slow one in twenty; **p99** — the tail.
  A scan with a good median but occasional multi-second p99 is a floor
  problem.
- **Local vs LAN** — local is the service/SQLite cost. The difference is
  transport. `--mode both` prints it directly.
- **Fresh TLS per call** — the current LAN client opens a new HTTPS
  connection for every request. The benchmark includes the handshake, because
  that is what the app actually does. The loopback delta is a few milliseconds;
  a busy Wi-Fi link can add much more. This is a baseline for a future
  keep-alive optimization, not a measurement of one.
- **Concurrency** — local SQLite allows one writer. More clients do not
  necessarily increase write throughput; `cycles/s` may flatten and
  `database is locked` errors may appear. That is the local logic limit.
  Run `--workers 1`, then `--workers 2`, `4`, `8` and compare. LAN throughput
  also includes the server's per-request TLS and SHA-256/PBKDF2 auth work
  (login is once per client; requests reuse the session token).
- **Scale** — seeding uses the real services, including audit rows. Scan time
  grows with the order because each scan reads the already-loaded items;
  `order_progress` and `history` return larger payloads. The **comfort limit**
  is the largest tested size whose p95 is under `--sla-ms` (default 200 ms).
  It is not a hard maximum; run larger sizes to find the cliff.

## Example baseline (dev host, loopback — not your factory LAN)

Measured on the development host: Fedora 44, 16 CPUs, Python 3.14,
SQLite 3.51, loopback HTTPS, 20 iterations, 4 clients x 5 cycles. These
numbers are only a reference; run the same commands on the packager and
dispatcher PCs.

| Operation | Local p50 | Loopback LAN p50 | Loopback delta |
|---|---:|---:|---:|
| create reel | 0.86 ms | 5.17 ms | 4.30 ms |
| pack box | 1.09 ms | 5.51 ms | 4.42 ms |
| create order | 0.85 ms | 4.90 ms | 4.05 ms |
| scan box into order | 0.87 ms | 5.21 ms | 4.34 ms |
| order progress (20 boxes) | 0.47 ms | 3.77 ms | 3.30 ms |
| ready stock (20 boxes) | 0.59 ms | 4.26 ms | 3.66 ms |

Concurrency on the same run (four independent clients, five complete
`reel -> box -> order -> scan -> complete` cycles each):

| Path | Cycles/s | Calls/s | Errors |
|---|---:|---:|---:|
| local services | 168.77 | 1,181.41 | 0 |
| loopback HTTPS | 72.32 | 506.24 | 0 |

The loopback penalty is almost entirely per-call TLS setup: the current client
does not keep the connection alive. A real Ethernet/Wi-Fi link adds network
round-trip time on top of that. This is the baseline to improve.

Scale on the same host (real service writes, 5 read repetitions):

| Boxes | Coils | DB MB | Seed s | Scan avg ms | stock p95 | progress p95 | history p95 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 500 | 2,000 | 1.20 | 3.4 | 1.48 | 6.75 ms | 4.92 ms | 14.03 ms |
| 2,000 | 8,000 | 4.34 | 21.5 | 3.31 | 25.98 ms | 14.48 ms | 59.50 ms |

The scale run is dominated by real writes and audits; the scan average rises
as the one order grows. At 2,000 boxes all measured reads were still under the
200 ms comfort SLA on this host; larger sizes (`--scale-sizes 5000,10000`) are
where to look for the local cliff. Those runs take minutes and create many
temporary rows — use a spare machine or run once off-shift.

## What the benchmark does not cover

- Qt widget paint/layout time, the scale indicator, scanner wedges, or printer
  drivers. Those are UI/hardware paths, not the data service.
- SQLite on a network share, antivirus scanning the DB, or a failing disk.
  Those show up as much worse local numbers; run the local benchmark on the
  actual packager PC.
- Multi-user LAN login cost beyond one login per concurrent client.
- Real production data volume unless you seed a comparable temp DB with
  `--serve --seed-boxes` / `--scale-sizes`.

## Troubleshooting

| Symptom | Check |
|---|---|
| `openssl CLI not found` | Install OpenSSL or pass `--cert`/`--key` from `LAN_SETUP.md`. |
| TLS certificate error on the dispatcher | The cert SAN must contain the host/IP used in `--host`; use `--cert-host` on `--serve`. Copy the **cert**, not the key. |
| `Cannot reach LAN server` | Firewall, port, packager IP, and that `--serve` is still running. |
| `LanPermissionError` / skipped rows | External read-only mode uses the supplied login's role. Use `dispatcher`/`admin` for history/orders; `daily_summary` is admin-only. |
| Writes rejected with role error | External write mode needs an admin. Use the temporary server's `bench-admin`, not a production packager login. |
| `database is locked` in local concurrency | Expected at the SQLite writer limit; reduce `--workers` or use the measured throughput as the limit. |
| Scale run takes too long | `--no-scale`, or use smaller `--scale-sizes` / `--scale-iterations`. |

## Files

- `factory/benchmark.py` — measurement library, temp DB/TLS server, scenario
  code.
- `tools/benchmark.py` — CLI, report writing, safety checks.
- `tests/test_benchmark.py` — statistics, local path/concurrency, scale, LAN
  loopback test.

The release build does not ship the benchmark as a UI feature; it is a
repository tool. The service and LAN code it measures are the same code the
installed app runs.
