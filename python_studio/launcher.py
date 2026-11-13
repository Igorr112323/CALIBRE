import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / 'requirements.txt'
MARKER = Path(sys.prefix) / 'kolibri-requirements.sha256'


def installed():
    command = 'import fastapi,uvicorn,cryptography,pdfplumber,pypdfium2,rapidocr_onnxruntime,onnxruntime,numpy,PIL,multipart,httpx,jsonschema,tesserocr,cv2'
    return subprocess.run([sys.executable, '-c', command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def setup():
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        raise RuntimeError('Нужен Python 3.10, 3.11 или 3.12 x64.')
    fingerprint = hashlib.sha256(REQUIREMENTS.read_bytes() + b'headless-opencv-4.10.0.84-v1').hexdigest()
    if MARKER.exists() and MARKER.read_text(encoding='utf-8') == fingerprint and installed():
        return
    print('Установка зависимостей. При первом запуске потребуется интернет.', flush=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(REQUIREMENTS)], check=True, cwd=ROOT)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--no-deps', '--force-reinstall', 'opencv-python-headless==4.10.0.84'], check=True, cwd=ROOT)
    if not installed():
        raise RuntimeError('Не удалось импортировать зависимости. Проверьте совместимость Python и операционной системы.')
    MARKER.write_text(fingerprint, encoding='utf-8')


def open_when_ready(port):
    for _ in range(90):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                webbrowser.open(f'http://127.0.0.1:{port}/')
                return
        except OSError:
            time.sleep(.25)


def main():
    setup()
    if os.environ.get('KOLIBRI_SETUP_ONLY') == '1':
        print('Зависимости готовы.', flush=True)
        return
    import uvicorn
    host = os.environ.get('KOLIBRI_HOST', '127.0.0.1')
    port = int(os.environ.get('KOLIBRI_PORT', '8765'))
    if os.environ.get('KOLIBRI_NO_BROWSER') != '1':
        threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    print(f'Kolibri Studio: http://127.0.0.1:{port}/', flush=True)
    uvicorn.run('main:app', host=host, port=port, proxy_headers=True, forwarded_allow_ips='*')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print('Ошибка запуска:', error, file=sys.stderr, flush=True)
        raise SystemExit(1)
