"""Живая лента событий для DEV-консоли: что программа делает прямо сейчас (запросы к API, задачи, входы, действия админов).

Важно для приватности: сюда пишутся только тип события, модель, размер и время. Тексты запросов, промпты и картинки
в ленту НЕ попадают никогда. Лента живёт в памяти этого компьютера и пропадает при перезапуске."""
import collections
import re
import threading
import time

_L = threading.Lock()
_N = 0
_BUF = collections.deque(maxlen=800)

# понятные описания вызовов программы (показываются в ленте рядом с каждым запросом)
_DESC = [
    (r"^POST /api/chat$", "Чат: сообщение уходит в локальную языковую модель (Ollama)"),
    (r"^POST /api/image$", "Картинки: задача поставлена в очередь на видеокарту"),
    (r"^POST /api/video$", "Видео: задача поставлена в очередь на видеокарту"),
    (r"^POST /api/edit/plan$", "Редактор: ИИ смотрит на картинку и составляет план правки"),
    (r"^POST /api/edit$", "Редактор: правка поставлена в очередь"),
    (r"^POST /api/enhance$", "ИИ-помощник улучшает описание картинки"),
    (r"^POST /api/analyze$", "ИИ смотрит на картинку (vision-модель)"),
    (r"^POST /api/live$", "Live: кадр рисуется, пока вы печатаете"),
    (r"^POST /api/live/start$", "Live: загрузка быстрой модели в видеопамять"),
    (r"^POST /api/live/save$", "Live: кадр сохранён в папку outputs"),
    (r"^POST /api/power$", "Включение или выключение ИИ"),
    (r"^POST /api/title$", "ИИ придумывает название чата"),
    (r"^POST /api/upload$", "Загрузка вашей картинки в локальную папку uploads"),
    (r"^POST /api/jobs/[^/]+/cancel$", "Отмена задачи"),
    (r"^GET /api/gallery$", "Галерея: список файлов в папке outputs на этом компьютере"),
    (r"^DELETE /api/gallery/", "Галерея: файл удалён с диска"),
    (r"^GET /api/models", "Список моделей и их состояние"),
    (r"^POST /api/admin/ban$", "Админ: блокировка или разблокировка (проверяет облако)"),
    (r"^POST /api/admin/role$", "Админ: смена ранга (проверяет облако)"),
    (r"^POST /api/admin/models$", "Админ: модель заблокирована или разблокирована для всех"),
    (r"^POST /api/admin/safety$", "Админ: режим безопасности изменён для всех"),
    (r"^POST /api/admin/rank-", "Владелец: изменение рангов"),
    (r"^GET /api/admin/users$", "Админ: список пользователей из облака"),
    (r"^GET /api/admin/audit$", "Админ: журнал действий из облака"),
    (r"^POST /api/dev/exec$", "DEV-консоль: выполнена команда"),
    (r"^POST /api/auth/send-code$", "Вход: письмо с кодом запрошено через облако"),
    (r"^POST /api/auth/verify$", "Вход: проверка кода из письма в облаке"),
    (r"^POST /api/auth/logout$", "Выход из аккаунта"),
    (r"^POST /api/auth/switch$", "Переключение на другой сохранённый аккаунт"),
    (r"^POST /api/auth/tos/accept$", "Условия использования приняты"),
    (r"^POST /api/workspace", "Рабочая папка: действие с файлами проекта"),
    (r"^POST /api/codwecode", "Codwecode: шаг агента (скриншот, проверка, поиск)"),
]
_DESC = [(re.compile(a), b) for a, b in _DESC]

# эти пути опрашиваются постоянно и только засоряют ленту
QUIET = re.compile(r"^(GET /api/(status|auth/state|dev/feed|dev/commands|jobs/[^/]+|live/status)$|GET /(static|outputs|uploads|wsfiles))")


def describe(method, path):
    key = f"{method} {path}"
    for rx, text in _DESC:
        if rx.search(key):
            return text
    return ""


def log(kind, text):
    """kind: api | cmd | job | auth | admin | block | warn | err | info"""
    global _N
    with _L:
        _N += 1
        _BUF.append({"n": _N, "t": time.time(), "k": kind, "x": str(text)[:400]})
        return _N


def since(n=0, limit=300):
    with _L:
        items = [e for e in _BUF if e["n"] > n]
        return items[-limit:], _N
