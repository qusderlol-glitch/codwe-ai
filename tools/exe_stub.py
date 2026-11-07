"""Вход для «Codwe AI.exe»: ничего не решает сам, а запускает launcher.py с диска. Благодаря этому лаунчер обновляется
вместе с программой без пересборки exe. Импорты ниже нужны, чтобы PyInstaller положил эти модули внутрь exe."""
import ctypes  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import runpy
import socket  # noqa: F401
import subprocess  # noqa: F401
import sys
import time  # noqa: F401
import urllib.request  # noqa: F401
import webbrowser  # noqa: F401
from pathlib import Path

import webview  # noqa: F401

home = Path(sys.executable).resolve().parent
runpy.run_path(str(home / "launcher.py"), run_name="__main__")
