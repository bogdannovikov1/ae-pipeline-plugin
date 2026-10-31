#!/usr/bin/env python3
"""Замораживает транскрипт после ревью или снимает заморозку.

Пример:
    python3 approve_transcript.py voice.timeline.json
    python3 approve_transcript.py voice.timeline.json --reopen
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def transcript_hash(data):
    """Считает хэш по словам: по нему следующие этапы проверяют актуальность транскрипта."""
    fields = [[w["i"], w["word"], w["start"], w["end"]] for w in data["words"]]
    text = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_warnings(data):
    """Возвращает места, о которых стоит напомнить: заморозку они не останавливают."""
    words = data["words"]
    warnings = [f"слово {w['i']} «{w['word']}» ждёт проверки" for w in words if w["needs_review"]]
    warnings += [f"слово {w['i']} «{w['word']}» заходит на следующее" for w, n in zip(words, words[1:]) if w["end"] > n["start"]]
    return warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("timeline", type=Path, help="путь к .timeline.json")
    parser.add_argument("--reopen", action="store_true", help="снять заморозку для новых правок")
    args = parser.parse_args()

    data = json.loads(args.timeline.read_text(encoding="utf-8"))

    if args.reopen:
        data["status"] = "draft"
        data.pop("transcript_hash", None)
        data.pop("approved_at", None)
    else:
        for warning in find_warnings(data):
            print(f"Внимание: {warning}")
        data["status"] = "approved"
        data["transcript_hash"] = transcript_hash(data)
        data["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    args.timeline.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Готово: {args.timeline} (статус: {data['status']})")


if __name__ == "__main__":
    main()
