"""Offscreen UI smoke checks for v2 native theme (no style mirroring).

Useful behavior only: role-gated nav (real authenticated sessions — forged
role dicts fail closed), read-only tables, empty messages, login
focus/error state. Runs with QT_QPA_PLATFORM=offscreen against a
temporary SIMPLD_DB — never the production data/factory.db.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._auth_helper import ensure_default_users

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _fresh_db():
    from factory import db as DB
    from factory import auth as A
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-ui-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    ensure_default_users(tmp)
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


def test_role_nav_gating():
    _fresh_db()
    _app()
    from factory.ui.main_window import MainWindow
    _login("packager")
    pack = MainWindow()
    assert set(pack.pages.keys()) == {"packaging", "stock", "jobs"}, pack.pages.keys()
    assert "Simpl'd" in pack.windowTitle()
    _login("dispatcher")
    disp = MainWindow()
    assert set(disp.pages.keys()) == {"stock", "dispatch", "history"}, disp.pages.keys()
    _login("admin")
    adm = MainWindow()
    assert set(adm.pages.keys()) == {"packaging", "stock", "dispatch", "jobs", "history", "summary"}
    assert hasattr(adm, "btn_users")
    assert not hasattr(pack, "btn_users")
    # nav is exclusive + first button checked
    assert pack.btns[0].isChecked()
    assert pack.btns[0] is pack.pack_nav["reel"]
    assert getattr(disp, "pack_nav", {}) == {}
    for main in (pack, adm):
        assert set(main.pack_nav) == {"reel", "carton"}
        main.pack_nav["carton"].click()
        assert main.pages["packaging"].pack_mode == "carton"
        assert main.stack.currentIndex() == main.stack.indexOf(main.pages["packaging"])
        main.pack_nav["reel"].click()
        assert main.pages["packaging"].pack_mode == "reel"
        # collapsible group: header stays, children hide, navigation re-expands
        assert main.pack_group.is_expanded()
        assert not main.pack_tree.isHidden()
        main.pack_group.click()
        assert not main.pack_group.is_expanded()
        assert main.pack_tree.isHidden()
        assert not main.pack_group.isHidden(), "the group header must stay"
        main.pack_nav["carton"].click()
        assert main.pack_group.is_expanded()
        assert not main.pack_tree.isHidden()
    pack.close()
    disp.close()
    adm.close()


def test_forged_session_fails_closed():
    import secrets
    _fresh_db()
    _app()
    from factory import auth as A
    from factory.ui.main_window import MainWindow
    A.set_session(None)
    try:
        MainWindow({"username": "p", "role": "packager"})
        raise SystemExit("FAIL: forged dict without session opened a window")
    except PermissionError:
        pass
    A.set_session(secrets.token_urlsafe(32))  # never issued
    try:
        MainWindow()
        raise SystemExit("FAIL: forged token opened a window")
    except PermissionError:
        pass
    A.set_session(None)


def test_tables_are_quiet_readonly():
    from PySide6.QtWidgets import QAbstractItemView
    _fresh_db()
    _app()
    _login("admin")
    from factory.ui.main_window import MainWindow
    w = MainWindow()
    for key, page in w.pages.items():
        tbl = getattr(page, "table", None)
        if tbl is not None:
            assert tbl.editTriggers() == QAbstractItemView.NoEditTriggers, key
            assert tbl.selectionBehavior() == QAbstractItemView.SelectRows, key
            assert not tbl.showGrid(), key
    # packaging history: Roll/Net/Status/Label(print state), no manual-pack widgets
    pg = w.pages["packaging"]
    assert pg.table.columnCount() == 4
    assert pg.box_table.editTriggers() == QAbstractItemView.NoEditTriggers
    assert pg.box_table.selectionBehavior() == QAbstractItemView.SelectRows
    assert not pg.box_table.showGrid()
    assert pg.box_table.columnCount() == 6  # Box/Reels/Gross/Tare/Net/Sticker
    assert not hasattr(pg, "btn_select_all") and not hasattr(pg, "btn_clear")
    # history keeps production/Reels/Boxes/Dispatch tabs + search
    hp = w.pages["history"]
    assert hp.tabs.count() == 4
    assert hp.tabs.tabText(3) == "Dispatch"
    assert hp.search is not None
    w.close()


def test_empty_messages_and_login_state():
    _fresh_db()
    app = _app()
    _login("admin")
    from factory.ui.main_window import MainWindow
    w = MainWindow()  # production starts empty: no demo jobs seeded
    w.pages["jobs"].refresh()
    w.pages["stock"].refresh()
    w.pages["history"].refresh()
    w.pages["packaging"].refresh_history()
    assert "No jobs" in w.pages["jobs"].empty.text()
    assert "No boxes in stock" in w.pages["stock"].count.text()
    assert "No " in w.pages["history"].count.text()
    assert "No reels yet" in w.pages["packaging"].sel_info.text()
    # dispatch is a real workspace (orders + scan + complete/cancel)
    dp = w.pages["dispatch"]
    assert dp.order_combo is not None and dp.scan is not None
    assert dp.btn_complete is not None and dp.btn_cancel is not None
    w.close()
    # login: error hidden initially, shows + focuses password on bad attempt
    from factory.ui.login import LoginDialog
    dlg = LoginDialog()
    assert dlg.err.text() == "" and dlg.err.isHidden()
    assert "Simpl'd" in dlg.windowTitle()
    dlg.name.setText("admin")
    dlg.pw.setText("wrong")
    dlg.try_login()
    assert dlg.user is None
    assert "Wrong username" in dlg.err.text()
    assert not dlg.err.isHidden()
    app.closeAllWindows()


def test_cleanup_no_banners_or_descs():
    """No persistent banner widget; no page-desc subheadings on any page."""
    from PySide6.QtWidgets import QLabel
    _fresh_db()
    _app()
    _login("admin")
    from factory.ui.main_window import MainWindow
    w = MainWindow()
    pg = w.pages["packaging"]
    assert not hasattr(pg, "msg") or isinstance(getattr(pg, "msg"), str), \
        "packaging must not keep a banner QLabel (toast only)"
    for key, page in w.pages.items():
        for lb in page.findChildren(QLabel):
            assert lb.property("page") != "desc", \
                f"{key} still has a page-desc subheading: {lb.text()[:60]!r}"
    w.close()


def test_jobs_single_row_selection_gating():
    """Whole-row single selection; actions disabled until valid, reset on refresh."""
    from PySide6.QtWidgets import QAbstractItemView
    from factory import services as S
    _fresh_db()
    _app()
    _login("admin")
    from factory.ui.main_window import MainWindow
    w = MainWindow()
    jobs = w.pages["jobs"]
    assert jobs.table.selectionBehavior() == QAbstractItemView.SelectRows
    assert jobs.table.selectionMode() == QAbstractItemView.SingleSelection
    jobs.refresh()
    assert not jobs.btn_close.isEnabled() and not jobs.btn_slip.isEnabled(), \
        "actions must start disabled with no selection"
    assert jobs.selected() is None
    j = S.create_job("SEL JOB", size_mm=160, core_wt=0.280)
    jobs.refresh()
    assert not jobs.btn_close.isEnabled() and not jobs.btn_slip.isEnabled(), \
        "refresh must reset to disabled"
    jobs.table.selectRow(0)
    assert jobs.selected() is not None and jobs.selected()["id"] == j["id"]
    assert jobs.btn_close.isEnabled() and jobs.btn_slip.isEnabled(), \
        "valid row must enable close/reopen + slip"
    jobs.refresh()
    assert jobs.selected() is None
    assert not jobs.btn_close.isEnabled() and not jobs.btn_slip.isEnabled()
    # clearSelection must invalidate: currentRow alone can stay valid
    jobs.table.selectRow(0)
    assert jobs.selected() is not None
    jobs.table.clearSelection()
    assert jobs.selected() is None, \
        "clearSelection must clear validity (do not use currentRow alone)"
    assert not jobs.btn_close.isEnabled() and not jobs.btn_slip.isEnabled()
    w.close()


def test_packaging_preview_room_and_table_fill():
    """Weigh/preview share a vertical split; the right table fills its card."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea, QSplitter
    _fresh_db()
    _app()
    _login("admin")
    from factory.ui.main_window import MainWindow
    w = MainWindow()
    pg = w.pages["packaging"]
    # Weigh card scrolls above the preview in a 60/40 split.
    assert pg.sticker_preview.minimumHeight() <= 80, \
        pg.sticker_preview.minimumHeight()
    assert isinstance(pg._left_split, QSplitter)
    assert pg._left_split.orientation() == Qt.Vertical
    assert pg._left_split.indexOf(pg._preview_card) == 1
    assert tuple(pg._left_split.widget(i).sizePolicy().verticalStretch()
                 for i in range(2)) == (3, 2)
    weigh_scroll = pg._left_split.widget(0)
    assert isinstance(weigh_scroll, QScrollArea)
    assert weigh_scroll.widget().isAncestorOf(pg.gross)
    lay = pg._right_lay
    assert pg.right_stack.currentWidget() is pg.reel_panel, \
        "reel packing is the default workspace"
    assert lay.stretch(lay.indexOf(pg.right_stack)) == 1, \
        "the reel/box workspace must take the stretch"
    rp = pg.reel_panel.layout()
    assert rp.stretch(rp.indexOf(pg.table)) == 1, "table must take the stretch"
    assert pg._right_card.parent() is not None
    p = pg._right_card.parent()
    while p is not None and not isinstance(p, QScrollArea):
        p = p.parent()
    assert p is None, "right card must not sit inside a scroll area"
    w.close()


