# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Richmor MDVR CONFIG desktop app (richmor_config.* toolset).
#   build:  pyinstaller richmor_config_app.spec
# Produces a single windowed binary that runs the aiohttp server + a native webview window.
# Note: builds for the OS you run it on (Windows -> .exe, Linux -> ELF, macOS -> Mach-O). Not a cross-compiler.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_all

# ── data files served to the webview (must ship inside the bundle) ──
datas = [
    ('richmor_config.html', '.'),
    ('richmor_codec_jt808.js', '.'),
    ('richmor_codec_ttx.js', '.'),
    ('richmor_codec_native.js', '.'),
]
if os.path.isdir('assets'):
    datas += [('assets', 'assets')]

# ffmpeg static binary (imageio-ffmpeg) — used to mux clip downloads
datas += collect_data_files('imageio_ffmpeg')

# pywebview: bundle its data, native binaries and backend hidden imports
wv_datas, wv_binaries, wv_hidden = collect_all('webview')
datas += wv_datas

hiddenimports = wv_hidden + ['imageio_ffmpeg']

a = Analysis(
    ['richmor_config_app.py'],
    pathex=[],
    binaries=wv_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RichmorConfig',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,            # windowed app (set True temporarily if you need to see logs while debugging)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/favicon.ico',
)
