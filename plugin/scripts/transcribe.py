#!/usr/bin/env python3
"""Пословный транскрипт аудио через Whisper (MLX на Mac, faster-whisper на Windows) для маркеров After Effects.

Пример:
    python3 transcribe.py voice.mp3 --fps 30 --prompt "After Effects, ExtendScript"
"""
import argparse
import json
import os
import sys
from pathlib import Path

from refine_timings import refine_timeline
from settings import settings_for

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


def add_cuda_dlls():
    """Windows: библиотеки CUDA из pip (nvidia-cublas-cu12, nvidia-cudnn-cu12) лежат в site-packages/nvidia/*/bin
    и сами в путь поиска DLL не попадают. Без них faster-whisper работает только на процессоре."""
    if os.name != "nt":
        return
    try:
        import nvidia
    except ImportError:
        return
    for root in nvidia.__path__:
        for folder in Path(root).glob("*/bin"):
            os.add_dll_directory(str(folder))
            os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ['PATH']}"


def recognize(audio, model, language, prompt, settings):
    """Распознаёт запись выбранным движком и отдаёт результат в формате Whisper:
    text и segments со словами (word, start, end, probability).

    mlx: MLX Whisper, только Mac на Apple Silicon. faster-whisper: Windows и Linux.
    Импорт внутри, чтобы --help и ошибки аргументов не ждали загрузку модели."""
    backend = settings.get("backend", "mlx")
    if backend == "mlx":
        import mlx_whisper
        return mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=model,
            language=language,
            initial_prompt=prompt,
            word_timestamps=True,
        )
    if backend != "faster-whisper":
        sys.exit(f"Неизвестный движок распознавания transcribe.backend: {backend}")
    add_cuda_dlls()
    from faster_whisper import WhisperModel
    # Модель — имя (скачается сама) или папка на диске, в том числе с ~.
    local = Path(model).expanduser()
    engine = WhisperModel(str(local) if local.is_dir() else model, device=settings.get("device", "auto"), compute_type=settings.get("compute_type", "default"))
    segments, _ = engine.transcribe(str(audio), language=language, initial_prompt=prompt, word_timestamps=True)
    result = {"segments": []}
    for segment in segments:
        result["segments"].append({
            "text": segment.text,
            "words": [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability} for w in segment.words or []],
        })
    result["text"] = "".join(s["text"] for s in result["segments"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path, help="путь к аудиофайлу")
    parser.add_argument("-o", "--output", type=Path, help="куда сохранить JSON (по умолчанию <аудио>.timeline.json)")
    parser.add_argument("--language", help="язык записи, по умолчанию из настроек")
    parser.add_argument("--model", help="модель Whisper, по умолчанию из настроек")
    parser.add_argument("--prompt", help="подсказка с терминами и брендами")
    parser.add_argument("--fps", type=float, help="добавить номера кадров для этой частоты")
    parser.add_argument("--review-below", type=float, help="порог уверенности для пометки needs_review")
    parser.add_argument("--no-refine", action="store_true", help="не подрезать границы слов по звуку")
    parser.add_argument("--force", action="store_true", help="перезаписать готовый транскрипт вместе с его ревью")
    args = parser.parse_args()

    if not args.audio.is_file():
        sys.exit(f"Файл не найден: {args.audio}")

    settings = settings_for(args.audio)["transcribe"]
    args.language = args.language or settings["language"]
    args.model = args.model or settings["model"]
    args.review_below = settings["review_below"] if args.review_below is None else args.review_below

    output = args.output or args.audio.with_suffix(".timeline.json")
    if output.exists() and not args.force:
        sys.exit(f"Транскрипт уже есть: {output}. Чтобы заменить его вместе с ревью, добавьте --force.")
    if output.exists() and json.loads(output.read_text(encoding="utf-8")).get("status") == "approved":
        sys.exit(f"Транскрипт заморожен: {output}. Сначала снимите заморозку: approve_transcript.py --reopen")

    print(f"Распознаю {args.audio} моделью {args.model}...", file=sys.stderr)
    result = recognize(args.audio, args.model, args.language, args.prompt, settings)

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
