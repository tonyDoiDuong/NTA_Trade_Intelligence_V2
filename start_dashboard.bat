@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Khong tim thay Python. Cai Python 3.11+ va chon Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Tao moi truong Python...
  %PY% -m venv .venv
  if errorlevel 1 goto error
)
echo Cai/kiem tra thu vien Python...
".venv\Scripts\python.exe" -m pip install -r "bridge\requirements.txt"
if errorlevel 1 goto error
echo.
echo Mo Chrome/Edge vao http://127.0.0.1:8000
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn bridge.server:app --host 127.0.0.1 --port 8000
pause
exit /b 0
:error
echo Cai dat that bai. Doc docs\HUONG_DAN.txt
pause
exit /b 1
