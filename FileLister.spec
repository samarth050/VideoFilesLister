# -*- mode: python ; coding: utf-8 -*-
#
# FileLister Portable - PyInstaller specification
# Phase 6: package the Phase 2-5 UI assets into the one-file EXE.
#
# app.py uses RESOURCE_DIR for bundled read-only assets and APP_DIR for
# user-writable files/databases beside the executable.

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('FileLister.ico', '.'),
        ('assets/FileLister.png', 'assets'),
        ('assets/FileLister_header.png', 'assets'),
        ('assets/FileLister_watermark.png', 'assets'),
        ('assets/tab_icons/files.png', 'assets/tab_icons'),
        ('assets/tab_icons/folder.png', 'assets/tab_icons'),
        ('assets/tab_icons/stats.png', 'assets/tab_icons'),
        ('assets/tab_icons/database.png', 'assets/tab_icons'),
        ('assets/tab_icons/gallery.png', 'assets/tab_icons'),
        ('assets/tab_icons/movie.png', 'assets/tab_icons'),
        ('assets/tab_icons/update.png', 'assets/tab_icons'),
        ('assets/tab_icons/download.png', 'assets/tab_icons'),
        ('assets/tab_icons/duplicates.png', 'assets/tab_icons'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='FileLister',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['FileLister.ico'],
)
