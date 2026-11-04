#!/usr/bin/env python3
"""Библиотека звуков-кандидатов вне базы: структура по событиям, измерения, прослушка, черновик токенов.

Пример:
    python3 sound_library.py init
    python3 sound_library.py scan
    python3 sound_library.py preview
    python3 sound_library.py propose
"""
import argparse
import html
import json
import re
import subprocess
from pathlib import Path

from settings import PLUGIN_ROOT, load_settings
from sound_measure import hit_and_loudness, measure, pcm

SCHEME_PATH = PLUGIN_ROOT / "config" / "sound_library.json"
SOUND_EXT = {".wav", ".mp3", ".aif", ".aiff", ".ogg", ".flac", ".m4a"}
HINT_FILE = "ЧТО_СЮДА.txt"
PREVIEW_RATE = 22050


def library_dir(settings):
    return Path(settings["sound_library_dir"]).expanduser()


def load_scheme():
    return json.loads(SCHEME_PATH.read_text(encoding="utf-8"))


def init(root, scheme):
    """Создаёт папки по схеме и подсказки в них. Существующие файлы и папки не трогает."""
    made = 0
    for cat in scheme["categories"]:
        top = root / cat["folder"]
        top.mkdir(parents=True, exist_ok=True)
        write_hint(top, f"Событие {cat['event']}. Блоки: {cat['blocks']}.")
        for role, spec in cat["roles"].items():
            folder = top / role
            folder.mkdir(exist_ok=True)
            event = spec.get("event", cat["event"])
            write_hint(folder, f"Событие {event}. {spec['hint']}\nЛицензия: CC0 или без указания автора. Источник пишите в sources.json рядом.")
            for name in scheme["bins"].get(spec.get("bins"), []):
                (folder / name).mkdir(exist_ok=True)
                made += 1
    return made


def write_hint(folder, text):
    path = folder / HINT_FILE
    if not path.exists():
        path.write_text(text + "\n", encoding="utf-8")


def classify(rel, scheme):
    """Событие, роль и характер по пути файла внутри библиотеки."""
    parts = rel.parts[:-1]
    cat = next((c for c in scheme["categories"] if parts and parts[0] == c["folder"]), None)
    if not cat:
        return None
    role_name = next((p for p in parts[1:] if p in scheme["roles"]), "одиночный")
    spec = cat["roles"].get(role_name, {})
    character = [p for p in parts[1:] if p != role_name and not p.endswith("с")]
    return {
        "event": spec.get("event", cat["event"]),
        "role": scheme["roles"][role_name],
        "character": character[0] if character else None,
    }


def sound_id(rel):
    stem = re.sub(r"[^0-9a-zA-Z]+", "_", rel.stem).strip("_").lower()
    return stem or "sound"


def scan(root, scheme):
    """Меряет все звуки selection и пишет catalog.json. Уже измеренные файлы не перемеряет."""
    path = root / "catalog.json"
    selection = root / "selection"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    known = {f.get("library_file"): f for f in old.get("files", [])}
    sources = {}
    for src in selection.rglob("sources.json"):
        for name, info in json.loads(src.read_text(encoding="utf-8")).items():
            sources[str((src.parent / name).relative_to(root))] = info
    files, ids = [], {}
    for file in sorted(selection.rglob("*")):
        if file.suffix.lower() not in SOUND_EXT or "Корзина" in file.parts:
            continue
        place = classify(file.relative_to(selection), scheme)
        if not place:
            continue
        key = str(file.relative_to(root))
        entry = dict(known.get(key) or {})
        if entry.get("bytes") != file.stat().st_size or "hit" not in entry:
            audible = measure(file)
            hit, loud = hit_and_loudness(pcm(file), audible)
            entry.update(audible=audible, hit=hit, loudness=loud, bytes=file.stat().st_size)
        sid = sound_id(file)
        while ids.get(sid, entry["bytes"]) != entry["bytes"]:
            sid += "_2"
        ids[sid] = entry["bytes"]
        entry.update(place, id=sid, library_file=key)
        entry.update(sources.get(key, {}))
        files.append(entry)
    path.write_text(json.dumps({"files": files}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return files


def place_hits(times, gap, limit):
    kept = []
    for t in sorted(times):
        if not kept or t - kept[-1] >= gap:
            kept.append(t)
    return kept[:limit]


def render(root, layout, out):
    """layout: [(файл, начало, громкость в дБ)] → один WAV."""
    if not layout:
        return False
    args, chain = [], ""
    for i, (file, start, gain) in enumerate(layout):
        args += ["-i", str(root / file)]
        ms = max(0, int(start * 1000))
        chain += f"[{i}:a]aresample={PREVIEW_RATE},aformat=channel_layouts=mono,volume={gain}dB,adelay={ms}|{ms}[a{i}];"
    chain += "".join(f"[a{i}]" for i in range(len(layout))) + f"amix=inputs={len(layout)}:normalize=0,alimiter=limit=0.9,apad=pad_dur=0.3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", chain, str(out)], check=True)
    return True


# Тайминги прослушки повторяют блоки базы: появление 0,45 с, шаг группы 0,12 с, граф по уровням.
POP = 0.45
GROUPS = {"массив 10": [i * 0.12 for i in range(10)], "граф 4 уровня": [0, 0.4, 0.8, 1.2]}
TARGET_DB = -20  # средняя громкость прослушки: файлы звучат ровно и их можно сравнивать
MAX_GAIN_DB = 15


