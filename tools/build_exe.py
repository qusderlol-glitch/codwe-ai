r"""Собирает «Codwe AI.exe»: маленькая программа-оболочка, которая запускает сервер и открывает окно приложения.
    venv\Scripts\python tools\build_exe.py
Результат: «Codwe AI.exe» в папке программы (рядом должны лежать папка venv и файлы Codwe AI, поэтому exe «привязан» к папке).
Для сборки создаётся отдельная папка tools\exe_venv (PyInstaller + pywebview), основное окружение не трогается."""
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EV = ROOT / "tools" / "exe_venv"
py = EV / "Scripts" / "python.exe"
if not py.exists():
    print("Создаю окружение для сборки (один раз)…")
    venv.create(EV, with_pip=True)
    subprocess.run([str(py), "-m", "pip", "install", "--quiet", "pyinstaller", "pywebview==6.2.1"], check=True)
work = ROOT / "tools" / "exe_work"
cmd = [str(py), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole", "--name", "Codwe AI",
       "--icon", str(ROOT / "web" / "codwe.ico"), "--distpath", str(ROOT), "--workpath", str(work), "--specpath", str(work), "--hidden-import", "webview",
       str(HERE / "exe_stub.py")]
subprocess.run(cmd, check=True, cwd=str(ROOT))
print("Готово:", ROOT / "Codwe AI.exe")
