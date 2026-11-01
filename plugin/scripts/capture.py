#!/usr/bin/env python3
"""Снимает ключевые кадры сцены и собирает контактные листы для предревью.

Пример:
    python3 capture.py F1.scene.json --round 1
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from ae_run import run_jsx
from settings import PLUGIN_ROOT, load_settings
from validate import read_json, resolve_base, resolve_path

CAPTURE_SCRIPT = PLUGIN_ROOT / "ae" / "capture_frames.jsx"


def item_start(item, words, starts, lead):
    """Повторяет Engine.time из engine.jsxinc: где элемент начинается."""
    delay = item.get("delay", 0)
    if "at_word" in item:
        return max(0, words[item["at_word"]]["start"] - lead + delay)
    if "after" in item:
        return starts.get(item["after"], 0) + delay
    if "at" in item:
        return item["at"] + delay
    return None


def moments(scene, words, lead, offsets, limit, duration, from_time=0):
    """Возвращает моменты съёмки вокруг начала элементов, равномерно прореженные до лимита."""
    times = set()
    starts = {}
    for number, item in enumerate(scene["items"]):
        if item.get("always"):
            continue
        start = item_start(item, words, starts, lead)
        if start is None:
            continue
        starts[item.get("id") or f"{item.get('block')}_{number}"] = start
        times.update(round(min(duration, max(0, start + offset)), 3) for offset in offsets)
    ordered = sorted(t for t in times if t >= from_time)
    if len(ordered) <= limit:
        return ordered
    step = (len(ordered) - 1) / max(1, limit - 1)
    return [ordered[round(n * step)] for n in range(limit)]


def wait_for_frames(paths, timeout):
    """saveFrameToPng работает асинхронно: ждём файлы и их полную запись."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(p.is_file() and p.stat().st_size for p in paths):
            sizes = [p.stat().st_size for p in paths]
            time.sleep(0.5)
            if sizes == [p.stat().st_size for p in paths]:
                return
        time.sleep(0.5)
    missing = sum(not p.is_file() for p in paths)
    raise RuntimeError(f"Кадры не сохранились за {timeout} с, нет файлов: {missing}")


def build_sheets(folder, review):
    """Склеивает кадры в листы: ffmpeg без drawtext, поэтому время лежит в index.json."""
    columns, rows = review["sheet_columns"], review["sheet_rows"]
    tile = f"scale={review['sheet_frame_width']}:-1,tile={columns}x{rows}:padding=4:color=white"
    command = [
        "ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", str(folder / "frame_%04d.png"),
        "-vf", tile, "-q:v", "3", str(folder / "sheet_%02d.jpg"),
    ]
    subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scene", type=Path)
    parser.add_argument("--round", type=int, default=1, help="номер круга предревью")
    parser.add_argument("--label", help="имя папки кадров вместо round_N, для разового ревью")
    parser.add_argument("--from-time", type=float, default=0, help="снимать только с этого момента, с")
    parser.add_argument("--video", type=Path, help="папка видео с её настройками")
    args = parser.parse_args()

    settings = load_settings(args.video)
    review = settings["review"]
    scene_path = args.scene.resolve()
    scene = read_json(scene_path)
    words = read_json(resolve_path(scene["timeline"], scene_path.parent))["words"]
    tokens = read_json(resolve_base(scene["base"], settings) / "tokens.json")
    lead = scene.get("tokens", {}).get("motion", {}).get("lead", tokens["motion"]["lead"])
    times = moments(scene, words, lead, review["offsets"], review["max_frames"], words[-1]["end"] + scene["tail"], args.from_time)

    folder = scene_path.parent / "frames" / (args.label or f"round_{args.round}")
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*"):
        old.unlink()
    spec = folder / "spec.json"
    spec.write_text(json.dumps({"comp": scene["comp"]["name"], "folder": str(folder), "times": times}), encoding="utf-8")

    try:
        run_jsx(CAPTURE_SCRIPT, {"CAPTURE_SPEC": spec}, settings)
        frames = [folder / f"frame_{n + 1:04d}.png" for n in range(len(times))]
        wait_for_frames(frames, review["frame_timeout"])
        build_sheets(folder, review)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f"Съёмка не удалась: {error}")
    spec.unlink()

    per_sheet = review["sheet_columns"] * review["sheet_rows"]
    index = [
        {
            "time": moment,
            "frame": f"frame_{n + 1:04d}.png",
            "sheet": f"sheet_{n // per_sheet + 1:02d}.jpg",
            "row": n % per_sheet // review["sheet_columns"] + 1,
            "column": n % review["sheet_columns"] + 1,
        }
        for n, moment in enumerate(times)
    ]
    (folder / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Готово: {folder} (кадров: {len(times)}, листов: {len(list(folder.glob('sheet_*.jpg')))})")


if __name__ == "__main__":
    main()