def gain(f):
    return min(MAX_GAIN_DB, TARGET_DB - f["loudness"])


def series_layout(f, hits, anchor):
    kept = place_hits(hits, 0.08, 10)
    return [(f["library_file"], t + POP * anchor - f["hit"], gain(f) - 6 * i / max(1, len(kept) - 1))
            for i, t in enumerate(kept)]


def preview(root, scheme, files):
    """Страница прослушки: каждый файл с ровной громкостью, серии и подложки по таймингам блоков."""
    out = root / "preview"
    out.mkdir(exist_ok=True)
    player = lambda src: f"<audio controls preload=none src='{html.escape(src)}'></audio>"
    rows = []
    for cat in scheme["categories"]:
        events = {spec.get("event", cat["event"]) for spec in cat["roles"].values()} | {cat["event"]}
        for event in sorted(events):
            mine = sorted((f for f in files if f["event"] == event), key=lambda f: (f["role"], f["audible"]))
            if not mine:
                continue
            series = [f for f in mine if f["role"] == "series"]
            rows.append(f"<h2>{html.escape(cat['folder'])}: {event}</h2><table>")
            for f in mine:
                demos = []
                base = sound_id(Path(f["library_file"]))
                one = out / f"{event}__{base}.wav"
                render(root, [(f["library_file"], 0.05, gain(f))], one)
                demos.append(player(f"preview/{one.name}"))
                if f["role"] == "series":
                    for name, hits in GROUPS.items():
                        wav = out / f"{event}__{base}__{sound_id(Path(name))}.wav"
                        render(root, series_layout(f, hits, cat["anchor"]), wav)
                        demos.append(f"{name} {player('preview/' + wav.name)}")
                if f["role"] == "bed" and series:
                    wav = out / f"{event}__{base}__bed.wav"
                    hits = GROUPS["граф 4 уровня"]
                    render(root, series_layout(series[0], hits, cat["anchor"]) + [(f["library_file"], 0, gain(f) - 10)], wav)
                    demos.append(f"с серией {player('preview/' + wav.name)}")
                source = f.get("source", "")
                link = f"<a href='{html.escape(source)}'>источник</a>" if source.startswith("http") else ""
                rows.append(
                    f"<tr><td>{f['role']}</td><td>{html.escape(Path(f['library_file']).name)}</td>"
                    f"<td>{f['audible']:.2f} с</td><td>{html.escape(str(f.get('license', '')))} {link}</td>"
                    f"<td>{' '.join(demos)}</td></tr>"
                )
            rows.append("</table>")
    page = root / "listen.html"
    page.write_text(
        "<!doctype html><meta charset=utf-8><title>Звуки</title>"
        "<style>body{font:14px sans-serif;margin:16px}td{padding:2px 8px}audio{height:28px;vertical-align:middle}</style>"
        + "\n".join(rows), encoding="utf-8")
    return page


def propose(scheme, files):
    """Черновик токенов sounds по наполненным папкам: решает автор, вносит librarian."""
    events, fallback = {}, {}
    for cat in scheme["categories"]:
        for old in cat.get("fallback_from", []):
            fallback[old] = cat["event"]
        names = {spec.get("event", cat["event"]) for spec in cat["roles"].values()} | {cat["event"]}
        for event in sorted(names):
            mine = [f for f in files if f["event"] == event]
            singles = sorted((f for f in mine if f["role"] == "single"), key=lambda f: f["audible"])
            series = unique(f["id"] for f in mine if f["role"] == "series")
            beds = [f["id"] for f in sorted(mine, key=lambda f: -f["audible"]) if f["role"] == "bed"]
            if not (singles or series):
                continue
            role_anchor = [r.get("anchor") for r in cat["roles"].values() if r.get("event") == event and "anchor" in r]
            spec = {"anchor": role_anchor[0] if role_anchor else cat["anchor"]}
            if singles:
                spec["tiers"] = unique(f["id"] for f in singles)
            if series:
                spec["series"] = {"sounds": series, "min_hits": 3, "min_span": 0.7, "gap": 0.1, "max_hits": 10, "fade_db": -6, "pitch_step": 0}
            if beds:
                spec["bed"] = {"sound": beds[0], "min_span": 1.2, "level": -10}
            events[event] = spec
    events["dim"] = None
    return {"level": -14, "reference_loudness": -20, "fallback": fallback, "events": events}


def unique(ids):
    seen = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["init", "scan", "preview", "propose"])
    parser.add_argument("--root", type=Path, help="папка библиотеки вместо sound_library_dir")
    args = parser.parse_args()
    root = (args.root or library_dir(load_settings())).expanduser()
    selection = root / "selection"
    scheme = load_scheme()
    if args.command == "init":
        print(f"Папок длины создано или найдено: {init(selection, scheme)} в {selection}")
        return
    files = scan(root, scheme)
    if args.command == "scan":
        print(f"Звуков: {len(files)}, каталог: {root / 'catalog.json'}")
    elif args.command == "preview":
        print(f"Прослушка: {preview(root, scheme, files)}")
    else:
        print(json.dumps(propose(scheme, files), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
