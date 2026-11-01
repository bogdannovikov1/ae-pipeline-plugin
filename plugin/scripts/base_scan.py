#!/usr/bin/env python3
"""Собирает из папки видео факты для обновления базы, чтобы агент не читал всё подряд.

Пример:
    python3 base_scan.py ~/Videos/my_video
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from settings import load_settings


def token_paths(tokens, prefix=""):
    """Разворачивает вложенные токены в ключи вида motion.lead."""
    for key, value in tokens.items():
        if isinstance(value, dict):
            yield from token_paths(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}"


def scan(video, min_repeats):
    """Находит блоки вне базы, повторяющиеся находки ревью и повторяющиеся замены токенов."""
    local_blocks, rejected = [], []
    findings, tokens = Counter(), Counter()
    for fragment in sorted((Path(video) / "fragments").iterdir()):
        if not fragment.is_dir():
            continue
        blocks = fragment / "blocks"
        if blocks.is_dir():
            local_blocks += [f"{fragment.name}/{b.name}" for b in sorted(blocks.iterdir()) if b.is_dir()]
        for report in sorted(fragment.glob("reviews/round_*.json")):
            findings.update(f.get("rule") for f in json.loads(report.read_text(encoding="utf-8"))["findings"])
        for path in fragment.glob("*.scene.json"):
            for item in json.loads(path.read_text(encoding="utf-8")).get("items", []):
                tokens.update(token_paths(item.get("tokens", {})))
        state = fragment / "state.json"
        if state.is_file():
            stages = json.loads(state.read_text(encoding="utf-8"))["stages"]
            rejected += [f"{fragment.name}/{name}: {s['note']}" for name, s in stages.items() if s.get("note")]
    return {
        "local_blocks": local_blocks,
        "repeated_findings": {k: v for k, v in findings.items() if v >= min_repeats},
        "repeated_tokens": {k: v for k, v in tokens.items() if v >= min_repeats},
        "author_notes": rejected,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path, help="папка видео")
    args = parser.parse_args()
    settings = load_settings(args.video)
    result = scan(args.video.expanduser(), settings["base_update"]["min_repeats"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
