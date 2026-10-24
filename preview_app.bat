@echo off
chcp 65001 >nul
cd /d "%~dp0mobile"
if not exist "node_modules\vite\bin\vite.js" (
  echo Устанавливаем локальные пакеты приложения...
  call npm ci
  if errorlevel 1 goto :error
)
echo.
echo Предпросмотр КАЛИБР доступен только на этом компьютере: http://127.0.0.1:5173
 echo Чтобы загрузить каталог и проверить синхронизацию, отдельно запустите run.bat.
echo Для остановки нажмите Ctrl+C.
call npm run dev:local
goto :end
:error
echo Не удалось подготовить браузерный предпросмотр. Проверьте Node.js и npm.
pause
exit /b 1
:end
