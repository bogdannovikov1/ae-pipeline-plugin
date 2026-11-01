import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "scripts"))

from approve_transcript import transcript_hash
from base_index import asset_lines, block_lines
from base_scan import scan
from capture import moments
from extend_timeline import merge as merge_part
from pipeline import Fragment, PipelineError
from settings import load_settings, merge
from view import review_lines, scene_lines, text_lines, timing_warnings
from setup_base import STARTER, create_base
from validate import check_animation, check_scene, check_timeline


def timeline(status="approved"):
    words = [{"i": n, "word": f"w{n}", "start": n * 0.5, "end": n * 0.5 + 0.4, "needs_review": False} for n in range(4)]
    data = {"status": status, "words": words, "phrases": []}
    data["transcript_hash"] = transcript_hash(data)
    return data


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.video = self.root / "video"
        self.dir = self.video / "fragments" / "F1"
        self.dir.mkdir(parents=True)
        self.bases = self.root / "bases"
        block = self.bases / "b" / "blocks" / "label"
        block.mkdir(parents=True)
        (block / "block.jsxinc").write_text("")
        (self.bases / "b" / "tokens.json").write_text("{}")
        self.settings = merge(load_settings(user_path=self.root / "none"), {"bases_dir": str(self.bases), "base": "b"})
        self.write("F1.wav", "audio")

    def write(self, name, value):
        path = self.dir / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def fragment(self):
        fragment = Fragment(self.video, "F1", settings=self.settings)
        if not fragment.state:
            fragment.create()
        return fragment

    def scene(self, **change):
        scene = {"base": "b", "audio": "F1.wav", "timeline": "F1.timeline.json", "tail": 1,
                 "comp": {"name": "c"}, "items": [{"id": "a", "block": "label", "at_word": 1}]}
        scene.update(change)
        return scene

    def to_preflight(self):
        f = self.fragment()
        f.start("transcript")
        self.write("F1.timeline.json", timeline())
        f.done("transcript")
        f.approve("transcript")
        f.start("plan")
        self.write("F1.animation.json", {"transcript_hash": timeline()["transcript_hash"], "beats": [{"at_word": 0}]})
        f.done("plan")
        f.approve("plan")
        f.start("build")
        self.write("F1.scene.json", self.scene())
        f.done("build")
        f.start("preflight")
        return f

    def report(self, number, *findings):
        self.write(f"reviews/round_{number}.json", {"findings": list(findings)})


class Settings(Case):
    def test_merge_keeps_neighbours(self):
        self.assertEqual(merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}), {"a": {"x": 1, "y": 3}})

    def test_video_layer_wins(self):
        (self.video / "settings.json").write_text('{"review": {"max_rounds": 5}}')
        settings = load_settings(self.video, user_path=self.root / "none")
        self.assertEqual(settings["review"]["max_rounds"], 5)
        self.assertEqual(settings["review"]["block_on"], "major")


class Validate(Case):
    def test_timeline_final_needs_approval(self):
        path = self.write("t.json", timeline("draft"))
        self.assertEqual(check_timeline(path), [])
        self.assertTrue(check_timeline(path, final=True))

    def test_timeline_bad_hash(self):
        data = timeline()
        data["words"][0]["word"] = "другое"
        self.assertIn("transcript_hash", check_timeline(self.write("t.json", data), final=True)[0])

    def test_animation_old_transcript(self):
        self.write("t.json", timeline())
        path = self.write("a.json", {"transcript_hash": "старый", "beats": [{"at_word": 0}]})
        self.assertTrue(check_animation(path, self.dir / "t.json"))

    def test_scene_ok(self):
        self.write("F1.timeline.json", timeline())
        self.assertEqual(check_scene(self.write("s.json", self.scene()), self.settings), [])

    def test_scene_bad_word_block_and_after(self):
        self.write("F1.timeline.json", timeline())
        items = [{"block": "label", "at_word": 99}, {"block": "нет", "at_word": 1}, {"block": "label", "after": "x"}]
        errors = check_scene(self.write("s.json", self.scene(items=items)), self.settings)
        self.assertEqual(len(errors), 3)

    def test_scene_old_transcript(self):
        self.write("F1.timeline.json", timeline())
        errors = check_scene(self.write("s.json", self.scene(transcript_hash="старый")), self.settings)
        self.assertTrue(errors)


