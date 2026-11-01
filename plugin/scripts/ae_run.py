#!/usr/bin/env python3
"""Запускает JSX в After Effects с таймаутом из настроек.

Пример:
    python3 ae_run.py build F1.scene.json
    python3 ae_run.py script ../ae/add_word_markers.jsx
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from settings import PLUGIN_ROOT, bases_dir, load_settings

BUILD_SCRIPT = PLUGIN_ROOT / "ae" / "engine" / "build_scene.jsx"
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


def run_jsx(script, globals_, settings):
    """Выполняет JSX и возвращает его итог. Ошибка скрипта становится RuntimeError."""
    ae = settings["ae"]
    with tempfile.TemporaryDirectory() as folder:
        result = Path(folder) / "result.txt"
        runner = Path(folder) / "runner.jsx"
        pattern = project_pattern(ae["autosave_projects"])
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
    parser.add_argument("command", choices=["build", "script"])
    parser.add_argument("path", type=Path, help="сцена для build или JSX для script")
    parser.add_argument("--append-from", type=int, help="для build: дополнить композицию элементами с этого номера")
    parser.add_argument("--video", type=Path, help="папка видео с её настройками")
    args = parser.parse_args()

    settings = load_settings(args.video)
    try:
        if args.command == "build":
            values = {"SCENE_PATH": args.path.resolve(), "BASES_DIR": bases_dir(settings)}
            if args.append_from is not None:
                values["APPEND_FROM"] = args.append_from
            run_jsx(BUILD_SCRIPT, values, settings)
        else:
            run_jsx(args.path.resolve(), {}, settings)
    except RuntimeError as error:
        sys.exit(f"Ошибка AE: {error}")
    print("Готово")


if __name__ == "__main__":
    main()
