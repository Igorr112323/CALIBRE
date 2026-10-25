"""Self-contained Windows launcher: local browser preview plus a phone-only LAN API."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path


def configure_user_data() -> None:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "CALIBRE"
    else:
        base = Path(__file__).resolve().parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    os.environ["CALIBRE_DATA_DIR"] = str(base)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def free_port(host: str, preferred: int = 0) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, preferred))
        return int(listener.getsockname()[1])


def run_self_test() -> None:
    import uvicorn
    import server as server_module

    port = free_port("127.0.0.1")
    local_server = uvicorn.Server(uvicorn.Config(
        server_module.app, host="127.0.0.1", port=port,
        log_level="critical", access_log=False, log_config=None,
    ))
    thread = threading.Thread(target=local_server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not local_server.started:
            if not thread.is_alive():
                raise RuntimeError("Windows EXE self-test: сервер не запустился")
            time.sleep(0.1)
        if not local_server.started:
            raise TimeoutError("Windows EXE self-test: превышено время запуска")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/", timeout=10) as response:
            html = response.read().decode("utf-8")
            if response.status != 200 or "./assets/" not in html:
                raise RuntimeError("Windows EXE self-test: встроенная HTML-страница не открылась")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/admin", timeout=10) as response:
            admin_html = response.read().decode("utf-8")
            if response.status != 200 or "Kolibri — панель знаний" not in admin_html:
                raise RuntimeError("Windows EXE self-test: админ-панель Kolibri не открылась")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/admin/static/admin/admin.js", timeout=10) as response:
            admin_js = response.read().decode("utf-8")
            if (response.status != 200 or "/api/admin/nodes" not in admin_js
                    or "/api/admin/media" not in admin_js
                    or "/api/admin/assistant/answer" not in admin_js):
                raise RuntimeError("Windows EXE self-test: ресурсы панели техники и диагностики не загрузились")
        if "Проверить неисправность по руководству" not in admin_html:
            raise RuntimeError("Windows EXE self-test: форма диагностики неисправностей не загрузилась")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/admin/static/admin/kolibri-mark.png", timeout=10) as response:
            if response.status != 200 or not response.headers.get("Content-Type", "").startswith("image/png"):
                raise RuntimeError("Windows EXE self-test: логотип Kolibri не загрузился")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/admin/assistant/status", timeout=10) as response:
            ai_status = json.loads(response.read())
            if ai_status.get("reason") != "not_configured":
                raise RuntimeError("Windows EXE self-test: безопасный статус ИИ не получен")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/mobile/sync", timeout=10) as response:
            payload = json.loads(response.read())
        if not payload.get("changed") or not payload.get("catalog", {}).get("demo_machines"):
            raise RuntimeError("Windows EXE self-test: демонстрационный каталог не загрузился")
    finally:
        local_server.should_exit = True
        thread.join(timeout=5)


def main() -> None:
    configure_user_data()
    if "--self-test" in sys.argv:
        try:
            run_self_test()
        except BaseException:
            log_path = Path(os.environ["CALIBRE_DATA_DIR"]) / "self-test.log"
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
            raise
        return
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("Kolibri — экспертный помощник для ПК")
    root.geometry("470x320")
    root.resizable(False, False)
    root.configure(bg="#eef2ef")

    state: dict[str, object] = {"servers": [], "threads": [], "error": None, "ready": False, "page_url": "", "admin_url": "", "phone_url": ""}
    tk.Label(root, text="Kolibri", font=("Segoe UI", 17, "bold"), fg="white", bg="#2e7d32", pady=11).pack(fill="x")
    status = tk.Label(root, text="Готовим страницу и локальную базу…", font=("Segoe UI", 11), bg="#eef2ef", fg="#1b1f1d", wraplength=440)
    status.pack(pady=(13, 5))

    tk.Label(root, text="Админ-панель Kolibri на этом компьютере", font=("Segoe UI", 9, "bold"), fg="#5f6b64", bg="#eef2ef").pack()
    page_address = tk.StringVar(value="Запуск…")
    tk.Label(root, textvariable=page_address, font=("Segoe UI", 10, "underline"), fg="#2e7d32", bg="#eef2ef").pack(pady=(1, 6))

    tk.Label(root, text="Адрес API для приложения на телефоне (та же Wi-Fi-сеть)", font=("Segoe UI", 9, "bold"), fg="#5f6b64", bg="#eef2ef").pack()
    phone_address = tk.StringVar(value="Определяем адрес компьютера…")
    tk.Label(root, textvariable=phone_address, font=("Segoe UI", 10), fg="#1b1f1d", bg="#eef2ef").pack(pady=(1, 6))

    help_text = tk.Label(root, text="Предпросмотр браузера доступен только на этом ПК.\nДля связи с телефоном разрешите частную сеть в Windows Firewall.\nЗакройте это окно, чтобы остановить Kolibri.", font=("Segoe UI", 8), fg="#5f6b64", bg="#eef2ef", justify="center")
    help_text.pack(pady=(0, 7))

    buttons = tk.Frame(root, bg="#eef2ef")
    buttons.pack(pady=2)

    def open_page() -> None:
        url = str(state.get("admin_url") or "")
        if url:
            webbrowser.open(url, new=2, autoraise=True)

    def copy_phone_address() -> None:
        value = str(state.get("phone_url") or "")
        if value and "НЕ НАЙДЕН" not in value:
            root.clipboard_clear()
            root.clipboard_append(value)
            status.configure(text="Адрес телефона скопирован.", fg="#2e7d32")

    open_button = tk.Button(buttons, text="Открыть админку", command=open_page, state="disabled", width=17, bg="#2e7d32", fg="white", relief="flat", padx=5, pady=5)
    open_button.pack(side="left", padx=4)
    copy_button = tk.Button(buttons, text="Копировать адрес телефона", command=copy_phone_address, state="disabled", width=24, padx=5, pady=5)
    copy_button.pack(side="left", padx=4)

    def stop_and_close() -> None:
        for server in state.get("servers", []):
            server.should_exit = True
        root.destroy()

    tk.Button(buttons, text="Закрыть", command=stop_and_close, width=9, padx=5, pady=5).pack(side="left", padx=4)
    root.protocol("WM_DELETE_WINDOW", stop_and_close)

    def start_servers() -> None:
        try:
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            required = [
                bundle_root / "alembic.ini",
                bundle_root / "migrations" / "versions" / "6a404288fafc_initial_catalogue_schema.py",
                bundle_root / "web" / "admin" / "index.html",
                bundle_root / "mobile" / "www" / "index.html",
            ]
            missing = [str(path) for path in required if not path.is_file()]
            if missing:
                raise FileNotFoundError("В приложении отсутствуют встроенные ресурсы:\n" + "\n".join(missing))

            import uvicorn
            import server as server_module

            # Separate listeners: the browser preview binds only to loopback;
            # the phone-facing process exposes the limited mobile API, not /preview.
            local_app = server_module.app
            phone_app = server_module.create_app(enable_preview=False)
            local_port = free_port("127.0.0.1")
            try:
                phone_port = free_port("0.0.0.0", 8765)
            except OSError:
                phone_port = free_port("0.0.0.0")

            local_server = uvicorn.Server(uvicorn.Config(
                local_app, host="127.0.0.1", port=local_port,
                log_level="critical", access_log=False, log_config=None,
            ))
            phone_server = uvicorn.Server(uvicorn.Config(
                phone_app, host="0.0.0.0", port=phone_port,
                log_level="critical", access_log=False, log_config=None,
            ))
            servers = [local_server, phone_server]
            threads = [
                threading.Thread(target=instance.run, name=f"calibre-server-{index}", daemon=True)
                for index, instance in enumerate(servers)
            ]
            state["servers"] = servers
            state["threads"] = threads
            state["page_url"] = f"http://127.0.0.1:{local_port}/preview/"
            state["admin_url"] = f"http://127.0.0.1:{local_port}/admin"
            ip_address = server_module.local_lan_ip()
            state["phone_url"] = f"http://{ip_address}:{phone_port}"
            for thread in threads:
                thread.start()

            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if all(instance.started for instance in servers):
                    state["ready"] = True
                    return
                if not all(thread.is_alive() for thread in threads):
                    raise RuntimeError("Один из локальных серверов завершился до открытия страницы.")
                time.sleep(0.1)
            raise TimeoutError("Серверы Kolibri не успели запуститься.")
        except Exception:
            state["error"] = traceback.format_exc()

    def poll_startup() -> None:
        if state.get("ready"):
            page_address.set(str(state["admin_url"]))
            phone_address.set(str(state["phone_url"]))
            open_button.configure(state="normal")
            if "НЕ НАЙДЕН" not in str(state["phone_url"]):
                copy_button.configure(state="normal")
            status.configure(text="Готово. Страница открывается в браузере автоматически.", fg="#2e7d32")
            try:
                opened = webbrowser.open(str(state["page_url"]), new=2, autoraise=True)
                if not opened:
                    status.configure(text="Предпросмотр открыт. Нажмите «Открыть админку».", fg="#765500")
            except Exception:
                status.configure(text="Предпросмотр открыт. Нажмите «Открыть админку».", fg="#765500")
            return
        if state.get("error"):
            status.configure(text="Не удалось запустить Kolibri.", fg="#a22121")
            messagebox.showerror("Kolibri", str(state["error"]))
            stop_and_close()
            return
        root.after(100, poll_startup)

    threading.Thread(target=start_servers, name="calibre-startup", daemon=True).start()
    root.after(100, poll_startup)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        log_root = Path(os.environ.get("CALIBRE_DATA_DIR", Path.home() / "CALIBRE"))
        log_root.mkdir(parents=True, exist_ok=True)
        (log_root / "launcher-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