class Stages(Case):
    def test_order_and_resume(self):
        f = self.fragment()
        self.assertEqual(f.next_action()["stage"], "transcript")
        with self.assertRaises(PipelineError):
            f.start("plan")
        f.start("transcript")
        again = Fragment(self.video, "F1", settings=self.settings)
        self.assertEqual(again.next_action()["action"], "resume")

    def test_approve_needs_validation(self):
        f = self.fragment()
        f.start("transcript")
        self.write("F1.timeline.json", timeline("draft"))
        f.done("transcript")
        with self.assertRaises(PipelineError):
            f.approve("transcript")

    def test_changed_input_marks_dependents_stale(self):
        f = self.to_preflight()
        f.state["stages"]["preflight"]["status"] = "pending"
        f.save()
        self.write("F1.wav", "новая запись")
        f.refresh()
        self.assertEqual(f.status("transcript"), "stale")
        self.assertEqual(f.status("build"), "stale")
        self.assertTrue((self.dir / "F1.scene.json").exists())

    def test_reopened_transcript_is_stale(self):
        f = self.to_preflight()
        self.write("F1.timeline.json", timeline("draft"))
        f.refresh()
        self.assertEqual(f.status("transcript"), "stale")

    def test_reject_returns_with_note(self):
        f = self.fragment()
        f.start("transcript")
        self.write("F1.timeline.json", timeline())
        f.done("transcript")
        f.reject("transcript", "поправить слово")
        action = f.next_action()
        self.assertEqual((action["stage"], action["note"]), ("transcript", "поправить слово"))

    def test_polish_is_protected(self):
        f = self.to_preflight()
        self.report(1)
        f.round()
        f.start("polish")
        self.write("F1.aep", "project")
        f.done("polish")
        f.approve("polish")
        f.settings["base_update"]["every_fragments"] = 0
        self.assertEqual(f.next_action()["action"], "finished")
        self.write("F1.aep", "автор сохранил проект")
        self.assertEqual(f.next_action()["action"], "finished")

        scene = self.scene(tail=2)
        self.write("F1.scene.json", scene)
        self.assertEqual(f.next_action()["stage"], "build")
        f.start("build")
        f.done("build")
        f.start("preflight")
        self.report(1)
        f.round()
        self.assertEqual(f.next_action()["action"], "ask_author_rebuild")
        with self.assertRaises(PipelineError):
            f.start("polish")
        f.start("polish", force=True)


class BaseUpdate(Case):
    def finish_polish(self):
        f = self.to_preflight()
        self.report(1)
        f.round()
        f.start("polish")
        self.write("F1.aep", "project")
        f.done("polish")
        f.approve("polish")
        return f

    def test_due_on_every_fragment(self):
        self.assertEqual(self.finish_polish().next_action()["stage"], "curate")

    def test_due_on_every_second_fragment(self):
        self.settings["base_update"]["every_fragments"] = 2
        self.assertEqual(self.finish_polish().next_action()["action"], "finished")
        (self.video / "fragments" / "F2").mkdir()
        fragment = Fragment(self.video, "F2", settings=self.settings)
        self.assertTrue(fragment.is_due())

    def test_scan_counts_repeats(self):
        self.write("reviews/round_1.json", {"findings": [{"rule": "color.contrast"}]})
        self.write("reviews/round_2.json", {"findings": [{"rule": "color.contrast"}, {"rule": "type.size"}]})
        block = self.dir / "blocks" / "new_block"
        block.mkdir(parents=True)
        items = [{"block": "label", "at_word": 1, "tokens": {"motion": {"lead": 0.2}}},
                 {"block": "label", "at_word": 2, "tokens": {"motion": {"lead": 0.2}}}]
        self.write("F1.scene.json", self.scene(items=items))
        result = scan(self.video, 2)
        self.assertEqual(result["local_blocks"], ["F1/new_block"])
        self.assertEqual(result["repeated_findings"], {"color.contrast": 2})
        self.assertEqual(result["repeated_tokens"], {"motion.lead": 2})


