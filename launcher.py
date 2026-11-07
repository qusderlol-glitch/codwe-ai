"""Запускает сервер Codwe AI (с автоперезапуском при сбое) и открывает окно-приложение.

Без аргументов: запустить сервер (если не запущен) и открыть окно.
--supervise:   (служебный режим) держать сервер запущенным.

Окно: настоящее окно приложения (WebView2 через pywebview): без адресной строки, без F12 и без меню «Просмотреть код».
Если pywebview не установлен, открывается окно Edge/Chrome в режиме приложения (там F12 ловит страж страницы).
Этот же файл собирается в «Codwe AI.exe» (tools/build_exe.py): рядом с exe должны лежать папка venv и файлы программы.
"""
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HOME = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
# Свой порт, чтобы не мешать другим программам (раньше был 8765, его занял «Codwe Studio»). Если занят, берётся следующий свободный.
DEFAULT_PORT = 38765
PORT = int(os.environ.get("CODWE_PORT") or DEFAULT_PORT)
URL = f"http://127.0.0.1:{PORT}"
VENV = HOME / "venv" / "Scripts"
NO_WINDOW, DETACHED = 0x08000000, 0x00000008


def port_open(port=None):
    try:
        with socket.create_connection(("127.0.0.1", port or PORT), timeout=0.6):
            return True
    except OSError:
        return False


def is_ours(port=None):
    """Отвечает ли на этом порту именно Codwe AI (а не чужая программа)."""
    p = port or PORT
    if not port_open(p):
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{p}/api/auth/whoami", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "Codwe AI"
    except Exception:
        return False


def can_bind(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port():
    """Порт, где уже работает наш сервер, иначе первый свободный начиная с DEFAULT_PORT."""
    if os.environ.get("CODWE_PORT"):
        return int(os.environ["CODWE_PORT"])
    cands = range(DEFAULT_PORT, DEFAULT_PORT + 40)
    for p in cands:
        if is_ours(p):
            return p
    for p in cands:
        if not port_open(p) and can_bind(p):
            return p
    raise RuntimeError("не нашёл свободный порт")


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


def open_edge_window(url=None):
    url = url or URL
    b = browser_path()
    if b:
        subprocess.Popen([b, f"--app={url}", "--window-size=1380,900"])
    else:
        webbrowser.open(url)


def open_app_window():
    """Окно приложения. True — окно было и закрыто пользователем; False — pywebview недоступен (нужен запасной путь)."""
    try:
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window("Codwe AI", URL, width=1380, height=900, min_size=(1000, 640), background_color="#060a09", text_select=True)
        # private_mode=False и storage_path: чаты и настройки окна сохраняются между запусками
        webview.start(gui="edgechromium", debug=False, private_mode=False, storage_path=str(HOME / "webview_profile"))
        return True
    except Exception as e:  # нет WebView2 / нет pywebview / ошибка окна
        try:
            with open(HOME / "server.log", "ab") as log:
                log.write(f"\n[launcher] окно приложения не открылось ({type(e).__name__}: {e}); открываю окно браузера\n".encode("utf-8"))
        except OSError:
            pass
        return False


def migrate_old_window():
    """Тем, кто раньше пользовался окном браузера: один раз просим старое окно сохранить чаты на диск, чтобы они появились в новом."""
    state, sess = HOME / "app_state.bin", HOME / "auth_session.json"
    if state.exists() or not sess.exists():
        return
    open_edge_window(URL)
    for _ in range(90):  # страница сохраняет копию через ~4 секунды после загрузки
        if state.exists():
            break
        time.sleep(0.5)
    if state.exists():
        message("Ваши чаты перенесены в новое окно Codwe AI.\nСтарое окно браузера теперь можно закрыть.", 0x40)


def open_window():
    migrate_old_window()
    if not open_app_window():
        open_edge_window()


def message(text, flags=0x10):
    ctypes.windll.user32.MessageBoxW(0, text, "Codwe AI", flags)


def update_files():
    """Тихо подтягивает обновления программы (подписанный список файлов с GitHub). Нет связи или ошибка — не страшно."""
    up = HOME / "updater.py"
    if not up.exists() or (HOME / "dev.flag").exists() or (HOME / ".git").exists():
        return  # у разработчика файлы не трогаем
    try:
        subprocess.run([str(VENV / "python.exe"), str(up), "--apply"], cwd=str(HOME), timeout=120,
                       creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    global PORT, URL
    if "--supervise" in sys.argv:
        supervise()
        return
    try:
        PORT = pick_port()
    except RuntimeError:
        message("Не нашлось свободного порта для Codwe AI. Закройте лишние программы и запустите снова.")
        return
    URL = f"http://127.0.0.1:{PORT}"
    if not is_ours():
        update_files()
        pyw = VENV / "pythonw.exe"
        exe = str(pyw if pyw.exists() else VENV / "python.exe")
        env = dict(os.environ, CODWE_PORT=str(PORT))
        try:
            (HOME / "port.txt").write_text(str(PORT), encoding="utf-8")  # чтобы stop_codwe.bat знал, какой порт останавливать
        except OSError:
            pass
        subprocess.Popen([exe, str(HOME / "launcher.py"), "--supervise"], cwd=str(HOME), env=env,
                         creationflags=NO_WINDOW | DETACHED, close_fds=True)
        for _ in range(180):
            if is_ours():
                break
            time.sleep(0.5)
        else:
            message("Сервер Codwe AI не запустился. Подробности — в файле server.log рядом с программой.")
            return
    open_window()


if __name__ == "__main__":
    main()
