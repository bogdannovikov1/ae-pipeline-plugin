#!/usr/bin/env python3
"""Печатает короткий список блоков и ассетов базы, чтобы агент не читал их файлы.

Пример:
    python3 base_index.py
    python3 base_index.py array_cells
"""
import argparse
import json
import sys

from settings import load_settings
from validate import read_json, resolve_base


def block_lines(base):
    for path in sorted((base / "blocks").glob("*/block.json")):
        block = read_json(path)
        yield f"{block['id']} ({', '.join(block.get('params', {}))}): {block['description']}"


def asset_lines(base):
    for asset in read_json(base / "assets" / "assets.json"):
        yield f"{asset['id']} [{asset['type']}]: {asset['description']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("block", nargs="?", help="показать параметры одного блока")
    parser.add_argument("--base", help="имя базы, по умолчанию активная")
    parser.add_argument("--video", help="папка видео с её настройками")
    args = parser.parse_args()

    settings = load_settings(args.video)
    base = resolve_base(args.base or settings["base"], settings)
    if args.block:
        path = base / "blocks" / args.block / "block.json"
        if not path.is_file():
            sys.exit(f"Нет блока: {args.block}")
        print(json.dumps(read_json(path), ensure_ascii=False, indent=2))
        return
    print("Блоки:")
    print("\n".join(block_lines(base)))
    print("Ассеты:")
    print("\n".join(asset_lines(base)))


if __name__ == "__main__":
    main()
