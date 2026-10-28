"""Dispatch slip: frozen at completion, printed, reprintable.

Completing a dispatch freezes the whole order details (header + every box +
totals) in dispatch_slips in the SAME transaction as the status change, so
the saved record can never disagree with what went out. The slip prints
from that frozen record and reprints identically later. Temp DB only.
"""
import json
import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import auth as A
from factory import db as DB
from factory import services as S
from factory.db import init_db
from tests._auth_helper import ensure_default_users, reset_auth


def _db(tmp_path, name="factory.db"):
    p = str(tmp_path / name)
    DB.DB_PATH = p
    init_db(p)
    ensure_default_users(p)
    assert A.authenticate("admin", "admin123", p) is not None
    return p


def _bundle_order(p):
    """Job + 2 reels in one carton + scanned bundle order, ready to complete."""
    job = S.create_job("SLIP JOB", size_mm=160, core_wt=0.280, path=p)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    ct = S.pack_carton(job["id"], [r1["id"], r2["id"]], path=p)
    detail = S.carton_detail(ct["id"], path=p)
    order = S.create_dispatch_order("PARLE", job["id"], required_bundles=1,
                                    customer_po="PO-7", batch_no="B1", path=p)
    S.scan_into_order(order["id"], detail["qr"], path=p)
    return job, ct, order


def test_slip_frozen_at_completion(tmp_path):
    p = _db(tmp_path)
    try:
        _, ct, order = _bundle_order(p)
        done = S.complete_dispatch(order["id"], path=p)
        assert done["status"] == "complete"
        assert done.get("slip_id"), done.keys()
        slip = done["slip"]
        assert slip["order_no"] == order["order_no"]
        assert slip["customer"] == "PARLE"
        assert slip["customer_po"] == "PO-7"
        assert slip["batch_no"] == "B1"
        assert slip["loaded_boxes"] == 1
        assert len(slip["rows"]) == 1
        row = slip["rows"][0]
        assert row["box_no"] == 1
        assert ct["carton_no"] in row["carton_no"] or row["carton_no"] in ct["carton_no"], row
        assert row["num_reels"] == 2
        assert abs(row["net"] - ct["total_net"]) < 0.001, (row, ct)
        assert slip["loaded_net"] == pytest.approx(row["net"])
        assert slip["frozen"] is True

        # Frozen row in the DB: identical content on re-fetch.
        again = S.get_dispatch_slip(order["id"], path=p)
        assert again["frozen"] is True
        assert again["slip_id"] == done["slip_id"]
        assert again["rows"] == slip["rows"]
        assert again["loaded_net"] == slip["loaded_net"]

        raw = DB.get_conn(p).execute(
            "SELECT slip_json FROM dispatch_slips WHERE order_id=?",
            (order["id"],)).fetchone()["slip_json"]
        assert json.loads(raw)["rows"] == slip["rows"]
    finally:
        reset_auth()


def test_piece_order_slip_counts_wrappers(tmp_path):
    p = _db(tmp_path)
    try:
        prod = S.create_product("SLIP WRAP PRODUCT", 1000, path=p)
        job = S.create_job_for_product(prod["id"], core_wt=0.280, path=p)
        r1 = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
        r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
        ct = S.pack_carton(job["id"], [r1["id"], r2["id"]], path=p)
        detail = S.carton_detail(ct["id"], path=p)
        order = S.create_dispatch_order("PARLE", job["id"], required_pieces=1500,
                                        customer_po="PO-9", path=p)
        S.scan_into_order(order["id"], detail["qr"], path=p)
        done = S.complete_dispatch(order["id"], path=p)
        slip = done["slip"]
        assert slip["piece_unit"] == "wrapper"
        assert slip["loaded_pieces"] == 2000  # 2 coils x 1000 wrappers
        assert slip["rows"][0]["pieces"] == 2000
    finally:
        reset_auth()


def test_recomplete_blocked_and_slip_unchanged(tmp_path):
    p = _db(tmp_path)
    try:
        _, _, order = _bundle_order(p)
        first = S.complete_dispatch(order["id"], path=p)
        with pytest.raises(ValueError, match="only loading orders"):
            S.complete_dispatch(order["id"], path=p)
        again = S.get_dispatch_slip(order["id"], path=p)
        assert again["slip_id"] == first["slip_id"]
        assert again["rows"] == first["slip"]["rows"]
    finally:
        reset_auth()


def test_slip_live_fallback_for_pre_release_orders(tmp_path):
    """Orders completed before this release have no frozen row: the slip
    rebuilds live from the still-linked dispatch tables."""
    p = _db(tmp_path)
    try:
        _, _, order = _bundle_order(p)
        done = S.complete_dispatch(order["id"], path=p)
        c = DB.get_conn(p)
        try:
            c.execute("DELETE FROM dispatch_slips WHERE order_id=?", (order["id"],))
            c.commit()
        finally:
            c.close()
        slip = S.get_dispatch_slip(order["id"], path=p)
        assert slip["frozen"] is False
        assert slip["order_no"] == order["order_no"]
        assert len(slip["rows"]) == 1
        assert slip["rows"] == done["slip"]["rows"]
    finally:
        reset_auth()


