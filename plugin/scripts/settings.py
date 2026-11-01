#!/usr/bin/env python3
"""Настройки плагина: значения по умолчанию, личные и настройки одного видео.

Пример:
    python3 settings.py show --video ~/Videos/my_video
    python3 settings.py get review.max_rounds
"""
import argparse
import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = PLUGIN_ROOT / "config" / "defaults.json"
USER_PATH = Path.home() / ".config" / "ae-video" / "settings.json"
VIDEO_FILE = "settings.json"


def merge(base, override):
    """Накладывает override на base: вложенные словари сливаются, остальное заменяется."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def read_layer(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_settings(video=None, user_path=USER_PATH):
    settings = merge(read_layer(DEFAULTS_PATH), read_layer(user_path))
    if video:
        settings = merge(settings, read_layer(Path(video) / VIDEO_FILE))
    return settings


def find_video(path):
    """Папка видео — та, что содержит fragments/ над файлом. Для файлов вне видео возвращает None."""
    for parent in Path(path).expanduser().resolve().parents:
        if parent.name == "fragments":
            return parent.parent
    return None


def settings_for(path):
    """Настройки с учётом видео, к которому относится файл."""
    return load_settings(find_video(path))


def bases_dir(settings):
    return Path(settings["bases_dir"]).expanduser()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["show", "get"])
    parser.add_argument("key", nargs="?", help="для get: путь через точку")
    parser.add_argument("--video", type=Path, help="папка видео")
    args = parser.parse_args()

    value = load_settings(args.video)
    if args.command == "get":
        if not args.key:
            parser.error("укажите ключ")
        for part in args.key.split("."):
            value = value[part]
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
