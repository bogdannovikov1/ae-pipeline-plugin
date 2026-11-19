#!/usr/bin/env python3
"""Проверяет, готов ли компьютер к работе плагина."""
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import secret_store
from settings import PLUGIN_ROOT, bases_dir, load_settings
from validate import ENGINE_VERSION



# After Effects на Windows не находит начертание внутри вариативного файла (подставляет Times New Roman)
# и не видит шрифт, скопированный в папку шрифтов вручную: ставить обычный файл начертания через Windows.
FONT_HINT = ("установите шрифт базы обычным файлом начертания, не вариативным: правый клик по файлу > «Установить»"
             if platform.system() == "Windows" else "установите шрифт базы")


def has_font(name, folders):
    """Ищет файл шрифта по имени семейства. У вариативных шрифтов файл называется
    по семейству (Caveat[wght].ttf), а PostScript-имя добавляет Roman (CaveatRoman-Bold)."""
    names = {name, re.sub(r"Roman$", "", name)}
    return any(next(Path(os.path.expandvars(f)).expanduser().glob(f"{n}*"), None) for f in folders for n in names if n)


def base_fonts(tokens):
    return sorted({value.split("-")[0] for value in tokens.get("text", {}).get("fonts", {}).values()})


def python3_works():
    """На Windows python3 часто ведёт в заглушку Microsoft Store, а инструкции плагина зовут именно его."""
    found = shutil.which("python3")
    return bool(found) and subprocess.run([found, "-c", "1"], capture_output=True).returncode == 0


def system_checks(settings):
    ae = settings["ae"]
    app = Path(ae["apps_dir"], ae["app"])
    if platform.system() == "Windows":
        return [
            ("Windows", True, ""),
            ("команда python3", python3_works(), "отключите псевдонимы python3 в Параметры > Приложения > Псевдонимы выполнения "
             "и положите копию python.exe под именем python3.exe рядом с ним"),
            ("ffmpeg", shutil.which("ffmpeg"), "winget install Gyan.FFmpeg"),
            ("numpy", importlib.util.find_spec("numpy"), "python -m pip install -r requirements.txt"),
            ("faster_whisper", importlib.util.find_spec("faster_whisper"), "python -m pip install faster-whisper"),
            ("keyring", importlib.util.find_spec("keyring"), "python -m pip install keyring"),
            (ae["app"], (app / ae["exe"]).is_file(), "проверьте ae.app и ae.apps_dir в настройках"),
        ]
    return [
        ("macOS на Apple Silicon", platform.system() == "Darwin" and platform.machine() == "arm64", "на Mac нужен Apple Silicon, иначе Windows"),
        ("ffmpeg", shutil.which("ffmpeg"), "brew install ffmpeg"),
        ("numpy", importlib.util.find_spec("numpy"), "pip3 install mlx-whisper numpy"),
        ("mlx_whisper", importlib.util.find_spec("mlx_whisper"), "pip3 install mlx-whisper numpy"),
        (ae["app"], app.is_dir(), "проверьте ae.app в настройках"),
    ]


def main():
    settings = load_settings()
    base = bases_dir(settings) / settings["base"]
    checks = [("Python 3.9+", sys.version_info >= (3, 9), "обновите Python")] + system_checks(settings) + [
        (f"база {base}", (base / "tokens.json").is_file(), "запустите /ae-video:setup или проверьте bases_dir и base в настройках"),
    ]
    if (base / "base.json").is_file():
        needed = json.loads((base / "base.json").read_text(encoding="utf-8")).get("engine", 1)
        checks.append((f"движок плагина {ENGINE_VERSION} подходит базе (нужен {needed})", needed == ENGINE_VERSION, "запущена устаревшая копия плагина или база новее: обновите плагин"))
    voice = settings.get("voice", {})
    if voice.get("voice_name"):
        found = secret_store.get(voice["keychain_service"]) is not None
        checks.append((f"ключ ElevenLabs в {secret_store.where()}", found, secret_store.hint(voice["keychain_service"])))
    if (base / "tokens.json").is_file():
        for font in base_fonts(json.loads((base / "tokens.json").read_text(encoding="utf-8"))):
            checks.append((f"шрифт {font}", has_font(font, settings["doctor"]["font_folders"]), FONT_HINT))

    print(f"Плагин: {PLUGIN_ROOT}")
    failed = 0
    for name, passed, fix in checks:
        print(f"{'ok ' if passed else 'НЕТ'} {name}" + ("" if passed else f": {fix}"))
        failed += not passed
    print("Вручную в AE: Settings > Scripting & Expressions > Allow Scripts to Write Files and Access Network")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
