"""Requested floor-UI behaviors (Sol6.1 planning ONLY scope).

Native Qt offscreen against a temporary SIMPLD_DB — never production
data/factory.db. Fake printers/scales live STRICTLY in these tests; the
production path blocks blank hardware via DEV.require_scale/require_printer
and never fabricates records. Auth uses real temp-DB users (no forged dicts).

Physical Essae scale + Citizen printer NOT verified here (no hardware on
this machine) — print paths use test fakes and are labelled as such.

Covers: products reuse via dropdown (no Size/Core on New Job), product
validation retains input, nested nav Reel/Box packing, 60/40 left split,
box packing consumes loose reels and queues stickers, ALL-reels history
with exact-row select + real
Label printed state (printer-accepted success only), fresh-reweigh keeps
number/time and clears printed, admin-only safe delete, real RBAC on
direct invoke + UI, stale-read reset, no sample startup.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._auth_helper import ensure_default_users

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SIMPLD_ALLOW_MOCK", "1")


def _fresh_db():
    from factory import db as DB
    from factory import auth as A
    from factory import data_access as DA
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-req-ui-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    A._SESSIONS.clear()
    A.set_session(None)
    ensure_default_users(tmp)
    try:
        DA.configure_local()
    except Exception:
        pass
    return tmp


def _login(role):
    from factory import auth as A
    creds = {"admin": ("admin", "admin123"), "packager": ("packager", "pack123"),
             "dispatcher": ("dispatcher", "dispatch123")}
    u, pw = creds[role]
    out = A.authenticate(u, pw)
    assert out is not None and out["role"] == role
    return out


def _app():
    from PySide6.QtWidgets import QApplication
    from factory.ui.styles import APP_QSS
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    return app


def _page():
    from factory.ui.page_packaging import PackagingPage
    _app()
    return PackagingPage()


def _select_job(pg, job_id):
    pg.refresh_jobs()
    for i in range(pg.job_combo.count()):
        if pg.job_combo.itemData(i) == job_id:
            pg.job_combo.setCurrentIndex(i)
            return
    raise AssertionError("job not in combo")


def _has_px(label) -> bool:
    p = label.pixmap()
    return p is not None and not p.isNull()


# ---- 1. products reuse: dropdown, no Size/Core on New Job ----

def test_new_job_dropdown_reuses_products_no_size_core():
    """New Job offers the products master in a dropdown; no Size/Core fields."""
    from unittest import mock as _mock
    from PySide6.QtWidgets import QDialog, QComboBox, QPushButton, QLabel
    from factory import services as S
    from factory.ui.page_packaging import NewJobDialogMixin
    p = _fresh_db()
    _login("admin")
    _app()
    prod = S.create_product("REQ UI WIDGET-160 BODY", 16500, path=p)
    prod2 = S.create_product("REQ UI SECOND", 12000, path=p)
    # reuse: two jobs from one product share product_id, distinct job_no
    j1 = S.create_job_for_product(prod["id"], path=p)
    j2 = S.create_job_for_product(prod["id"], path=p)
    assert j1["product_id"] == j2["product_id"] == prod["id"]
    assert j1["job_no"] != j2["job_no"]
    assert j1["wrappers_per_reel"] == 16500
    # dialog structure: capture the live QDialog instead of mirroring source
    from factory.ui.page_packaging import PackagingPage
    pg = PackagingPage()
    captured = {}

    orig_exec = QDialog.exec

    def fake_exec(self):
        captured["dlg"] = self
        return QDialog.Rejected

    with _mock.patch.object(QDialog, "exec", fake_exec):
        assert NewJobDialogMixin.ask_new_job(pg) is None
    dlg = captured["dlg"]
    texts = [w.text() for w in dlg.findChildren(QLabel)] + \
            [w.text() for w in dlg.findChildren(QPushButton)] + \
            [w.placeholderText() for w in dlg.findChildren(QComboBox)]
    joined = " ".join(texts)
    assert "Size" not in joined and "Core" not in joined, joined
    combos = dlg.findChildren(QComboBox)
    assert len(combos) >= 1, "product dropdown missing"
    btns = [b.text() for b in dlg.findChildren(QPushButton)]
    assert any("New product" in b for b in btns), btns
    assert any("pieces" in b.casefold() for b in btns), btns
    # dropdown lists both products with wrappers counts (user-visible reuse)
    items = [combos[0].itemText(i) for i in range(combos[0].count())]
    assert any("REQ UI WIDGET-160 BODY" in t and "16500" in t for t in items), items
    assert any("REQ UI SECOND" in t and "12000" in t for t in items), items
    pg.close()


def test_new_product_validation_retains_typed_input():
    """Bad wrappers keep the typed name/wrappers for the next attempt."""
    from unittest import mock as _mock
    from PySide6.QtWidgets import QDialog, QLineEdit
    from factory.ui.page_packaging import NewJobDialogMixin
    _fresh_db()
    _login("admin")
    _app()
    from factory.ui.page_packaging import PackagingPage
    pg = PackagingPage()
    seen = []
    calls = {"n": 0}

    real_create = None
    import factory.services as S
    real_create = S.create_product

    def fake_create(name, wrappers, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500)")
        return real_create(name, wrappers, *a, **k)

    def fake_exec(self):
        edits = self.findChildren(QLineEdit)
        assert len(edits) >= 2
        if len(seen) == 0:
            edits[0].setText("RETAINED PROD")
            edits[1].setText("notanumber")
            seen.append((edits[0].text(), edits[1].text()))
        else:
            # second dialog must open retaining the failed attempt verbatim;
            # then the user corrects wrappers before saving.
            seen.append((edits[0].text(), edits[1].text()))
            edits[1].setText("16500")
        return QDialog.Accepted

    with _mock.patch.object(QDialog, "exec", fake_exec), \
         _mock.patch("factory.ui.page_packaging.DA.create_product", side_effect=fake_create), \
         _mock.patch("factory.ui.page_packaging.QMessageBox.warning"):
        out = NewJobDialogMixin.ask_new_product(pg, initial_name="", initial_wrappers="")
    assert out is not None and out["name"] == "RETAINED PROD"
    assert len(seen) == 2, seen
    assert seen[1] == ("RETAINED PROD", "notanumber"), \
        f"second attempt must retain first attempt input, got {seen[1]!r}"
    pg.close()


# ---- 2. preview split + live type captions ----

def test_preview_split_and_live_type_captions():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSplitter
    from factory import services as S
    p = _fresh_db()
    _login("admin")
    app, pg = _app(), _page()
    job = S.create_job_for_product(
        S.create_product("PREVIEW PROD", 16500, path=p)["id"], path=p)
    _select_job(pg, job["id"])
    assert isinstance(pg._left_split, QSplitter)
    assert pg._left_split.orientation() == Qt.Vertical
    assert tuple(pg._left_split.widget(i).sizePolicy().verticalStretch()
                 for i in range(2)) == (3, 2)
    assert pg._left_split.indexOf(pg._preview_card) == 1
    assert pg.sticker_preview.minimumHeight() <= 80
    # idle: hint, no art
    pg.set_mode("reel")
    pg.gross.setValue(0)
    app.processEvents()
    assert not _has_px(pg.sticker_preview)
    assert "Weigh" in pg.sticker_preview.text()
    # reel mode with weight: Type-1 art only
    pg.gross.setValue(11.380)
    app.processEvents()
    assert pg.pack_mode == "reel"
    assert _has_px(pg.sticker_preview)
    assert "Type-1" in pg.sticker_cap.text() and "NO QR" in pg.sticker_cap.text()
    # Box preview uses the next loose reel and queues its QR sticker.
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    pg.refresh_history()
    pg.set_mode("carton")
    app.processEvents()
    assert pg.pack_mode == "carton"
    assert _has_px(pg.sticker_preview)
    caption = pg.sticker_cap.text()
    assert "QR" in caption and "box sticker" in caption.casefold()
    assert "box-only" not in caption.casefold() and "independent" not in caption.casefold()
    pg.close()


# ---- 3. nested nav modes: hide other controls, gate actions ----

def test_nested_nav_modes_gate_controls_and_action():
    from factory import services as S
    p = _fresh_db()
    _login("packager")
    app = _app()
    pg = _page()
    job = S.create_job_for_product(
        S.create_product("STAGE PROD", 16500, path=p)["id"], path=p)
    _select_job(pg, job["id"])
    pg.show()
    app.processEvents()
    # Reel packing shows session core and requires a fresh weight.
    pg.set_mode("reel")
    pg.gross.setValue(0)
    app.processEvents()
    assert pg.core_wrap.isVisible()
    assert pg.box_wrap.isHidden()
    assert "Reel packing" in pg.stage_caption.text()
    assert not pg.btn_action.isEnabled() and "Weigh first" in pg.btn_action.text()
    pg.gross.setValue(11.380)
    app.processEvents()
    assert pg.btn_action.isEnabled() and "reel" in pg.btn_action.text().casefold()
    # Box packing requires loose reels as well as a measured box weight.
    pg.set_mode("carton")
    app.processEvents()
    assert pg.core_wrap.isHidden()
    assert pg.box_wrap.isVisible()
    assert "Box packing" in pg.stage_caption.text()
    pg.gross.setValue(0)
    app.processEvents()
    assert not pg.btn_action.isEnabled()
    assert "loose" in pg.btn_action.text().casefold()
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    pg.refresh_history()
    pg.set_mode("carton")
    pg.gross.setValue(22.0)
    app.processEvents()
    assert pg.btn_action.isEnabled() and "Pack" in pg.btn_action.text()
    # Dispatcher is read-only (UI + direct invoke).
    _login("dispatcher")
    pg._refresh_state()
    app.processEvents()
    assert "Read-only" in pg.lcd.text()
    assert not pg.btn_action.isEnabled()
    pg.gross.setValue(22.0)
    pg.do_action()
    assert "Read-only" in pg.msg
    assert S.ready_stock(job["id"], path=p) == []
    pg.close()


def test_stale_scale_read_resets_card():
    """Failed scale read must zero the card so a stale gross cannot be saved."""
    from factory import services as S
    _fresh_db()
    _login("packager")
    app = _app()
    pg = _page()
    prod = S.create_product("STALE PROD", 16500)
    job = S.create_job_for_product(prod["id"])
    _select_job(pg, job["id"])
    pg.show()
    pg.set_mode("reel")
    pg.gross.setValue(11.380)
    app.processEvents()
    assert pg.btn_action.isEnabled()
    import os as _os
    old = _os.environ.pop("SIMPLD_ALLOW_MOCK", None)
    try:
        pg.read_scale()
    finally:
        if old is not None:
            _os.environ["SIMPLD_ALLOW_MOCK"] = old
    assert pg.gross.value() == 0, "stale gross must be invalidated to 0"
    assert not pg.btn_action.isEnabled()
    assert "Scale not configured" in pg.msg or "Devices" in pg.msg, pg.msg
    pg.close()


# ---- 4. history: ALL reels, exact row, real Label state ----

def test_history_all_reels_exact_row_and_label_state():
    from factory import services as S
    p = _fresh_db()
    _login("admin")
    app = _app()
    pg = _page()
    job = S.create_job_for_product(
        S.create_product("HIST PROD", 16500, path=p)["id"], path=p)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    # independent weight-only box must NOT shrink the reel history
    S.pack_weight_carton(job["id"], 22.5, path=p)
    _select_job(pg, job["id"])
    pg.refresh_history()
    app.processEvents()
    assert pg.table.columnCount() == 4
    assert pg.table.rowCount() == 2, "history shows ALL reels, not loose-only"
    assert pg.table.horizontalHeaderItem(3).text() == "Label"
    # exact row select: second row maps to r2, not r1
    pg.table.selectRow(1)
    sel = pg.selected_history()
    assert sel is not None and sel["id"] == r2["id"], sel
    assert pg.table.item(1, 3).text() == "Pending"
    # printer-accepted success flips Label; failure stays pending
    S.mark_reel_printed(r2["id"], path=p)
    pg.refresh_history()
    pg.table.selectRow(1)
    assert pg.table.item(1, 3).text().startswith("Printed"), pg.table.item(1, 3).text()
    pg.table.selectRow(0)
    assert pg.table.item(0, 3).text() == "Pending"
    assert pg.selected_history()["id"] == r1["id"]
    pg.close()


def test_reel_dialog_print_success_accepted_failure_pending():
    """(Test-fake printer — physical Citizen hardware NOT verified.)"""
    from unittest import mock as _mock
    from factory import services as S
    from factory.ui.label_preview import ReelLabelDialog
    p = _fresh_db()
    _login("packager")
    app = _app()
    job = S.create_job_for_product(
        S.create_product("DLG PROD", 16500, path=p)["id"], path=p)
    r = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)

    class FakeOK:
        def print_reel_label(self, payload):
            assert "reel" in payload and "job" in payload
            return True

    class FakeFail:
        def print_reel_label(self, payload):
            return False

    class FakeBoom:
        def print_reel_label(self, payload):
            raise RuntimeError("jam")

    pg = _page()
    _select_job(pg, job["id"])
    with _mock.patch("factory.ui.label_preview.QMessageBox.warning") as w:
        d = ReelLabelDialog(pg, job, r, FakeFail())
        d.do_print()
        assert w.call_count == 1
    from factory.db import get_conn
    c = get_conn(p)
    try:
        st = c.execute("SELECT print_status, print_count FROM reels WHERE id=?",
                       (r["id"],)).fetchone()
        assert st["print_status"] == "pending" and st["print_count"] == 0
    finally:
        c.close()
    with _mock.patch("factory.ui.label_preview.QMessageBox.warning") as w:
        d = ReelLabelDialog(pg, job, r, FakeBoom())
        d.do_print()
        assert w.call_count == 1
    d = ReelLabelDialog(pg, job, r, FakeOK())
    d.do_print()
    c = get_conn(p)
    try:
        st = c.execute("SELECT print_status, print_count FROM reels WHERE id=?",
                       (r["id"],)).fetchone()
        assert st["print_status"] == "printed" and st["print_count"] == 1
    finally:
        c.close()
    pg.close()


def test_history_print_marks_selected_row_only():
    """Print/Reprint acts on the selected history row via the real dialog."""
    from unittest import mock as _mock
    from factory import services as S
    p = _fresh_db()
    _login("packager")
    app = _app()
    pg = _page()
    job = S.create_job_for_product(
        S.create_product("HPRINT PROD", 16500, path=p)["id"], path=p)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    _select_job(pg, job["id"])
    pg.table.selectRow(1)
    assert pg.selected_history()["id"] == r2["id"]

    real_dialog = None
    import factory.ui.page_packaging as PP

    orig = PP.ReelLabelDialog

    class AutoPrint(orig):
        def exec(self):
            from PySide6.QtWidgets import QDialog as _QD
            self.do_print()
            return _QD.Accepted

    class FakePrinter:
        def print_reel_label(self, payload):
            return True

    import factory.hardware.mock as HW
    old_printer = HW.printer
    HW.printer = FakePrinter()
    try:
        with _mock.patch.object(PP, "ReelLabelDialog", AutoPrint):
            pg.history_print()
    finally:
        HW.printer = old_printer
    app.processEvents()
    rows = {x["id"]: x for x in S.job_reels(job["id"], path=p)}
    assert rows[r2["id"]]["print_status"] == "printed"
    assert rows[r1["id"]]["print_status"] == "pending"
    pg.refresh_history()
    pg.table.selectRow(1)
    assert pg.table.item(1, 3).text().startswith("Printed")
    pg.close()


# ---- 5. reweigh: fresh only, keeps number/time, clears printed ----

def test_reweigh_keeps_identity_clears_printed_resets_card():
    from unittest import mock as _mock
    from PySide6.QtWidgets import QMessageBox
    from factory import services as S
    p = _fresh_db()
    _login("packager")
    app = _app()
    pg = _page()
    job = S.create_job_for_product(
        S.create_product("REWEIGH PROD", 16500, path=p)["id"], path=p)
    r = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    S.mark_reel_printed(r["id"], path=p)
    before = [x for x in S.job_reels(job["id"], path=p) if x["id"] == r["id"]][0]
    assert before["print_status"] == "printed"
    _select_job(pg, job["id"])
    pg.table.selectRow(0)
    # no fresh gross -> blocked, old weight never reused
    pg.gross.setValue(0)
    with _mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        pg.history_reweigh()
    assert "fresh" in pg.msg.casefold(), pg.msg
    assert [x for x in S.job_reels(job["id"], path=p)
            if x["id"] == r["id"]][0]["gross"] == 11.380
    # fresh measurement -> keeps number/time, clears printed, resets card+select
    pg.gross.setValue(12.280)
    with _mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        pg.history_reweigh()
    after = [x for x in S.job_reels(job["id"], path=p) if x["id"] == r["id"]][0]
    assert after["roll_no"] == before["roll_no"]
    assert after["roll_period"] == before["roll_period"]
    assert after["created_at"] == before["created_at"]
    assert after["gross"] == 12.280
    assert after["print_status"] == "pending", "reweigh invalidates old print"
    assert pg.gross.value() == 0
    assert pg.selected_history() is None, "selection cleared so stale read cannot re-apply"
    assert "reprint" in pg.msg.casefold(), pg.msg
    pg.close()


# ---- 6. delete: admin-only, safe, numbers never reused ----

def test_delete_admin_only_safe_numbers_retired():
    from unittest import mock as _mock
    from PySide6.QtWidgets import QMessageBox
    from factory import services as S
    p = _fresh_db()
    _login("packager")
    app = _app()
    pg = _page()
    job = S.create_job_for_product(
        S.create_product("DEL PROD", 16500, path=p)["id"], path=p)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    packed = S.pack_carton(job["id"], [r1["id"]], path=p)
    _select_job(pg, job["id"])
    # packager UI: delete disabled with Admin-only hint
    pg.refresh_history()
    pg.table.selectRow(0)
    app.processEvents()
    assert not pg.btn_hist_delete.isEnabled()
    assert "Admin" in pg.btn_hist_delete.toolTip()
    # packager direct invoke denied, record intact
    pg.history_delete()
    assert "Only admin" in pg.msg
    assert len(S.job_reels(job["id"], path=p)) == 2
    # dispatcher direct service denied too
    _login("dispatcher")
    try:
        S.delete_reel(r2["id"], path=p)
        raise SystemExit("FAIL: dispatcher delete accepted")
    except PermissionError:
        pass
    # admin: packed reel blocked, loose reel deleted, number never reused
    _login("admin")
    pg._refresh_state()
    pg.refresh_history()
    # select the packed row explicitly (find by id, not position)
    for row in range(pg.table.rowCount()):
        pg.table.selectRow(row)
        if pg.selected_history()["id"] == r1["id"]:
            break
    assert pg.btn_hist_delete.isEnabled(), "admin must see Delete enabled"
    with _mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        pg.history_delete()
    assert "blocked" in pg.msg.casefold() or "packed" in pg.msg.casefold(), pg.msg
    assert len(S.job_reels(job["id"], path=p)) == 2
    for row in range(pg.table.rowCount()):
        pg.table.selectRow(row)
        if pg.selected_history()["id"] == r2["id"]:
            break
    with _mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        pg.history_delete()
    assert "retired" in pg.msg.casefold(), pg.msg
    remaining = S.job_reels(job["id"], path=p)
    assert [x["id"] for x in remaining] == [r1["id"]]
    nxt = S.predict_next_reel_seq(path=p)
    assert nxt > r2["roll_no"], f"deleted number must never recycle (next={nxt}, deleted={r2['roll_no']})"
    r3 = S.create_reel(job["id"], gross=10.980, core=0.280, path=p)
    assert r3["roll_no"] != r2["roll_no"]
    pg.close()


# ---- 7. RBAC: real sessions, forged dicts ignored ----

def test_forged_actor_dict_grants_nothing():
    from factory import services as S
    from factory import auth as A
    _fresh_db()
    _app()
    A.set_session(None)
    from factory.ui.page_packaging import PackagingPage
    pg = PackagingPage(actor={"username": "p", "role": "packager"})
    assert not pg._can_produce()
    pg.gross.setValue(11.0)
    pg.do_action()
    assert "Read-only" in pg.msg or "role" in pg.msg.casefold(), pg.msg
    # forged token also denied
    import secrets as _secrets
    A.set_session(_secrets.token_urlsafe(32))
    pg2 = PackagingPage()
    assert not pg2._can_produce()
    A.set_session(None)
    pg.close()
    pg2.close()


def test_service_permissions_on_direct_invoke():
    from factory import services as S
    p = _fresh_db()
    _login("admin")
    job = S.create_job_for_product(
        S.create_product("PERM PROD", 16500, path=p)["id"], path=p)
    _login("dispatcher")
    for fn in (lambda: S.create_reel(job["id"], gross=10.0, core=0.28, path=p),
               lambda: S.pack_weight_carton(job["id"], 10.0, path=p),
               lambda: S.create_product("NOPE", 100, path=p),
               lambda: S.delete_reel(999999, path=p)):
        try:
            fn()
            raise SystemExit(f"FAIL: dispatcher production op accepted: {fn}")
        except (PermissionError, ValueError):
            pass
    _login("packager")
    try:
        S.create_dispatch_order("X", job["id"], 1, path=p)
        raise SystemExit("FAIL: packager dispatch accepted")
    except PermissionError:
        pass
    _login("admin")
    assert S.create_reel(job["id"], gross=10.28, core=0.28, path=p)["net"] > 0


# ---- 8. no sample startup, blank hardware blocks, mode labelled ----

def test_fresh_startup_empty_no_sample_and_hardware_labelled():
    """Production boots empty; blank Devices blocks; mode text is explicit."""
    from factory import services as S
    from factory.hardware import devices as DEV
    import os as _os
    p = _fresh_db()
    _login("admin")
    _app()
    assert S.list_jobs(path=p) == []
    assert S.production_history(path=p) == []
    from factory.ui.main_window import MainWindow
    w = MainWindow()
    assert w.pages["jobs"].empty.isVisible() or "No jobs" in w.pages["jobs"].empty.text()
    assert "No reels yet" in w.pages["packaging"].sel_info.text()
    assert set(w.pack_nav) == {"reel", "carton"}
    w.pack_nav["carton"].click()
    assert w.pages["packaging"].pack_mode == "carton"
    from factory.hardware import status as HWStatus
    states = {d["key"]: d["state"]
              for d in HWStatus.station_status("packaging")}
    assert states == {"scale": "setup", "printer": "setup"}, states
    old = _os.environ.pop("SIMPLD_ALLOW_MOCK", None)
    try:
        try:
            DEV.require_scale()
            raise SystemExit("FAIL: blank scale allowed production weighing")
        except RuntimeError as e:
            assert "Devices" in str(e) or "Scale" in str(e)
        try:
            DEV.require_printer()
            raise SystemExit("FAIL: blank printer allowed production printing")
        except RuntimeError as e:
            assert "Devices" in str(e) or "Printer" in str(e)
    finally:
        if old is not None:
            _os.environ["SIMPLD_ALLOW_MOCK"] = old
    w.close()


# ---- 9. history page: actual all-production + search ----

def test_history_page_all_production_search_and_counts():
    from factory import services as S
    p = _fresh_db()
    _login("admin")
    _app()
    job = S.create_job_for_product(
        S.create_product("HPAGE PROD", 16500, path=p)["id"], path=p)
    r = S.create_reel(job["id"], gross=10.280, core=0.280, path=p)
    ct = S.pack_weight_carton(job["id"], 9.5, path=p)
    _login("dispatcher")
    from factory.ui.pages_misc import HistoryPage
    hp = HistoryPage()
    hp.tabs.setCurrentIndex(0)
    hp.search.setText("")
    hp.refresh()
    assert hp.table.rowCount() >= 2, "All production must list reels + boxes"
    kinds = {hp.table.item(rw, 0).text() for rw in range(hp.table.rowCount())}
    assert "Reel" in kinds and "Box" in kinds, kinds
    labels = [hp.table.item(rw, 7).text() for rw in range(hp.table.rowCount())]
    assert all(l in ("Pending",) or l.startswith("Printed") for l in labels), labels
    hp.search.setText(ct["carton_no"].lower())
    hp.refresh()
    assert hp.table.rowCount() >= 1
    assert ct["carton_no"] in " ".join(
        hp.table.item(0, c).text() for c in range(hp.table.columnCount()))
    hp.search.setText("zzzz-no-such-record")
    hp.refresh()
    assert "No records" in hp.count.text()
    hp.close()
