@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установите Python 3.11 или новее и отметьте Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Создаём изолированное окружение Python...
  python -m venv .venv
  if errorlevel 1 goto :error
)
".venv\Scripts\python.exe" ensure_dependencies.py
if errorlevel 1 goto :error
".venv\Scripts\python.exe" server.py
pause
exit /b 0
:error
echo Не удалось подготовить запуск. Проверьте интернет и текст ошибки выше.
pause
exit /b 1
