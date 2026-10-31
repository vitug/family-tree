# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: однофайловый FamilyTree.exe (см. docs/BUILD_DESKTOP.md)
# Сборка: pyinstaller family_tree.spec

a = Analysis(
    ["desktop_main.py"],
    pathex=[],
    binaries=[],
    datas=[
        # статика (SPA, js, css, иконки PWA) — кладётся рядом с app/ внутри бандла
        ("app/static", "app/static"),
    ],
    hiddenimports=[
        "app.main",
        "app.routers.auth",
        "app.routers.export_data",
        "app.routers.import_data",
        "app.routers.media",
        "app.routers.persons",
        "app.routers.relationships",
        "app.routers.tree",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "sqlalchemy.dialects.sqlite",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FamilyTree",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # консоль нужна: там печатается EDIT-ссылка и Ctrl+C
    icon=None,  # можно "app/static/icons/prod-512.png" после конвертации в .ico
)