def test_dispatch_workspace_records_history():
    """Dispatch page is a real workspace and the data layer records history."""
    from factory import services as S
    _fresh_db()
    _app()
    _login("admin")
    from factory.ui.main_window import MainWindow
    from factory.ui.page_dispatch import DispatchPage
    w = MainWindow()
    assert isinstance(w.pages["dispatch"], DispatchPage)
    job = S.create_job("DISP KEEP JOB", core_wt=0.280)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280)
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280)
    ct = S.pack_carton(job["id"], [r1["id"], r2["id"]])
    o = S.create_dispatch_order("KEEP CO", job["id"], 2)
    S.scan_into_order(o["id"], ct["carton_no"])
    w.pages["dispatch"].refresh()
    assert w.pages["dispatch"].order_combo.count() >= 1
    prod2 = S.create_product("PIECE PRODUCT", 2500)
    job2 = S.create_job_for_product(prod2["id"], core_wt=0.280)
    for _ in range(4):
        S.create_reel(job2["id"], gross=11.380, core=0.280)
    boxes = [S.pack_next_box(job2["id"], 22.0, 0.300, reels_per_box=2)
             for _ in range(2)]
    order = S.create_dispatch_order("PIECE CO", job2["id"],
                                    required_pieces=6000,
                                    customer_po="4500048181")
    for box in boxes:
        S.scan_into_order(order["id"], box["carton_no"])
    progress = S.order_progress(order["id"])
    assert progress["loaded_pieces"] == 10000 >= progress["required_pieces"] == 6000
    assert [it["client_box_no"] for it in progress["items"]] == [1, 2]
    assert progress["customer_po"] == "4500048181"
    # Client box numbers are the bare 1..N sequence, not 01/02 padding.
    dp = w.pages["dispatch"]
    dp.refresh(select_id=order["id"])
    assert [dp.table.item(r, 0).text() for r in range(dp.table.rowCount())] == ["1", "2"]
    assert S.complete_dispatch(order["id"])["status"] == "complete"
    # Completion flips the loaded boxes and their reels to dispatched.
    assert S.ready_stock(job2["id"]) == []
    reels2 = S.job_reels(job2["id"])
    assert len(reels2) == 4 and all(r["status"] == "dispatched" for r in reels2), \
        [r["status"] for r in reels2]
    assert S.production_history(), "history must survive dispatch operations"
    w.close()


if __name__ == "__main__":
    test_role_nav_gating()
    test_forged_session_fails_closed()
    test_tables_are_quiet_readonly()
    test_empty_messages_and_login_state()
    test_cleanup_no_banners_or_descs()
    test_jobs_single_row_selection_gating()
    test_packaging_preview_room_and_table_fill()
    test_dispatch_workspace_records_history()
    print("test_ui_smoke PASS")
