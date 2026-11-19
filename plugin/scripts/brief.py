#!/usr/bin/env python3
"""Печатает полный контекст роли: кто ты, правила, пути и задание. Работает для любой модели.

Пример:
    python3 brief.py env --video ~/Videos/my_video
    python3 brief.py make ~/Videos/my_video F1
    python3 brief.py animator ~/Videos/my_video F1 --task "поправить звук в конце"
    python3 brief.py animator ~/Videos/my_video F1 --task-only
    python3 brief.py agents-md ~/Videos/my_video
"""
import argparse
import json
import re
import sys
from pathlib import Path

from settings import PLUGIN_ROOT, bases_dir, load_settings
from validate import ENGINE_VERSION, read_json

RULES = PLUGIN_ROOT / "rules.md"
MARK_BEGIN = "<!-- ae-video:begin (раздел пишет brief.py, правки здесь затрутся) -->"
MARK_END = "<!-- ae-video:end -->"


def plugin_version():
    path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    return read_json(path).get("version", "?") if path.is_file() else "?"


def split_front(text):
    """Отделяет frontmatter: возвращает словарь простых полей, список skills и тело."""
    meta, skills = {}, []
    if text.startswith("---\n"):
        head, _, text = text[4:].partition("\n---\n")
        key = None
        for line in head.splitlines():
            item = re.match(r"\s+-\s+(\S+)", line)
            if item and key == "skills":
                skills.append(item.group(1))
                continue
            pair = re.match(r"([\w-]+):\s*(.*)", line)
            if pair:
                key, meta[pair.group(1)] = pair.group(1), pair.group(2).strip('"')
    return meta, skills, text.strip()


def resolve(text, arguments=""):
    """Подставляет настоящие пути вместо переменных Claude Code."""
    return text.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_ROOT)).replace("$ARGUMENTS", arguments or "(не переданы)")


def role_file(role):
    for path in (PLUGIN_ROOT / "agents" / f"{role}.md", PLUGIN_ROOT / "skills" / role / "SKILL.md"):
        if path.is_file():
            return path
    return None


