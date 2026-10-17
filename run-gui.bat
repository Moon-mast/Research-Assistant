@echo off
rem 双击启动 harness 桌面窗口（pythonw 隐藏控制台）
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [提示] 尚未创建虚拟环境，请先执行：
    echo     py -3.12 -m venv .venv
    echo     .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist ".env" (
    echo [提示] 未找到 .env，可先复制 .env.example 为 .env 并填入密钥。
    echo        没配也能打开窗口，只是对话会报错。
)

start "" ".venv\Scripts\pythonw.exe" -m harness.gui
