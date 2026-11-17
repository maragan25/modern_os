@echo off
rem Starts and checks the whole project (see README, section 8).
rem   start.bat           check everything, then start subscriber.py and api.py
rem   start.bat --check   only look, change nothing
rem   start.bat --stop    stop subscriber.py and api.py
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo There is no .venv folder yet. Create it once, in this folder:
  echo    py -m venv .venv
  echo    .venv\Scripts\pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" start.py %*
echo.
pause
