#!/usr/bin/env python3
"""Хранит состояние фрагмента и решает, какой этап следующий.

Пример:
    python3 pipeline.py init ~/Videos/my_video F1
    python3 pipeline.py next ~/Videos/my_video F1
    python3 pipeline.py approve ~/Videos/my_video F1 transcript
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from settings import PLUGIN_ROOT, load_settings
from validate import run_check

STATE_FILE = "state.json"
REPORTS_DIR = "reviews"
AUDIO_TYPES = (".wav", ".mp3", ".m4a", ".aif", ".aiff")
SEVERITIES = ["minor", "major", "critical"]
STATE_VERSION = 1


class PipelineError(Exception):
    """Действие запрещено состоянием этапов."""


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_stages():
    return json.loads((PLUGIN_ROOT / "config" / "pipeline.json").read_text(encoding="utf-8"))["stages"]


class Fragment:
    """Состояние одного фрагмента видео."""

    def __init__(self, video, name, stages=None, settings=None):
        self.video = Path(video).expanduser()
        self.name = name
        self.folder = self.video / "fragments" / name
        self.stages = stages or load_stages()
        self.settings = settings or load_settings(self.video)
        self.path = self.folder / STATE_FILE
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else None

    def create(self, audio=None):
        if self.state:
            raise PipelineError(f"Фрагмент уже начат: {self.path}")
        self.folder.mkdir(parents=True, exist_ok=True)
        if not audio:
            found = [p for p in sorted(self.folder.glob(f"{self.name}.*")) if p.suffix.lower() in AUDIO_TYPES]
            if not found:
                raise PipelineError(f"Положите запись в {self.folder}/{self.name}.wav или укажите --audio")
            audio = found[0].name
        self.state = {
            "version": STATE_VERSION,
            "fragment": self.name,
            "audio": audio,
            "rounds": 0,
            "stages": {stage["id"]: {"status": "pending", "note": None} for stage in self.stages},
        }
        self.save()

    def save(self):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def stage(self, stage_id):
        for stage in self.stages:
            if stage["id"] == stage_id:
                return stage
        raise PipelineError(f"Неизвестный этап: {stage_id}")

    def status(self, stage_id):
        return self.state["stages"][stage_id]["status"]

    def names(self, patterns):
        return [p.format(f=self.name, audio=self.state["audio"]) for p in patterns]

    def hashes(self, patterns):
        """Хэши существующих файлов; отсутствующий файл получает None."""
        result = {}
        for name in self.names(patterns):
            path = self.folder / name
            result[name] = file_hash(path) if path.is_file() else None
        return result

    def is_outdated(self, stage):
        record = self.state["stages"][stage["id"]]
        if record.get("inputs") != self.hashes(stage["inputs"]):
            return True
        if record["status"] == "approved" and stage.get("track_outputs", True):
            return record.get("outputs") != self.hashes(stage["outputs"])
        return False

    def refresh(self):
        """Помечает устаревшим этап с изменившимися файлами и всё, что от него зависит. Файлы не удаляются."""
        changed = False
        outdated = set()
        for stage in self.stages:
            status = self.status(stage["id"])
            if status in ("approved", "review", "stale"):
                if status == "stale" or set(stage["needs"]) & outdated or self.is_outdated(stage):
                    outdated.add(stage["id"])
                    if status != "stale":
                        self.state["stages"][stage["id"]]["status"] = "stale"
                        changed = True
                    if stage["id"] == "preflight":
                        self.state["rounds"] = 0
        if changed:
            self.save()

    def blocked_by(self, stage):
        return [need for need in stage["needs"] if self.status(need) != "approved"]

    def seal(self, stage, status):
        record = self.state["stages"][stage["id"]]
        record.update(
            status=status,
            inputs=self.hashes(stage["inputs"]),
            outputs=self.hashes(stage["outputs"]),
            settings=hashlib.sha256(json.dumps(self.settings, sort_keys=True).encode()).hexdigest()[:12],
            finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.save()

    def check_outputs(self, stage, final):
        """Проверяет выходы этапа: файлы на месте и проверка из pipeline.json пройдена."""
        errors = []
        for name in self.names(stage["outputs"]):
            if not (self.folder / name).is_file():
                errors.append(f"нет файла {name}")
        if not errors and stage.get("validate"):
            names = {"timeline": f"{self.name}.timeline.json"}
            path = self.folder / self.names(stage["outputs"])[0]
            errors = run_check(stage["validate"], path, self.folder, names, self.settings, final)
        return errors

    def start(self, stage_id, force=False):
        self.refresh()
        stage = self.stage(stage_id)
        waiting = self.blocked_by(stage)
        if waiting:
            raise PipelineError(f"Этап {stage_id} ждёт подтверждения: {', '.join(waiting)}")
        if stage.get("protected") and self.status(stage_id) == "stale" and not force:
            raise PipelineError(f"Этап {stage_id} содержит ручные правки. Пересборка только по решению автора: --force")
        self.state["stages"][stage_id]["status"] = "running"
        self.save()

    def done(self, stage_id):
        stage = self.stage(stage_id)
        if self.status(stage_id) != "running":
            raise PipelineError(f"Этап {stage_id} не запущен")
        if stage["review"] == "rounds":
            raise PipelineError("Предревью завершает команда round")
        errors = self.check_outputs(stage, final=False)
        if errors:
            raise PipelineError("; ".join(errors))
        self.seal(stage, "approved" if stage["review"] == "auto" else "review")

    def approve(self, stage_id):
        stage = self.stage(stage_id)
        self.refresh()
        if self.status(stage_id) != "review":
            raise PipelineError(f"Этап {stage_id} не ждёт ревью: {self.status(stage_id)}")
        errors = self.check_outputs(stage, final=True)
        if errors:
            raise PipelineError("; ".join(errors))
        self.seal(stage, "approved")

    def reject(self, stage_id, note):
        if self.status(stage_id) not in ("review", "approved", "stale"):
            raise PipelineError(f"Этап {stage_id} нечего отклонять: {self.status(stage_id)}")
        record = self.state["stages"][stage_id]
        record.update(status="rejected", note=note)
        if self.stage(stage_id)["review"] == "rounds":
            self.state["rounds"] = 0
        self.save()
        self.refresh_after_reject(stage_id)

    def refresh_after_reject(self, stage_id):
        """Этапы после отклонённого больше не считаются подтверждёнными."""
        seen = {stage_id}
        for stage in self.stages:
            if set(stage["needs"]) & seen:
                seen.add(stage["id"])
                record = self.state["stages"][stage["id"]]
                if record["status"] in ("approved", "review"):
                    record["status"] = "stale"
        self.save()

    def report_path(self, number):
        return self.folder / REPORTS_DIR / f"round_{number}.json"

    def blocking(self, report):
        """Находки не ниже порога из настроек; порог none отключает исправления."""
        threshold = self.settings["review"]["block_on"]
        if threshold not in SEVERITIES:
            return []
        level = SEVERITIES.index(threshold)
        return [f for f in report["findings"] if SEVERITIES.index(f["severity"]) >= level]

    def finish_rounds(self):
        """Сцена менялась между кругами: фиксируем её последнюю версию в сборке и предревью."""
        self.seal(self.stage("build"), "approved")
        self.seal(self.stage("preflight"), "approved")

    def round(self):
        """Учитывает отчёт очередного круга и говорит, исправлять или передавать автору."""
        if self.status("preflight") != "running":
            raise PipelineError("Предревью не запущено")
        number = self.state["rounds"] + 1
        path = self.report_path(number)
        if not path.is_file():
            raise PipelineError(f"Нет отчёта {path}")
        report = json.loads(path.read_text(encoding="utf-8"))
        for finding in report["findings"]:
            if finding.get("severity") not in SEVERITIES:
                raise PipelineError(f"Неизвестная серьёзность: {finding.get('severity')}")

        blocking = self.blocking(report)
        keys = {finding_key(f) for f in blocking}
        repeated = False
        if number > 1:
            previous = json.loads(self.report_path(number - 1).read_text(encoding="utf-8"))
            repeated = bool(keys) and keys <= {finding_key(f) for f in self.blocking(previous)}

        self.state["rounds"] = number
        limit = self.settings["review"]["max_rounds"]
        if not blocking:
            reason = "clean"
        elif repeated:
            reason = "repeated"
        elif number >= limit:
            reason = "limit"
        else:
            self.save()
            return {"decision": "fix", "round": number, "limit": limit, "findings": blocking}
        self.finish_rounds()
        return {"decision": "author", "reason": reason, "round": number, "limit": limit, "findings": report["findings"]}

    def next_action(self):
        """Возвращает единственное следующее действие."""
        self.refresh()
        for stage in self.stages:
            stage_id = stage["id"]
            status = self.status(stage_id)
            if status == "approved" or (stage.get("periodic") and not self.is_due()):
                continue
            action = {
                "stage": stage_id,
                "title": stage["title"],
                "status": status,
                "executor": stage["executor"],
                "folder": str(self.folder),
                "inputs": self.names(stage["inputs"]),
                "outputs": self.names(stage["outputs"]),
                "note": self.state["stages"][stage_id].get("note"),
            }
            if status == "review":
                action["action"] = "ask_author"
            elif stage.get("protected") and status == "stale":
                action["action"] = "ask_author_rebuild"
            elif status == "running":
                action["action"] = "resume"
            else:
                action["action"] = "start"
            if stage["review"] == "rounds":
                number = self.state["rounds"] + 1
                action["report"] = str(self.report_path(number).relative_to(self.folder))
                action["round"] = number
                action["limit"] = self.settings["review"]["max_rounds"]
            return action
        return {"action": "finished", "fragment": self.name, "folder": str(self.folder)}

    def is_due(self):
        """Периодический этап идёт на каждом N-м фрагменте видео; 0 отключает его."""
        every = self.settings["base_update"]["every_fragments"]
        if not every:
            return False
        names = sorted(p.name for p in (self.video / "fragments").iterdir() if p.is_dir())
        return (names.index(self.name) + 1) % every == 0

    def overview(self):
        self.refresh()
        return {
            "fragment": self.name,
            "rounds": self.state["rounds"],
            "stages": {stage["id"]: self.status(stage["id"]) for stage in self.stages},
        }


def finding_key(finding):
    """Находка считается той же, если совпали закон и момент."""
    return finding.get("rule"), finding.get("time")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["init", "status", "next", "start", "done", "approve", "reject", "round"])
    parser.add_argument("video", type=Path, help="папка видео")
    parser.add_argument("fragment", help="имя фрагмента")
    parser.add_argument("stage", nargs="?", help="этап для start, done, approve, reject")
    parser.add_argument("--audio", help="для init: имя файла записи в папке фрагмента")
    parser.add_argument("--note", help="для reject: что исправить")
    parser.add_argument("--force", action="store_true", help="для start: пересобрать этап с ручными правками")
    args = parser.parse_args()

    fragment = Fragment(args.video, args.fragment)
    try:
        if args.command == "init":
            fragment.create(args.audio)
            result = fragment.next_action()
        elif fragment.state is None:
            raise PipelineError(f"Фрагмент не начат, сначала init: {fragment.folder}")
        elif args.command == "status":
            result = fragment.overview()
        elif args.command == "next":
            result = fragment.next_action()
        elif args.command == "round":
            result = fragment.round()
        else:
            if not args.stage:
                parser.error("укажите этап")
            if args.command == "start":
                fragment.start(args.stage, args.force)
            elif args.command == "done":
                fragment.done(args.stage)
            elif args.command == "approve":
                fragment.approve(args.stage)
            else:
                if not args.note:
                    parser.error("укажите --note")
                fragment.reject(args.stage, args.note)
            result = fragment.next_action()
    except PipelineError as error:
        sys.exit(f"Отказ: {error}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