class Extend(Case):
    def polished(self):
        f = self.to_preflight()
        self.report(1)
        f.round()
        f.start("polish")
        self.write("F1.aep", "project")
        f.done("polish")
        f.approve("polish")
        return f

    def merged(self):
        main = timeline("draft")
        part = {"source": "F1_p2.wav", "text": "new", "words": [dict(timeline()["words"][0])],
                "phrases": [{"i": 0, "text": "new", "start": 0, "end": 0.4, "first_word": 0, "last_word": 0}]}
        main.update(source="F1.wav", text="old", fps=30)
        merged = merge_part(main, part, 10.0)
        merged["status"] = "approved"
        merged["transcript_hash"] = transcript_hash(merged)
        return merged

    def test_merge_continues_numbers_and_time(self):
        merged = self.merged()
        self.assertEqual(merged["words"][-1]["i"], 4)
        self.assertEqual(merged["words"][-1]["start"], 10.0)
        self.assertEqual(merged["words"][-1]["start_frame"], 300)
        self.assertEqual(merged["phrases"][-1]["first_word"], 4)
        self.assertEqual(merged["parts"][1], {"audio": "F1_p2.wav", "at": 10.0, "first_word": 4})

    def test_extend_runs_only_new_part(self):
        f = self.polished()
        self.write("F1_p2.wav", "new audio")
        f.extend("F1_p2.wav")
        action = f.next_action()
        self.assertEqual(action["stage"], "transcript")
        self.assertEqual(action["extension"]["first_word"], 4)
        self.assertTrue(action["extension"]["append"])

        f.start("transcript")
        with self.assertRaises(PipelineError):
            f.done("transcript")
        merged = self.merged()
        self.write("F1.timeline.json", merged)
        f.done("transcript")
        f.approve("transcript")

        f.start("plan")
        hash_ = merged["transcript_hash"]
        self.write("F1.animation.json", {"transcript_hash": hash_, "beats": [{"at_word": 1}, {"at_word": 4}]})
        with self.assertRaises(PipelineError):
            f.done("plan")
        self.write("F1.animation.json", {"transcript_hash": hash_, "beats": [{"at_word": 0}, {"at_word": 4}]})
        f.done("plan")
        f.approve("plan")

        f.start("build")
        old = self.scene()["items"]
        self.write("F1.scene.json", self.scene(items=[{"id": "x", "block": "label", "at_word": 1}]))
        with self.assertRaises(PipelineError):
            f.done("build")
        new = {"id": "b", "block": "label", "at_word": 4, "params": {"target": "a"}}
        self.write("F1.scene.json", self.scene(items=old + [new]))
        with self.assertRaises(PipelineError):
            f.done("build")
        new["params"] = {}
        self.write("F1.scene.json", self.scene(items=old + [new]))
        f.done("build")
        self.assertEqual(f.next_action()["extension"]["from_time"], 10.0)

    def test_old_words_are_protected(self):
        f = self.polished()
        self.write("F1_p2.wav", "new audio")
        f.extend("F1_p2.wav")
        f.start("transcript")
        merged = self.merged()
        merged["words"][0]["word"] = "другое"
        self.write("F1.timeline.json", merged)
        with self.assertRaises(PipelineError):
            f.done("transcript")

    def extended(self):
        f = self.polished()
        self.write("F1_p2.wav", "new audio")
        f.extend("F1_p2.wav")
        f.start("transcript")
        self.write("F1.timeline.json", self.merged())
        f.done("transcript")
        f.approve("transcript")
        return f

    def test_cancel_restores_files_and_stages(self):
        f = self.extended()
        hash_ = timeline()["transcript_hash"]
        f.start("plan")
        self.write("F1.animation.json", {"transcript_hash": "новый", "beats": [{"at_word": 0}, {"at_word": 4}]})
        result = f.cancel_extend()
        self.assertEqual(result["words"], 4)
        data = json.loads((self.dir / "F1.timeline.json").read_text())
        self.assertEqual(len(data["words"]), 4)
        self.assertEqual(data["transcript_hash"], hash_)
        self.assertNotIn("parts", data)
        plan = json.loads((self.dir / "F1.animation.json").read_text())
        self.assertEqual((len(plan["beats"]), plan["transcript_hash"]), (1, hash_))
        self.assertEqual(f.state["parts"], [])
        self.assertEqual(check_timeline(self.dir / "F1.timeline.json", final=True), [])
        self.assertTrue((self.dir / "F1.timeline.extended.json").exists())
        for stage in ("transcript", "plan", "build", "preflight", "polish"):
            self.assertEqual(f.status(stage), "approved", stage)
        self.assertEqual(f.next_action()["stage"], "curate")

    def test_cancel_uses_unfrozen_backup(self):
        f = self.extended()
        self.write("F1.timeline.backup.json", timeline("draft"))
        data = json.loads((self.dir / "F1.timeline.backup.json").read_text())
        data.pop("transcript_hash")
        self.write("F1.timeline.backup.json", data)
        f.cancel_extend()
        self.assertEqual(check_timeline(self.dir / "F1.timeline.json", final=True), [])

    def test_cancel_works_for_state_without_saved_statuses(self):
        f = self.extended()
        del f.state["parts"][-1]["stages_before"]
        f.cancel_extend()
        self.assertEqual(f.status("polish"), "approved")
        self.assertEqual(f.next_action()["stage"], "curate")

    def test_cancel_refuses_when_old_part_changed(self):
        f = self.extended()
        self.write("F1.animation.json", {"transcript_hash": "x", "beats": [{"at_word": 2}]})
        with self.assertRaises(PipelineError):
            f.cancel_extend()
        self.assertEqual(len(f.state["parts"]), 1)

    def test_cancel_reports_appended_layers(self):
        f = self.extended()
        scene = self.scene()
        scene["items"].append({"id": "b", "block": "label", "at_word": 4})
        self.write("F1.scene.json", scene)
        self.assertTrue(f.cancel_extend()["composition_changed"])

    def test_cancel_needs_extension(self):
        with self.assertRaises(PipelineError):
            self.fragment().cancel_extend()

    def test_needs_approved_plan_and_audio(self):
        f = self.fragment()
        with self.assertRaises(PipelineError):
            f.extend("F1_p2.wav")
        f = self.polished()
        with self.assertRaises(PipelineError):
            f.extend("нет.wav")

    def test_full_rebuild_before_polish(self):
        f = self.to_preflight()
        self.report(1)
        f.round()
        self.write("F1_p2.wav", "new audio")
        f.extend("F1_p2.wav")
        self.assertFalse(f.next_action()["extension"]["append"])


