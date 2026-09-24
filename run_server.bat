@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install_server.bat first.
  pause
  exit /b 1
)
if "%MODERATOR_PASSWORD%"=="" set /p MODERATOR_PASSWORD=Enter a new moderator password:
if "%MODERATOR_PASSWORD%"=="" (
  echo Password is required.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
endlocal
