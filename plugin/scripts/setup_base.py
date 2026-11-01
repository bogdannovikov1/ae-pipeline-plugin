#!/usr/bin/env python3
"""Создаёт рабочую базу из стартовой, если её ещё нет. Существующую базу не трогает."""
import shutil
import sys

from settings import PLUGIN_ROOT, bases_dir, load_settings

STARTER = PLUGIN_ROOT / "starter_base"


def create_base(target):
    """Возвращает True, если база создана, и False, если она уже была."""
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STARTER, target)
    return True


def main():
    settings = load_settings()
    target = bases_dir(settings) / settings["base"]
    if create_base(target):
        print(f"База создана: {target}")
    else:
        print(f"База уже есть: {target}", file=sys.stderr)


if __name__ == "__main__":
    main()
