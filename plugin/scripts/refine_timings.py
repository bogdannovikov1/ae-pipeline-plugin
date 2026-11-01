#!/usr/bin/env python3
"""Подрезает границы слов по громкости звука, убирая тишину из таймкодов Whisper.

Пример:
    python3 refine_timings.py voice.wav voice.timeline.json

Исходные таймкоды сохраняются в raw_start и raw_end, поэтому повторный запуск
считает от них заново, а не подрезает уже подрезанное.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from settings import load_settings

SAMPLE_RATE = 16000
SETTINGS = load_settings()["refine"]
FRAME_SECONDS = SETTINGS["frame_seconds"]
MIN_VOICED_FRAMES = SETTINGS["min_voiced_frames"]  # короткие щелчки не считаем речью
MAX_GAP_FRAMES = SETTINGS["max_gap_frames"]  # более длинная тишина разделяет звук на отдельные куски
MAX_BELOW_SPEECH_DB = SETTINGS["max_below_speech_db"]  # всё, что настолько тише речи, не считаем речью
PAD_START = SETTINGS["pad_start"]  # запас, чтобы не срезать тихое начало слова
PAD_END = SETTINGS["pad_end"]
THRESHOLD_POSITION = SETTINGS["threshold_position"]  # порог между шумом (0) и громкой речью (1)


def load_mono(audio_path):
    """Декодирует аудио в моно float32 через ffmpeg."""
    command = [
        "ffmpeg", "-v", "error", "-i", str(audio_path),
        "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-",
    ]
    output = subprocess.run(command, capture_output=True, check=True).stdout
    return np.frombuffer(output, dtype=np.float32)


def analyze(samples):
    """Возвращает для кадров по 10 мс признак речи и громкость в дБ."""
    frame_size = int(SAMPLE_RATE * FRAME_SECONDS)
    count = len(samples) // frame_size
    frames = samples[:count * frame_size].reshape(count, frame_size)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    levels = 20 * np.log10(rms + 1e-10)

    # Порог считается по самой записи и не зависит от её общей громкости.
    noise, speech = np.percentile(levels, [10, 90])
    threshold = max(noise + THRESHOLD_POSITION * (speech - noise), speech - MAX_BELOW_SPEECH_DB)
    voiced = levels >= threshold

    # Кадр остаётся речью, только если входит в серию из MIN_VOICED_FRAMES кадров.
    window = np.ones(MIN_VOICED_FRAMES, dtype=int)
    runs = np.convolve(voiced.astype(int), window) >= MIN_VOICED_FRAMES
    return runs[MIN_VOICED_FRAMES - 1:] | runs[:len(voiced)], levels


def loudest_run(frames, levels):
    """Находит кусок звука с наибольшей энергией: хвосты соседних слов тише."""
    runs = []
    for frame in frames:
        if runs and frame - runs[-1][1] <= MAX_GAP_FRAMES:
            runs[-1][1] = frame
        else:
            runs.append([frame, frame])
    return max(runs, key=lambda run: np.sum(10 ** (levels[run[0]:run[1] + 1] / 10)))


def refine_word(word, voiced, levels):
    """Сужает границы слова до основного звука внутри его исходного отрезка."""
    if word.get("manual"):
        return

    start = word.setdefault("raw_start", word["start"])
    end = word.setdefault("raw_end", word["end"])

    first_frame = max(0, int(start / FRAME_SECONDS))  # Whisper бывает отдаёт начало меньше нуля
    found = np.flatnonzero(voiced[first_frame:int(np.ceil(end / FRAME_SECONDS))])
    if found.size == 0:
        word["start"], word["end"] = max(0.0, start), end
        return

    run_start, run_end = loudest_run(found + first_frame, levels)
    voice_start = run_start * FRAME_SECONDS
    voice_end = (run_end + 1) * FRAME_SECONDS
    word["start"] = round(max(start, 0.0, voice_start - PAD_START), 3)
    word["end"] = round(min(end, voice_end + PAD_END), 3)


def refine_timeline(data, audio_path):
    """Обновляет таймкоды слов, кадры и границы фраз в данных таймлайна."""
    voiced, levels = analyze(load_mono(audio_path))
    fps = data.get("fps")

    for word in data["words"]:
        refine_word(word, voiced, levels)
        if fps:
            word["start_frame"] = round(word["start"] * fps)
            word["end_frame"] = round(word["end"] * fps)

    for phrase in data["phrases"]:
        phrase["start"] = data["words"][phrase["first_word"]]["start"]
        phrase["end"] = data["words"][phrase["last_word"]]["end"]

    data["refined"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path, help="путь к аудиофайлу")
    parser.add_argument("timeline", type=Path, help="путь к .timeline.json")
    args = parser.parse_args()

    data = json.loads(args.timeline.read_text(encoding="utf-8"))
    if data.get("status") == "approved":
        sys.exit("Транскрипт заморожен. Сначала снимите заморозку: approve_transcript.py --reopen")
    refine_timeline(data, args.audio)
    args.timeline.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    changed = sum(w["start"] != w["raw_start"] or w["end"] != w["raw_end"] for w in data["words"])
    print(f"Готово: {args.timeline} (слов изменено: {changed} из {len(data['words'])})")


if __name__ == "__main__":
    main()
