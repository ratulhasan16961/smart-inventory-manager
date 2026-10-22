# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for Enterprise POS.   Build:  pyinstaller --clean --noconfirm pos_erp.spec

Windows -> dist/EnterprisePOS.exe (one file).   macOS -> dist/EnterprisePOS.app (one bundle).
matplotlib is left out to keep the app small: the Analytics window then shows its text breakdown instead of a pie.
To include it:  pip install matplotlib  and delete "matplotlib" from EXCLUDES below.
"""
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "pos_erp" / "__init__.py").read_text()).group(1)
ICON_FILE = ROOT / "assets" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")
ICON = str(ICON_FILE) if ICON_FILE.exists() else None            # optional: put an icon in assets/
EXCLUDES = ["matplotlib", "numpy", "PIL", "pytest"]

datas = [(str(ROOT / "LICENSE"), "."), (str(ROOT / "README.md"), ".")]
if (ROOT / "assets").exists():
    datas.append((str(ROOT / "assets"), "assets"))

a = Analysis(
    ["inventory_app.py"], pathex=[str(ROOT)], binaries=[], datas=datas,
    hiddenimports=collect_submodules("pos_erp"),                  # includes pos_erp.__main__, loaded through runpy
    excludes=EXCLUDES, noarchive=False)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EnterprisePOS", console=False, icon=ICON)
    coll = COLLECT(exe, a.binaries, a.datas, name="EnterprisePOS")
    app = BUNDLE(coll, name="EnterprisePOS.app", icon=ICON, bundle_identifier="io.github.ratulhasan16961.pos-erp",
                 version=VERSION, info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": VERSION})
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="EnterprisePOS", debug=False, strip=False, upx=False,
              runtime_tmpdir=None, console=False, icon=ICON)