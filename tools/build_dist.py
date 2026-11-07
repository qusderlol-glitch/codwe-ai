r"""Собирает зашифрованную копию интерфейса (web_dist) и пересчитывает integrity.json.
    venv\Scripts\python tools\build_dist.py
Нужен Node.js и пакет javascript-obfuscator (один раз: cd tools\node_obf && npm install javascript-obfuscator).
Запускайте после ЛЮБОГО изменения файлов в web\ — иначе у пользователей будет старый интерфейс."""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
node = shutil.which("node")
if not node:
    sys.exit("Не найден Node.js (нужен только для сборки, пользователям он не нужен).")
if not (HERE / "node_obf" / "node_modules" / "javascript-obfuscator").exists():
    sys.exit("Установите обфускатор: cd tools\node_obf && npm install javascript-obfuscator@4.1.1")
subprocess.run([node, str(HERE / "obfuscate.js")], check=True, cwd=str(ROOT))
sys.path.insert(0, str(ROOT))
import auth_api  # noqa: E402

shutil.copyfile(ROOT / "web_dist" / "guard.js", ROOT / "docs" / "guard.js")  # тот же страж стережёт и публичный сайт
d = auth_api.build_manifest()
print(f"integrity.json: {len(d)} файлов")
