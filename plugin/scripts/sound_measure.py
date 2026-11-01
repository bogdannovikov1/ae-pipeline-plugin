#!/usr/bin/env python3
"""Записывает в assets.json слышимую длину звуков: без тишины в конце файла.

Пример:
    python3 sound_measure.py
"""
import json
import re
import subprocess

from settings import bases_dir, load_settings

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


def main():
    settings = load_settings()
    base = bases_dir(settings) / settings["base"]
    path = base / "assets" / "assets.json"
    assets = json.loads(path.read_text(encoding="utf-8"))
    for asset in assets:
        if asset["type"] == "sound":
            asset["audible"] = measure(base / "assets" / asset["file"])
            print(f"{asset['id']}: {asset['audible']} с")
    path.write_text(json.dumps(assets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
