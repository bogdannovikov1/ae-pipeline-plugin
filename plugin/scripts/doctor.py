#!/usr/bin/env python3
"""Проверяет, готов ли компьютер к работе плагина."""
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from settings import PLUGIN_ROOT, bases_dir, load_settings
from validate import ENGINE_VERSION



def has_font(name, folders):
    """Ищет файл шрифта по имени семейства. У вариативных шрифтов файл называется
    по семейству (Caveat[wght].ttf), а PostScript-имя добавляет Roman (CaveatRoman-Bold)."""
    names = {name, re.sub(r"Roman$", "", name)}
    return any(next(Path(f).expanduser().glob(f"{n}*"), None) for f in folders for n in names if n)


def base_fonts(tokens):
    return sorted({value.split("-")[0] for value in tokens.get("text", {}).get("fonts", {}).values()})


def main():
    settings = load_settings()
    base = bases_dir(settings) / settings["base"]
    checks = [
        ("macOS на Apple Silicon", platform.system() == "Darwin" and platform.machine() == "arm64", "плагин работает только на таком Mac"),
        ("Python 3.9+", sys.version_info >= (3, 9), "обновите Python"),
        ("ffmpeg", shutil.which("ffmpeg"), "brew install ffmpeg"),
        ("numpy", importlib.util.find_spec("numpy"), "pip3 install mlx-whisper numpy"),
        ("mlx_whisper", importlib.util.find_spec("mlx_whisper"), "pip3 install mlx-whisper numpy"),
        (settings["ae"]["app"], Path(settings["ae"]["apps_dir"], settings["ae"]["app"]).is_dir(), "проверьте ae.app в настройках"),
        (f"база {base}", (base / "tokens.json").is_file(), "запустите /ae-video:setup или проверьте bases_dir и base в настройках"),
    ]
    if (base / "base.json").is_file():
        needed = json.loads((base / "base.json").read_text(encoding="utf-8")).get("engine", 1)
        checks.append((f"движок плагина {ENGINE_VERSION} подходит базе (нужен {needed})", needed == ENGINE_VERSION, "запущена устаревшая копия плагина или база новее: обновите плагин"))
    voice = settings.get("voice", {})
    if voice.get("voice_name"):
        found = subprocess.run(["security", "find-generic-password", "-s", voice["keychain_service"]], capture_output=True).returncode == 0
        checks.append(("ключ ElevenLabs в Связке ключей", found, f'security add-generic-password -U -a "$USER" -s {voice["keychain_service"]} -w'))
    if (base / "tokens.json").is_file():
        for font in base_fonts(json.loads((base / "tokens.json").read_text(encoding="utf-8"))):
            checks.append((f"шрифт {font}", has_font(font, settings["doctor"]["font_folders"]), "установите шрифт базы"))

    print(f"Плагин: {PLUGIN_ROOT}")
    failed = 0
    for name, passed, fix in checks:
        print(f"{'ok ' if passed else 'НЕТ'} {name}" + ("" if passed else f": {fix}"))
        failed += not passed
    print("Вручную в AE: Settings > Scripting & Expressions > Allow Scripts to Write Files and Access Network")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
