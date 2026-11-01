"""Расширение фрагмента новой записью и отмена расширения."""
import hashlib
import json
import shutil
from pathlib import Path

from approve_transcript import transcript_hash
from pipeline_error import PipelineError


def list_hash(items):
    text = json.dumps(items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def kept_words(words):
    """Сравниваем то, что определяет слово на таймлайне."""
    return [[w["i"], w["word"], w["start"], w["end"]] for w in words]


def frozen(data):
    """Транскрипт до расширения был заморожен, резервная копия могла быть снята раньше заморозки."""
    data["status"] = "approved"
    data["transcript_hash"] = transcript_hash(data)
    return data


class ExtensionMixin:
    """Часть Fragment: новая запись дописывается, а утверждённое остаётся как было."""

    def read(self, suffix):
        path = self.folder / f"{self.name}.{suffix}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def snapshot(self):
        """Запоминает, что расширение не должно изменить."""
        words = self.read("timeline")["words"]
        beats = (self.read("animation") or {}).get("beats", [])
        items = (self.read("scene") or {}).get("items", [])
        return {
            "first_word": len(words),
            "words": list_hash(kept_words(words)),
            "beats_from": len(beats),
            "beats": list_hash(beats),
            "items_from": len(items),
            "items": list_hash(items),
        }

    def check_extension(self, stage):
        """Этап добавляет только новое: старое должно остаться как было."""
        keep = self.state["parts"][-1]["keep"]
        if stage["id"] == "transcript":
            words = self.read("timeline")["words"]
            if len(words) <= keep["first_word"]:
                return ["новая часть не добавлена в транскрипт: extend_timeline.py"]
            if list_hash(kept_words(words[:keep["first_word"]])) != keep["words"]:
                return ["расширение изменило слова первой части"]
        if stage["id"] == "plan":
            beats = self.read("animation")["beats"]
            if list_hash(beats[:keep["beats_from"]]) != keep["beats"]:
                return ["расширение изменило старые биты плана"]
        if stage["id"] == "build":
            return self.check_items(keep)
        return []

    def check_items(self, keep):
        items = self.read("scene")["items"]
        start = keep["items_from"]
        if list_hash(items[:start]) != keep["items"]:
            return ["расширение изменило старые элементы сцены"]
        if not self.state["parts"][-1]["append"]:
            return []
        old = {item.get("id") or f"{item.get('block')}_{n}" for n, item in enumerate(items[:start])}
        return [
            f"{item.get('id')}: цель {item['params']['target']} собрана раньше, в дополняемой композиции она недоступна"
            for item in items[start:]
            if item.get("params", {}).get("target") in old
        ]

    def extend(self, audio):
        """Начинает новую часть фрагмента: всё утверждённое остаётся, этапы идут только для добавленного."""
        self.refresh()
        if self.status("transcript") != "approved" or self.status("plan") != "approved":
            raise PipelineError("Расширять можно фрагмент с утверждённым транскриптом и планом")
        if self.status("build") == "running" or self.status("preflight") == "running":
            raise PipelineError("Сначала завершите текущий этап")
        if not (self.folder / audio).is_file():
            raise PipelineError(f"Нет файла {audio} в {self.folder}")
        if audio in self.audios():
            raise PipelineError(f"Часть уже есть: {audio}")
        self.state.setdefault("parts", []).append({
            "audio": audio,
            "keep": self.snapshot(),
            "append": self.status("polish") == "approved",
            "stages_before": {stage["id"]: self.status(stage["id"]) for stage in self.stages},
        })
        self.state["rounds"] = 0
        for stage in self.stages:
            self.state["stages"][stage["id"]] = {"status": "pending", "note": None}
        self.save()

    def statuses_before(self, part):
        """Статусы до расширения. Старые записи их не хранят: восстанавливаем по тому, что было готово."""
        if "stages_before" in part:
            return part["stages_before"]
        keep = part["keep"]
        built = keep["items_from"] > 0
        finished = part["append"]
        return {
            "transcript": "approved",
            "plan": "approved",
            "build": "approved" if built else "pending",
            "preflight": "approved" if finished else "pending",
            "polish": "approved" if finished else "pending",
            "curate": "approved" if (self.folder / f"{self.name}.base_proposals.md").is_file() else "pending",
        }

    def restored_timeline(self, keep):
        """Возвращает транскрипт без новой части. Первая часть обязана остаться прежней."""
        name = f"{self.name}.timeline.json"
        backup = self.folder / f"{self.name}.timeline.backup.json"
        if backup.is_file():
            saved = json.loads(backup.read_text(encoding="utf-8"))
            if len(saved["words"]) == keep["first_word"] and list_hash(kept_words(saved["words"])) == keep["words"]:
                return frozen(saved)
        data = json.loads((self.folder / name).read_text(encoding="utf-8"))
        count = keep["first_word"]
        data["words"] = data["words"][:count]
        if list_hash(kept_words(data["words"])) != keep["words"]:
            raise PipelineError("Слова первой части изменились, автоматический откат невозможен")
        data["phrases"] = [phrase for phrase in data["phrases"] if phrase["last_word"] < count]
        data["text"] = " ".join(phrase["text"] for phrase in data["phrases"])
        data.pop("parts", None)
        return frozen(data)

    def cancel_extend(self):
        """Отменяет последнюю часть: файлы и статусы этапов возвращаются к состоянию до extend."""
        if not self.state.get("parts"):
            raise PipelineError("Расширения нет, отменять нечего")
        part = self.state["parts"][-1]
        keep = part["keep"]

        plan = self.read("animation")
        scene = self.read("scene")
        if plan and list_hash(plan["beats"][:keep["beats_from"]]) != keep["beats"]:
            raise PipelineError("Старые биты плана изменились, автоматический откат невозможен")
        if scene and list_hash(scene["items"][:keep["items_from"]]) != keep["items"]:
            raise PipelineError("Старые элементы сцены изменились, автоматический откат невозможен")

        was_built = bool(scene) and len(scene["items"]) > keep["items_from"]
        timeline_data = self.restored_timeline(keep)
        for name in (f"{self.name}.timeline.json", f"{self.name}.animation.json", f"{self.name}.scene.json"):
            path = self.folder / name
            if path.is_file():
                shutil.copy(path, path.with_name(name.replace(".json", ".extended.json")))

        self.write_json("timeline", timeline_data)
        if plan:
            plan["beats"] = plan["beats"][:keep["beats_from"]]
            plan["transcript_hash"] = timeline_data["transcript_hash"]
            self.write_json("animation", plan)
        if scene:
            scene["items"] = scene["items"][:keep["items_from"]]
            if "transcript_hash" in scene:
                scene["transcript_hash"] = timeline_data["transcript_hash"]
            self.write_json("scene", scene)

        statuses = self.statuses_before(part)
        self.state["parts"].pop()
        self.state["rounds"] = 0
        for stage in self.stages:
            status = statuses[stage["id"]]
            self.state["stages"][stage["id"]] = {"status": "pending", "note": None}
            if status in ("approved", "review"):
                self.seal(stage, status)
        self.save()
        return {"cancelled": part["audio"], "words": keep["first_word"], "composition_changed": was_built and part["append"]}

    def write_json(self, suffix, data):
        path = self.folder / f"{self.name}.{suffix}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def extension(self):
        """Что должен знать исполнитель этапа, когда идёт новая часть."""
        if not self.state.get("parts"):
            return None
        part = self.state["parts"][-1]
        keep = part["keep"]
        result = {
            "audio": part["audio"],
            "timeline": f"{Path(part['audio']).stem}.timeline.json",
            "first_word": keep["first_word"],
            "beats_from": keep["beats_from"],
            "items_from": keep["items_from"],
            "append": part["append"],
        }
        words = self.read("timeline")["words"]
        if len(words) > keep["first_word"]:
            result["from_time"] = words[keep["first_word"]]["start"]
        return result
