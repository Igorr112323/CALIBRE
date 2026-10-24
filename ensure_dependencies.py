"""Install pinned Python packages once, and again only when requirements change."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
requirements = root / "requirements.txt"
marker = Path(sys.prefix) / "calibre-requirements.sha256"
expected = hashlib.sha256(requirements.read_bytes()).hexdigest()

if marker.exists() and marker.read_text(encoding="ascii").strip() == expected:
    raise SystemExit(0)

print("Подготавливаются локальные библиотеки КАЛИБР (при первом запуске нужен интернет).", flush=True)
result = subprocess.run([
    sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)
], cwd=root)
if result.returncode:
    raise SystemExit(result.returncode)
marker.write_text(expected + "\n", encoding="ascii")
