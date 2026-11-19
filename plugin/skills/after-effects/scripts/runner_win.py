#!/usr/bin/env python3
"""runner.sh для Windows: находит After Effects, выполняет ExtendScript и печатает результат.

Usage: python runner_win.py [--background] <script.jsx> [args-json]

Файлы обмена лежат во временной папке Windows (%TEMP%), её же JSX видит как Folder.temp.
AfterFX.exe -r передаёт скрипт запущенному AE и возвращается сразу, поэтому итог ждём по файлу.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

TEMP = Path(tempfile.gettempdir())
ARGS_FILE = TEMP / "ae-assistant-args.json"
RESULT_FILE = TEMP / "ae-assistant-result.json"
ERROR_FILE = TEMP / "ae-assistant-error.txt"
WRAPPER_FILE = TEMP / "ae-assistant-wrapper.jsx"
CONFIG_FILE = Path.home() / ".ae-assistant-config"
LOG_FILE = Path.home() / ".ae-assistant-log"
APPS_DIR = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Adobe"
TIMEOUT = 60


def log(message):
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")


def fail(message, script, code=1):
    log(f"ERROR: {message}")
    print(json.dumps({"error": message, "script": str(script)}, ensure_ascii=False))
    sys.exit(code)


def pick_app():
    if CONFIG_FILE.is_file() and CONFIG_FILE.read_text(encoding="utf-8").strip():
        return CONFIG_FILE.read_text(encoding="utf-8").strip()
    versions = sorted(p.name for p in APPS_DIR.glob("Adobe After Effects*") if (p / "Support Files" / "AfterFX.exe").is_file())
    if not versions:
        print(json.dumps({"error": f"No Adobe After Effects installation found in {APPS_DIR}"}), file=sys.stderr)
        sys.exit(1)
    if len(versions) > 1:
        print("Multiple After Effects versions found:")
        for n, name in enumerate(versions, 1):
            print(f"  {n}. {name}")
        print("\nSet your preferred version by running:")
        print(f"  echo '{versions[-1]}' > ~/.ae-assistant-config")
        sys.exit(2)
    CONFIG_FILE.write_text(versions[0], encoding="utf-8")
    return versions[0]


def is_running():
    listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq AfterFX.exe", "/NH"], capture_output=True, text=True, errors="replace")
    return "afterfx.exe" in listed.stdout.lower()


def main():
    args = sys.argv[1:]
    if args and args[0] == "--background":
        args = args[1:]
    if not args:
        sys.exit("Usage: runner_win.py [--background] <script.jsx> [args-json]")
    script = Path(args[0]).resolve()
    args_json = args[1] if len(args) > 1 else "{}"
    log(f"RUN: {script}")
    log(f"ARGS: {args_json}")

    app = pick_app()
    exe = APPS_DIR / app / "Support Files" / "AfterFX.exe"
    log(f"AE_APP: {app}")
    if not is_running():
        fail("After Effects is not running. Start it first", script)

    ARGS_FILE.write_text(args_json, encoding="utf-8")
    for path in (RESULT_FILE, ERROR_FILE):
        path.unlink(missing_ok=True)
    # Ошибка вне try/catch самого скрипта попадает в файл ошибки, как в runner.sh.
    WRAPPER_FILE.write_text(
        f"try {{ $.evalFile(new File({json.dumps(script.as_posix())})); }} catch (e) {{\n"
        f"  var f = new File({json.dumps(ERROR_FILE.as_posix())}); f.encoding = 'UTF-8'; f.open('w');\n"
        f"  f.write('\"' + String(e).replace(/\\\\/g, '\\\\\\\\').replace(/\"/g, '\\\\\"') + ' (line ' + e.line + ')\"'); f.close();\n"
        f"}}\n",
        encoding="utf-8",
    )
    subprocess.Popen([str(exe), "-r", str(WRAPPER_FILE)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + TIMEOUT
    while time.time() < deadline and not RESULT_FILE.is_file() and not ERROR_FILE.is_file():
        time.sleep(0.5)
    time.sleep(0.2)  # даём AE дописать и закрыть файл

    if RESULT_FILE.is_file():
        result = RESULT_FILE.read_text(encoding="utf-8")
        log(f"RESULT: {result}")
        print(result)
    elif ERROR_FILE.is_file():
        detail = ERROR_FILE.read_text(encoding="utf-8")
        log(f"EXTENDSCRIPT ERROR: {detail}")
        print(f'{{"error": "ExtendScript error", "detail": {detail}, "script": {json.dumps(str(script))}}}')
    else:
        fail(f"Script did not produce a result file in {TIMEOUT} seconds. Check that AE has 'Allow Scripts to Write Files "
             "and Access Network' enabled in Preferences > Scripting & Expressions and no dialog is open", script)


if __name__ == "__main__":
    main()
