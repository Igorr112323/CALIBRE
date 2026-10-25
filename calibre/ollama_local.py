"""Local-only Ollama access for desktop parsing, grounded answers, and embeddings.

All HTTP calls are pinned to loopback. This module deliberately has no remote URL
setting and never falls back to a cloud provider.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

import httpx
from sqlalchemy.orm import Session

from calibre.models import AppSetting

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_CHAT_MODEL = "qwen3:8b"
DEFAULT_SEARCH_MODEL = "bge-m3"
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_DOWNLOAD_LOCK = threading.Lock()
_DOWNLOAD_THREADS: dict[str, threading.Thread] = {}


def validate_model_name(name: str) -> str:
    value = name.strip()
    if not _MODEL_RE.fullmatch(value):
        raise ValueError("Недопустимое имя Ollama-модели")
    return value


def model_settings(session: Session) -> dict[str, str]:
    def get(key: str, fallback: str) -> str:
        setting = session.get(AppSetting, key)
        return setting.value.strip() if setting and setting.value.strip() else fallback
    return {
        "chat_model": get("ollama_chat_model", DEFAULT_CHAT_MODEL),
        "search_model": get("ollama_search_model", DEFAULT_SEARCH_MODEL),
    }


def save_model_settings(session: Session, chat_model: str, search_model: str) -> dict[str, str]:
    values = {
        "chat_model": validate_model_name(chat_model),
        "search_model": validate_model_name(search_model),
    }
    session.merge(AppSetting(key="ollama_chat_model", value=values["chat_model"]))
    session.merge(AppSetting(key="ollama_search_model", value=values["search_model"]))
    session.commit()
    return values


def ollama_binary() -> str | None:
    candidate = os.environ.get("OLLAMA_PATH", "").strip()
    if candidate and Path(candidate).is_file():
        return candidate
    discovered = shutil.which("ollama")
    if discovered:
        return discovered
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        for path in (local / "Programs" / "Ollama" / "ollama.exe", Path("C:/Program Files/Ollama/ollama.exe")):
            if path.is_file():
                return str(path)
    return None


def _model_installed(wanted: str, installed: set[str]) -> bool:
    for candidate in installed:
        if candidate == wanted:
            return True
        if ":" not in wanted and candidate == f"{wanted}:latest":
            return True
    return False


def ollama_status(session: Session) -> dict:
    settings = model_settings(session)
    installed: set[str] = set()
    running = False
    version = None
    error = None
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            response = client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            body = response.json()
            installed = {str(model.get("name", "")) for model in body.get("models", [])}
            running = True
            try:
                version = client.get(f"{OLLAMA_URL}/api/version").json().get("version")
            except Exception:
                version = None
    except Exception as exc:
        error = type(exc).__name__
    binary = ollama_binary()
    return {
        "installed": bool(binary or running),
        "running": running,
        "version": version,
        "binary_found": bool(binary),
        "chat_model": settings["chat_model"],
        "search_model": settings["search_model"],
        "chat_model_ready": _model_installed(settings["chat_model"], installed),
        "search_model_ready": _model_installed(settings["search_model"], installed),
        "installed_models": sorted(installed),
        "download": load_download_state(session),
        "reason": "ready" if running else ("service_not_running" if binary else "ollama_not_installed"),
        "connection_error": error,
        "inference_network": "loopback only; no cloud fallback",
    }


def _download_key(model: str) -> str:
    return f"ollama_download:{model}"


def load_download_state(session: Session, model: str | None = None) -> dict | None:
    if model:
        setting = session.get(AppSetting, _download_key(model))
        if not setting:
            return None
        try:
            result = json.loads(setting.value)
            return result if isinstance(result, dict) else None
        except (TypeError, json.JSONDecodeError):
            return None
    values = session.query(AppSetting).filter(AppSetting.key.like("ollama_download:%")).all()
    latest = []
    for setting in values:
        try:
            record = json.loads(setting.value)
            if isinstance(record, dict):
                latest.append(record)
        except (TypeError, json.JSONDecodeError):
            continue
    return latest[-1] if latest else None


def _save_download(factory, model: str, payload: dict) -> None:
    with factory() as session:
        session.merge(AppSetting(key=_download_key(model), value=json.dumps(payload, ensure_ascii=False)))
        session.commit()


def start_model_download(factory, model: str) -> dict:
    model = validate_model_name(model)
    with _DOWNLOAD_LOCK:
        existing = _DOWNLOAD_THREADS.get(model)
        if existing and existing.is_alive():
            return {"accepted": True, "model": model, "already_running": True}
        thread = threading.Thread(target=_download_worker, args=(factory, model),
                                  name=f"ollama-pull-{model.replace(':', '-')}", daemon=True)
        _DOWNLOAD_THREADS[model] = thread
        _save_download(factory, model, {"model": model, "status": "queued", "progress": 0, "message": "Ожидание запуска Ollama"})
        thread.start()
    return {"accepted": True, "model": model, "already_running": False}


def _download_worker(factory, model: str) -> None:
    def update(status: str, progress: int | None, message: str, **extra) -> None:
        value = {"model": model, "status": status, "progress": progress, "message": message, **extra}
        _save_download(factory, model, value)

    try:
        update("running", 0, "Подключение к локальному Ollama; загрузка продолжится при повторном запуске, если поддерживается установленной версией.")
        try:
            with httpx.Client(timeout=None, trust_env=False) as client:
                with client.stream("POST", f"{OLLAMA_URL}/api/pull", json={"name": model, "stream": True}) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        item = json.loads(line)
                        total = item.get("total")
                        completed = item.get("completed")
                        progress = round(100 * completed / total) if isinstance(total, (int, float)) and total else None
                        update("running", progress, str(item.get("status") or "Загрузка модели"),
                               completed=completed, total=total)
                        if item.get("error"):
                            raise RuntimeError(str(item["error"]))
            update("complete", 100, "Модель доступна локально.")
        except (httpx.ConnectError, httpx.ConnectTimeout):
            binary = ollama_binary()
            if not binary:
                raise RuntimeError("Ollama не установлен. Установите приложение Ollama, затем повторите загрузку модели.")
            process = subprocess.Popen([binary, "pull", model], stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            last_message = "Загрузка модели Ollama"
            for line in process.stdout or ():
                last_message = line.strip()[:240] or last_message
                match = re.search(r"(\d{1,3})%", last_message)
                update("running", min(100, int(match.group(1))) if match else None, last_message)
            code = process.wait()
            if code:
                raise RuntimeError(f"ollama pull завершился с кодом {code}: {last_message}")
            update("complete", 100, "Модель доступна локально.")
    except Exception as exc:
        update("error", None, str(exc)[:500])


def chat_json(model: str, system: str, prompt: str, timeout: float = 900.0) -> dict:
    model = validate_model_name(model)
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(f"{OLLAMA_URL}/api/chat", json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_ctx": 16384},
        })
        response.raise_for_status()
        payload = response.json()
    content = payload.get("message", {}).get("content", "")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("Модель вернула не JSON-объект")
    return parsed


def embeddings(model: str, texts: list[str], timeout: float = 300.0) -> list[list[float]]:
    model = validate_model_name(model)
    if not texts:
        return []
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(f"{OLLAMA_URL}/api/embed", json={"model": model, "input": texts})
        response.raise_for_status()
        body = response.json()
    vectors = body.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError("Ollama вернул неполный поисковый индекс")
    return vectors


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0
