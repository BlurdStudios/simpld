"""Branded splash + About dialog.

The splash is deliberately cheap: drawn once as a QPixmap and shown while the
database bootstraps, then handed off. Nothing blocks on a timer — it is closed
as soon as the real window comes up.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import (QPixmap, QPainter, QColor, QFont, QLinearGradient,
                           QGuiApplication, QTextDocument)
from PySide6.QtWidgets import (QSplashScreen, QDialog, QVBoxLayout, QHBoxLayout,
                               QLabel, QPushButton, QFrame, QFileDialog,
                               QMessageBox, QCheckBox)

from factory.ui import brand as _brand


def _splash_pixmap(w: int = 460, h: int = 262) -> QPixmap:
    """Compose the splash in Qt so all text uses real system fonts."""
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor("#FFFFFF"))

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # accent hairline across the top edge
    grad = QLinearGradient(0, 0, w, 0)
    grad.setColorAt(0.0, QColor(_brand.ACCENT))
    grad.setColorAt(1.0, QColor(_brand.ACCENT_HOVER))
    p.fillRect(0, 0, w, 3, grad)

    # Mark: draw the 256px master into a logical rect, so it stays sharp at any
    # device pixel ratio instead of scaling a 1:1 bitmap.
    side = 84
    p.drawPixmap(QRect((w - side) // 2, 46, side, side), _brand.mark_pixmap(256))

    # Wordmark through QTextDocument: a plain drawText() cannot colour the
    # apostrophe differently from the rest of the word.
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    # Literal U+2019, not &#8217; — Qt's HTML parser does not decode numeric
    # character references and would print the entity verbatim.
    doc.setHtml(
        f'<div align="center" style="font-size:26pt;font-weight:700;'
        f'color:{_brand.INK};margin:0;padding:0;">'
        f"Simpl<span style=\"color:{_brand.ACCENT};\">\u2019</span>d</div>")
    doc.setTextWidth(w)
    p.save()
    p.translate(0, 146)
    doc.drawContents(p, QRectF(0, 0, w, 54))
    p.restore()

    f = QFont()
    f.setFamilies(["Segoe UI", "Noto Sans", "Inter", "sans-serif"])
    f.setPointSize(10)
    p.setFont(f)
    p.setPen(QColor(_brand.INK_FAINT))
    p.drawText(QRect(0, 208, w, 20), Qt.AlignmentFlag.AlignCenter,
               f"Version {_brand.VERSION}")
    p.drawText(QRect(0, 230, w, 20), Qt.AlignmentFlag.AlignCenter,
               f"A {_brand.PARENT_NAME} product")
    p.end()
    return pm


def show_splash() -> QSplashScreen | None:
    """Show the branded splash. Returns None if anything is unavailable."""
    try:
        sc = QSplashScreen(_splash_pixmap())
        sc.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        sc.show()
        QGuiApplication.processEvents()   # let it actually paint
        return sc
    except Exception:
        return None


def close_splash(sc) -> None:
    if sc is None:
        return
    try:
        sc.close()
    except Exception:
        pass


class AboutDialog(QDialog):
    """Version, attribution and the identity lockup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {_brand.PRODUCT_NAME}")
        self.setFixedWidth(480)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(14)

        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(_brand.lockup(size=28, mark_px=44, parent=True))
        holder.addStretch(1)
        outer.addLayout(holder)

        card = QFrame()
        card.setProperty("card", True)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(7)
        for k, v in (("Version", _brand.VERSION),
                     ("Product of", _brand.PARENT_NAME),
                     ("Works offline", "Yes — no data leaves this computer"),
                     ("Storage", "Local SQLite database on this PC")):
            row = QHBoxLayout()
            row.setSpacing(10)
            a = QLabel(k)
            a.setProperty("field", "caption")
            a.setFixedWidth(110)
            b = QLabel(v)
            b.setProperty("hint", True)
            b.setWordWrap(True)
            row.addWidget(a)
            row.addWidget(b, 1)
            cl.addLayout(row)
        outer.addWidget(card)

        # ---- licensing card (Plan 009 Phase 2; offline, fail-closed) ----
        try:
            from factory import license as _LIC
            _st = _LIC.license_status()
            _disp = _LIC.license_display(_st)
            _mid = _LIC.machine_fingerprint()
        except Exception as _e:
            _LIC, _st, _disp, _mid = None, {}, f"Licence check failed: {_e}", "?"
        lic_card = QFrame()
        lic_card.setProperty("card", True)
        lc2 = QVBoxLayout(lic_card)
        lc2.setContentsMargins(16, 14, 16, 14)
        lc2.setSpacing(7)
        lt2 = QLabel("LICENCE")
        lt2.setProperty("field", "caption")
        lc2.addWidget(lt2)
        lic_lbl = QLabel(_disp)
        lic_lbl.setProperty("hint", True)
        lic_lbl.setWordWrap(True)
        lc2.addWidget(lic_lbl)
        # Trial countdown (vivid): days-left banner inside About.
        try:
            _lic_d = (_st or {}).get("lic") or {}
            if str(_lic_d.get("edition") or "") == "trial" and _LIC is not None:
                _left = _LIC.license_days_left(_lic_d)
                from factory.ui.styles import set_banner as _setb
                _trial_lbl = QLabel("")
                _trial_lbl.setWordWrap(True)
                if _left is None:
                    pass
                elif _left < 0:
                    _trial_lbl.setText(f"Trial expired {-_left}d ago — contact your vendor for a full key.")
                    _setb(_trial_lbl, "err")
                    lc2.addWidget(_trial_lbl)
                elif _left == 0:
                    _trial_lbl.setText("Trial expires TODAY — back up your data and contact your vendor.")
                    _setb(_trial_lbl, "warn")
                    lc2.addWidget(_trial_lbl)
                else:
                    _trial_lbl.setText(f"Trial: {_left} day{'s' if _left != 1 else ''} left"
                                       f" (expires {_lic_d.get('expires')}).")
                    _setb(_trial_lbl, "warn" if _left <= 3 else "info")
                    lc2.addWidget(_trial_lbl)
        except Exception:
            pass
        mid_lbl = QLabel(f"Machine ID: {_mid}")
        mid_lbl.setProperty("hint", True)
        mid_lbl.setWordWrap(True)
        mid_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lc2.addWidget(mid_lbl)
        try:
            from factory import data_access as _DA2
            _me2 = _DA2.current_user() or {}
            _is_lan = _DA2.is_lan()
        except Exception:
            _me2, _is_lan = {}, False
        if _LIC is not None and not _is_lan:
            lr2 = QHBoxLayout()
            lr2.setSpacing(8)
            imp_btn = QPushButton("Import licence…")
            imp_btn.setMinimumHeight(36)
            from factory.ui.styles import style_button as _sb
            _sb(imp_btn, "outline")
            lc2.addLayout(lr2)
            lr2.addWidget(imp_btn, 1)
            if str(_me2.get("role") or "") == "admin":
                try:
                    _stations = _LIC.list_stations()
                    _s_lbl = QLabel(
                        f"Stations seen (30d): {len(_stations)}"
                        + (" — " + ", ".join(
                            str(s.get('station_id') or '')[:8]
                            for s in _stations[:6]) if _stations else ""))
                    _s_lbl.setProperty("hint", True)
                    _s_lbl.setWordWrap(True)
                    lc2.addWidget(_s_lbl)
                except Exception:
                    pass

            def _do_import():
                import shutil
                p, _ = QFileDialog.getOpenFileName(
                    imp_btn, "Import licence file", "",
                    "Licence (*.lic);;All (*)")
                if not p:
                    return
                try:
                    dest = _LIC.license_path()
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copyfile(p, dest)
                    st2 = _LIC.license_status()
                    lic_lbl.setText(_LIC.license_display(st2))
                    QMessageBox.information(
                        imp_btn, "Licence",
                        f"Imported.\n{_LIC.license_display(st2)}")
                except Exception as e:
                    QMessageBox.warning(imp_btn, "Licence",
                                        f"Could not import:\n{e}")
            imp_btn.clicked.connect(_do_import)
        elif _is_lan:
            _h = QLabel("Licence is enforced on the packager — "
                        "contact admin if this PC is rejected.")
            _h.setProperty("hint", True)
            _h.setWordWrap(True)
            lc2.addWidget(_h)
        outer.addWidget(lic_card)

        note = QLabel("The company name prints where the old paper did — "
                      "stickers and packing slips. The app itself is Simpl'd.")
        note.setProperty("hint", True)
        note.setWordWrap(True)
        outer.addWidget(note)

        self._logo_b64 = ""
        self._logo_card = QFrame()
        self._logo_card.setProperty("card", True)
        lc = QVBoxLayout(self._logo_card)
        lc.setContentsMargins(16, 12, 16, 12)
        lc.setSpacing(8)
        lt = QLabel("CLIENT STICKER LOGO")
        lt.setProperty("field", "caption")
        lc.addWidget(lt)
        self._logo_preview = QLabel("No logo — the company title prints centred")
        self._logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo_preview.setMinimumHeight(62)
        self._logo_preview.setStyleSheet(
            "QLabel { background: #FFFFFF; border: 1px solid #E2E8F0;"
            " border-radius: 6px; color: #64748B; }")
        lc.addWidget(self._logo_preview)
        lh = QLabel("Uploaded here and stored in the shared database, so every "
                    "PC prints the same logo on the wide client sticker.")
        lh.setProperty("hint", True)
        lh.setWordWrap(True)
        lc.addWidget(lh)
        try:
            from factory import data_access as _DA
            me = _DA.current_user() or {}
        except Exception:
            _DA, me = None, {}
        if str(me.get("role") or "") == "admin":
            lr = QHBoxLayout()
            lr.setSpacing(8)
            self._logo_btn = QPushButton("Upload logo…")
            self._logo_btn.setMinimumHeight(36)
            self._logo_clear = QPushButton("Remove")
            self._logo_clear.setMinimumHeight(36)
            from factory.ui.styles import style_button
            style_button(self._logo_btn, "outline")
            style_button(self._logo_clear, "secondary")
            self._logo_btn.clicked.connect(self._upload_logo)
            self._logo_clear.clicked.connect(self._clear_logo)
            lr.addWidget(self._logo_btn, 1)
            lr.addWidget(self._logo_clear)
            lc.addLayout(lr)
            self._load_logo()
        else:
            lh.setText("An admin can upload the company logo here.")
            self._load_logo()
        outer.addWidget(self._logo_card)

        if str(me.get("role") or "") == "admin":
            from factory.hardware import devices as _HWDevices
            test_card = QFrame()
            test_card.setProperty("card", True)
            tc = QVBoxLayout(test_card)
            tc.setContentsMargins(16, 10, 16, 10)
            tc.setSpacing(4)
            tt = QLabel("DISPATCH TEST AID")
            tt.setProperty("field", "caption")
            tc.addWidget(tt)
            self._test_cb = QCheckBox(
                "Show Test scan in Dispatch on this PC")
            try:
                self._test_cb.setChecked(_HWDevices.mock_qr_enabled())
            except Exception:
                self._test_cb.setChecked(False)
            self._test_cb.toggled.connect(self._toggle_mock_qr)
            tc.addWidget(self._test_cb)
            th = QLabel(
                "Loads the next Ready box as if its QR was scanned, so the "
                "client sticker can be tested without a scanner. It does not "
                "change the printer or scale. Turn it off after testing.")
            th.setProperty("hint", True)
            th.setWordWrap(True)
            tc.addWidget(th)
            outer.addWidget(test_card)

        outer.addStretch(1)
        row = QHBoxLayout()
        # Update entry point lives here (plus the silent startup check): the
        # About dialog is the one place an operator looks for "what version".
        self.update_btn = QPushButton("Check for updates")
        self.update_btn.setMinimumHeight(36)
        self.update_btn.setMinimumWidth(160)
        from factory.ui.styles import style_button
        style_button(self.update_btn, "outline")
        self.update_btn.clicked.connect(self._check_updates)
        row.addWidget(self.update_btn)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setMinimumHeight(36)
        close.setMinimumWidth(96)
        style_button(close, "accent")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        outer.addLayout(row)

    def _load_logo(self):
        from PySide6.QtGui import QPixmap
        import base64
        from factory import data_access as DA
        try:
            self._logo_b64 = DA.get_company_logo() or ""
        except Exception:
            self._logo_b64 = ""
        if not self._logo_b64:
            self._logo_preview.setPixmap(QPixmap())
            self._logo_preview.setText("No logo — the company title prints centred")
            if hasattr(self, "_logo_clear"):
                self._logo_clear.setEnabled(False)
            return
        try:
            raw = base64.b64decode(self._logo_b64)
            pm = QPixmap()
            if not pm.loadFromData(raw) or pm.isNull():
                raise ValueError("Logo image could not be decoded")
            self._logo_preview.setPixmap(
                pm.scaled(360, 62, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation))
            self._logo_preview.setText("")
            if hasattr(self, "_logo_clear"):
                self._logo_clear.setEnabled(True)
        except Exception:
            self._logo_preview.setPixmap(QPixmap())
            self._logo_preview.setText("Logo could not be displayed")
            if hasattr(self, "_logo_clear"):
                self._logo_clear.setEnabled(True)

    def _upload_logo(self):
        import base64
        import io
        from factory import data_access as DA
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the company logo", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        try:
            from PIL import Image
            im = Image.open(path)
            im.load()
            im = im.convert("RGBA")
            # 600px wide is ample for a 203dpi 210mm label and keeps the
            # shared-DB / LAN payload small.
            im.thumbnail((600, 300), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            DA.set_company_logo(b64)
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Company logo", str(e))
        except Exception as e:
            return QMessageBox.warning(
                self, "Company logo",
                f"Could not read that image: {str(e).strip() or 'unknown error'}")
        self._load_logo()

    def _clear_logo(self):
        from factory import data_access as DA
        ans = QMessageBox.question(
            self, "Remove logo",
            "Remove the company logo from the wide client sticker?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            DA.set_company_logo("")
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Company logo", str(e))
        except Exception as e:
            return QMessageBox.warning(self, "Company logo", str(e))
        self._load_logo()

    def _toggle_mock_qr(self, on: bool) -> None:
        """Admin-only switch for the Dispatch test-scan aid on this PC."""
        from factory.hardware import config as HWConfig
        from factory.hardware import devices as HWDevices
        if on:
            ans = QMessageBox.question(
                self, "Enable Test scan?",
                "This adds a Test scan button to Dispatch on this PC.\n\n"
                "It loads the next Ready box as if its QR was scanned, so you "
                "can test the client sticker without a scanner. It does not "
                "change the printer or scale.\n\nTurn it off after testing.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                self._test_cb.blockSignals(True)
                self._test_cb.setChecked(False)
                self._test_cb.blockSignals(False)
                return
        try:
            cfg = HWConfig.load()
            cfg["mock_qr"] = bool(on)
            HWConfig.save(cfg)
            HWDevices.reload_hardware(cfg)
        except Exception as e:
            self._test_cb.blockSignals(True)
            self._test_cb.setChecked(not on)
            self._test_cb.blockSignals(False)
            return QMessageBox.warning(
                self, "Test scan", f"Could not change the test aid: {e}")
        # The Dispatch page may already be built behind this dialog.
        try:
            page = getattr(self.parent(), "pages", {}).get("dispatch")
            if page is not None:
                page.refresh()
        except Exception:
            pass

    def _check_updates(self):
        from factory.ui import update_ui
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Checking…")

        def restore():
            self.update_btn.setEnabled(True)
            self.update_btn.setText("Check for updates")

        update_ui.check_now(self, on_done=restore)