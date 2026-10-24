import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MobileAppStageThreeTests(unittest.TestCase):
    def test_mobile_navigation_has_only_home_and_machines_and_no_settings_panel(self):
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        html = (ROOT / "web/app/index.html").read_text(encoding="utf-8")
        self.assertIn('data-action="home"', html)
        self.assertIn('data-action="catalog"', html)
        navigation = html.split('<nav class="bottom-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertEqual(navigation.count('<button'), 2)
        self.assertNotIn('data-action="settings"', html)
        self.assertNotIn('settings-shortcut', html)
        self.assertNotIn('statusStrip', html)
        self.assertNotIn('renderSettings', app)
        self.assertNotIn('renderStatus', app)

    def test_mobile_has_two_empty_test_machines_and_requested_home_messages_are_removed(self):
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        html = (ROOT / "web/app/index.html").read_text(encoding="utf-8")
        css = (ROOT / "web/app/app.css").read_text(encoding="utf-8")
        self.assertIn("model: 'Машина 1'", app)
        self.assertIn("model: 'Машина 2'", app)
        self.assertEqual(app.count("model: 'Машина "), 2)
        self.assertNotIn("ДЕМО", app)
        self.assertNotIn("demo", app.lower())
        self.assertNotIn(" · офлайн", html)
        self.assertNotIn(" · офлайн", app)
        self.assertNotIn("Поиск работает по сохранённым карточкам", app)
        self.assertNotIn("Перед настройкой сверяйтесь", app)
        self.assertNotIn("Хранилище:", app)
        self.assertNotIn(".status-strip", css)
        self.assertNotIn(".notice", css)

    def test_offline_storage_and_queue_support_remains_in_mobile_source(self):
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        self.assertIn("IndexedDB в браузере", app)
        self.assertIn("localStorageFallback", app)
        self.assertIn("@capacitor-community/sqlite", app)
        self.assertIn("@capacitor/network", app)
        self.assertIn("feedbackQueue", app)
        self.assertIn("questionQueue", app)
        self.assertIn("networkStatusChange", app)

    def test_pc_preview_is_bound_to_loopback_and_proxies_only_to_local_server(self):
        config = (ROOT / "mobile/vite.config.mjs").read_text(encoding="utf-8")
        batch = (ROOT / "preview_app.bat").read_text(encoding="utf-8")
        self.assertIn("host: '127.0.0.1'", config)
        self.assertIn("target: 'http://127.0.0.1:8765'", config)
        self.assertNotIn("host: '0.0.0.0'", config)
        self.assertIn("http://127.0.0.1:5173", batch)

    def test_windows_launcher_is_onefile_with_loopback_preview_and_auto_opens_browser(self):
        launcher = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/build-android.yml").read_text(encoding="utf-8")
        self.assertIn("webbrowser.open(url", launcher)
        self.assertIn('host="127.0.0.1"', launcher)
        self.assertIn('host="0.0.0.0"', launcher)
        self.assertIn("create_app(enable_preview=False)", launcher)
        self.assertIn("LOCALAPPDATA", launcher)
        self.assertIn("--onefile", workflow)
        self.assertIn("CALIBRE-PC.exe", workflow)
        self.assertIn("--self-test", workflow)
        self.assertIn("--self-test", launcher)
        self.assertTrue((ROOT / "licenses" / "PyInstaller-COPYING.txt").is_file())
        self.assertIn("licenses/PyInstaller-COPYING.txt", workflow)

    def test_android_allows_local_http_and_one_command_apk_output_is_configured(self):
        manifest = (ROOT / "mobile/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        batch = (ROOT / "build_apk.bat").read_text(encoding="utf-8")
        self.assertIn('android:usesCleartextTraffic="true"', manifest)
        self.assertIn("dist\\CALIBRE-debug.apk", batch)
        self.assertIn("assembleDebug", batch)

    def test_demo_seed_contains_only_explicit_non_instructional_placeholders(self):
        seed = (ROOT / "calibre/demo.py").read_text(encoding="utf-8")
        self.assertIn("ДЕМО", seed)
        for unsafe_sample in ("485 мм", "200–300 Н", "700–900 Н", "0,12–0,55 мм", "15 км/ч", "30 км/ч"):
            self.assertNotIn(unsafe_sample, seed)


if __name__ == "__main__":
    unittest.main()
