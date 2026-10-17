# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Simpl'd Windows demo (onedir).

Produces dist/Simpld/Simpld.exe — a real Windows PE, built with the Windows
Python interpreter + PyInstaller (under Wine on a Linux host, or natively on
Windows). NOT a renamed Linux binary.

Bundled IN: run.py, factory/ source package, UI assets (factory/ui/assets),
PySide6/Pillow/qrcode/pyserial from the Windows site-packages.

Bundled OUT (explicitly): production data/factory.db, data/hardware.json,
data/print_log.txt, data/backups/, data/*.csv, tests/, .venv/, __pycache__.
The frozen app creates its writable DB at %LOCALAPPDATA%/Simpld/factory.db on
first launch (see factory/db.py app_data_dir); nothing is written next to the
.exe or into the read-only sys._MEIPASS extraction dir.
"""

import glob as _glob
import os as _os

ROOT = _os.path.dirname(_os.path.dirname(SPECPATH))  # <staged-src>/  (spec lives in build/windows/)
ASSETS = _os.path.join(ROOT, "factory", "ui", "assets")

_data_files = sorted(_glob.glob(_os.path.join(ASSETS, "*.png")))
datas = [(_f, "factory/ui/assets") for _f in _data_files]

# Brand assets: the mark raster ladder + the icon set live in assets/brand/.
# The top-level glob above does NOT recurse, so without this the frozen app
# starts with a blank window icon and no sidebar glyphs.
_brand_files = sorted(_glob.glob(_os.path.join(ASSETS, "brand", "*.png")))
datas += [(_f, "factory/ui/assets/brand") for _f in _brand_files]

# The .ico is embedded in the exe via `icon=` below, but ship it too so the
# installed copy can set the window icon / be reused by shortcuts.
_ico_file = _os.path.join(ASSETS, "simpld.ico")
if _os.path.isfile(_ico_file):
    datas.append((_ico_file, "factory/ui/assets"))

binaries = []

# --- Qt platform plugins (fixes "no Qt platform plugin could be initialized") ---
# PyInstaller's PySide6 hook *should* collect PySide6/plugins, but the broken
# demo build shipped without any plugins/ folder (no qwindows.dll), so force it
# here. At build time this runs under Windows Python with PySide6 installed.
_ps_dir = None
try:
    # Wine invokes PyInstaller without PATH set, which makes PySide6/__init__.py
    # raise KeyError('PATH') on import — pre-seed minimal env first.
    _os.environ.setdefault("PATH", "")
    _os.environ.setdefault("SYSTEMROOT", "C:\\Windows")
    _os.environ.setdefault("WINDIR", "C:\\Windows")
    import PySide6 as _ps
    _ps_dir = _os.path.dirname(_ps.__file__)
except Exception:
    _ps_dir = None
if _ps_dir is None:
    # Fallback: locate the package dir without importing it (avoids the
    # __init__ side effects entirely). Covers Wine + future split wheels.
    try:
        import sys as _sys
        for _p in _sys.path:
            _cand = _os.path.join(_p, "PySide6")
            if _os.path.isdir(_os.path.join(_cand, "plugins")):
                _ps_dir = _cand
                break
    except Exception:
        pass
if _ps_dir and _os.path.isdir(_ps_dir):
    # Only the plugin families this Widgets+Printing app needs. Copying the
    # whole plugins/ tree pulls 200MB+ of transitive Qt DLLs (WebEngine, 3D,
    # Quick, Multimedia via webview/assetimporters/designer/...) that the app
    # never imports — that was the 34M -> 159M zip bloat.
    _plugins = _os.path.join(_ps_dir, "plugins")
    _need_plugins = (
        "platforms",  # qwindows.dll — without it: "no Qt platform plugin..."
        "styles",  # qmodernwindowsstyle.dll (Fusion/native)
        "imageformats",  # qjpeg/qico/qsvg (stickers, QSS png assets)
        "iconengines",  # qsvgicon.dll
        "tls",  # QtNetwork backends (qschannel/qopenssl)
        "generic",  # qtuiotouchplugin
        "networkinformation",  # QtNetwork backend
        "platforminputcontexts",  # input method
        "vectorimageformats",  # qsvg fallback
    )
    if _os.path.isdir(_plugins):
        for _sub in _need_plugins:
            _src = _os.path.join(_plugins, _sub)
            if _os.path.isdir(_src):
                datas.append((_src, "PySide6/plugins/" + _sub))
    # Some distributions use PySide6/Qt/plugins instead — cover both.
    _qt_plugins = _os.path.join(_ps_dir, "Qt", "plugins")
    if _os.path.isdir(_qt_plugins):
        for _sub in _need_plugins:
            _src = _os.path.join(_qt_plugins, _sub)
            if _os.path.isdir(_src):
                datas.append((_src, "PySide6/Qt/plugins/" + _sub))
    # Helper runtime DLLs that live next to the Qt6*.dlls (software OpenGL,
    # ANGLE, D3D compiler). The hook normally grabs these; explicit is safe.
    for _pat in ("opengl32sw.dll", "d3dcompiler_*.dll", "libEGL.dll",
                 "libGLESv2.dll", "Qt6OpenGL.dll", "Qt6Svg.dll"):
        for _dll in _glob.glob(_os.path.join(_ps_dir, _pat)):
            binaries.append((_dll, "PySide6"))

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtPrintSupport",  # function-level import in factory/slip_pdf.py
    "PySide6.QtSvg",  # QSS / imageformat fallback path
    "PySide6.QtNetwork",  # pulled by Qt print / TLS plugin chain
    "serial",
    "qrcode",
    "PIL",
]

a = Analysis(
    [_os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

_icon = _os.path.join(ROOT, "factory", "ui", "assets", "simpld.ico")

# ---- exe version resource (Windows Properties > Details) ----
# Generated from factory/version.py at build time: the exe metadata, the
# installer, the About dialog and the release tag must never disagree.
# (A static version_info.txt drifted once: it still said 1.0.0 while the
# app said 1.0.1, so Properties > Details showed the wrong version.)
import importlib.util as _ilu
_vspec = _ilu.spec_from_file_location(
    "_simpld_version", _os.path.join(ROOT, "factory", "version.py"))
_vmod = _ilu.module_from_spec(_vspec)
_vspec.loader.exec_module(_vmod)
_ver_str = str(_vmod.VERSION)
_ver_parts = tuple(([int(x) for x in _ver_str.split(".")] + [0, 0, 0, 0])[:4])
_ver = _os.path.join(
    globals().get("workpath") or _os.path.join(ROOT, "build", "simpld"),
    "version_info.generated.txt")
_os.makedirs(_os.path.dirname(_ver), exist_ok=True)
with open(_ver, "w", encoding="utf-8") as _fh:
    _fh.write(
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={_ver_parts!r},\n"
        f"    prodvers={_ver_parts!r},\n"
        "    mask=0x3f,\n"
        "    flags=0x0,\n"
        "    OS=0x40004,\n"
        "    fileType=0x1,\n"
        "    subtype=0x0,\n"
        "    date=(0, 0),\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo(\n"
        "      [\n"
        "        StringTable(\n"
        "          '040904B0',\n"
        "          [\n"
        "            StringStruct('CompanyName', \"Blur'd\"),\n"
        "            StringStruct('FileDescription', \"Simpl'd \u2014 inventory and dispatch\"),\n"
        f"            StringStruct('FileVersion', '{_ver_str}'),\n"
        "            StringStruct('InternalName', 'Simpld'),\n"
        "            StringStruct('LegalCopyright', \"Blur'd\"),\n"
        "            StringStruct('OriginalFilename', 'Simpld.exe'),\n"
        "            StringStruct('ProductName', \"Simpl'd\"),\n"
        f"            StringStruct('ProductVersion', '{_ver_str}'),\n"
        "          ],\n"
        "        ),\n"
        "      ],\n"
        "    ),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])]),\n"
        "  ],\n"
        ")\n")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Simpld",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI demo: no console window behind the app
    icon=_icon if _os.path.isfile(_icon) else None,
    version=_ver,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Simpld",
)
