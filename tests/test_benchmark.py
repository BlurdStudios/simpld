"""Benchmark harness: statistics, temp-DB path, concurrency and LAN transport."""
import math
import shutil

import pytest

from factory import benchmark as B


def test_percentile_and_summary():
    vals = [1.0, 2.0, 3.0, 4.0]
    assert B.percentile(vals, 0) == 1.0
    assert B.percentile(vals, 50) == 2.5
    assert math.isclose(B.percentile(vals, 95), 3.85)
    assert B.percentile([], 95) == 0.0

    s = B.summarize(vals, errors=1)
    assert s["n"] == 4
    assert s["p50_ms"] == 2.5
    assert s["errors"] == 1
    assert s["ops_per_s"] == 400.0

    empty = B.summarize([])
    assert empty["n"] == 0 and empty["p95_ms"] == 0.0


def test_local_path_and_concurrency_are_measured_safely():
    with B.temp_db() as (_d, path, password):
        backend = B.LocalBackend(path, B.BENCH_USER, password)
        try:
            result = B.run_path(backend, iterations=2, warmup=1)
        finally:
            backend.close()
        ops = {row["op"]: row for row in result["ops"]}
        assert {"create_reel", "pack_next_box", "scan_into_order",
                "order_progress", "ready_stock", "client_label_detail",
                "list_orders", "daily_summary"} <= set(ops)
        assert all(row["error_count"] == 0 for row in result["ops"])

        conc = B.run_concurrency(
            lambda: B.LocalBackend(path, B.BENCH_USER, password),
            workers=2, cycles=1)
        assert conc["completed_cycles"] == 2
        assert conc["error_count"] == 0
        assert conc["ops"]


def test_scale_sweep_measures_reads_and_limits():
    rows = B.run_scale([20], iterations=2)
    assert len(rows) == 1
    row = rows[0]
    assert row["boxes"] == 20
    assert row["reels"] == 80
    assert row["db_mb"] >= 0
    assert row["scan_avg_ms"] >= 0
    reads = {r["op"]: r for r in row["reads"]}
    assert "ready_stock" in reads and "order_progress" in reads
    assert reads["order_progress"]["stats"]["n"] == 2

    limits = B.measured_limits(rows, sla_ms=10_000)
    assert limits["ready_stock"].endswith("(largest tested)")


def test_markdown_report_contains_sections():
    report = {
        "environment": B.environment(),
        "config": {"iterations": 1, "warmup": 0, "workers": 1, "cycles": 1,
                   "scale_sizes": [], "scale_iterations": 1, "sla_ms": 200},
        "local": None,
        "lan": None,
        "comparison": [],
        "limits": {},
    }
    md = B.render_markdown(report)
    assert "# Simpl'd benchmark report" in md
    assert "## Environment" in md
    assert "How to read this" in md


@pytest.mark.skipif(shutil.which("openssl") is None,
                    reason="openssl CLI not available for temp LAN cert")
def test_lan_temp_server_path_and_concurrency():
    with B.temp_lan_server() as srv:
        backend = B.LanBackend(
            srv["host"], srv["port"], srv["ca"],
            B.BENCH_USER, srv["password"], timeout=10.0)
        try:
            assert backend.role == "admin"
            result = B.run_path(backend, iterations=2, warmup=1)
            assert all(row["error_count"] == 0 for row in result["ops"]), result
            assert any(row["op"] == "scan_into_order" for row in result["ops"])
            read_only = B.run_read_only(backend, iterations=1, warmup=0)
            assert read_only["role"] == "admin"
            assert any(row["op"] == "ready_stock"
                       for row in read_only["ops"])
            assert not read_only["skipped"]
            conc = B.run_concurrency(
                lambda: B.LanBackend(
                    srv["host"], srv["port"], srv["ca"],
                    B.BENCH_USER, srv["password"], timeout=10.0),
                workers=2, cycles=1)
            assert conc["completed_cycles"] == 2
            assert conc["error_count"] == 0
        finally:
            backend.close()