def roles():
    agents = sorted(p.stem for p in (PLUGIN_ROOT / "agents").glob("*.md"))
    skills = sorted(p.parent.name for p in (PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    return agents, skills


def environment(video=None):
    """Пути и совместимость: что модель не должна угадывать."""
    settings = load_settings(video)
    base = bases_dir(settings) / settings["base"]
    needed = read_json(base / "base.json").get("engine", 1) if (base / "base.json").is_file() else None
    info = {
        "plugin": str(PLUGIN_ROOT),
        "plugin_version": plugin_version(),
        "engine": ENGINE_VERSION,
        "scripts": f"python3 {PLUGIN_ROOT / 'scripts'}/<скрипт>.py",
        "base": str(base),
        "base_found": (base / "tokens.json").is_file(),
        "base_engine": needed,
        "compatible": needed == ENGINE_VERSION,
    }
    if video:
        info["video"] = str(Path(video).expanduser().resolve())
    return info


def env_lines(info):
    lines = [
        f"- Плагин: `{info['plugin']}` (версия {info['plugin_version']}, движок {info['engine']}). Все скрипты запускай отсюда: `{info['scripts']}`.",
        f"- База: `{info['base']}`" + ("" if info["base_found"] else " — НЕ НАЙДЕНА: проверь bases_dir и base в настройках") + ".",
    ]
    if re.fullmatch(r"\d+\.\d+\.\d+", Path(info["plugin"]).name):
        lines.append(f"- Это копия плагина с номером версии. После обновления плагина этой папки не будет: возьми самую новую версию в `{Path(info['plugin']).parent.as_posix()}` и обнови этот файл командой `brief.py agents-md <видео>` оттуда.")
    if info["base_found"] and not info["compatible"]:
        lines.append(f"- ВНИМАНИЕ: база требует движок {info['base_engine']}, а этот плагин содержит движок {info['engine']}. Ты запущен из устаревшей копии плагина или база новее. Не собирай, скажи автору.")
    if "video" in info:
        lines.append(f"- Видео: `{info['video']}`.")
    return lines


def task_lines(video, fragment, task):
    lines = []
    if video and fragment:
        from pipeline import Fragment, PipelineError
        state = Fragment(video, fragment)
        if state.state is None:
            lines.append(f"- Фрагмент `{fragment}` ещё не начат: первая команда `pipeline.py init {video} {fragment}`.")
        else:
            try:
                action = state.next_action()
            except PipelineError as error:
                action = {"error": str(error)}
            lines.append(f"- Папка фрагмента: `{state.folder}`. Пути файлов ниже — относительно неё.")
            lines.append("- Следующее действие конвейера (`pipeline.py next`):")
            lines.append("```json\n" + json.dumps(action, ensure_ascii=False, indent=2) + "\n```")
    if task:
        lines.append(f"- Задание словами автора: {task}")
    return lines or ["- Задание не передано: спроси оркестратора или автора."]


def brief(role, video=None, fragment=None, task=None, task_only=False):
    path = role_file(role)
    if not path:
        agents, skills = roles()
        raise SystemExit(f"Нет роли {role}. Агенты: {', '.join(agents)}. Навыки: {', '.join(skills)}")
    meta, skills, body = split_front(path.read_text(encoding="utf-8"))
    arguments = " ".join(str(x) for x in (video, fragment, task) if x)
    out = []
    if not task_only:
        kind = "агент" if path.parent.name == "agents" else "навык"
        out += [f"# Роль: {meta.get('name', role)} ({kind} плагина ae-video)", "", meta.get("description", ""), "", resolve(body, arguments)]
        for skill in skills:
            skill_path = PLUGIN_ROOT / "skills" / skill / "SKILL.md"
            if skill_path.is_file():
                out += ["", f"# Опора: навык {skill}", "", resolve(split_front(skill_path.read_text(encoding="utf-8"))[2])]
        out += ["", resolve(RULES.read_text(encoding="utf-8").strip())]
    out += ["", "# Окружение", ""] + env_lines(environment(video))
    out += ["", "# Задание", ""] + task_lines(video, fragment, task)
    return "\n".join(out).strip() + "\n"


def agents_section(video):
    info = environment(video)
    rules = RULES.read_text(encoding="utf-8").strip().split("\n", 1)[1].strip()
    return "\n".join([
        MARK_BEGIN,
        "## Плагин ae-video",
        "",
        "Это видео ведётся плагином ae-video. Любая модель (Claude, ChatGPT, Codex и другие) работает так:",
        "",
        f"1. Узнай фрагмент и получи свою роль: `python3 {PLUGIN_ROOT}/scripts/brief.py make {info['video']} <фрагмент>`. Выполняй вывод по шагам.",
        f"2. Исполнителю этапа (transcriber, director, animator, checker, reviewer, librarian) передавай целиком вывод `python3 {PLUGIN_ROOT}/scripts/brief.py <роль> {info['video']} <фрагмент> --task \"<слова автора>\"`. Своими словами роль не пересказывай.",
        f"3. Состояние этапов: `python3 {PLUGIN_ROOT}/scripts/pipeline.py next {info['video']} <фрагмент>`.",
        "",
        *env_lines(info),
        "",
        "### Жёсткие правила",
        "",
        rules,
        MARK_END,
    ])


def write_agents_md(video, settings=None):
    """Пишет или обновляет раздел плагина в инструкции видео. Текст автора вне раздела не трогает."""
    settings = settings or load_settings(video)
    name = settings.get("agents_file")
    if not name:
        return None
    path = Path(video).expanduser() / name
    section = agents_section(video)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    if MARK_BEGIN in old and MARK_END in old:
        head, rest = old.split(MARK_BEGIN, 1)
        new = head + section + rest.split(MARK_END, 1)[1]
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "# Правила этого видео\n\n") + section + "\n"
    if new != old:
        path.write_text(new, encoding="utf-8")
    return path


def main():
    agents, skills = roles()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("role", help=f"env, agents-md или роль: {', '.join(agents + skills)}")
    parser.add_argument("video", nargs="?", type=Path, help="папка видео")
    parser.add_argument("fragment", nargs="?", help="имя фрагмента")
    parser.add_argument("--task", help="задание словами автора")
    parser.add_argument("--task-only", action="store_true", help="без текста роли: агент уже знает её (Claude Code ae-video:<роль>)")
    parser.add_argument("--video", dest="video_opt", type=Path, help="для env: папка видео")
    args = parser.parse_args()
    video = args.video or args.video_opt

    if args.role == "env":
        print("\n".join(env_lines(environment(video))))
        return
    if args.role == "agents-md":
        if not video:
            parser.error("укажите папку видео")
        path = write_agents_md(video)
        print(f"Обновлено: {path}" if path else "Отключено настройкой agents_file")
        return
    if args.role == "make" and video:
        write_agents_md(video)
    sys.stdout.write(brief(args.role, video, args.fragment, args.task, args.task_only))


if __name__ == "__main__":
    main()
