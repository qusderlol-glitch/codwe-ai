"""Пересчитывает integrity.json (хеши файлов программы). Запускайте после ЛЮБОГО изменения файлов из списка GUARDED:
    venv\\Scripts\\python tools\\build_manifest.py
Без этого публичная версия программы покажет экран блокировки (в режиме разработчика, файл dev.flag, проверка отключена)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auth_api  # noqa: E402

d = auth_api.build_manifest()
print(f"integrity.json: {len(d)} файлов")
