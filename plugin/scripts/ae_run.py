#!/usr/bin/env python3
"""Запускает JSX в After Effects с таймаутом из настроек.

Пример:
    python3 ae_run.py build F1.scene.json
    python3 ae_run.py review F1.wav
    python3 ae_run.py apply-review F1.wav
    python3 ae_run.py open F1.aep
    python3 ae_run.py diff F1.scene.json --comp F1_copy --apply
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from settings import PLUGIN_ROOT, bases_dir, load_settings, settings_for
from scene_diff import item_id, run as diff_scene, snapshot_path
from validate import build_stamp_path, file_hash, read_json
from view import layout_path, sound_log_path

BUILD_SCRIPT = PLUGIN_ROOT / "ae" / "engine" / "build_scene.jsx"
REVIEW_SCRIPT = PLUGIN_ROOT / "ae" / "open_review.jsx"
APPLY_SCRIPT = PLUGIN_ROOT / "ae" / "update_transcript_from_markers.jsx"
LAYOUT_SCRIPT = PLUGIN_ROOT / "ae" / "export_layout.jsx"
OPEN_SCRIPT = PLUGIN_ROOT / "ae" / "open_project.jsx"
TRANSFORMS_SCRIPT = PLUGIN_ROOT / "ae" / "export_transforms.jsx"
ERROR_PREFIX = "error: "


def js_string(value):
    return json.dumps(str(value), ensure_ascii=True)


def project_pattern(patterns):
    """Переводит шаблоны автосохранения в регулярное выражение для JSX."""
    parts = [re.escape(str(Path(p).expanduser())).replace(r"\*", "[^/]*") for p in patterns]
    return "^(" + "|".join(parts) + ")(/|$)"


def wrapper(script, globals_, pattern, result, readonly=False):
    """Собирает обёртку: сохраняет разрешённый проект, запускает скрипт, пишет итог в файл.

    readonly: скрипт только читает проект, поэтому несохранённые правки ему не мешают.
    """
    guard = "false" if readonly else "app.project.dirty"
    assigns = "".join(f"$.global.{key} = {js_string(value)};\n" for key, value in globals_.items())
    clears = "".join(f"$.global.{key} = undefined;\n" for key in globals_)
    return f"""var outcome = "ok";
try {{
if ({guard}) {{
    var projectFile = app.project.file;
    if (projectFile && new RegExp({js_string(pattern)}).test(projectFile.fsName)) {{
        app.project.save();
    }} else {{
        throw new Error("The open project has unsaved changes");
    }}
}}
{assigns}$.evalFile(new File({js_string(script)}));
}} catch (e) {{
    outcome = {js_string(ERROR_PREFIX)} + e.toString() + " (line " + e.line + ")";
}}
{clears}var result = new File({js_string(result)});
result.encoding = "UTF-8";
result.open("w");
result.write(outcome);
result.close();
"""


def is_running(settings):
    check = f'application {json.dumps(settings["ae"]["app"])} is running'
    return subprocess.run(["osascript", "-e", check], capture_output=True, text=True).stdout.strip() == "true"


def launch(settings, allowed):
    """Запускает After Effects только с разрешения автора и ждёт готовности к скриптам."""
    ae = settings["ae"]
    if not is_running(settings) and not allowed:
        raise RuntimeError("After Effects не запущен. Спросите автора и повторите команду с --launch")
    if subprocess.run(["open", "-a", ae["app"]], capture_output=True).returncode:
        raise RuntimeError(f"Не удалось запустить {ae['app']}: проверьте настройку ae.app")
    command = (
        f'with timeout of {ae["ready_check_timeout"]} seconds\n'
        f'tell application {json.dumps(ae["app"])} to DoScript "1"\n'
        f'end timeout'
    )
    deadline = time.time() + ae["launch_timeout"]
    while time.time() < deadline:
        if subprocess.run(["osascript", "-e", command], capture_output=True).returncode == 0:
            return
        time.sleep(ae["ready_check_pause"])
    raise RuntimeError(f"After Effects не ответил за {ae['launch_timeout']} с")


def run_jsx(script, globals_, settings, extra_autosave=(), readonly=False):
    """Выполняет JSX и возвращает его итог. Ошибка скрипта становится RuntimeError."""
    ae = settings["ae"]
    with tempfile.TemporaryDirectory() as folder:
        result = Path(folder) / "result.txt"
        runner = Path(folder) / "runner.jsx"
        pattern = project_pattern(list(ae["autosave_projects"]) + list(extra_autosave))
        runner.write_text(wrapper(script, globals_, pattern, result, readonly), encoding="utf-8")
        command = (
            f'with timeout of {ae["timeout"]} seconds\n'
            f'tell application {json.dumps(ae["app"])} to DoScriptFile {json.dumps(str(runner))}\n'
            f'end timeout'
        )
        done = subprocess.run(["osascript", "-e", command], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr.strip())
        if not result.is_file():
            raise RuntimeError("After Effects не сообщил итог: проверьте настройку Allow Scripts to Write Files")
        outcome = result.read_text(encoding="utf-8")
    if outcome.startswith(ERROR_PREFIX):
        raise RuntimeError(outcome[len(ERROR_PREFIX):])
    return outcome


def build_values(scene_path, settings, append_from=None):
    """Глобальные переменные сборки: сцена, база и настройки, которые движок применяет сам."""
    values = {
        "SCENE_PATH": Path(scene_path).resolve(),
        "BASES_DIR": bases_dir(settings),
        "KEEP_BUILDS": settings["ae"].get("keep_builds", 1),
        "TRIM_HIDDEN": 1 if settings["ae"]["trim_hidden"] else 0,
        "TRIM_PAD": settings["ae"]["trim_pad"],
        "MUTE_OFFSCREEN": 1 if settings["ae"]["mute_offscreen"] else 0,
        "OFFSCREEN_MARGIN": settings["ae"]["offscreen_margin"],
    }
    if append_from is not None:
        values["APPEND_FROM"] = append_from
    return values


def export_layout(scene_path, settings):
    """Выгружает положение слоёв из AE. Результат читает view.py layout."""
    scene = read_json(scene_path)
    journal = read_json(sound_log_path(scene_path))
    spec = {
        "comp": scene["comp"]["name"],
        "out": str(layout_path(scene_path)),
        "settle": settings["layout"]["settle"],
        "full_frame": settings["layout"]["full_frame"],
        "starts": journal["starts"],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as file:
        json.dump(spec, file)
    try:
        # Выгрузка только читает: несохранённая после сборки композиция ей не мешает.
        run_jsx(LAYOUT_SCRIPT, {"LAYOUT_SPEC": file.name}, settings, readonly=True)
    finally:
        Path(file.name).unlink()


def export_transforms(out, settings, comp="", latest=False):
    """Выгружает трансформы слоёв композиции до выражений: активной или названной."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as file:
        json.dump({"comp": comp, "latest": latest, "out": str(out)}, file)
    try:
        run_jsx(TRANSFORMS_SCRIPT, {"TRANSFORMS_SPEC": file.name}, settings, readonly=True)
    finally:
        Path(file.name).unlink()


