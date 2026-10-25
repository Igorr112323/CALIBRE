import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MobileAppStageThreeTests(unittest.TestCase):
    def test_mobile_has_no_redundant_bottom_navigation_or_settings_panel(self):
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        html = (ROOT / "web/app/index.html").read_text(encoding="utf-8")
        self.assertNotIn('<nav class="bottom-nav"', html)
        self.assertNotIn('data-action="catalog"', html)
        self.assertNotIn('data-action="settings"', html)
        self.assertNotIn('settings-shortcut', html)
        self.assertNotIn('statusStrip', html)
        self.assertNotIn("renderCatalog", app)
        self.assertNotIn('data-action="catalog"', app)
        self.assertNotIn('renderSettings', app)
        self.assertNotIn('renderStatus', app)

    def test_mobile_uses_only_prefilled_grn471_demo_content_in_frozen_shell(self):
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        html = (ROOT / "web/app/index.html").read_text(encoding="utf-8")
        css = (ROOT / "web/app/app.css").read_text(encoding="utf-8")
        data = (ROOT / "web/app/grn471-demo.json").read_text(encoding="utf-8")
        self.assertIn("./grn471-demo.json", app)
        self.assertIn("ГРН-471 Kolibri (демо)", data)
        self.assertIn("59", data)
        self.assertIn("auto_verified", app)
        self.assertIn("renderScheme", app)
        self.assertIn('class="scheme"', app)
        self.assertNotIn("TEST_MACHINES", app)
        self.assertNotIn("МТЗ-82.1", app)
        self.assertNotIn("Кировец К-700А", app)
        self.assertNotIn("СИНТЕТИЧЕСКИЕ ДАННЫЕ", app)
        self.assertNotIn(" · офлайн", html)
        self.assertNotIn(".status-strip", css)
        self.assertNotIn(".notice", css)

    def test_kolibri_splash_uses_the_brand_video_and_waits_for_app_readiness(self):
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        html = (ROOT / "web/app/index.html").read_text(encoding="utf-8")
        css = (ROOT / "web/app/app.css").read_text(encoding="utf-8")
        video = (ROOT / "web/app/kolibri-intro.mp4").read_bytes()
        self.assertIn('id="launchAnimation" autoplay muted playsinline', html)
        self.assertIn('src="./kolibri-intro.mp4"', html)
        self.assertIn("launchAnimation.addEventListener('ended'", app)
        self.assertIn("launchAnimation.addEventListener('error'", app)
        self.assertIn("if (!appReadyForIntro || !introAnimationFinished)", app)
        self.assertIn("background:#f7f8f1", css)
        self.assertEqual(video[4:8], b"ftyp")
        self.assertLess(len(video), 2_000_000)

    def test_offline_storage_and_queue_support_remains_in_mobile_source(self):
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        self.assertIn("IndexedDB в браузере", app)
        self.assertIn("localStorageFallback", app)
        self.assertIn("@capacitor-community/sqlite", app)
        self.assertIn("@capacitor/network", app)
        self.assertIn("feedbackQueue", app)
        self.assertIn("questionQueue", app)
        self.assertIn("networkStatusChange", app)

    def test_app_brand_is_kolibri_in_android_and_interface(self):
        html = (ROOT / "web/app/index.html").read_text(encoding="utf-8")
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        capacitor = (ROOT / "mobile/capacitor.config.json").read_text(encoding="utf-8")
        strings = (ROOT / "mobile/android/app/src/main/res/values/strings.xml").read_text(encoding="utf-8")
        self.assertIn('appName": "Kolibri"', capacitor)
        self.assertIn('<string name="app_name">Kolibri</string>', strings)
        self.assertIn('<span class="logo">Kolibri</span>', html)
        self.assertIn("return ['Kolibri'", app)
        self.assertNotIn('class="logo">КАЛИБР', html)

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
        self.assertIn("Kolibri-GRN-471-demo.exe", workflow)
        self.assertIn("--self-test", workflow)
        self.assertIn("--self-test", launcher)
        self.assertTrue((ROOT / "licenses" / "PyInstaller-COPYING.txt").is_file())
        self.assertIn("licenses/PyInstaller-COPYING.txt", workflow)

    def test_android_allows_local_http_and_one_command_apk_output_is_configured(self):
        manifest = (ROOT / "mobile/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        batch = (ROOT / "build_apk.bat").read_text(encoding="utf-8")
        self.assertIn('android:usesCleartextTraffic="true"', manifest)
        self.assertIn("dist\\Kolibri-GRN-471-demo.apk", batch)
        self.assertIn("assembleDebug", batch)

    def test_internal_lan_photos_are_cached_without_a_new_mobile_screen(self):
        app = (ROOT / "web/app/app.js").read_text(encoding="utf-8")
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("async function syncInternalMedia()", app)
        self.assertIn("mediaCache", app)
        self.assertIn("/api/mobile/media/{photo_id}", server)
        self.assertIn("X-Calibre-Internal-LAN", server)
        self.assertNotIn("screen: 'chat'", app)

    def test_demo_seed_uses_cited_packaged_content_without_pdf_assets(self):
        seed = (ROOT / "calibre/demo.py").read_text(encoding="utf-8")
        data = (ROOT / "web/app/grn471-demo.json").read_text(encoding="utf-8")
        self.assertIn("GRN-471-KOLIBRI-V4-DEMO", seed)
        self.assertIn("packaged_demo", seed)
        self.assertIn("59", data)
        self.assertIn("auto_verified", (ROOT / "calibre/models.py").read_text(encoding="utf-8"))
        self.assertNotIn("DEMO-ONLY-01", data)
        self.assertFalse((ROOT / "web/app/GRN-471-Kolibri-v4.pdf").exists())


if __name__ == "__main__":
    unittest.main()
