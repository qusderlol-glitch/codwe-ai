r"""Готовит обновление для всех пользователей: собирает зашифрованный интерфейс, повышает версию и подписывает список файлов.
    venv\Scripts\python tools\publish_update.py
Дальше нужно закоммитить и отправить изменения в GitHub (update.json, update.sig, version.txt и сами файлы): у пользователей
программа обновится сама при следующем запуске. Приватный ключ подписи лежит ВНЕ репозитория: %USERPROFILE%\.codwe\update_key.pem.
Потеряете ключ: обновления без нового скачивания уже не выпустить (тогда придётся выдать новый ключ и новую сборку вручную).
Параметр --out папка: только записать список и файлы туда (для проверки), репозиторий не трогать."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import updater  # noqa: E402

KEY = Path(os.environ.get("CODWE_SIGN_KEY") or Path.home() / ".codwe" / "update_key.pem")


def load_key():
    from cryptography.hazmat.primitives import serialization
    if not KEY.exists():
        sys.exit(f"Нет ключа подписи: {KEY}\nСоздайте командой: python tools/publish_update.py --newkey")
    return serialization.load_pem_private_key(KEY.read_bytes(), password=None)


def new_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if KEY.exists():
        sys.exit(f"Ключ уже есть: {KEY} (не перезаписываю)")
    k = Ed25519PrivateKey.generate()
    KEY.parent.mkdir(parents=True, exist_ok=True)
    KEY.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    print("Ключ создан:", KEY, "\nПубличная часть (вставьте в updater.py как PUBKEY_HEX):", pub)


def main():
    if "--newkey" in sys.argv:
        return new_key()
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
    key = load_key()
    from cryptography.hazmat.primitives import serialization
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    if pub != updater.PUBKEY_HEX:
        sys.exit("Публичная часть ключа не совпадает с PUBKEY_HEX в updater.py: пользователи отвергнут такие обновления.")
    ver_file = ROOT / "version.txt"
    old_ver_text = ver_file.read_text(encoding="utf-8") if ver_file.exists() else "0"
    ver = updater.local_version(ROOT) + 1
    ver_file.write_text(str(ver), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_dist.py")], check=True, cwd=str(ROOT))  # также пересчитает integrity.json
    names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True,
                           cwd=str(ROOT), encoding="utf-8").stdout.splitlines()
    files = {}
    for n in sorted(set(names)):
        n = n.strip().strip('"')
        if n in ("update.json", "update.sig") or not updater.path_ok(n) or not (ROOT / n).is_file():
            continue
        files[n] = updater.norm_sha((ROOT / n).read_bytes())
    raw = json.dumps({"version": ver, "files": files}, indent=1, sort_keys=True).encode("utf-8")
    sig = key.sign(raw).hex()
    dst = out or ROOT
    if out:
        for n in files:
            (out / n).parent.mkdir(parents=True, exist_ok=True)
            (out / n).write_bytes((ROOT / n).read_bytes())
    if out:
        ver_file.write_text(old_ver_text, encoding="utf-8")  # пробная выкладка не повышает версию в репозитории
    (dst / "update.json").write_bytes(raw)
    (dst / "update.sig").write_text(sig, encoding="ascii")
    print(f"Версия {ver}: файлов в обновлении {len(files)}. Готово: {dst / 'update.json'}")


if __name__ == "__main__":
    main()