def test_mark_printed_counts_only_after_print(tmp_path):
    p = _db(tmp_path)
    try:
        _, _, order = _bundle_order(p)
        with pytest.raises(ValueError, match="No saved dispatch slip"):
            S.mark_dispatch_slip_printed(order["id"], path=p)
        S.complete_dispatch(order["id"], path=p)
        slip = S.get_dispatch_slip(order["id"], path=p)
        assert slip["print_count"] == 0
        out = S.mark_dispatch_slip_printed(order["id"], path=p)
        assert out["print_count"] == 1
        out = S.mark_dispatch_slip_printed(order["id"], path=p)
        assert out["print_count"] == 2
        slip = S.get_dispatch_slip(order["id"], path=p)
        assert slip["print_count"] == 2
        assert slip["last_printed_at"], slip
    finally:
        reset_auth()


def test_mark_printed_needs_dispatcher_or_admin(tmp_path):
    from tests._auth_helper import login_as
    p = _db(tmp_path)
    try:
        _, _, order = _bundle_order(p)
        S.complete_dispatch(order["id"], path=p)
        login_as("packager", p)
        with pytest.raises(PermissionError):
            S.mark_dispatch_slip_printed(order["id"], path=p)
    finally:
        reset_auth()


def test_render_html_has_whole_order(tmp_path):
    from factory.dispatch_slip_render import render_html
    p = _db(tmp_path)
    try:
        _, _, order = _bundle_order(p)
        done = S.complete_dispatch(order["id"], path=p)
        html = render_html(done["slip"])
        assert order["order_no"] in html
        assert "PARLE" in html
        assert "PO-7" in html
        assert "frozen at completion" in html
        live = S.dispatch_slip_data(order["id"], path=p)
        assert "Live view" in render_html(live)
    finally:
        reset_auth()


def test_dialog_builds_offscreen_with_prefetched_slip(tmp_path):
    from PySide6.QtWidgets import QApplication
    from factory.ui.label_preview import DispatchSlipDialog
    from factory.ui.styles import APP_QSS
    p = _db(tmp_path)
    try:
        _, _, order = _bundle_order(p)
        done = S.complete_dispatch(order["id"], path=p)
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(APP_QSS)
        dlg = DispatchSlipDialog(None, order["id"], slip=done["slip"])
        assert dlg.windowTitle().endswith(order["order_no"])
        assert dlg.table.rowCount() == 1
        assert dlg.printer_combo is not None
        # Teardown hygiene: a live top-level dialog at interpreter shutdown
        # aborts native Windows Qt (green summary, then Fatal Python error).
        dlg.close()
        dlg.deleteLater()
        app.processEvents()
    finally:
        reset_auth()


def test_migration_creates_slips_table_on_old_db(tmp_path):
    """A DB created without dispatch_slips gains it via init_db."""
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.executescript(DB.SCHEMA.replace(
        "CREATE TABLE IF NOT EXISTS dispatch_slips", "CREATE TABLE IF NOT EXISTS _no_slips"))
    c.commit()
    c.close()
    tables = {r[0] for r in sqlite3.connect(p).execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "dispatch_slips" not in tables
    DB.DB_PATH = p
    init_db(p)
    tables = {r[0] for r in sqlite3.connect(p).execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "dispatch_slips" in tables
    reset_auth()


def test_cancelled_orders_hidden_in_dropdown(tmp_path):
    from PySide6.QtWidgets import QApplication
    from factory.ui.page_dispatch import DispatchPage
    from factory.ui.styles import APP_QSS
    p = _db(tmp_path)
    try:
        job = S.create_job("DROPDOWN JOB", size_mm=160, core_wt=0.280, path=p)
        keep = S.create_dispatch_order("KEEP CO", job["id"], required_bundles=1,
                                       path=p)
        drop = S.create_dispatch_order("DROP CO", job["id"], required_bundles=1,
                                       path=p)
        S.cancel_order(drop["id"], path=p)
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(APP_QSS)
        pg = DispatchPage()
        try:
            pg.refresh()
            labels = [pg.order_combo.itemText(i)
                      for i in range(pg.order_combo.count())]
            assert any(keep["order_no"] in t for t in labels), labels
            assert not any(drop["order_no"] in t for t in labels), labels
        finally:
            # Teardown hygiene: destroy the page while QApplication lives —
            # live widgets at shutdown abort native Windows Qt.
            pg.close()
            pg.deleteLater()
            app.processEvents()
    finally:
        reset_auth()
