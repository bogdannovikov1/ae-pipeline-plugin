#!/usr/bin/env python3
"""Пословный транскрипт аудио через MLX Whisper для маркеров After Effects.

Пример:
    python3 transcribe.py voice.mp3 --fps 30 --prompt "After Effects, ExtendScript"
"""
import argparse
import json
import sys
from pathlib import Path

from refine_timings import refine_timeline

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
SCHEMA_VERSION = 4


def build_timeline(result, fps, review_below):
    """Превращает результат Whisper в списки слов и фраз."""
    words = []
    phrases = []

    for segment in result["segments"]:
        first_word = len(words)
        for word_info in segment.get("words", []):
            text = word_info["word"].strip()
            if not text:
                continue
            confidence = round(float(word_info["probability"]), 3)
            word = {
                "i": len(words),
                "word": text,
                "start": round(word_info["start"], 3),
                "end": round(word_info["end"], 3),
                "confidence": confidence,
                "needs_review": confidence < review_below,
            }
            if fps:
                word["start_frame"] = round(word_info["start"] * fps)
                word["end_frame"] = round(word_info["end"] * fps)
            words.append(word)

        if len(words) > first_word:
            phrases.append({
                "i": len(phrases),
                "text": segment["text"].strip(),
                "start": words[first_word]["start"],
                "end": words[-1]["end"],
                "first_word": first_word,
                "last_word": len(words) - 1,
            })

    return words, phrases


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path, help="путь к аудиофайлу")
    parser.add_argument("-o", "--output", type=Path, help="куда сохранить JSON (по умолчанию <аудио>.timeline.json)")
    parser.add_argument("--language", default="ru", help="язык записи (по умолчанию ru)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="модель MLX Whisper")
    parser.add_argument("--prompt", help="подсказка с терминами и брендами")
    parser.add_argument("--fps", type=float, help="добавить номера кадров для этой частоты")
    parser.add_argument("--review-below", type=float, default=0.6, help="порог уверенности для пометки needs_review")
    parser.add_argument("--no-refine", action="store_true", help="не подрезать границы слов по звуку")
    parser.add_argument("--force", action="store_true", help="перезаписать готовый транскрипт вместе с его ревью")
    args = parser.parse_args()

    if not args.audio.is_file():
        sys.exit(f"Файл не найден: {args.audio}")

    output = args.output or args.audio.with_suffix(".timeline.json")
    if output.exists() and not args.force:
        sys.exit(f"Транскрипт уже есть: {output}. Чтобы заменить его вместе с ревью, добавьте --force.")
    if output.exists() and json.loads(output.read_text(encoding="utf-8")).get("status") == "approved":
        sys.exit(f"Транскрипт заморожен: {output}. Сначала снимите заморозку: approve_transcript.py --reopen")

    # Импорт здесь, чтобы --help и ошибки аргументов не ждали загрузку MLX.
    import mlx_whisper

    print(f"Распознаю {args.audio} моделью {args.model}...", file=sys.stderr)
    result = mlx_whisper.transcribe(
        str(args.audio),
        path_or_hf_repo=args.model,
        language=args.language,
        initial_prompt=args.prompt,
        word_timestamps=True,
    )

    words, phrases = build_timeline(result, args.fps, args.review_below)
    if not words:
        sys.exit("В записи не распознано ни одного слова.")

    data = {
        "schema_version": SCHEMA_VERSION,
        "status": "draft",
        "source": args.audio.name,
        "language": args.language,
        "model": args.model,
        "fps": args.fps,
        "text": result["text"].strip(),
        "words": words,
        "phrases": phrases,
    }
    if not args.no_refine:
        refine_timeline(data, args.audio)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    flagged = sum(word["needs_review"] for word in words)
    print(f"Готово: {output} (слов: {len(words)}, фраз: {len(phrases)}, на проверку: {flagged})")


if __name__ == "__main__":
    main()
