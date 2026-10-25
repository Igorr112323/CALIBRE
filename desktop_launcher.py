"""Windows launcher for the PC admin/server and a phone-only LAN API."""
from __future__ import annotations

import json
import math
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
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
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/", timeout=10)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        else:
            raise RuntimeError("Windows EXE self-test: мобильный интерфейс не должен раздаваться на ПК")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/admin", timeout=10) as response:
            admin_html = response.read().decode("utf-8")
            if response.status != 200 or "Kolibri — панель знаний" not in admin_html:
                raise RuntimeError("Windows EXE self-test: админ-панель Kolibri не открылась")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/admin/static/admin/admin.js", timeout=10) as response:
            admin_js = response.read().decode("utf-8")
            if (response.status != 200 or "/api/admin/machines" not in admin_js
                    or "machine-create-form" not in admin_js
                    or "manual-upload-form" not in admin_js
                    or "/api/admin/nodes" not in admin_js
                    or "/api/admin/media" not in admin_js
                    or "/api/admin/assistant/answer" not in admin_js):
                raise RuntimeError("Windows EXE self-test: карточки машин и материалы не загрузились")
        if "Добавить машину" not in admin_html:
            raise RuntimeError("Windows EXE self-test: форма создания машины не загрузилась")
        if "Ollama" in admin_html or "local-ai-panel" in admin_html:
            raise RuntimeError("Windows EXE self-test: техническая панель локальной модели не должна отображаться")
        if ("new-machine-photo-previews" not in admin_html or "manual-question-form" not in admin_html
                or any(f'data-admin-tab="{tab}"' not in admin_html for tab in ("scheme", "nodes", "problems"))):
            raise RuntimeError("Windows EXE self-test: разделы карточки или вопроса по руководству не загрузились")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/admin/static/admin/kolibri-mark.png", timeout=10) as response:
            if response.status != 200 or not response.headers.get("Content-Type", "").startswith("image/png"):
                raise RuntimeError("Windows EXE self-test: логотип Kolibri не загрузился")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/admin/assistant/status", timeout=10) as response:
            ai_status = json.loads(response.read())
            if ai_status.get("inference_network") != "loopback only; no cloud fallback":
                raise RuntimeError("Windows EXE self-test: безопасный статус локального помощника не получен")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/admin/ollama/status", timeout=10) as response:
            ollama_status = json.loads(response.read())
            if ollama_status.get("inference_network") != "loopback only; no cloud fallback":
                raise RuntimeError("Windows EXE self-test: Ollama не ограничена локальным адресом")
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
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Kolibri")
    root.geometry("470x340")
    root.resizable(False, False)
    root.configure(bg="#f7f8f1")
    root.update_idletasks()
    x = (root.winfo_screenwidth() - 470) // 2
    y = (root.winfo_screenheight() - 340) // 2
    root.geometry(f"470x340+{x}+{y}")

    state: dict[str, object] = {"servers": [], "threads": [], "error": None,
                                "ready": False, "admin_url": "", "phone_url": ""}
    canvas = tk.Canvas(root, width=470, height=190, bg="#f7f8f1", highlightthickness=0)
    canvas.pack(pady=(18, 0))
    canvas.create_oval(190, 24, 280, 114, fill="#326c38", outline="")
    canvas.create_text(235, 69, text="K", fill="white", font=("Segoe UI", 35, "bold"))
    dots = [canvas.create_oval(0, 0, 0, 0, fill="#7ca878", outline="") for _ in range(8)]
    title = tk.Label(root, text="Kolibri", font=("Segoe UI", 22, "bold"),
                     fg="#20271f", bg="#f7f8f1")
    title.pack(pady=(0, 3))
    status = tk.Label(root, text="Готовим вашу мастерскую…", font=("Segoe UI", 11),
                      fg="#687167", bg="#f7f8f1")
    status.pack(pady=(0, 13))
    progress_style = ttk.Style(root)
    progress_style.theme_use("clam")
    progress_style.configure("Kolibri.Horizontal.TProgressbar", troughcolor="#e4e9df",
                              background="#4b8a50", bordercolor="#f7f8f1", lightcolor="#4b8a50",
                              darkcolor="#4b8a50")
    progress = ttk.Progressbar(root, mode="indeterminate", length=240,
                               style="Kolibri.Horizontal.TProgressbar")
    progress.pack()
    progress.start(12)

    animation = {"angle": 0}
    def animate() -> None:
        animation["angle"] = (animation["angle"] + 0.16) % (2 * 3.14159)
        for index, item in enumerate(dots):
            angle = animation["angle"] + index * (2 * 3.14159 / len(dots))
            radius = 64
            cx = 235 + radius * math.cos(angle)
            cy = 69 + radius * math.sin(angle)
            size = 4 + (index % 3)
            shade = ("#326c38", "#4b8a50", "#9ab991")[index % 3]
            canvas.coords(item, cx - size, cy - size, cx + size, cy + size)
            canvas.itemconfigure(item, fill=shade)
        if root.winfo_exists():
            root.after(45, animate)

    animate()

    def start_servers() -> None:
        try:
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            required = [
                bundle_root / "alembic.ini",
                bundle_root / "migrations" / "versions" / "6a404288fafc_initial_catalogue_schema.py",
                bundle_root / "web" / "admin" / "index.html",
            ]
            missing = [str(path) for path in required if not path.is_file()]
            if missing:
                raise FileNotFoundError("В Kolibri отсутствуют необходимые ресурсы")

            import uvicorn
            import server as server_module

            local_app = server_module.app
            phone_app = server_module.create_app(recover_analysis=False)
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
            threads = [threading.Thread(target=instance.run, name=f"kolibri-server-{index}", daemon=True)
                       for index, instance in enumerate(servers)]
            state["servers"] = servers
            state["threads"] = threads
            state["admin_url"] = f"http://127.0.0.1:{local_port}/admin"
            state["phone_url"] = f"http://{server_module.local_lan_ip()}:{phone_port}"
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if all(instance.started for instance in servers):
                    state["ready"] = True
                    return
                if not all(thread.is_alive() for thread in threads):
                    raise RuntimeError("Локальный сервис не запустился")
                time.sleep(0.1)
            raise TimeoutError("Превышено время запуска Kolibri")
        except Exception:
            state["error"] = traceback.format_exc()

    threading.Thread(target=start_servers, name="kolibri-startup", daemon=True).start()

    def finish_startup() -> None:
        if state.get("ready"):
            status.configure(text="Всё готово", fg="#326c38")
            progress.stop()
            root.after(350, root.destroy)
            return
        if state.get("error"):
            progress.stop()
            status.configure(text="Не удалось запустить Kolibri", fg="#a2402f")
            messagebox.showerror("Kolibri", "Не удалось открыть приложение. Перезапустите EXE или обратитесь в поддержку.")
            root.destroy()
            return
        root.after(100, finish_startup)

    root.after(100, finish_startup)
    root.mainloop()
    if not state.get("ready"):
        return

    def stop_servers(*_args) -> None:
        for server in state.get("servers", []):
            server.should_exit = True

    try:
        import webview
        window = webview.create_window(
            "Kolibri — справочник сельхозтехники", str(state["admin_url"]),
            width=520, height=860, min_size=(420, 640), background_color="#f7f8f1",
        )
        window.events.closed += stop_servers
        webview.start(gui="edgechromium", debug=False)
    except Exception:
        stop_servers()
        error_root = tk.Tk()
        error_root.withdraw()
        messagebox.showerror(
            "Kolibri",
            "Не удалось открыть окно приложения. Проверьте, что в Windows установлен Microsoft Edge WebView2 Runtime.",
            parent=error_root,
        )
        error_root.destroy()
    finally:
        stop_servers()
        for thread in state.get("threads", []):
            thread.join(timeout=3)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        log_root = Path(os.environ.get("CALIBRE_DATA_DIR", Path.home() / "CALIBRE"))
        log_root.mkdir(parents=True, exist_ok=True)
        (log_root / "launcher-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
