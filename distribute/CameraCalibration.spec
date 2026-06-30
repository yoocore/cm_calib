# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('E:\\Coding\\VibeCoding\\cm_calib\\distribute\\..\\src\\gui_app\\icon.svg', 'src\\gui_app')]
binaries = []
hiddenimports = ['src.entry.launch_gui', 'src.gui_app', 'src.gui_app.app', 'src.gui_app.main_window', 'src.health.verify_runtime_chain_baseline']
tmp_ret = collect_all('PySide6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['E:\\Coding\\VibeCoding\\cm_calib\\distribute\\..\\src\\entry\\launch_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['cmapi', 'apoc', 'infofiles', 'IPG'],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [('O', None, 'OPTION'), ('O', None, 'OPTION')],
    exclude_binaries=True,
    name='CameraCalibration',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CameraCalibration',
)
