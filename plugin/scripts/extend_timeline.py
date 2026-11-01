#!/usr/bin/env python3
"""Дописывает транскрипт новой части в конец транскрипта фрагмента.

Пример:
    python3 extend_timeline.py F1.timeline.json F1_p2.timeline.json

Слова первой части не меняются: новые номера и время продолжают старые.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from settings import settings_for

TIME_FIELDS = ("start", "end", "raw_start", "raw_end")


def audio_duration(path):
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    return float(subprocess.run(command, capture_output=True, text=True, check=True).stdout)


def shift_word(word, index, offset, fps):
    shifted = dict(word, i=index)
    for field in TIME_FIELDS:
        if field in shifted:
            shifted[field] = round(shifted[field] + offset, 3)
    if fps:
        shifted["start_frame"] = round(shifted["start"] * fps)
        shifted["end_frame"] = round(shifted["end"] * fps)
    return shifted


def merge(main, part, offset):
    """Добавляет часть к данным транскрипта. Заморозку нужно поставить заново."""
    count = len(main["words"])
    phrase_count = len(main["phrases"])
    main.setdefault("parts", [{"audio": main["source"], "at": 0, "first_word": 0}])
    main["parts"].append({"audio": part["source"], "at": round(offset, 3), "first_word": count})
    main["words"] += [shift_word(w, count + n, offset, main.get("fps")) for n, w in enumerate(part["words"])]
    for number, phrase in enumerate(part["phrases"]):
        main["phrases"].append(dict(
            phrase,
            i=phrase_count + number,
            start=round(phrase["start"] + offset, 3),
            end=round(phrase["end"] + offset, 3),
            first_word=phrase["first_word"] + count,
            last_word=phrase["last_word"] + count,
        ))
    main["text"] = f"{main['text']} {part['text']}".strip()
    main["status"] = "draft"
    main.pop("transcript_hash", None)
    main.pop("approved_at", None)
    return main


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("timeline", type=Path, help="транскрипт фрагмента")
    parser.add_argument("part", type=Path, help="утверждённый транскрипт новой части")
    args = parser.parse_args()

    data = json.loads(args.timeline.read_text(encoding="utf-8"))
    part = json.loads(args.part.read_text(encoding="utf-8"))
    if part.get("status") != "approved":
        sys.exit("Сначала пройдите ревью новой части и заморозьте её транскрипт.")
    known = data.get("parts", [{"audio": data["source"], "at": 0}])
    if part["source"] in [item["audio"] for item in known]:
        sys.exit(f"Часть уже добавлена: {part['source']}")

    gap = settings_for(args.timeline)["extend"]["gap_seconds"]
    offset = known[-1]["at"] + audio_duration(args.timeline.parent / known[-1]["audio"]) + gap
    shutil.copy(args.timeline, args.timeline.with_name(args.timeline.name.replace(".timeline.", ".timeline.backup.")))
    merge(data, part, offset)
    args.timeline.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Готово: {args.timeline} (часть начинается на {offset:.3f} с, первое слово {data['parts'][-1]['first_word']})")


if __name__ == "__main__":
    main()
