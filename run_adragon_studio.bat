@echo off
chcp 65001 > nul
echo ========================================================
echo    🚀 KHỞI ĐỘNG ADRAGON STUDIO (MULTI-SERVICE HUB)
echo ========================================================
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set HF_HUB_OFFLINE=1
set VIENEU_PRECISION=int8
set PORT=8000

if exist .venv\Scripts\python.exe (
    set PY_EXE=.venv\Scripts\python.exe
) else (
    set PY_EXE=python
)

echo Đang khởi chạy Adragon Studio tại http://localhost:8000 ...
"%PY_EXE%" -u -m apps.unified_server
pause
