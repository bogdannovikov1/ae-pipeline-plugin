#!/usr/bin/env python3
"""Сверяет сцену с планом и блоки между собой, без модели и AE.

Проверяет: каждый бит плана собран, утверждённые блоки бита стоят в сцене и не заменены,
target ссылается на элемент, который уже построен, и на блок того типа, которого ждёт блок.

Пример:
    python3 plan_check.py F1.scene.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

GLOBAL_BEAT = "global"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def beat_id(beat, number):
    """Бит без id получает номер по порядку: b1, b2, ... Так старые планы тоже проверяются."""
    return str(beat.get("id") or f"b{number + 1}")


def approved_blocks(beat):
    """Блоки, которые автор утвердил для бита: blocks, а в старом формате block."""
    if isinstance(beat.get("blocks"), list):
        return [str(block) for block in beat["blocks"] if block]
    return [beat["block"]] if beat.get("block") else []


def plan_path(scene_path):
    scene_path = Path(scene_path)
    return scene_path.with_name(re.sub(r"(\.scene)?\.json$", ".animation.json", scene_path.name))


def item_label(item, number):
    return item.get("id") or f"{item.get('block')}_{number}"


def plan_errors(plan):
    """Ошибки самого плана: повтор id, неверный формат blocks."""
    errors, seen = [], set()
    for number, beat in enumerate(plan.get("beats") or []):
        ident = beat_id(beat, number)
        if ident in seen:
            errors.append(f"бит {number}: id {ident} повторяется")
        seen.add(ident)
        if "blocks" in beat and not (isinstance(beat["blocks"], list) and all(isinstance(b, str) for b in beat["blocks"])):
            errors.append(f"бит {ident}: blocks должен быть списком имён блоков")
    return errors


def coverage(scene, plan, helpers=()):
    """Ошибки и предупреждения сверки сцены с планом.

    Блоки общих элементов (beat "global" или always без beat) засчитываются каждому биту.
    Вспомогательные блоки из настройки plan_check.helper_blocks обязательны только на словах:
    их отсутствие — предупреждение. Сцена без поля beat у элементов — старый формат: сверка
    пропускается с предупреждением.
    """
    items = scene.get("items") or []
    beats = plan.get("beats") or []
    helpers = set(helpers)
    if not any("beat" in item for item in items):
        return [], ["элементы сцены не привязаны к битам плана (поле beat): сверка с планом пропущена"]

    errors, warnings = [], []
    ids = [beat_id(beat, number) for number, beat in enumerate(beats)]
    by_beat = {ident: [] for ident in ids}
    shared = set()
    for number, item in enumerate(items):
        label, beat = item_label(item, number), item.get("beat")
        if beat == GLOBAL_BEAT or (beat is None and item.get("always")):
            shared.add(item.get("block"))
        elif beat is None:
            warnings.append(f"{label}: не привязан к биту плана (поле beat)")
        elif beat not in by_beat:
            errors.append(f"{label}: бит {beat} не найден в плане")
        else:
            by_beat[beat].append(item)

    for number, beat in enumerate(beats):
        ident = ids[number]
        need = approved_blocks(beat)
        main = [block for block in need if block not in helpers]
        own = {item.get("block") for item in by_beat[ident]}
        used = own | shared
        if not by_beat[ident] and not (need and set(need) <= shared):
            message = f"бит {ident} (слова {beat.get('at_word')}–{beat.get('until_word')}): в сцене нет элементов"
            (errors if main else warnings).append(message)
            continue
        for block in main:
            if block not in used:
                errors.append(
                    f"бит {ident}: нет утверждённого блока {block}. Заменять приём без решения автора нельзя: "
                    f"верни {block} или согласуй с автором замену и запиши её в план"
                )
        unused = sorted(block for block in need if block in helpers and block not in used)
        if unused:
            warnings.append(f"бит {ident}: вспомогательные блоки {', '.join(unused)} из плана не использованы")
        extra = sorted(str(b) for b in own - set(need) - helpers) if need else []
        if extra:
            warnings.append(f"бит {ident}: блоки {', '.join(extra)} не указаны в плане; если это замена приёма, спроси автора")
    return errors, warnings


def block_info(block, folder, base):
    for root in (Path(folder), Path(base)):
        path = root / "blocks" / str(block) / "block.json"
        if path.is_file():
            return read_json(path)
    return {}


def expected_targets(description):
    """Из описания параметра target вида «id блока array_cells» берёт имена блоков.

    Открытый список («или другого ...») ничего не ограничивает.
    """
    match = re.match(r"id блока ([a-z0-9_, ]+(?: или [a-z0-9_]+)?)(.*)", str(description))
    if not match or "друг" in match.group(2) or "друг" in match.group(1):
        return []
    return [name for name in re.split(r",\s*|\s+или\s+", match.group(1).strip()) if name]


def target_issues(scene, folder, base):
    """target должен указывать на уже построенный элемент: движок строит элементы по порядку."""
    errors, warnings = [], []
    items = scene.get("items") or []
    position = {item_label(item, number): number for number, item in enumerate(items)}
    for number, item in enumerate(items):
        target = (item.get("params") or {}).get("target")
        if not isinstance(target, str):
            continue
        label = item_label(item, number)
        if target not in position:
            errors.append(f"{label}: target {target} — такого элемента в сцене нет")
            continue
        if position[target] >= number:
            errors.append(f"{label}: target {target} стоит в сцене позже; движок строит по порядку, перенеси {target} выше")
            continue
        wanted = expected_targets(block_info(item.get("block"), folder, base).get("params", {}).get("target", ""))
        actual = items[position[target]].get("block")
        if wanted and actual not in wanted:
            warnings.append(f"{label}: блок {item.get('block')} ждёт target блока {' или '.join(wanted)}, а {target} — {actual}")
    return errors, warnings


def check(scene_path, base, helpers=()):
    """Все проверки этого модуля: (ошибки, предупреждения). helpers: настройка plan_check.helper_blocks."""
    scene_path = Path(scene_path)
    scene = read_json(scene_path)
    errors, warnings = target_issues(scene, scene_path.parent, base)
    plan_file = plan_path(scene_path)
    if plan_file.is_file():
        plan = read_json(plan_file)
        errors += plan_errors(plan)
        more_errors, more_warnings = coverage(scene, plan, helpers)
        errors += more_errors
        warnings += more_warnings
    return errors, warnings


def coverage_lines(scene, plan):
    """Таблица: бит, слова, утверждённые блоки, элементы сцены."""
    items = scene.get("items") or []
    lines = []
    for number, beat in enumerate(plan.get("beats") or []):
        ident = beat_id(beat, number)
        mine = [f"{item_label(item, n)}:{item.get('block')}" for n, item in enumerate(items) if item.get("beat") == ident]
        lines.append(f"{ident} [{beat.get('at_word')}–{beat.get('until_word')}] план: {', '.join(approved_blocks(beat)) or 'новый блок'} | сцена: {', '.join(mine) or '—'}")
    return lines


def main():
    from settings import settings_for
    from validate import resolve_base

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scene", type=Path)
    args = parser.parse_args()
    scene_path = args.scene.resolve()
    scene = read_json(scene_path)
    settings = settings_for(scene_path)
    base = resolve_base(scene["base"], settings)
    errors, warnings = check(scene_path, base, settings["plan_check"]["helper_blocks"])
    plan_file = plan_path(scene_path)
    if plan_file.is_file():
        print("\n".join(coverage_lines(scene, read_json(plan_file))))
    for error in errors:
        print(f"Ошибка: {error}")
    for warning in warnings:
        print(f"Предупреждение: {warning}")
    if errors:
        sys.exit(1)
    print("Сверка пройдена" + (" с предупреждениями" if warnings else ""))


if __name__ == "__main__":
    main()
