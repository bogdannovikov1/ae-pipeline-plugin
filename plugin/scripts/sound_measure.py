#!/usr/bin/env python3
"""Записывает в assets.json слышимую длину звуков без тишины в конце, время удара и громкость.

Пример:
    python3 sound_measure.py
"""
import array
import json
import math
import re
import subprocess

from settings import bases_dir, load_settings

RATE = 22050  # частота анализа: удар и громкость читаются и на ней
SILENCE_BELOW_PEAK_DB = 30  # порог считается от пика: тихий звук не должен казаться короче


def trailing_start(log, duration):
    """Возвращает начало тишины, если она доходит до конца файла."""
    starts = [float(v) for v in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", log)]
    if starts and (len(ends) < len(starts) or ends[-1] >= duration - 0.02):
        return starts[-1]
    return duration


def ffmpeg_log(path, audio_filter):
    command = ["ffmpeg", "-hide_banner", "-i", str(path), "-af", audio_filter, "-f", "null", "-"]
    return subprocess.run(command, capture_output=True, text=True).stderr


def measure(path):
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout)
    peak = float(re.search(r"max_volume: ([-\d.]+) dB", ffmpeg_log(path, "volumedetect")).group(1))
    log = ffmpeg_log(path, f"silencedetect=noise={peak - SILENCE_BELOW_PEAK_DB}dB:d=0.03")
    return round(trailing_start(log, duration), 3)


def pcm(path):
    """Моно-отсчёты файла как float от -1 до 1."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    samples = array.array("h", raw[: len(raw) // 2 * 2])
    return [s / 32768 for s in samples]


def hit_and_loudness(samples, audible):
    """Время пика и средняя громкость слышимой части в дБ."""
    if not samples:
        return 0.0, -99.0
    peak = max(range(len(samples)), key=lambda i: abs(samples[i]))
    part = samples[: max(1, int(audible * RATE))]
    rms = math.sqrt(sum(s * s for s in part) / len(part))
    return round(peak / RATE, 3), round(20 * math.log10(max(rms, 1e-5)), 1)


def main():
    settings = load_settings()
    base = bases_dir(settings) / settings["base"]
    path = base / "assets" / "assets.json"
    assets = json.loads(path.read_text(encoding="utf-8"))
    for asset in assets:
        if asset["type"] == "sound":
            path = base / "assets" / asset["file"]
            asset["audible"] = measure(path)
            asset["hit"], asset["loudness"] = hit_and_loudness(pcm(path), asset["audible"])
            print(f"{asset['id']}: {asset['audible']} с, удар {asset['hit']} с, {asset['loudness']} дБ")
    path.write_text(json.dumps(assets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
