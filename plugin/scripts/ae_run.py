#!/usr/bin/env python3
"""Запускает JSX в After Effects с таймаутом из настроек.

Пример:
    python3 ae_run.py build F1.scene.json
    python3 ae_run.py review F1.wav
    python3 ae_run.py apply-review F1.wav
    python3 ae_run.py open F1.aep
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
from validate import build_stamp_path, file_hash

BUILD_SCRIPT = PLUGIN_ROOT / "ae" / "engine" / "build_scene.jsx"
REVIEW_SCRIPT = PLUGIN_ROOT / "ae" / "open_review.jsx"
APPLY_SCRIPT = PLUGIN_ROOT / "ae" / "update_transcript_from_markers.jsx"
OPEN_SCRIPT = PLUGIN_ROOT / "ae" / "open_project.jsx"
READY_CHECK_TIMEOUT = 20
READY_CHECK_PAUSE = 3
ERROR_PREFIX = "error: "


def js_string(value):
    return json.dumps(str(value), ensure_ascii=True)


def project_pattern(patterns):
    """Переводит шаблоны автосохранения в регулярное выражение для JSX."""
    parts = [re.escape(str(Path(p).expanduser())).replace(r"\*", "[^/]*") for p in patterns]
    return "^(" + "|".join(parts) + ")(/|$)"


def wrapper(script, globals_, pattern, result):
    """Собирает обёртку: сохраняет разрешённый проект, запускает скрипт, пишет итог в файл."""
    assigns = "".join(f"$.global.{key} = {js_string(value)};\n" for key, value in globals_.items())
    clears = "".join(f"$.global.{key} = undefined;\n" for key in globals_)
    return f"""var outcome = "ok";
try {{
if (app.project.dirty) {{
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
        f'with timeout of {READY_CHECK_TIMEOUT} seconds\n'
        f'tell application {json.dumps(ae["app"])} to DoScript "1"\n'
        f'end timeout'
    )
    deadline = time.time() + ae["launch_timeout"]
    while time.time() < deadline:
        if subprocess.run(["osascript", "-e", command], capture_output=True).returncode == 0:
            return
        time.sleep(READY_CHECK_PAUSE)
    raise RuntimeError(f"After Effects не ответил за {ae['launch_timeout']} с")


def run_jsx(script, globals_, settings, extra_autosave=()):
    """Выполняет JSX и возвращает его итог. Ошибка скрипта становится RuntimeError."""
    ae = settings["ae"]
    with tempfile.TemporaryDirectory() as folder:
        result = Path(folder) / "result.txt"
        runner = Path(folder) / "runner.jsx"
        pattern = project_pattern(list(ae["autosave_projects"]) + list(extra_autosave))
        runner.write_text(wrapper(script, globals_, pattern, result), encoding="utf-8")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build", "script", "review", "apply-review", "open"])
    parser.add_argument("path", type=Path, help="сцена, JSX, запись или проект, по команде")
    parser.add_argument("--launch", action="store_true", help="автор разрешил запустить After Effects")
    parser.add_argument("--append-from", type=int, help="для build: дополнить композицию элементами с этого номера")
    parser.add_argument("--video", type=Path, help="папка видео с её настройками")
    args = parser.parse_args()

    settings = load_settings(args.video) if args.video else settings_for(args.path)
    try:
        if args.command == "build":
            values = {"SCENE_PATH": args.path.resolve(), "BASES_DIR": bases_dir(settings)}
            if args.append_from is not None:
                values["APPEND_FROM"] = args.append_from
            run_jsx(BUILD_SCRIPT, values, settings)
            stamp = {"scene_hash": file_hash(args.path), "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            build_stamp_path(args.path).write_text(json.dumps(stamp, indent=2), encoding="utf-8")
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
