#!/usr/bin/env python3
"""Проверяет, готов ли компьютер к работе плагина."""
import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path

from settings import bases_dir, load_settings

FONT_FOLDERS = ["~/Library/Fonts", "/Library/Fonts"]


def has_font(name):
    return any(next(Path(f).expanduser().glob(f"{name}*"), None) for f in FONT_FOLDERS)


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
        (settings["ae"]["app"], Path("/Applications", settings["ae"]["app"]).is_dir(), "проверьте ae.app в настройках"),
        (f"база {base}", (base / "tokens.json").is_file(), "запустите /ae-video:setup или проверьте bases_dir и base в настройках"),
    ]
    if (base / "tokens.json").is_file():
        for font in base_fonts(json.loads((base / "tokens.json").read_text(encoding="utf-8"))):
            checks.append((f"шрифт {font}", has_font(font), "установите шрифт базы"))

    failed = 0
    for name, passed, fix in checks:
        print(f"{'ok ' if passed else 'НЕТ'} {name}" + ("" if passed else f": {fix}"))
        failed += not passed
    print("Вручную в AE: Settings > Scripting & Expressions > Allow Scripts to Write Files and Access Network")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