def write_snapshot(scene_path, settings):
    """Снимок сборки для diff: слои только что собранной композиции, adjust и delay сцены на момент сборки."""
    scene = read_json(scene_path)
    out = snapshot_path(scene_path)
    export_transforms(out, settings, scene["comp"]["name"], latest=True)
    data = read_json(out)
    data["scene_hash"] = file_hash(scene_path)
    data["adjust"] = {item_id(item, n): item["adjust"] for n, item in enumerate(scene["items"]) if item.get("adjust")}
    data["delay"] = {item_id(item, n): item.get("delay") or 0 for n, item in enumerate(scene["items"])}
    journal = sound_log_path(scene_path)
    data["starts"] = read_json(journal).get("starts", {}) if journal.is_file() else {}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def diff(scene_path, settings, comp, write, show_all):
    """Сравнивает правленую композицию со снимком сборки и печатает короткую сводку."""
    if not snapshot_path(scene_path).is_file():
        raise RuntimeError(f"нет снимка сборки {snapshot_path(scene_path).name}: он появляется после ae_run.py build")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "edited.json"
        export_transforms(out, settings, comp or "")
        print(diff_scene(scene_path, read_json(out), settings, write, show_all))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build", "script", "review", "apply-review", "open", "layout", "diff"])
    parser.add_argument("path", type=Path, help="сцена, JSX, запись или проект, по команде")
    parser.add_argument("--launch", action="store_true", help="автор разрешил запустить After Effects")
    parser.add_argument("--append-from", type=int, help="для build: дополнить композицию элементами с этого номера")
    parser.add_argument("--video", type=Path, help="папка видео с её настройками")
    parser.add_argument("--comp", help="для diff: композиция с правками автора, по умолчанию активная")
    parser.add_argument("--apply", action="store_true", help="для diff: перенести однозначные правки в сцену")
    parser.add_argument("--all", action="store_true", help="для diff: все строки сводки")
    args = parser.parse_args()

    settings = load_settings(args.video) if args.video else settings_for(args.path)
    try:
        if args.command == "build":
            run_jsx(BUILD_SCRIPT, build_values(args.path, settings, args.append_from), settings)
            stamp = {"scene_hash": file_hash(args.path), "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            build_stamp_path(args.path).write_text(json.dumps(stamp, indent=2), encoding="utf-8")
            write_snapshot(args.path.resolve(), settings)
            export_layout(args.path.resolve(), settings)
            journal = sound_log_path(args.path.resolve())
            for lost in (read_json(journal).get("edits_lost", []) if journal.is_file() else []):
                print(f"Правка автора без слоя (блок изменился), не применена: {lost}")
        elif args.command == "diff":
            diff(args.path.resolve(), settings, args.comp, args.apply, args.all)
            return
        elif args.command == "layout":
            export_layout(args.path.resolve(), settings)
        elif args.command == "review":
            launch(settings, args.launch)
            ae = settings["ae"]
            size = f"{ae['review_width']},{ae['review_height']},{ae['review_fps']}"
            run_jsx(REVIEW_SCRIPT, {"REVIEW_AUDIO": args.path.resolve(), "REVIEW_SIZE": size}, settings)
        elif args.command == "apply-review":
            # Свой проект ревью сохраняем без вопроса: иначе правки маркеров останутся только в памяти AE.
            audio = args.path.resolve()
            run_jsx(APPLY_SCRIPT, {"REVIEW_COMP": audio.stem}, settings, [audio.with_suffix(".aep")])
        elif args.command == "open":
            launch(settings, args.launch)
            run_jsx(OPEN_SCRIPT, {"PROJECT_PATH": args.path.resolve()}, settings)
        else:
            run_jsx(args.path.resolve(), {}, settings)
    except RuntimeError as error:
        sys.exit(f"Ошибка AE: {error}")
    print("Готово")


if __name__ == "__main__":
    main()
