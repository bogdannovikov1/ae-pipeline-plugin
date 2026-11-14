#!/usr/bin/env python3
"""Проверяет файлы этапов без модели и After Effects.

Пример:
    python3 validate.py timeline F1.timeline.json --final
    python3 validate.py scene F1.scene.json
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from approve_transcript import transcript_hash
from plan_check import check as plan_check, plan_errors
from settings import bases_dir, settings_for


ENGINE_VERSION = 4  # растёт, когда блоки базы перестают работать на старом движке


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_stamp_path(scene):
    """Отметка успешной сборки: её пишет ae_run.py, а не агент."""
    scene = Path(scene)
    return scene.with_name(re.sub(r"(\.scene)?\.json$", ".build.json", scene.name))


def check_built(scene):
    """Сборка считается состоявшейся, если AE собрал именно эту версию сцены."""
    stamp = build_stamp_path(scene)
    if not stamp.is_file():
        return ["сцена не собрана: нет отметки сборки, запустите ae_run.py build"]
    if read_json(stamp)["scene_hash"] != file_hash(scene):
        return ["сцена изменилась после сборки, пересоберите её"]
    return []


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_timeline(path, final=False):
    """Возвращает список ошибок транскрипта. final требует заморозки."""
    data = read_json(path)
    words = data.get("words") or []
    errors = [] if words else ["в транскрипте нет слов"]
    for number, word in enumerate(words):
        if word.get("i") != number:
            errors.append(f"слово {number}: номер i равен {word.get('i')}")
        if word.get("start", 0) < 0:
            errors.append(f"слово {number}: отрицательное начало")
        if word.get("start", 0) > word.get("end", -1):
            errors.append(f"слово {number}: начало позже конца")
    for word, following in zip(words, words[1:]):
        if word.get("start", 0) > following.get("start", 0):
            errors.append(f"слово {word.get('i')}: порядок таймкодов нарушен")
    if final:
        if data.get("status") != "approved":
            errors.append("транскрипт не заморожен")
        elif not errors and data.get("transcript_hash") != transcript_hash(data):
            errors.append("transcript_hash не совпадает со словами")
    return errors


def word_errors(item, count, label):
    """Проверяет ссылки на номера слов и их порядок."""
    errors = []
    for field in ("at_word", "until_word"):
        value = item.get(field)
        if value is not None and not 0 <= value < count:
            errors.append(f"{label}: {field} {value} вне транскрипта из {count} слов")
    start, end = item.get("at_word"), item.get("until_word")
    if not errors and start is not None and end is not None and end < start:
        errors.append(f"{label}: until_word раньше at_word")
    return errors


def check_animation(path, timeline):
    """Проверяет план: актуальность транскрипта и номера слов."""
    plan = read_json(path)
    data = read_json(timeline)
    errors = []
    if plan.get("transcript_hash") != data.get("transcript_hash"):
        errors.append("план сделан по другому транскрипту")
    beats = plan.get("beats") or []
    if not beats:
        errors.append("в плане нет битов")
    for number, beat in enumerate(beats):
        errors += word_errors(beat, len(data.get("words", [])), f"бит {number}")
    return errors + plan_errors(plan)


def resolve_path(value, folder):
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path(folder) / path


def resolve_base(value, settings):
    """База задаётся именем в папке баз или путём."""
    if "/" not in value:
        return bases_dir(settings) / value
    return Path(value).expanduser()


def adjust_errors(item, ids, label):
    """Поле adjust: ручные правки автора, которые перенёс ae_run.py diff --apply."""
    adjust = item.get("adjust")
    if adjust is None:
        return []
    if not isinstance(adjust, dict):
        return [f"{label}: adjust должен быть объектом"]
    errors = []
    number = (int, float)
    offset = adjust.get("offset")
    if offset is not None and not (isinstance(offset, list) and 2 <= len(offset) <= 3
                                   and all(isinstance(x, number) for x in offset)):
        errors.append(f"{label}: adjust.offset — список из 2 или 3 чисел")
    scale = adjust.get("scale")
    if scale is not None and not (isinstance(scale, number) and scale > 0):
        errors.append(f"{label}: adjust.scale — положительное число")
    if "rotation" in adjust and not isinstance(adjust["rotation"], number):
        errors.append(f"{label}: adjust.rotation — число градусов")
    if "below" in adjust and "above" in adjust:
        errors.append(f"{label}: adjust.below и adjust.above вместе не бывают")
    for key in ("below", "above"):
        if key in adjust and (adjust[key] not in ids or adjust[key] == item.get("id")):
            errors.append(f"{label}: adjust.{key} ссылается на {adjust[key]}, такого другого элемента нет")
    layers = adjust.get("layers")
    if layers is not None:
        props = {"position", "scale", "rotation", "opacity", "anchor"}
        good = isinstance(layers, dict) and all(
            isinstance(one, dict) and set(one) <= props and all(
                isinstance(edit, dict) and (("v" in edit) != ("keys" in edit))
                and (isinstance(edit.get("keys"), list) and all(isinstance(k, list) and len(k) >= 2 and isinstance(k[0], number)
                                                               for k in edit["keys"]) if "keys" in edit else True)
                for edit in one.values())
            for one in layers.values())
        if not good:
            errors.append(f"{label}: adjust.layers — {{слой: {{свойство: {{v}} или {{keys: [[время, значение, изгиб]]}}}}}}, "
                          f"свойства {', '.join(sorted(props))}")
    unknown = set(adjust) - {"offset", "scale", "rotation", "below", "above", "layers"}
    if unknown:
        errors.append(f"{label}: в adjust неизвестные поля {', '.join(sorted(unknown))}")
    return errors


def check_scene(path, settings=None):
    """Проверяет сцену: файлы, транскрипт, блоки и ссылки между элементами."""
    settings = settings or settings_for(path)
    path = Path(path)
    scene = read_json(path)
    folder = path.parent
    errors = []

    for field in ("base", "audio", "timeline", "comp", "items"):
        if field not in scene:
            errors.append(f"в сцене нет поля {field}")
    if errors:
        return errors

    base = resolve_base(scene["base"], settings)
    if not (base / "tokens.json").is_file():
        errors.append(f"база не найдена: {base}")
    else:
        needed = read_json(base / "base.json").get("engine", 1) if (base / "base.json").is_file() else 1
        if needed != ENGINE_VERSION:
            errors.append(f"база рассчитана на движок {needed}, а плагин содержит движок {ENGINE_VERSION}: обновите плагин или базу")
    if not resolve_path(scene["audio"], folder).is_file():
        errors.append(f"аудио не найдено: {scene['audio']}")
    timeline = resolve_path(scene["timeline"], folder)
    if not timeline.is_file():
        return errors + [f"транскрипт не найден: {scene['timeline']}"]

    data = read_json(timeline)
    if "transcript_hash" in scene and scene["transcript_hash"] != data.get("transcript_hash"):
        errors.append("сцена сделана по другому транскрипту")

    count = len(data.get("words", []))
    known = set()
    ids = {item.get("id") or f"{item.get('block')}_{n}" for n, item in enumerate(scene["items"])}
    for number, item in enumerate(scene["items"]):
        label = item.get("id") or f"элемент {number}"
        block = item.get("block")
        if not any((root / "blocks" / str(block) / "block.jsxinc").is_file() for root in (folder, base)):
            errors.append(f"{label}: неизвестный блок {block}")
        if "after" in item and item["after"] not in known:
            errors.append(f"{label}: after ссылается на {item['after']}, которого раньше нет")
        if not item.get("always") and not {"at_word", "after", "at"} & set(item):
            errors.append(f"{label}: не задано время")
        errors += word_errors(item, count, label)
        errors += adjust_errors(item, ids, label)
        known.add(item.get("id") or f"{block}_{number}")
    if not errors and (base / "tokens.json").is_file():
        errors += plan_check(path, base, settings.get("plan_check", {}).get("helper_blocks", []))[0]
    return errors


def check_report(path):
    """Отчёт ревью кода: сводка и находки с серьёзностью, местом и исправлением."""
    try:
        data = read_json(path)
    except (OSError, ValueError) as error:
        return [f"не читается отчёт {path.name}: {error}"]
    errors = []
    if not isinstance(data.get("summary"), str):
        errors.append("нет summary")
    findings = data.get("findings")
    if not isinstance(findings, list):
        return errors + ["findings должен быть списком"]
    for number, finding in enumerate(findings):
        if finding.get("severity") not in ("minor", "major", "critical"):
            errors.append(f"находка {number}: severity — minor, major или critical")
        if not finding.get("beat") and not finding.get("item"):
            errors.append(f"находка {number}: укажи beat или item")
        for key in ("what", "fix"):
            if not finding.get(key):
                errors.append(f"находка {number}: нет {key}")
    return errors


def run_check(kind, path, folder, names, settings=None, final=False):
    """Единая точка для конвейера: names подсказывает путь транскрипта."""
    if kind == "timeline":
        return check_timeline(path, final)
    if kind == "animation":
        return check_animation(path, folder / names["timeline"])
    if kind == "scene":
        return check_scene(path, settings)
    if kind == "check":
        return check_report(path)
    if kind == "voice":
        from voice import text_errors
        text = folder / names["voice"]
        if not text.is_file():
            return [f"нет текста озвучки {names['voice']}"]
        return text_errors(text.read_text(encoding="utf-8"), (settings or {}).get("voice", {"allowed_tags": []}))
    raise ValueError(f"Неизвестная проверка: {kind}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=["timeline", "animation", "scene", "check"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--timeline", type=Path, help="для animation: путь к транскрипту")
    parser.add_argument("--final", action="store_true", help="для timeline: требовать заморозку")
    args = parser.parse_args()

    if args.kind == "timeline":
        errors = check_timeline(args.path, args.final)
    elif args.kind == "animation":
        errors = check_animation(args.path, args.timeline or args.path.with_name(args.path.name.replace(".animation.", ".timeline.")))
    elif args.kind == "check":
        errors = check_report(args.path)
    else:
        errors = check_scene(args.path)
    for error in errors:
        print(f"Ошибка: {error}")
    if errors:
        sys.exit(1)
    print("Проверка пройдена")


if __name__ == "__main__":
    main()
