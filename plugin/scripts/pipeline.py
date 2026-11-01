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

from pipeline_error import PipelineError
from pipeline_extend import ExtensionMixin
from pipeline_rounds import RoundsMixin
from settings import PLUGIN_ROOT, load_settings
from validate import check_built, file_hash, run_check
from view import scene_warnings

STATE_FILE = "state.json"
AUDIO_TYPES = (".wav", ".mp3", ".m4a", ".aif", ".aiff")
STATE_VERSION = 2


def load_stages():
    return json.loads((PLUGIN_ROOT / "config" / "pipeline.json").read_text(encoding="utf-8"))["stages"]


class Fragment(ExtensionMixin, RoundsMixin):
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
            "parts": [],
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

    def audios(self):
        return [self.state["audio"]] + [part["audio"] for part in self.state.get("parts", [])]

    def names(self, patterns):
        names = []
        for pattern in patterns:
            if pattern == "{audio}":
                names += self.audios()
            else:
                names.append(pattern.format(f=self.name))
        return names

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
            if not errors and stage.get("needs_build"):
                errors = check_built(path)
        if not errors and self.state.get("parts"):
            errors = self.check_extension(stage)
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

    def skip(self, stage_id):
        """Автор решил не проводить необязательный этап."""
        self.refresh()
        stage = self.stage(stage_id)
        if not stage.get("optional"):
            raise PipelineError(f"Этап {stage_id} нельзя пропустить")
        waiting = self.blocked_by(stage)
        if waiting:
            raise PipelineError(f"Этап {stage_id} ждёт подтверждения: {', '.join(waiting)}")
        if self.status(stage_id) not in ("pending", "stale"):
            raise PipelineError(f"Этап {stage_id} уже идёт: {self.status(stage_id)}")
        self.state["stages"][stage_id]["skipped"] = True
        self.seal(stage, "approved")

    def done(self, stage_id):
        stage = self.stage(stage_id)
        self.refresh()
        # Правка готовой сцены завершается без start: иначе она зря перезапускала бы этап.
        edited = stage["review"] == "auto" and self.status(stage_id) == "stale"
        if self.status(stage_id) != "running" and not edited:
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
            elif stage.get("optional") and status in ("pending", "stale"):
                action["action"] = "ask_author_run"
            else:
                action["action"] = "start"
            if self.extension():
                action["extension"] = self.extension()
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["init", "status", "next", "start", "done", "approve", "reject", "round", "extend", "skip", "finish", "cancel-extend"])
    parser.add_argument("video", type=Path, help="папка видео")
    parser.add_argument("fragment", help="имя фрагмента")
    parser.add_argument("stage", nargs="?", help="этап для start, done, approve, reject, skip, finish")
    parser.add_argument("--audio", help="для init и extend: имя файла записи в папке фрагмента")
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
        elif args.command == "cancel-extend":
            result = dict(fragment.cancel_extend(), next=fragment.next_action())
        elif args.command == "extend":
            if not args.audio:
                parser.error("укажите --audio")
            fragment.extend(args.audio)
            result = fragment.next_action()
        else:
            if not args.stage:
                parser.error("укажите этап")
            if args.command == "start":
                fragment.start(args.stage, args.force)
            elif args.command == "skip":
                fragment.skip(args.stage)
            elif args.command == "finish":
                fragment.finish(args.stage)
            elif args.command == "done":
                fragment.done(args.stage)
            elif args.command == "approve":
                fragment.approve(args.stage)
            else:
                if not args.note:
                    parser.error("укажите --note")
                fragment.reject(args.stage, args.note)
            result = fragment.next_action()
            if args.command == "done" and fragment.stage(args.stage).get("needs_build"):
                scene = fragment.folder / fragment.names(fragment.stage(args.stage)["outputs"])[0]
                result["warnings"] = scene_warnings(scene, fragment.settings)
    except PipelineError as error:
        sys.exit(f"Отказ: {error}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
