#!/usr/bin/env python3
"""Песочница блока: новый приём живёт отдельно от базы, пока автор его не утвердит.

Пример:
    python3 block_lab.py new soft_pop
    python3 block_lab.py new array_cells --from-base
    python3 block_lab.py set soft_pop params.size=120 tokens.motion.lead=0.2
    python3 block_lab.py build soft_pop
    python3 block_lab.py promote soft_pop
"""
import argparse
import json
import shutil
import sys
import wave
from pathlib import Path

from approve_transcript import transcript_hash
from settings import PLUGIN_ROOT, bases_dir, load_settings
from validate import check_scene, read_json

LAB_SCRIPT = PLUGIN_ROOT / "ae" / "lab_project.jsx"
SCENE = "lab.scene.json"
TIMELINE = "lab.timeline.json"
AUDIO = "lab.wav"
PROJECT = "lab.aep"
BRIEF = "brief.md"
SAMPLE_RATE = 16000

TEMPLATE = """// {id}
// Черновик песочницы: замените анимацию. Вкусовые числа берите из params с запасным значением из Engine.tokens.
Engine.register("{id}", function (params, at) {{
    var layer = Engine.text(params.text || "{id}", "{id}", {{
        font: Engine.font(params.font || "code_strong"), size: params.size || 64,
        color: Engine.color(params.color || "accent_blue"), position: [params.x || 960, params.y || 540]
    }});
    Engine.fadeIn(layer, at, params.duration || 0.3);
    return [layer];
}});
"""


class LabError(Exception):
    pass


def labs_dir(settings):
    return Path(settings["labs_dir"]).expanduser()


def base_dir(settings):
    return bases_dir(settings) / settings["base"]


def lab_dir(settings, block):
    folder = labs_dir(settings) / block
    if not (folder / SCENE).is_file():
        raise LabError(f"Песочницы {block} нет: сначала block_lab.py new {block}")
    return folder


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lab_timeline(seconds, step):
    """Слова-метки через равный шаг: блок привязывается к ним так же, как к речи."""
    count = max(2, int(round(seconds / step)))
    words = [{"i": n, "word": str(n), "start": round(n * step, 3), "end": round(n * step + step * 0.8, 3),
              "needs_review": False} for n in range(count)]
    data = {"schema_version": 4, "status": "approved", "source": AUDIO, "words": words, "phrases": []}
    data["transcript_hash"] = transcript_hash(data)
    return data


def write_silence(path, seconds):
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(SAMPLE_RATE)
        file.writeframes(b"\0\0" * int(SAMPLE_RATE * seconds))


def create(settings, block, from_base=False, seconds=None):
    """Создаёт песочницу: тишина, слова-метки, демо-сцена и черновик или копия блока базы."""
    base = base_dir(settings)
    folder = labs_dir(settings) / block
    if folder.exists():
        raise LabError(f"Песочница уже есть: {folder}")
    in_base = (base / "blocks" / block).is_dir()
    if from_base and not in_base:
        raise LabError(f"В базе нет блока {block}")
    if in_base and not from_base:
        raise LabError(f"В базе уже есть блок {block}: выберите другое имя или --from-base, чтобы улучшать его")

    lab = settings["lab"]
    target = folder / "blocks" / block
    if from_base:
        shutil.copytree(base / "blocks" / block, target)
    else:
        target.mkdir(parents=True)
        (target / "block.jsxinc").write_text(TEMPLATE.format(id=block), encoding="utf-8")
        write_json(target / "block.json", {"id": block, "description": "", "when": "", "params": {}})

    timeline = lab_timeline(seconds or lab["seconds"], lab["word_seconds"])
    write_json(folder / TIMELINE, timeline)
    write_silence(folder / AUDIO, timeline["words"][-1]["end"] + lab["tail"])
    items = []
    if (base / "blocks" / "background_dark").is_dir():
        items.append({"block": "background_dark", "always": True})
    items.append({"id": block, "block": block, "at_word": lab["start_word"], "params": {}, "tokens": {}})
    write_json(folder / SCENE, {
        "base": settings["base"], "audio": AUDIO, "timeline": TIMELINE,
        "transcript_hash": timeline["transcript_hash"], "tail": lab["tail"],
        "comp": {"name": f"lab_{block}", "width": lab["width"], "height": lab["height"]},
        "items": items,
    })
    (folder / BRIEF).write_text(f"# {block}\n\n## Задание автора\n\n## Итерации\n", encoding="utf-8")
    return folder


