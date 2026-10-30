"""Запускает сервер Codwe AI (с автоперезапуском при сбое) и открывает окно-приложение.

Без аргументов: запустить сервер (если не запущен) и открыть окно.
--supervise:   (служебный режим) держать сервер запущенным.
"""
import ctypes
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HOME = Path(__file__).resolve().parent
PORT = int(os.environ.get("CODWE_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"
VENV = HOME / "venv" / "Scripts"
NO_WINDOW, DETACHED = 0x08000000, 0x00000008


def port_open():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.6):
            return True
    except OSError:
        return False


def supervise():
    py = VENV / "python.exe"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    crashes = []
    with open(HOME / "server.log", "ab", buffering=0) as log:
        while True:
            code = subprocess.run([str(py), str(HOME / "server.py")], cwd=str(HOME), env=env,
                                  stdout=log, stderr=log, creationflags=NO_WINDOW).returncode
            if code == 0:
                return
            now = time.time()
            crashes = [t for t in crashes if now - t < 120] + [now]
            log.write(f"\n[launcher] сервер упал (код {code}), перезапуск…\n".encode("utf-8"))
            if len(crashes) >= 5:
                log.write("[launcher] слишком много падений подряд, остановка.\n".encode("utf-8"))
                return
            time.sleep(3)


def browser_path():
    pf = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LocalAppData", "")]
    for base in pf:
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            p = Path(base) / rel
            if base and p.exists():
                return str(p)
    return None


def open_window():
    b = browser_path()
    if b:
        subprocess.Popen([b, f"--app={URL}", "--window-size=1380,900"])
    else:
        webbrowser.open(URL)


def message(text):
    ctypes.windll.user32.MessageBoxW(0, text, "Codwe AI", 0x10)


def main():
    if "--supervise" in sys.argv:
        supervise()
        return
    if not port_open():
        pyw = VENV / "pythonw.exe"
        exe = str(pyw if pyw.exists() else VENV / "python.exe")
        subprocess.Popen([exe, str(Path(__file__).resolve()), "--supervise"], cwd=str(HOME),
                         creationflags=NO_WINDOW | DETACHED, close_fds=True)
        for _ in range(120):
            if port_open():
                break
            time.sleep(0.5)
        else:
            message("Сервер Codwe AI не запустился. Подробности — в файле server.log рядом с программой.")
            return
    open_window()


if __name__ == "__main__":
    main()
