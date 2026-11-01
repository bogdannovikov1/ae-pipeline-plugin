#!/usr/bin/env python3
"""Показывает транскрипт и сцену коротким текстом вместо тяжёлого JSON.

Пример:
    python3 view.py text F1.timeline.json --from-word 20 --to-word 60
    python3 view.py text F1.timeline.json --review
    python3 view.py scene F1.scene.json
    python3 view.py timing F1.scene.json
"""
import argparse
from pathlib import Path

from settings import load_settings
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["text", "scene", "timing"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--from-word", type=int, default=0)
    parser.add_argument("--to-word", type=int)
    parser.add_argument("--review", action="store_true", help="для text: только слова на проверку")
    args = parser.parse_args()

    settings = load_settings()
    timing = settings["timing"]
    if args.command == "text":
        data = read_json(args.path)
        lines = review_lines(data) if args.review else text_lines(data, timing, args.from_word, args.to_word)
    else:
        path = args.path.resolve()
        scene = read_json(path)
        data = read_json(resolve_path(scene["timeline"], path.parent))
        if args.command == "scene":
            lines = scene_lines(scene, data, timing)
        else:
            tokens = read_json(resolve_base(scene["base"], settings) / "tokens.json")
            lines = timing_warnings(scene, data, tokens, timing) or ["Тайминг в норме"]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