class BaseIndex(Case):
    def test_lists_blocks_and_assets(self):
        base = self.bases / "b"
        (base / "blocks" / "label" / "block.json").write_text(
            json.dumps({"id": "label", "description": "Подпись", "params": {"text": "текст", "x": "X"}}))
        (base / "assets").mkdir()
        (base / "assets" / "assets.json").write_text(json.dumps([{"id": "pop", "type": "audio", "description": "Щелчок"}]))
        self.assertEqual(list(block_lines(base)), ["label (text, x): Подпись"])
        self.assertEqual(list(asset_lines(base)), ["pop [audio]: Щелчок"])


class View(Case):
    timing = {"max_early": 0.4, "long_phrase_words": 7, "long_phrase_seconds": 2.5, "long_phrase_zone": 0.5}
    tokens = {"motion": {"lead": 0.1}}

    def data(self):
        words = [{"i": n, "word": f"w{n}", "start": n * 0.3, "end": n * 0.3 + 0.2, "needs_review": n == 2} for n in range(11)]
        return {"words": words, "phrases": [
            {"first_word": 0, "last_word": 1},
            {"first_word": 2, "last_word": 10},
        ]}

    def warnings(self, **item):
        return timing_warnings({"items": [item]}, self.data(), self.tokens, self.timing)

    def test_long_phrase_shows_anchor_zone(self):
        lines = list(text_lines(self.data(), self.timing))
        self.assertNotIn("опора", lines[0])
        self.assertIn("опора: 6-10", lines[1])

    def test_range_and_review(self):
        self.assertEqual(len(list(text_lines(self.data(), self.timing, 0, 1))), 1)
        self.assertIn("«w2»", list(review_lines(self.data()))[0])

    def test_good_anchor_passes(self):
        self.assertEqual(self.warnings(id="a", at_word=8), [])

    def test_start_of_long_phrase_warns(self):
        self.assertIn("в её начале", self.warnings(id="a", at_word=3)[0])
        self.assertEqual(self.warnings(id="a", at_word=3, anchor_reason="заголовок"), [])

    def test_steps_through_phrase_and_stagger_pass(self):
        items = [{"id": "a", "at_word": 3}, {"id": "b", "at_word": 3, "delay": 0.2}, {"id": "c", "at_word": 8}]
        self.assertEqual(timing_warnings({"items": items}, self.data(), self.tokens, self.timing), [])

    def test_short_phrase_has_no_zone(self):
        self.assertEqual(self.warnings(id="a", at_word=0), [])

    def test_late_and_too_early(self):
        self.assertIn("опаздывает", self.warnings(id="a", at_word=8, delay=0.3)[0])
        self.assertIn("раньше", self.warnings(id="a", at_word=8, delay=-0.5)[0])

    def test_scene_lines_put_items_under_phrase(self):
        scene = {"items": [{"block": "bg", "always": True}, {"id": "a", "block": "label", "at_word": 8}]}
        lines = list(scene_lines(scene, self.data(), self.timing))
        self.assertIn("всегда", lines[0])
        self.assertIn("слово 8", lines[-1])