def parse_value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def set_values(settings, block, assignments):
    """Меняет числа элемента блока в демо-сцене без модели: params.x=1, tokens.motion.lead=0.2."""
    path = lab_dir(settings, block) / SCENE
    scene = read_json(path)
    item = next((it for it in scene["items"] if it.get("id") == block), None)
    if item is None:
        raise LabError(f"В {SCENE} нет элемента с id {block}")
    changes = []
    for assignment in assignments:
        key, sep, raw = assignment.partition("=")
        parts = key.split(".")
        if not sep or len(parts) < 2 or parts[0] not in ("params", "tokens"):
            raise LabError(f"Ожидается params.<ключ>=<значение> или tokens.<путь>=<значение>: {assignment}")
        node = item.setdefault(parts[0], {})
        for part in parts[1:-1]:
            node = node.setdefault(part, {})
        old = node.get(parts[-1])
        node[parts[-1]] = parse_value(raw)
        changes.append(f"{key}: {json.dumps(old, ensure_ascii=False)} -> {json.dumps(node[parts[-1]], ensure_ascii=False)}")
    write_json(path, scene)
    return changes


def build(settings, block, allowed):
    """Собирает демо в проекте песочницы: свой проект сохраняется без вопроса, архив сборок чистится."""
    from ae_run import BUILD_SCRIPT, launch, run_jsx

    folder = lab_dir(settings, block)
    errors = check_scene(folder / SCENE, settings)
    if errors:
        raise LabError("Сцена песочницы не прошла проверку:\n" + "\n".join(f"- {e}" for e in errors))
    project = folder / PROJECT
    # Любой проект песочницы свой: его можно сохранить без вопроса, правки автора в нём остаются.
    autosave = [labs_dir(settings) / "*" / PROJECT]
    launch(settings, allowed)
    run_jsx(LAB_SCRIPT, {"LAB_PROJECT": project, "LAB_MODE": "open"}, settings, autosave)
    run_jsx(BUILD_SCRIPT, {"SCENE_PATH": (folder / SCENE).resolve(), "BASES_DIR": bases_dir(settings)}, settings, autosave)
    comp = read_json(folder / SCENE)["comp"]["name"]
    run_jsx(LAB_SCRIPT, {"LAB_PROJECT": project, "LAB_MODE": "clean", "LAB_COMP": comp}, settings, autosave)
    return project


def promote(settings, block, replace=False, lab=None):
    """Переносит утверждённый блок в базу. Вызывает только librarian после выбора автора.

    В одной песочнице может жить несколько блоков: lab называет её, если она не совпадает с блоком.
    """
    source = lab_dir(settings, lab or block) / "blocks" / block
    for name in ("block.jsxinc", "block.json"):
        if not (source / name).is_file():
            raise LabError(f"В песочнице нет {name}")
    meta = read_json(source / "block.json")
    missing = [field for field in ("description", "when", "params") if not meta.get(field)]
    if missing:
        raise LabError(f"В block.json не заполнено: {', '.join(missing)}")
    target = base_dir(settings) / "blocks" / block
    if target.exists():
        if not replace:
            raise LabError(f"В базе уже есть {block}: замена только с --replace")
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["new", "set", "build", "promote", "path"])
    parser.add_argument("block", help="id блока, snake_case")
    parser.add_argument("values", nargs="*", help="для set: params.<ключ>=<значение> или tokens.<путь>=<значение>")
    parser.add_argument("--from-base", action="store_true", help="для new: начать с копии блока базы")
    parser.add_argument("--seconds", type=float, help="для new: длина демо-сцены вместо lab.seconds")
    parser.add_argument("--launch", action="store_true", help="для build: автор разрешил запустить After Effects")
    parser.add_argument("--lab", help="для promote: песочница, если её имя не совпадает с блоком")
    parser.add_argument("--replace", action="store_true", help="для promote: заменить блок базы")
    args = parser.parse_args()

    settings = load_settings()
    try:
        if args.command == "new":
            print(create(settings, args.block, args.from_base, args.seconds))
        elif args.command == "set":
            print("\n".join(set_values(settings, args.block, args.values)))
        elif args.command == "build":
            print(f"Готово: {build(settings, args.block, args.launch)}")
        elif args.command == "promote":
            print(f"В базе: {promote(settings, args.block, args.replace, args.lab)}")
        else:
            print(lab_dir(settings, args.block))
    except (LabError, RuntimeError) as error:
        sys.exit(f"Отказ: {error}")


if __name__ == "__main__":
    main()
