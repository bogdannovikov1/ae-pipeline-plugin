"""Круги предревью: учёт отчётов и решение, исправлять или передавать автору."""
import json

from pipeline_error import PipelineError

REPORTS_DIR = "reviews"
SEVERITIES = ["minor", "major", "critical"]


def finding_key(finding):
    """Находка считается той же, если совпали закон и момент."""
    return finding.get("rule"), finding.get("time")


class RoundsMixin:
    """Часть Fragment: каждый круг запускается с согласия автора, здесь только учёт."""

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

    def finish(self, stage_id):
        """Автор решил больше не запускать ревью: этап закрывается с текущей сценой."""
        stage = self.stage(stage_id)
        if stage["review"] != "rounds" or self.status(stage_id) != "running":
            raise PipelineError(f"Этап {stage_id} нельзя завершить: {self.status(stage_id)}")
        self.finish_rounds()

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
