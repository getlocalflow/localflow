# -*- mode: python -*-
# Build (from repo root):  cd windows-installer && pyinstaller localflow-win.spec
#
# ROOT resolution note (verified against PyInstaller 6.x source,
# PyInstaller/building/api.py COLLECT.assemble / EXE.contents_directory):
#   core.config computes ROOT = Path(__file__).resolve().parent.parent.
#   In a frozen onedir build, core.config's __file__ is faked by the loader
#   to sys._MEIPASS/core/config.py (PyInstaller/loader/pyimod02_importers.py).
#   EXE() defaults contents_directory="_internal", and COLLECT places every
#   TOC entry except the EXECUTABLE/PKG under that contents_directory - which
#   is also where the bootloader points sys._MEIPASS in onedir mode. So
#   ROOT == dist/LocalFlow/_internal at runtime, and the datas destinations
#   below (relative to the COLLECT root, i.e. "_internal") land exactly on
#   ROOT/sounds, ROOT/windows/app_modes.windows.json, and ROOT/<samples>.
#   No "_internal/" prefix belongs in these dest paths - PyInstaller inserts
#   it automatically. CI has a Test-Path assertion step that verifies this
#   empirically against the built tree.
import os

block_cipher = None

a = Analysis(
    ["../windows/localflow_win.py"],
    pathex=[".."],
    datas=[
        ("../sounds", "sounds"),
        ("../windows/app_modes.windows.json", "windows"),
        ("../dictionary.sample.txt", "."),
        ("../replacements.sample.json", "."),
        ("../config.sample.toml", "."),
    ],
    hiddenimports=["pystray._win32", "winsound"],
    hookspath=[],
    excludes=["macos"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LocalFlow",
    icon="localflow.ico",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="LocalFlow")