class Starter(Case):
    def test_create_base_keeps_existing(self):
        target = self.root / "bases" / "new"
        self.assertTrue(create_base(target))
        self.assertTrue((target / "tokens.json").is_file())
        (target / "style.md").write_text("мой стиль")
        self.assertFalse(create_base(target))
        self.assertEqual((target / "style.md").read_text(), "мой стиль")

    def test_starter_is_complete_and_has_no_sounds(self):
        tokens = json.loads((STARTER / "tokens.json").read_text())
        self.assertEqual({k: v for k, v in tokens["sounds"].items() if k != "level" and v}, {})
        for block in (STARTER / "blocks").iterdir():
            self.assertTrue((block / "block.jsxinc").is_file(), block.name)
            self.assertTrue((block / "block.json").is_file(), block.name)


class Moments(Case):
    def test_order_offsets_and_limit(self):
        scene = {"items": [{"always": True}, {"id": "a", "at_word": 1}, {"after": "a", "delay": 0.2}, {"at": 5}]}
        words = [{"start": 0}, {"start": 1.0}]
        times = moments(scene, words, 0.1, [0, 0.25], 100, 10)
        self.assertEqual(times[0], 0.9)
        self.assertIn(1.1, times)
        self.assertEqual(len(moments(scene, words, 0.1, [0, 0.25], 3, 10)), 3)
        self.assertEqual(max(moments(scene, words, 0.1, [0, 0.25], 100, 5.1)), 5.1)
        self.assertEqual(moments(scene, words, 0.1, [0], 100, 10, from_time=4), [5])


class OptionalPreflight(Case):
    def built(self):
        f = self.to_preflight()
        f.state["stages"]["preflight"]["status"] = "pending"
        f.save()
        return f

    def test_asks_once_before_stage(self):
        f = self.built()
        self.assertEqual(f.next_action()["action"], "ask_author_run")
        f.start("preflight")
        self.assertEqual(f.next_action()["action"], "resume")

    def test_skip_goes_to_polish(self):
        f = self.built()
        f.skip("preflight")
        self.assertTrue(f.state["stages"]["preflight"]["skipped"])
        self.assertEqual(f.next_action()["stage"], "polish")

    def test_author_stops_after_findings(self):
        f = self.to_preflight()
        self.report(1, Rounds.major)
        self.assertEqual(f.round()["decision"], "fix")
        self.write("F1.scene.json", self.scene(tail=2))
        f.finish("preflight")
        self.assertEqual(f.next_action()["stage"], "polish")

    def test_only_optional_stage(self):
        with self.assertRaises(PipelineError):
            self.built().skip("polish")


class Rounds(Case):
    major = {"rule": "color.contrast", "time": 1.0, "severity": "major"}
    other = {"rule": "layout.safe", "time": 2.0, "severity": "major"}
    third = {"rule": "type.size", "time": 3.0, "severity": "critical"}

    def test_clean_goes_to_author(self):
        f = self.to_preflight()
        self.report(1, {"rule": "x", "time": 0, "severity": "minor"})
        result = f.round()
        self.assertEqual((result["decision"], result["reason"]), ("author", "clean"))
        self.assertEqual(result["findings"][0]["rule"], "x")
        self.assertEqual(f.next_action()["stage"], "polish")

    def test_limit(self):
        f = self.to_preflight()
        for number, finding in enumerate([self.major, self.other], 1):
            self.report(number, finding)
            self.assertEqual(f.round()["decision"], "fix")
        self.report(3, self.third)
        self.assertEqual(f.round()["reason"], "limit")

    def test_limit_is_configurable(self):
        self.settings["review"]["max_rounds"] = 1
        f = self.to_preflight()
        self.report(1, self.major)
        self.assertEqual(f.round()["reason"], "limit")

    def test_repeated_findings_stop(self):
        f = self.to_preflight()
        self.report(1, self.major)
        f.round()
        self.report(2, self.major)
        self.assertEqual(f.round()["reason"], "repeated")

    def test_scene_fix_between_rounds_is_not_stale(self):
        f = self.to_preflight()
        self.report(1, self.major)
        f.round()
        self.write("F1.scene.json", self.scene(tail=2))
        self.report(2)
        self.assertEqual(f.round()["decision"], "author")
        self.assertEqual(f.next_action()["stage"], "polish")


if __name__ == "__main__":
    unittest.main()
