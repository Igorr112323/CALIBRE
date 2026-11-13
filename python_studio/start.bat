@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
set "PYTHON="
py -3.12 -c "import sys; sys.exit(sys.maxsize < 2**32)" >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3.12"
if not defined PYTHON (
  py -3.11 -c "import sys; sys.exit(sys.maxsize < 2**32)" >nul 2>&1
  if not errorlevel 1 set "PYTHON=py -3.11"
)
if not defined PYTHON (
  py -3.10 -c "import sys; sys.exit(sys.maxsize < 2**32)" >nul 2>&1
  if not errorlevel 1 set "PYTHON=py -3.10"
)
if not defined PYTHON (
  python -c "import sys; sys.exit(not ((3,10) <= sys.version_info[:2] <= (3,12)) or sys.maxsize < 2**32)" >nul 2>&1
  if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
  echo Python 3.10, 3.11 or 3.12 x64 was not found. Install Python from python.org.
  pause
  exit /b 1
)
%PYTHON% -m venv .venv
if errorlevel 1 (
  echo Could not create the virtual environment.
  pause
  exit /b 1
)
:run
".venv\Scripts\python.exe" launcher.py
if errorlevel 1 (
  echo Kolibri Studio could not start. See the error above.
  pause
  exit /b 1
)
