# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the pydbplay desktop app.
# Build with:  uv run pyinstaller pydbplay.spec
# Output:      dist/pydbplay.app

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Hidden imports that PyInstaller cannot auto-detect — uvicorn/fastapi/keyring
# dynamically load these by string name, so we collect them explicitly.
hidden = []
hidden += collect_submodules("uvicorn")
hidden += collect_submodules("fastapi")
hidden += collect_submodules("starlette")
hidden += collect_submodules("sqlalchemy.dialects")
hidden += collect_submodules("pydbplay")
hidden += [
    "psycopg",
    "psycopg_binary",
    "pymysql",
    "sqlglot",
    "keyring.backends.macOS",
    "keyring.backends.fail",
    "keyring.backends.null",
    "pydantic_settings",
]

# Bundle templates, static assets, and SQLite migrations — accessed at runtime
# via Path(__file__).parent in the FastAPI app.
datas = [
    ("pydbplay/app/static", "pydbplay/app/static"),
    ("pydbplay/app/templates", "pydbplay/app/templates"),
    ("pydbplay/db/migrations", "pydbplay/db/migrations"),
]
datas += collect_data_files("webview")  # pywebview ships some JS/HTML resources

a = Analysis(
    ["pydbplay/desktop.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pydbplay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no terminal window — GUI app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pydbplay",
)

app = BUNDLE(
    coll,
    name="pydbplay.app",
    icon=None,
    bundle_identifier="dev.pydbplay.app",
    info_plist={
        "CFBundleName": "pydbplay",
        "CFBundleDisplayName": "pydbplay",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
    },
)
