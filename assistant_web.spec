# -*- mode: python ; coding: utf-8 -*-
# 打包「科研小助手 · HTML 内核桌面版」成单文件 exe（pywebview + Edge WebView2）。
# 用法：
#   .venv\Scripts\pyinstaller --clean --noconfirm assistant_web.spec
# 产物：dist\科研小助手.exe
# 说明：需要机器上装有 WebView2 Runtime（Win10/11 一般自带）；pythonnet/pywebview
#       的模块、程序集由 pyinstaller-hooks-contrib 的 hook-webview/hook-clr 自动带入。
#       skills/ 与 galaxy-main/ 作为数据文件捆绑（_MEIPASS 下解出同路径），
#       保证 research-assistant / galaxy-ui 技能开箱即用。

import os

block_cipher = None
ROOT = os.path.dirname(os.path.abspath(SPEC))


def tree(src_root, dest_root):
    """递归收集一个目录下所有文件为 PyInstaller datas 项。"""
    items = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, src_root)):
        rel = os.path.relpath(dirpath, ROOT)
        for fn in filenames:
            items.append((os.path.join(dirpath, fn), os.path.join(dest_root, os.path.relpath(dirpath, src_root))))
    return items


datas = [
    (os.path.join(ROOT, "assets", "assistant_ui.html"), "assets"),
    (os.path.join(ROOT, "assets", "assistant.ico"), "assets"),
]
datas += tree("skills", "skills")
datas += tree("galaxy-main", "galaxy-main")

a = Analysis(
    ["assistant_web.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "clr",                       # pythonnet：edgechromium 后端在运行时 import，需静态带上触发 hook-clr
        "clr_loader",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["test", "unittest", "cv2", "tkinter"],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="科研小助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, "assets", "assistant.ico"),
    disable_windowed_traceback=False,
)
