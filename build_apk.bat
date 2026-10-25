@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js LTS не найден. Установите Node.js LTS, перезапустите командную строку и повторите.
  goto :error
)
where npm >nul 2>nul
if errorlevel 1 (
  echo npm не найден. Переустановите Node.js LTS с включённым npm.
  goto :error
)
if not exist "mobile\android\gradlew.bat" (
  echo Не найден Gradle Wrapper: mobile\android\gradlew.bat
  goto :error
)
echo [1/5] Установка закреплённых зависимостей приложения...
cd /d "%~dp0mobile"
call npm ci
if errorlevel 1 goto :error

echo [2/5] Сборка локального интерфейса...
call npm run build
if errorlevel 1 goto :error

echo [3/5] Подключение Android-плагинов Capacitor...
call npx cap sync android
if errorlevel 1 goto :error

echo [4/5] Сборка отладочного APK. При первом запуске Gradle может загрузить компоненты Android.
call android\gradlew.bat -p android assembleDebug
if errorlevel 1 goto :error

echo [5/5] Копирование APK в dist...
cd /d "%~dp0"
if not exist "dist" mkdir "dist"
copy /Y "mobile\android\app\build\outputs\apk\debug\app-debug.apk" "dist\Kolibri-GRN-471-demo.apk" >nul
if errorlevel 1 goto :error
echo.
echo Готово: %~dp0dist\Kolibri-GRN-471-demo.apk
echo Это отладочная сборка для установки и проверки, не публикационный релиз.
pause
exit /b 0
:error
cd /d "%~dp0"
echo.
echo Сборка остановлена. Сверьтесь с разделом «Сборка APK» в README.md.
pause
exit /b 1
