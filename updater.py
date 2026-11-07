r"""Тихое обновление Codwe AI: при запуске программа сама подтягивает новые файлы, скачивать архив заново не нужно.

Как это защищено (чужой код на ваш компьютер попасть не должен):
  * Список файлов (update.json) подписан личным ключом автора (Ed25519). Публичная часть ключа зашита ниже. Подпись
    не подошла: ничего не меняется. Даже тот, кто получит доступ к репозиторию на GitHub, без ключа обновление не выпустит.
  * Каждый файл сверяется по SHA-256 из подписанного списка. Хоть один не совпал: ничего не меняется.
  * Версия должна расти (подсунуть старую уязвимую версию нельзя).
  * Обновляются только программные файлы (корень, web/, web_dist/) и только известных типов. Модели, чаты, ключи и
    папки venv/models/outputs не трогаются. Перед заменой старые файлы копируются в .update_backup.
  * У разработчика (есть dev.flag или папка .git) файлы не меняются.

Запуск: python updater.py --apply   (делает лаунчер при каждом старте)   |   --check   (только показать)
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_URL = "https://raw.githubusercontent.com/qusderlol-glitch/codwe-ai/master/"
PUBKEY_HEX = "090832b8af41539957534d521514fae7589c06e205ce4e91fb3460e25f27296d"
ALLOWED_EXT = {".py", ".js", ".html", ".css", ".svg", ".png", ".ico", ".json", ".enc", ".sig", ".bat", ".txt", ".md"}
ROOT_OK_RX = re.compile(r"^[A-Za-z0-9_\-]+\.[a-z]+$")
SUB_OK = ("web/", "web_dist/")
NEVER = {"cloud_config.json", "auth_session.json", "auth_accounts.json", "app_state.bin", "port.txt", "dev.flag", "safety_state.json", "terminal_state.json"}


def norm_sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()  # CRLF/LF не мешает (как в integrity.json)


def path_ok(rel: str) -> bool:
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ".." in rel.split("/") or "\\" in rel or ":" in rel:
        return False
    if rel in NEVER or Path(rel).suffix.lower() not in ALLOWED_EXT:
        return False
    if "/" not in rel:
        return bool(ROOT_OK_RX.match(rel))
    return rel.startswith(SUB_OK) and rel.count("/") == 1 and bool(re.match(r"^[A-Za-z0-9_\-.]+$", rel.split("/", 1)[1]))


def _log(msg):
    try:
        with open(HERE / "update.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def local_version(dest: Path) -> int:
    try:
        return int((dest / "version.txt").read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def verify_manifest(raw: bytes, sig_hex: str, pubkey_hex: str) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(bytes.fromhex(sig_hex.strip()), raw)
    except (InvalidSignature, ValueError):
        raise RuntimeError("подпись списка обновлений не подошла")
    return json.loads(raw.decode("utf-8"))


def fetch(url: str, timeout=15) -> bytes:
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "CodweAI-updater"})
    if r.status_code != 200:
        raise RuntimeError(f"{url}: HTTP {r.status_code}")
    return r.content


def apply(base_url=BASE_URL, dest: Path = HERE, pubkey_hex=PUBKEY_HEX, check_only=False, install_requirements=True):
    """-> (статус, подробности). Статус: 'up-to-date' | 'updated' | 'available' | 'skipped' | 'error'."""
    dest = Path(dest)
    if (dest / "dev.flag").exists() or (dest / ".git").exists():
        return "skipped", "режим разработчика"
    try:
        raw = fetch(base_url + "update.json")
        m = verify_manifest(raw, fetch(base_url + "update.sig").decode("ascii"), pubkey_hex)
        ver, files = int(m["version"]), m["files"]
        if ver <= local_version(dest):
            return "up-to-date", f"версия {local_version(dest)}"
        bad = [p for p in files if not path_ok(p)]
        if bad:
            raise RuntimeError("в списке недопустимые пути: " + ", ".join(bad[:3]))
        todo = []
        for rel, sha in files.items():
            f = dest / rel
            if not f.exists() or norm_sha(f.read_bytes()) != sha:
                todo.append((rel, sha))
        if check_only:
            return "available", f"версия {ver}, файлов к обновлению: {len(todo)}"
        tmp = dest / ".update_tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for rel, sha in todo:   # сначала скачиваем и проверяем всё; ничего не заменяем, пока не проверено каждое
            data = fetch(base_url + rel, timeout=30)
            if norm_sha(data) != sha:
                raise RuntimeError(f"файл {rel} не совпал с подписанным списком")
            t = tmp / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(data)
        bak = dest / ".update_backup" / str(local_version(dest))
        done = []
        try:
            for rel, _sha in todo:
                f = dest / rel
                if f.exists():
                    (bak / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, bak / rel)
                f.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp / rel, f)
                done.append(rel)
            (dest / "version.txt").write_text(str(ver), encoding="utf-8")
        except Exception:
            for rel in done:   # откат
                if (bak / rel).exists():
                    shutil.copy2(bak / rel, dest / rel)
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        _log(f"обновлено до версии {ver}: {len(todo)} файлов")
        if install_requirements and any(r == "requirements.txt" for r, _ in todo):
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(dest / "requirements.txt")],
                               timeout=900, creationflags=0x08000000)
            except Exception as e:  # noqa: BLE001
                _log("не удалось поставить библиотеки: " + str(e)[:120])
        return "updated", f"версия {ver}, файлов: {len(todo)}"
    except Exception as e:  # noqa: BLE001
        _log("ошибка обновления: " + str(e)[:200])
        return "error", str(e)[:200]


if __name__ == "__main__":
    st, info = apply(check_only="--check" in sys.argv)
    print(st, info)
