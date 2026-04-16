# -*- mode: python ; coding: utf-8 -*-

import os
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['__main__.py'],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=[],
    hiddenimports=[
        'win32gui', 'win32ui', 'win32con',
        'bootstrap', 'tm', 'gui', 'capture',
        'learn', 'ocr_suggest', 'transform',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'scipy', 'pandas', 'matplotlib', 'sklearn', 'sympy',
        'cv2', 'IPython', 'jupyter', 'notebook',
        'tensorflow', 'keras',
        'pytest', 'unittest',
    ],
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
    name='ohlearn',
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
    icon=os.path.join(SPEC_DIR, 'poker_chip.ico'),
)
