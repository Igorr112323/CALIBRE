# Android-приложение Kolibri

Офлайн-справочник сельхозтехники и регулировок для факультета механизации и механизаторов. Каталог ГРН-471 упакован в APK; сервер и сетевое подключение не нужны. Материалы автоматически проверены, но не сверены человеком: руководствуйтесь документацией производителя и правилами безопасности.

## Сборка debug APK

Установите Node.js 22, JDK 21 и Android SDK (compile SDK 36), затем выполните:

```sh
cd mobile
npm ci
npm run build
npx cap sync android
cd android
./gradlew assembleDebug
```

Результат: `android/app/build/outputs/apk/debug/app-debug.apk`. В Windows можно запустить корневой `build_apk.bat`, который создаёт `dist/Kolibri-Mehfak.apk`. Для установки debug APK может потребоваться разрешить установку из выбранного источника.

Gradle Wrapper включает код Gradle, Copyright 2015–2021 the original authors, распространяемый по лицензии [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

## Веб-стенд

Из `mobile/` выполните `npm ci`, затем `npx vite --config vite.config.mjs`. В браузере откроется интерактивный макет телефона с кнопками отладки и ссылкой на последний APK. На Android внешняя панель не отображается. `npm run build:stand` создаёт автономный файл `dist/Kolibri-stend.html`, который можно открыть в браузере без сервера.
