#!/usr/bin/env python3
"""Показывает транскрипт и сцену коротким текстом вместо тяжёлого JSON.

Пример:
    python3 view.py text F1.timeline.json --from-word 20 --to-word 60
    python3 view.py text F1.timeline.json --review
    python3 view.py scene F1.scene.json
    python3 view.py beats F1.scene.json --beat b3
    python3 view.py beats F1.scene.json --from-beat 9
    python3 view.py state F1.scene.json --at-word 130
    python3 view.py timing F1.scene.json
    python3 view.py sounds F1.scene.json
    python3 view.py layout F1.scene.json
    python3 view.py check F1.scene.json
    python3 view.py hash F1.timeline.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

from plan_check import approved_blocks, beat_id, check as plan_check, plan_path
from settings import load_settings, settings_for
from validate import read_json, resolve_base, resolve_path


def is_long(phrase, words, timing):
    count = phrase["last_word"] - phrase["first_word"] + 1
    seconds = words[phrase["last_word"]]["end"] - words[phrase["first_word"]]["start"]
    return count >= timing["long_phrase_words"] or seconds >= timing["long_phrase_seconds"]


def zone_start(phrase, timing):
    """Первое слово второй части фразы, где должна приходить анимация."""
    first, last = phrase["first_word"], phrase["last_word"]
    return first + round((last - first) * timing["long_phrase_zone"])


def text_lines(data, timing, first=0, last=None):
    """Фразы с номерами слов. У длинной указана зона, куда привязывать анимацию."""
    words = data["words"]
    last = len(words) - 1 if last is None else last
    for phrase in data["phrases"]:
        if phrase["last_word"] < first or phrase["first_word"] > last:
            continue
        marks = ""
        if is_long(phrase, words, timing):
            marks = f" | длинная, опора: {zone_start(phrase, timing)}-{phrase['last_word']}"
        span = words[phrase["last_word"]]["end"] - words[phrase["first_word"]]["start"]
        count = phrase["last_word"] - phrase["first_word"] + 1
        # Короткая реплика из пары слов всегда «быстрая»: темп считаем только у фраз от пяти слов.
        if timing.get("fast_words_per_second") and count >= 5 and span > 0 and count / span >= timing["fast_words_per_second"]:
            marks += f" | быстрая речь, {count / span:.1f} сл/с"
        tokens = " ".join(f"{n}:{words[n]['word']}" for n in range(phrase["first_word"], phrase["last_word"] + 1))
        yield f"{words[phrase['first_word']]['start']:.2f}-{words[phrase['last_word']]['end']:.2f}{marks}\n  {tokens}"


def review_lines(data):
    """Только слова на проверку, с соседями."""
    words = data["words"]
    for word in words:
        if word.get("needs_review"):
            around = " ".join(w["word"] for w in words[max(0, word["i"] - 2):word["i"] + 3])
            yield f"{word['i']} {word['start']:.2f} «{word['word']}» ({word.get('confidence')}): {around}"


def item_line(item, number):
    place = ""
    if "at_word" in item:
        place = f"слово {item['at_word']}"
        if "until_word" in item:
            place += f"-{item['until_word']}"
    elif "after" in item:
        place = f"после {item['after']}"
    elif "at" in item:
        place = f"{item['at']} с"
    elif item.get("always"):
        place = "всегда"
    delay = f" +{item['delay']}" if item.get("delay") else ""
    return f"{number} {item.get('id') or '-'} {item.get('block')} [{place}{delay}]"


def scene_lines(scene, data, timing):
    """Текст и элементы сцены рядом: видно, что происходит на какой фразе."""
    items = list(enumerate(scene["items"]))
    for number, item in items:
        if "at_word" not in item:
            yield item_line(item, number)
    for phrase in data["phrases"]:
        yield from text_lines({"words": data["words"], "phrases": [phrase]}, timing)
        for number, item in items:
            if phrase["first_word"] <= item.get("at_word", -1) <= phrase["last_word"]:
                yield "  > " + item_line(item, number)


def item_beats(item):
    """Бит элемента: строка или список, если объект живёт в нескольких битах."""
    beat = item.get("beat")
    return beat if isinstance(beat, list) else [beat] if beat else []


def item_alive(item, word):
    if item.get("always") or "at_word" not in item:
        return item.get("always") or False
    return item["at_word"] <= word and item.get("until_word", 10 ** 9) >= word


def state_lines(scene, data, layout, word, cfg=None):
    """Что в кадре на слове: объекты, их слова и рамки. Нужна на стыке расширения вместо чтения всей сцены."""
    cfg = cfg or load_settings()["layout"]
    words = data["words"]
    word = min(word, len(words) - 1)
    moment = words[word]["start"]
    boxes = {}
    for box in (layout or {}).get("boxes", []):
        if box["opacity"] > 0 and box["time"] <= moment + cfg["visible_ahead"] and box["until"] >= moment - cfg["visible_behind"]:
            frame = boxes.setdefault(box["item"], [box["left"], box["top"], box["right"], box["bottom"]])
            frame[:] = [min(frame[0], box["left"]), min(frame[1], box["top"]), max(frame[2], box["right"]), max(frame[3], box["bottom"])]
    yield f"слово {word} «{words[word]['word']}», {moment:.2f} с"
    alive = [item for item in scene["items"] if item_alive(item, word) or item.get("id") in boxes]
    for item in alive:
        until = item.get("until_word", "конца")
        frame = boxes.get(item.get("id"))
        place = f" рамка {frame[0]:.0f},{frame[1]:.0f}–{frame[2]:.0f},{frame[3]:.0f}" if frame else ""
        target = f" → {item['params']['target']}" if isinstance(item.get("params"), dict) and item["params"].get("target") else ""
        yield f"  {item.get('id') or '-'} {item.get('block')}{target} [{item.get('at_word', '-')}–{until}]{place}"
    if layout:
        height = layout["height"]
        bottom = [i for i, f in boxes.items() if f[3] > height * cfg["bottom_zone"]]
        yield "  нижняя полоса: " + (f"занята ({', '.join(bottom)})" if bottom else "свободна")


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def beat_lines(scene, plan, data, only=None, first_beat=0):
    """Досье битов для ревью кода: постановка плана, текст и элементы сцены этого бита рядом.

    first_beat пропускает биты до этого номера: при расширении смотрят только новые биты.
    """
    words = data["words"]
    items = list(enumerate(scene["items"]))
    known = set()
    beats = plan.get("beats", [])
    for number, beat in enumerate(beats):
        bid = beat_id(beat, number)
        known.add(bid)
        if (only and bid not in only) or number < first_beat:
            continue
        first = beat.get("at_word", 0)
        last = beat.get("until_word", first)
        if number + 1 < len(beats) and "until_word" not in beat:
            last = max(first, beats[number + 1].get("at_word", first) - 1)
        last = min(last, len(words) - 1)
        yield f"== {bid} слова {first}-{last} | блоки: {', '.join(approved_blocks(beat)) or '-'}"
        if beat.get("intent"):
            yield f"  замысел: {beat['intent']}"
        if beat.get("params"):
            yield f"  данные плана: {compact(beat['params'])}"
        yield "  текст: " + " ".join(f"{n}:{words[n]['word']}" for n in range(first, last + 1))
        for index, item in items:
            if item_beats(item) and bid in item_beats(item):
                yield "  > " + item_line(item, index) + (f" {compact(item['params'])}" if item.get("params") else "")
    if only or first_beat:
        return
    rest = [(index, item) for index, item in items if not set(item_beats(item)) & known]
    if rest:
        yield "== общие и без бита"
        for index, item in rest:
            yield "  > " + item_line(item, index) + (f" {compact(item['params'])}" if item.get("params") else "")


def item_lead(scene, tokens, item):
    for layer in (item, scene):
        lead = layer.get("tokens", {}).get("motion", {}).get("lead")
        if lead is not None:
            return lead
    return tokens["motion"]["lead"]


def timing_warnings(scene, data, tokens, timing):
    """Находит элементы не в такт речи. Лесенка на одном слове и шаги внутри фразы не считаются ошибкой."""
    words = data["words"]
    warnings = []
    by_word = {n: p for p in data["phrases"] for n in range(p["first_word"], p["last_word"] + 1)}
    timed = [(n, i) for n, i in enumerate(scene["items"]) if i.get("at_word") is not None and not i.get("always")]
    seen = set()
    for number, item in timed:
        at = item["at_word"]
        label = item.get("id") or f"элемент {number}"
        ahead = item_lead(scene, tokens, item) - item.get("delay", 0)
        if ahead < 0 and at not in seen:
            warnings.append(f"{label}: опаздывает за словом {at} на {-ahead:.2f} с")
        elif ahead > timing["max_early"]:
            warnings.append(f"{label}: раньше слова {at} на {ahead:.2f} с, предел {timing['max_early']}")
        seen.add(at)

        phrase = by_word.get(at)
        if not phrase or not is_long(phrase, words, timing) or item.get("anchor_reason"):
            continue
        alone = sum(by_word.get(other["at_word"]) is phrase for _, other in timed) == 1
        if alone and at < zone_start(phrase, timing):
            warnings.append(
                f"{label}: один объект на длинную фразу стоит в её начале, опора {zone_start(phrase, timing)}-{phrase['last_word']}"
            )
    return warnings


def sound_log_path(scene):
    return scene.with_name(re.sub(r"(\.scene)?\.json$", ".sounds.json", scene.name))


def layout_path(scene):
    return scene.with_name(re.sub(r"(\.scene)?\.json$", ".layout.json", scene.name))


def sound_warnings(sounds, timing):
    """Удар звука должен попасть в якорь (без якоря: середина в середину анимации), а события не звучать подряд.

    Подложка тянется под анимацией и в этих проверках не участвует. Удары одной серии проверяются по своему разрыву.
    """
    warnings = []
    centre = lambda sound: (sound["start"] + sound["end"]) / 2
    moment = lambda sound: sound["hit"] if sound.get("hit") is not None else centre(sound)
    strikes = [sound for sound in sounds if sound.get("role") != "bed"]
    for sound in strikes:
        if sound.get("anchor") is not None:
            shift = moment(sound) - sound["anchor"]
            target = "якоря"
        else:
            shift = centre(sound) - (sound["animation_start"] + sound["animation_end"]) / 2
            target = "середины анимации"
        # В начале ролика звук нельзя сдвинуть левее нуля.
        if abs(shift) > timing["sound_max_shift"] and sound["start"] > 0:
            side = "позже" if shift > 0 else "раньше"
            warnings.append(f"{sound['item']}: {sound['sound']} {side} {target} на {abs(shift):.2f} с")
    ordered = sorted(strikes, key=moment)
    for first, second in zip(ordered, ordered[1:]):
        gap = moment(second) - moment(first)
        series = first.get("series")
        if series is not None and series == second.get("series"):
            if gap < timing["sound_series_gap"]:
                warnings.append(f"{first['item']}: удары серии {series} теснее {timing['sound_series_gap']} с, разрыв {gap:.2f} с")
        elif gap < timing["sound_min_gap"]:
            warnings.append(f"{first['item']} и {second['item']}: звуки подряд, разрыв {gap:.2f} с, отключите один: sound false")
    return warnings


def layout_warnings(layout, cfg):
    """Находит выход за безопасное поле и наложение текста на текст. Большой JSON читает скрипт, агент видит итог."""
    width, height = layout["width"], layout["height"]
    margin = cfg["safe_margin"]
    visible = [box for box in layout["boxes"] if box["opacity"] > 0]
    objects = [
        box for box in visible
        if box["right"] - box["left"] < width * cfg["full_frame"] or box["bottom"] - box["top"] < height * cfg["full_frame"]
    ]
    warnings = []
    reported = set()
    for box in objects:
        outside = box["left"] < margin or box["top"] < margin or box["right"] > width - margin or box["bottom"] > height - margin
        if outside and box["item"] not in reported:
            reported.add(box["item"])
            warnings.append(f"{box['item']}: {box['layer']} вне безопасного поля на {box['time']:.2f} с")
    texts = [box for box in objects if box["text"]]
    for index, first in enumerate(texts):
        for second in texts[index + 1:]:
            together = max(first["time"], second["time"]) < min(first["until"], second["until"])
            wide = min(first["right"], second["right"]) - max(first["left"], second["left"])
            tall = min(first["bottom"], second["bottom"]) - max(first["top"], second["top"])
            pair = tuple(sorted((first["item"], second["item"])))
            if together and first["item"] != second["item"] and min(wide, tall) >= cfg["min_overlap"] and pair not in reported:
                reported.add(pair)
                warnings.append(f"{first['item']}: текст перекрывает {second['item']} на {max(first['time'], second['time']):.2f} с")
    return warnings


def scene_warnings(scene_path, settings):
    """Все проверки собранной сцены одним списком: план и target, тайминг, звуки, раскладка."""
    scene_path = Path(scene_path).resolve()
    scene = read_json(scene_path)
    data = read_json(resolve_path(scene["timeline"], scene_path.parent))
    base = resolve_base(scene["base"], settings)
    tokens = read_json(base / "tokens.json")
    errors, plan_warnings = plan_check(scene_path, base, settings.get("plan_check", {}).get("helper_blocks", []))
    warnings = [f"ошибка: {error}" for error in errors] + plan_warnings
    warnings += timing_warnings(scene, data, tokens, settings["timing"])
    log = sound_log_path(scene_path)
    if log.is_file():
        warnings += sound_warnings(read_json(log)["sounds"], settings["timing"])
    layout = layout_path(scene_path)
    if layout.is_file():
        layout_data = read_json(layout)
        warnings += layout_warnings(layout_data, settings["layout"])
        warnings += weight_warnings(layout_data, data, settings["layout"])
        warnings += motion_warnings(layout_data, scene, data, settings)
    return warnings


def motion_warnings(layout, scene, data, settings):
    """Косяки движения по выборкам слоёв из AE (layout.tracks): видно раньше слова, висит после ухода,
    скачок положения, мигание, застрявшая полупрозрачность, уход за край. Картинки не нужны: только числа."""
    tracks = layout.get("tracks")
    if not tracks:
        return []
    cfg, timing = settings["layout"], settings["timing"]
    words = data["words"]
    fps = (scene.get("comp") or {}).get("fps") or settings["ae"].get("review_fps", 30)
    frame = 1.0 / fps
    items = {item.get("id"): item for item in scene["items"] if item.get("id")}
    width, height, margin = layout["width"], layout["height"], cfg["safe_margin"]
    found, seen = [], set()

    def report(item, kind, text):
        if (item, kind) not in seen:
            seen.add((item, kind))
            found.append(f"{item}: {text}")

    for track in tracks:
        name, samples = track["item"], sorted(track["samples"])
        if not samples:
            continue
        # Седьмое поле drawn: слой с прозрачностью, но без нарисованного (масштаб 0, обрезка, скрытые буквы) не виден.
        shown = lambda s: s[1] > 1 and (len(s) < 7 or s[6])
        item = items.get(name, {})
        start = words[item["at_word"]]["start"] if "at_word" in item and item["at_word"] < len(words) else None
        until = words[item["until_word"]]["end"] if "until_word" in item and item["until_word"] < len(words) else None
        visible = [s for s in samples if shown(s)]
        if not visible:
            report(name, "never", f"слой «{track['layer']}» ни разу не виден")
            continue
        if start is not None and visible[0][0] < start - timing["max_early"] - frame and not item.get("anchor_reason"):
            report(name, "early", f"виден раньше своего слова: {visible[0][0]:.2f} с при слове на {start:.2f} с")
        if until is not None and visible[-1][0] > until + cfg["exit_allowance"]:
            report(name, "late", f"висит после своего конца (until_word {item['until_word']}) до {visible[-1][0]:.2f} с")
        for a, b in zip(samples, samples[1:]):
            dt = b[0] - a[0]
            if dt <= cfg["frame_tolerance"] * frame and shown(a) and shown(b):
                ca, cb = ((a[2] + a[4]) / 2, (a[3] + a[5]) / 2), ((b[2] + b[4]) / 2, (b[3] + b[5]) / 2)
                if abs(cb[0] - ca[0]) + abs(cb[1] - ca[1]) > cfg["max_jump_px"]:
                    report(name, "jump", f"скачок на {abs(cb[0] - ca[0]) + abs(cb[1] - ca[1]):.0f} px за кадр на {b[0]:.2f} с")
            inside = track["in"] + frame < a[0] and b[0] < track["out"] - frame
            if dt <= cfg["frame_tolerance"] * frame and inside and abs(b[1] - a[1]) > cfg["flash_delta"]:
                report(name, "flash", f"прозрачность прыгает {a[1]:.0f}→{b[1]:.0f} % за кадр на {b[0]:.2f} с")
        # Последняя серия выборок, где слой ни виден, ни скрыт: дольше секунды — он застрял на полпути.
        tail = []
        for s in reversed(samples):
            if not 1 < s[1] < cfg["opaque_percent"] or s[0] <= track["in"] + cfg["settle"]:
                break
            tail.insert(0, s)
        if tail and tail[-1][0] - tail[0][0] > cfg["stuck_seconds"]:
            report(name, "stuck", f"застрял полупрозрачным ({tail[-1][1]:.0f} %) с {tail[0][0]:.2f} с")
        for index, s in enumerate(visible):
            settled = s[0] > track["in"] + cfg["settle"]
            # Во время ухода (прозрачность падает) край кадра не ошибка: объект улетает.
            fading = index + 1 < len(visible) and visible[index + 1][1] < s[1]
            if fading:
                continue
            big = (s[4] - s[2]) >= width * cfg["full_frame"] or (s[5] - s[3]) >= height * cfg["full_frame"]
            if settled and not big and track.get("text") and (s[2] < margin or s[3] < margin or s[4] > width - margin or s[5] > height - margin):
                report(name, "edge", f"текст «{track['layer']}» за безопасным полем на {s[0]:.2f} с")
                break
    return found


def weight_warnings(layout, data, cfg):
    """Тяжёлая композиция: слишком много видимых слоёв на минуту речи — её трудно смотреть и долго рендерить."""
    limit = cfg.get("max_layers_per_minute")
    words = data.get("words") or []
    if not limit or not words:
        return []
    minutes = max(words[-1]["end"] / 60, cfg.get("min_minutes", 0.5))
    layers = len({(box["item"], box["layer"]) for box in layout["boxes"]})
    per_minute = layers / minutes
    if per_minute <= limit:
        return []
    return [f"тяжёлая композиция: {layers} видимых слоёв, {per_minute:.0f} в минуту при пределе {limit}. "
            "Переиспользуй объекты вместо новых, разбей фрагмент или сведи готовую часть в pre-composition"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["text", "scene", "beats", "state", "timing", "sounds", "layout", "check", "hash"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--from-word", type=int, default=0)
    parser.add_argument("--to-word", type=int)
    parser.add_argument("--review", action="store_true", help="для text: только слова на проверку")
    parser.add_argument("--beat", action="append", help="для beats: только эти биты, можно несколько раз")
    parser.add_argument("--from-beat", type=int, default=0, help="для beats: начиная с бита с этим номером (extension.beats_from)")
    parser.add_argument("--at-word", type=int, help="для state: слово, на котором показать кадр")
    args = parser.parse_args()

    settings = settings_for(args.path)
    timing = settings["timing"]
    if args.command == "hash":
        lines = [read_json(args.path).get("transcript_hash", "транскрипт не заморожен")]
    elif args.command == "text":
        data = read_json(args.path)
        lines = review_lines(data) if args.review else text_lines(data, timing, args.from_word, args.to_word)
    elif args.command == "check":
        lines = scene_warnings(args.path, settings) or ["Проверки пройдены"]
    elif args.command == "layout":
        path = args.path.resolve()
        data = read_json(layout_path(path))
        lines = layout_warnings(data, settings["layout"]) or ["Раскладка в норме"]
    elif args.command == "sounds":
        log = sound_log_path(args.path.resolve())
        if not log.is_file():
            sys.exit(f"Нет журнала звуков {log}: он появляется после сборки")
        lines = sound_warnings(read_json(log)["sounds"], timing) or ["Звуки в норме"]
    else:
        path = args.path.resolve()
        scene = read_json(path)
        data = read_json(resolve_path(scene["timeline"], path.parent))
        if args.command == "scene":
            lines = scene_lines(scene, data, timing)
        elif args.command == "beats":
            plan = plan_path(path)
            if not plan.is_file():
                sys.exit(f"Нет плана {plan}")
            lines = beat_lines(scene, read_json(plan), data, args.beat, args.from_beat)
        elif args.command == "state":
            if args.at_word is None:
                sys.exit("укажите --at-word")
            layout = layout_path(path)
            lines = state_lines(scene, data, read_json(layout) if layout.is_file() else None, args.at_word, settings["layout"])
        else:
            tokens = read_json(resolve_base(scene["base"], settings) / "tokens.json")
            lines = timing_warnings(scene, data, tokens, timing) or ["Тайминг в норме"]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
