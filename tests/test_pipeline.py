import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "scripts"))

from approve_transcript import transcript_hash
from base_index import asset_lines, block_lines
from base_scan import scan
from brief import brief as brief_text, env_lines as brief_env_lines, roles as brief_roles, write_agents_md
from block_lab import LabError, create as create_lab, lab_scenes, part as lab_part, promote, set_values, variant
from capture import moments
from extend_timeline import merge as merge_part
from pipeline import Fragment, PipelineError
from plan_check import check as plan_check, coverage, expected_targets, target_issues
from settings import find_video, load_settings, merge, settings_for
from sound_measure import trailing_start
from sound_library import classify, hit_and_loudness, init as init_library, load_scheme, place_hits, propose
from view import beat_lines, layout_warnings, motion_warnings, state_lines, weight_warnings, scene_warnings, sound_log_path, sound_warnings, review_lines, scene_lines, text_lines, timing_warnings
from setup_base import STARTER, create_base
from voice import chunks, direction_words, eq_filter, keep_take, plain_text, split_directions, tag_errors, text_errors
from validate import ENGINE_VERSION, check_built, file_hash
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
        (self.bases / "b" / "base.json").write_text(json.dumps({"engine": ENGINE_VERSION}))
        self.settings = merge(load_settings(user_path=self.root / "none"), {"bases_dir": str(self.bases), "base": "b",
                                                                        "code_review": {"enabled": False}})
        self.write("F1.wav", "audio")

    def write(self, name, value):
        path = self.dir / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def write_scene(self, scene):
        path = self.write("F1.scene.json", scene)
        self.write("F1.build.json", {"scene_hash": file_hash(path)})
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
        self.write_scene(self.scene())
        f.done("build")
        f.start("preflight")
        return f

    def report(self, number, *findings):
        self.write(f"reviews/round_{number}.json", {"findings": list(findings)})


class Built(Case):
    def test_needs_stamp_of_this_scene(self):
        path = self.write("F1.scene.json", self.scene())
        self.assertIn("не собрана", check_built(path)[0])
        self.write_scene(self.scene())
        self.assertEqual(check_built(path), [])
        self.write("F1.scene.json", self.scene(tail=2))
        self.assertIn("изменилась", check_built(path)[0])

    def test_done_refuses_unbuilt_scene(self):
        f = self.to_preflight()
        f.state["stages"]["preflight"]["status"] = "pending"
        f.save()
        self.write("F1.scene.json", self.scene(tail=3))
        f.refresh()
        self.assertEqual(f.status("build"), "stale")
        with self.assertRaises(PipelineError):
            f.done("build")

    def test_edit_finishes_without_start(self):
        f = self.to_preflight()
        f.state["stages"]["preflight"]["status"] = "pending"
        f.save()
        self.write_scene(self.scene(tail=3))
        f.done("build")
        self.assertEqual(f.status("build"), "approved")


class Check(Case):
    def test_done_reports_warnings_of_all_checks(self):
        self.write("F1.timeline.json", timeline())
        tokens = {"motion": {"lead": 0.1}}
        (self.bases / "b" / "tokens.json").write_text(json.dumps(tokens))
        path = self.write_scene(self.scene(items=[{"id": "a", "block": "label", "at_word": 1, "delay": 0.5}]))
        self.write("F1.sounds.json", {"sounds": [
            {"item": "a", "sound": "pop", "start": 1, "end": 1.1, "animation_start": 1, "animation_end": 2}]})
        self.write("F1.layout.json", {"width": 1920, "height": 1080, "boxes": [
            {"item": "a", "layer": "a", "text": True, "time": 1, "until": 2, "opacity": 100,
             "left": 0, "top": 200, "right": 300, "bottom": 300}]})
        warnings = scene_warnings(path, self.settings)
        self.assertEqual(len(warnings), 3)


class CodeReview(Case):
    def to_check(self):
        self.settings["code_review"]["enabled"] = True
        f = self.to_preflight_ready()
        self.assertEqual(f.next_action()["stage"], "check")
        f.start("check")
        return f

    def to_preflight_ready(self):
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
        self.write_scene(self.scene())
        f.done("build")
        return f

    def check_report(self, *findings):
        self.write("reviews/F1.check.json", {"summary": "s", "findings": list(findings)})

    def test_disabled_is_skipped_by_itself(self):
        f = self.to_preflight_ready()
        self.assertEqual(f.next_action()["stage"], "preflight")
        self.assertTrue(f.state["stages"]["check"].get("skipped"))

    def test_short_fragment_is_skipped(self):
        self.settings["code_review"].update(enabled=True, min_seconds=600)
        f = self.to_preflight_ready()
        self.assertEqual(f.next_action()["stage"], "preflight")

    def test_clean_report_closes_stage(self):
        f = self.to_check()
        self.check_report({"beat": "b1", "severity": "minor", "what": "w", "fix": "x"})
        self.assertEqual(f.done("check")["decision"], "clean")
        self.assertEqual(f.next_action()["stage"], "preflight")

    def test_fix_then_finish_without_second_review(self):
        f = self.to_check()
        finding = {"item": "a", "severity": "critical", "what": "w", "fix": "x"}
        self.check_report(finding)
        self.assertEqual(f.done("check"), {"decision": "fix", "findings": [finding]})
        self.assertEqual(f.status("check"), "running")
        self.write("F1.scene.json", self.scene(tail=2))
        with self.assertRaises(PipelineError):
            f.finish("check")
        self.write_scene(self.scene(tail=2))
        f.finish("check")
        self.assertEqual((f.status("build"), f.status("check")), ("approved", "approved"))
        self.assertEqual(f.next_action()["stage"], "preflight")

    def test_bad_report_is_refused(self):
        f = self.to_check()
        self.check_report({"severity": "big", "what": "w"})
        with self.assertRaises(PipelineError):
            f.done("check")

    def test_beats_dossier_puts_plan_text_and_items_together(self):
        self.write("F1.timeline.json", timeline())
        self.write("F1.animation.json", {"beats": [{"id": "s", "at_word": 0, "until_word": 1, "intent": "старт", "blocks": ["label"]}]})
        scene = self.scene(items=[{"id": "a", "beat": "s", "block": "label", "at_word": 0, "params": {"text": "hi"}},
                                  {"id": "bg", "beat": "global", "block": "label", "always": True}])
        plan = json.loads((self.dir / "F1.animation.json").read_text())
        out = "\n".join(beat_lines(scene, plan, timeline()))
        self.assertIn("== s слова 0-1 | блоки: label", out)
        self.assertIn("замысел: старт", out)
        self.assertIn('{"text":"hi"}', out)
        self.assertIn("== общие и без бита", out)


class SceneViews(Case):
    def test_object_in_several_beats_counts_for_each(self):
        plan = {"beats": [{"id": "s", "blocks": ["table"]}, {"id": "i", "blocks": ["table"]}]}
        scene = {"items": [{"id": "t", "block": "table", "beat": ["s", "i"]}]}
        self.assertEqual(coverage(scene, plan)[0], [])
        scene["items"][0]["beat"] = ["s", "x"]
        self.assertTrue(any("x" in e for e in coverage(scene, plan)[0]))

    def test_state_shows_objects_alive_at_word_and_bottom_band(self):
        scene = {"items": [{"id": "a", "block": "label", "at_word": 0, "until_word": 1},
                           {"id": "b", "block": "label", "at_word": 2},
                           {"id": "bg", "block": "background_dark", "always": True}]}
        layout = {"width": 1920, "height": 1080, "boxes": [
            {"item": "b", "layer": "x", "time": 1.0, "until": 9, "opacity": 100, "left": 10, "top": 900, "right": 50, "bottom": 950}]}
        out = "\n".join(state_lines(scene, timeline(), layout, 3))
        self.assertNotIn("  a label", out)
        self.assertIn("b label [2–конца] рамка 10,900–50,950", out)
        self.assertIn("нижняя полоса: занята (b)", out)

    def test_motion_glitches_from_tracks(self):
        scene = {"comp": {"fps": 30}, "items": [{"id": "a", "block": "label", "at_word": 2, "until_word": 2}]}
        f = 1 / 30
        samples = [[0.0, 100, 900, 500, 1000, 550], [0.5, 100, 900, 500, 1000, 550], [0.5 + f, 100, 1300, 500, 1400, 550],
                   [1.0, 100, 1300, 500, 1400, 550], [1.0 + f, 10, 1300, 500, 1400, 550], [4.0, 30, 1300, 500, 1400, 550]]
        layout = {"width": 1920, "height": 1080, "boxes": [],
                  "tracks": [{"item": "a", "layer": "t", "text": True, "in": 0, "out": 5, "samples": samples}]}
        out = "\n".join(motion_warnings(layout, scene, timeline(), self.settings))
        for word in ("раньше", "висит", "скачок", "прыгает", "застрял"):
            self.assertIn(word, out)
        layout["tracks"][0]["samples"] = [[1.0, 100, 900, 500, 1000, 550], [1.3, 100, 900, 500, 1000, 550]]
        self.assertEqual(motion_warnings(layout, scene, timeline(), self.settings), [])

    def test_heavy_composition_warns(self):
        boxes = [{"item": f"i{n}", "layer": "l"} for n in range(100)]
        data = {"words": [{"end": 60}]}
        self.assertEqual(weight_warnings({"boxes": boxes[:50]}, data, {"max_layers_per_minute": 80}), [])
        self.assertTrue(weight_warnings({"boxes": boxes}, data, {"max_layers_per_minute": 80}))


class Settings(Case):
    def test_merge_keeps_neighbours(self):
        self.assertEqual(merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}), {"a": {"x": 1, "y": 3}})

    def test_settings_of_file_use_video_layer(self):
        (self.video / "settings.json").write_text('{"transcribe": {"language": "en"}}')
        self.assertEqual(settings_for(self.dir / "F1.wav")["transcribe"]["language"], "en")

    def test_video_found_from_file(self):
        self.assertEqual(find_video(self.dir / "F1.wav"), self.video.resolve())
        self.assertIsNone(find_video(self.root / "other.wav"))

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

    def test_scene_old_engine(self):
        self.write("F1.timeline.json", timeline())
        (self.bases / "b" / "base.json").write_text(json.dumps({"engine": 1}))
        errors = check_scene(self.write("s.json", self.scene()), self.settings)
        self.assertIn("движок", errors[0])

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
        self.write_scene(scene)
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

    def test_asks_and_skip_runs_nothing(self):
        f = self.finish_polish()
        self.assertEqual(f.next_action()["action"], "ask_author_run")
        f.skip("curate")
        self.assertEqual(f.next_action()["action"], "finished")
        self.assertFalse((self.dir / "F1.base_proposals.md").exists())

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
        self.write_scene(self.scene(items=items))
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
        self.write_scene(self.scene(items=[{"id": "x", "block": "label", "at_word": 1}]))
        with self.assertRaises(PipelineError):
            f.done("build")
        new = {"id": "b", "block": "label", "at_word": 4, "params": {"target": "a"}}
        self.write_scene(self.scene(items=old + [new]))
        with self.assertRaises(PipelineError):
            f.done("build")
        new["params"] = {}
        self.write_scene(self.scene(items=old + [new]))
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
        self.write_scene(scene)
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


class Lab(Case):
    def setUp(self):
        super().setUp()
        self.settings = merge(self.settings, {"labs_dir": str(self.root / "labs")})

    def test_new_lab_is_a_valid_scene_outside_the_base(self):
        folder = create_lab(self.settings, "pop")
        self.assertEqual(check_scene(folder / "lab.scene.json", self.settings), [])
        self.assertFalse((self.bases / "b" / "blocks" / "pop").exists())
        with self.assertRaises(LabError):
            create_lab(self.settings, "label")

    def test_from_base_copies_the_block(self):
        folder = create_lab(self.settings, "label", from_base=True)
        self.assertTrue((folder / "blocks" / "label" / "block.jsxinc").is_file())

    def test_set_changes_only_the_block_item(self):
        folder = create_lab(self.settings, "pop")
        set_values(self.settings, "pop", ["params.size=120", "tokens.motion.lead=0.2", "params.text=Да"])
        item = json.loads((folder / "lab.scene.json").read_text())["items"][-1]
        self.assertEqual(item["params"], {"size": 120, "text": "Да"})
        self.assertEqual(item["tokens"], {"motion": {"lead": 0.2}})
        with self.assertRaises(LabError):
            set_values(self.settings, "pop", ["size=1"])

    def test_parts_build_instead_of_the_big_scene(self):
        folder = create_lab(self.settings, "pop")
        target = lab_part(self.settings, "pop", "styles", seconds=3)
        scene = json.loads((target / "lab.scene.json").read_text())
        self.assertEqual(scene["comp"]["name"], "lab_pop_styles")
        self.assertTrue((target / "blocks").is_symlink())
        self.assertEqual(check_scene(target / "lab.scene.json", self.settings), [])
        lab_part(self.settings, "pop", "speed")
        self.assertEqual([s.parent.name for s in lab_scenes(folder)], ["speed", "styles"])
        self.assertEqual([s.parent.name for s in lab_scenes(folder, ("styles",))], ["styles"])
        with self.assertRaises(LabError):
            lab_scenes(folder, ("nope",))

    def test_variant_is_a_scene_with_its_own_comp(self):
        folder = create_lab(self.settings, "pop")
        set_values(self.settings, "pop", ["params.size=120"])
        path, _ = variant(self.settings, "pop", "big", ["params.size=200"])
        scene = json.loads(path.read_text())
        self.assertEqual(scene["items"][-1]["params"], {"size": 200})
        self.assertEqual(scene["comp"]["name"], "lab_pop_big")
        self.assertEqual(check_scene(path, self.settings), [])
        self.assertEqual(json.loads((folder / "lab.scene.json").read_text())["items"][-1]["params"], {"size": 120})
        self.assertEqual([p.name for p in lab_scenes(folder)], ["lab.scene.json", "variant_big.scene.json"])
        with self.assertRaises(LabError):
            variant(self.settings, "pop", "../x", [])

    def test_promote_needs_description_and_consent_to_replace(self):
        folder = create_lab(self.settings, "pop")
        with self.assertRaises(LabError):
            promote(self.settings, "pop")
        meta = folder / "blocks" / "pop" / "block.json"
        meta.write_text(json.dumps({"id": "pop", "description": "d", "when": "w", "params": {"size": "s"}}))
        target = promote(self.settings, "pop")
        self.assertTrue((target / "block.jsxinc").is_file())
        with self.assertRaises(LabError):
            promote(self.settings, "pop")
        self.assertTrue(promote(self.settings, "pop", replace=True).is_dir())

    def test_promote_another_block_of_the_lab(self):
        folder = create_lab(self.settings, "pop")
        extra = folder / "blocks" / "wave"
        extra.mkdir()
        (extra / "block.jsxinc").write_text("")
        (extra / "block.json").write_text(json.dumps({"id": "wave", "description": "d", "when": "w", "params": {"a": "b"}}))
        self.assertTrue((promote(self.settings, "wave", lab="pop") / "block.json").is_file())


class Sounds(Case):
    timing = {"sound_max_shift": 0.03, "sound_min_gap": 0.3}

    def sound(self, item, start, end, animation_start, animation_end):
        return {"item": item, "sound": "pop", "start": start, "end": end,
                "animation_start": animation_start, "animation_end": animation_end}

    def test_centred_sound_passes(self):
        self.assertEqual(sound_warnings([self.sound("a", 1.4, 1.6, 1.0, 2.0)], self.timing), [])

    def test_off_centre(self):
        self.assertIn("раньше", sound_warnings([self.sound("a", 1.0, 1.2, 1.0, 2.0)], self.timing)[0])
        self.assertIn("позже", sound_warnings([self.sound("a", 1.8, 2.0, 1.0, 2.0)], self.timing)[0])

    def test_clamped_at_video_start_passes(self):
        self.assertEqual(sound_warnings([self.sound("a", 0, 0.4, 0, 0.1)], self.timing), [])

    def test_sounds_in_a_row(self):
        sounds = [self.sound("a", 1.0, 1.2, 1.0, 1.2), self.sound("b", 1.1, 1.3, 1.1, 1.3)]
        self.assertIn("подряд", sound_warnings(sounds, self.timing)[0])

    def test_trailing_silence(self):
        log = "silence_start: 0.24\nsilence_end: 0.43"
        self.assertEqual(trailing_start(log, 0.432), 0.24)
        self.assertEqual(trailing_start("silence_start: 0.1\nsilence_end: 0.2", 1.0), 1.0)

    def test_log_path(self):
        self.assertEqual(sound_log_path(Path("F1.scene.json")).name, "F1.sounds.json")


class SoundRoles(Case):
    timing = {"sound_max_shift": 0.03, "sound_min_gap": 0.3, "sound_series_gap": 0.08}

    def sound(self, item, hit, role="single", series=None, anchor=None):
        return {"item": item, "sound": "tick", "start": hit - 0.01, "end": hit + 0.09, "hit": hit,
                "anchor": hit if anchor is None else anchor, "role": role, "series": series,
                "animation_start": 1.0, "animation_end": 2.0}

    def test_series_uses_its_own_gap(self):
        sounds = [self.sound("g", t, "series", "g/0") for t in (1.0, 1.1, 1.2)]
        self.assertEqual(sound_warnings(sounds, self.timing), [])

    def test_series_too_dense(self):
        sounds = [self.sound("g", t, "series", "g/0") for t in (1.0, 1.05)]
        self.assertIn("серии", sound_warnings(sounds, self.timing)[0])

    def test_other_event_next_to_series(self):
        sounds = [self.sound("g", 1.0, "series", "g/0"), self.sound("g", 1.1, "series", "g/0"), self.sound("b", 1.2)]
        self.assertIn("подряд", sound_warnings(sounds, self.timing)[0])

    def test_bed_is_not_checked(self):
        bed = {"item": "g", "sound": "bed", "start": 1.0, "end": 2.0, "hit": None, "anchor": None,
               "role": "bed", "series": None, "animation_start": 1.0, "animation_end": 2.0}
        self.assertEqual(sound_warnings([bed, self.sound("g", 1.5)], self.timing), [])

    def test_anchor_shift(self):
        self.assertIn("якоря", sound_warnings([self.sound("a", 1.5, anchor=1.4)], self.timing)[0])


class Moments(Case):
    def test_order_offsets_and_limit(self):
        starts = [0.9, 1.1, 5]
        times = moments(starts, [0, 0.25], 100, 10)
        self.assertEqual(times[0], 0.9)
        self.assertIn(1.15, times)
        self.assertEqual(len(moments(starts, [0, 0.25], 3, 10)), 3)
        self.assertEqual(max(moments(starts, [0, 0.25], 100, 5.1)), 5.1)
        self.assertEqual(moments(starts, [0], 100, 10, from_time=4), [5])


class Layout(Case):
    cfg = {"safe_margin": 96, "min_overlap": 8, "full_frame": 0.9}

    def box(self, item, left, top, right, bottom, text=True, time=1, until=5, opacity=100):
        return {"item": item, "layer": item, "text": text, "time": time, "until": until,
                "opacity": opacity, "left": left, "top": top, "right": right, "bottom": bottom}

    def warnings(self, *boxes):
        return layout_warnings({"width": 1920, "height": 1080, "boxes": list(boxes)}, self.cfg)

    def test_good_layout_passes(self):
        self.assertEqual(self.warnings(self.box("a", 200, 200, 500, 300), self.box("b", 600, 200, 900, 300)), [])

    def test_outside_safe_field(self):
        self.assertIn("безопасного поля", self.warnings(self.box("a", 20, 200, 500, 300))[0])

    def test_full_frame_layer_ignored(self):
        self.assertEqual(self.warnings(self.box("bg", 0, 0, 1920, 1080, text=False)), [])

    def test_text_overlap(self):
        result = self.warnings(self.box("a", 200, 200, 500, 300), self.box("b", 400, 250, 700, 350))
        self.assertIn("перекрывает", result[0])

    def test_layers_of_same_item_and_other_time_do_not_clash(self):
        same = [self.box("a", 200, 200, 500, 300), self.box("a", 200, 200, 500, 300)]
        self.assertEqual(self.warnings(*same), [])
        later = self.box("b", 200, 200, 500, 300, time=9, until=12)
        self.assertEqual(self.warnings(self.box("a", 200, 200, 500, 300), later), [])

    def test_shape_under_text_is_fine(self):
        boxes = [self.box("a", 200, 200, 500, 300), self.box("b", 200, 200, 500, 300, text=False)]
        self.assertEqual(self.warnings(*boxes), [])

    def test_hidden_layer_ignored(self):
        self.assertEqual(self.warnings(self.box("a", 20, 200, 500, 300, opacity=0)), [])


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
        self.write_scene(self.scene(tail=2))
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
        self.write_scene(self.scene(tail=2))
        self.report(2)
        self.assertEqual(f.round()["decision"], "author")
        self.assertEqual(f.next_action()["stage"], "polish")


class SoundLibraryTest(unittest.TestCase):
    def setUp(self):
        self.scheme = load_scheme()

    def test_init_makes_folders_and_keeps_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_library(root, self.scheme)
            hint = root / "01_Появления" / "серия" / "ЧТО_СЮДА.txt"
            self.assertTrue(hint.is_file())
            self.assertTrue((root / "01_Появления" / "одиночный" / "1.00–2.00с").is_dir())
            hint.write_text("свой текст", encoding="utf-8")
            init_library(root, self.scheme)
            self.assertEqual(hint.read_text(encoding="utf-8"), "свой текст")

    def test_classify_by_path(self):
        place = classify(Path("02_Выделения/заливка/tick.wav"), self.scheme)
        self.assertEqual((place["event"], place["role"]), ("scan", "single"))
        old = classify(Path("01_Появления/универсальный/до_0.15с/pop.mp3"), self.scheme)
        self.assertEqual((old["event"], old["role"], old["character"]), ("appear", "single", "универсальный"))
        self.assertIsNone(classify(Path("Прочее/a.wav"), self.scheme))

    def test_series_hits_thinned(self):
        self.assertEqual(place_hits([0, 0.05, 0.1, 0.3], 0.08, 10), [0, 0.1, 0.3])
        self.assertEqual(len(place_hits([i * 0.1 for i in range(20)], 0.08, 10)), 10)

    def test_hit_and_loudness(self):
        samples = [0.0] * 100 + [0.9] + [0.1] * 100
        hit, loud = hit_and_loudness(samples, 1.0)
        self.assertAlmostEqual(hit, 100 / 22050, places=3)
        self.assertLess(loud, 0)

    def test_propose_by_roles(self):
        files = [
            {"id": "long", "event": "appear", "role": "single", "audible": 0.6},
            {"id": "short", "event": "appear", "role": "single", "audible": 0.1},
            {"id": "tap", "event": "appear", "role": "series", "audible": 0.05},
            {"id": "air", "event": "appear", "role": "bed", "audible": 2.0},
        ]
        sounds = propose(self.scheme, files)
        appear = sounds["events"]["appear"]
        self.assertEqual(appear["tiers"], ["short", "long"])
        self.assertEqual(appear["series"]["sounds"], ["tap"])
        self.assertEqual(appear["bed"]["sound"], "air")
        self.assertEqual(sounds["fallback"]["group"], "appear")
        self.assertNotIn("move", sounds["events"])
        self.assertIsNone(sounds["events"]["dim"])


class PlanCheck(Case):
    """Сцена сверяется с планом: утверждённые блоки не подменяются, target стоит раньше."""

    def setUp(self):
        super().setUp()
        for name in ("fill_cells", "array_cells", "boxes"):
            folder = self.bases / "b" / "blocks" / name
            folder.mkdir(parents=True)
            (folder / "block.jsxinc").write_text("")
        (self.bases / "b" / "blocks" / "fill_cells" / "block.json").write_text(
            json.dumps({"id": "fill_cells", "params": {"target": "id блока array_cells"}}))
        self.write("F1.timeline.json", timeline())
        self.write("F1.animation.json", {"transcript_hash": timeline()["transcript_hash"], "beats": [
            {"id": "intro", "at_word": 0, "until_word": 1, "block": "array_cells"},
            {"id": "scan", "at_word": 2, "until_word": 3, "blocks": ["fill_cells"]}]})

    def items(self, *extra):
        return [{"id": "cells", "block": "array_cells", "at_word": 0, "beat": "intro"}] + list(extra)

    def test_old_scene_without_beats_only_warns(self):
        path = self.write("F1.scene.json", self.scene())
        self.assertEqual(check_scene(path, self.settings), [])
        errors, warnings = plan_check(path, self.bases / "b")
        self.assertEqual(errors, [])
        self.assertIn("сверка с планом пропущена", warnings[0])

    def test_substituted_block_is_error(self):
        path = self.write("F1.scene.json", self.scene(items=self.items(
            {"id": "mark", "block": "boxes", "at_word": 2, "beat": "scan"})))
        errors = check_scene(path, self.settings)
        self.assertTrue(any("нет утверждённого блока fill_cells" in e for e in errors))
        self.assertTrue(any("boxes не указаны в плане" in w for w in plan_check(path, self.bases / "b")[1]))

    def test_matching_scene_passes(self):
        path = self.write("F1.scene.json", self.scene(items=self.items(
            {"id": "fill", "block": "fill_cells", "at_word": 2, "beat": "scan", "params": {"target": "cells"}},
            {"id": "bg", "block": "label", "always": True, "beat": "global"})))
        self.assertEqual(check_scene(path, self.settings), [])
        self.assertEqual(plan_check(path, self.bases / "b"), ([], []))

    def test_global_items_count_for_every_beat(self):
        self.write("F1.animation.json", {"transcript_hash": timeline()["transcript_hash"], "beats": [
            {"id": "intro", "at_word": 0, "until_word": 1, "blocks": ["array_cells", "label"]}]})
        path = self.write("F1.scene.json", self.scene(items=[
            {"id": "bg", "block": "label", "always": True, "beat": "global"},
            {"id": "cells", "block": "array_cells", "at_word": 0, "beat": "intro"}]))
        self.assertEqual(plan_check(path, self.bases / "b"), ([], []))

    def test_missing_helper_block_only_warns(self):
        self.write("F1.animation.json", {"transcript_hash": timeline()["transcript_hash"], "beats": [
            {"id": "intro", "at_word": 0, "until_word": 1, "blocks": ["array_cells", "label"]}]})
        path = self.write("F1.scene.json", self.scene(items=self.items()))
        self.assertTrue(any("нет утверждённого блока label" in e for e in check_scene(
            path, merge(self.settings, {"plan_check": {"helper_blocks": []}}))))
        self.assertEqual(check_scene(path, self.settings), [])
        errors, warnings = plan_check(path, self.bases / "b", ["label"])
        self.assertEqual(errors, [])
        self.assertIn("вспомогательные блоки label", warnings[0])

    def test_unknown_beat_is_error(self):
        path = self.write("F1.scene.json", self.scene(items=self.items(
            {"id": "fill", "block": "fill_cells", "at_word": 2, "beat": "nope"})))
        self.assertTrue(any("бит nope не найден" in e for e in check_scene(path, self.settings)))

    def test_target_must_be_built_earlier(self):
        items = [{"id": "fill", "block": "fill_cells", "at_word": 2, "params": {"target": "cells"}},
                 {"id": "cells", "block": "array_cells", "at_word": 0}]
        path = self.write("F1.scene.json", self.scene(items=items))
        self.assertTrue(any("позже" in e for e in check_scene(path, self.settings)))
        items[0]["params"]["target"] = "ghost"
        path = self.write("F1.scene.json", self.scene(items=items))
        self.assertTrue(any("такого элемента в сцене нет" in e for e in check_scene(path, self.settings)))

    def test_target_of_wrong_block_warns(self):
        items = [{"id": "row", "block": "boxes", "at_word": 0},
                 {"id": "fill", "block": "fill_cells", "at_word": 2, "params": {"target": "row"}}]
        path = self.write("F1.scene.json", self.scene(items=items))
        self.assertEqual(target_issues({"items": items}, path.parent, self.bases / "b")[1],
                         ["fill: блок fill_cells ждёт target блока array_cells, а row — boxes"])

    def test_expected_targets_parse(self):
        self.assertEqual(expected_targets("id блока array_cells"), ["array_cells"])
        self.assertEqual(expected_targets("id блока plane, тогда точка в его единицах"), ["plane"])
        self.assertEqual(expected_targets("id блока array_cells, bar_chart или другого с ячейками"), [])
        self.assertEqual(expected_targets("id элемента"), [])

    def test_plan_ids_unique_and_old_plan_valid(self):
        plan = self.write("F1.animation.json", {"transcript_hash": timeline()["transcript_hash"], "beats": [
            {"id": "x", "at_word": 0}, {"id": "x", "at_word": 1}]})
        self.assertTrue(any("повторяется" in e for e in check_animation(plan, self.dir / "F1.timeline.json")))
        plan = self.write("F1.animation.json", {"transcript_hash": timeline()["transcript_hash"], "beats": [
            {"at_word": 0, "block": "label"}]})
        self.assertEqual(check_animation(plan, self.dir / "F1.timeline.json"), [])


class Brief(Case):
    """Каждая роль получает полный контекст без переменных Claude Code."""

    def test_every_role_resolves_paths(self):
        agents, skills = brief_roles()
        self.assertIn("animator", agents)
        for role in agents + skills:
            text = brief_text(role)
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text, role)
            self.assertIn("Жёсткие правила", text, role)

    def test_agent_gets_preloaded_skills(self):
        text = brief_text("animator")
        self.assertIn("# Роль: animator", text)
        self.assertIn("# Опора: навык montage-fundamentals", text)

    def test_task_only_skips_role(self):
        text = brief_text("animator", task="поправить звук", task_only=True)
        self.assertNotIn("# Роль", text)
        self.assertIn("поправить звук", text)

    def test_versioned_copy_explains_update(self):
        info = {"plugin": "/x/cache/ae-video/0.4.0", "plugin_version": "0.4.0", "engine": 3, "scripts": "s",
                "base": "/b", "base_found": True, "base_engine": 3, "compatible": True}
        self.assertTrue(any("самую новую версию в `/x/cache/ae-video`" in line for line in brief_env_lines(info)))
        info["plugin"] = "/repo/plugin"
        self.assertFalse(any("номером версии" in line for line in brief_env_lines(info)))

    def test_agents_md_keeps_author_text(self):
        path = self.video / "AGENTS.md"
        path.write_text("# Моё\n\n- 30 fps\n")
        write_agents_md(self.video, self.settings)
        first = path.read_text()
        self.assertTrue(first.startswith("# Моё"))
        self.assertIn("brief.py make", first)
        write_agents_md(self.video, self.settings)
        self.assertEqual(path.read_text(), first)
        self.assertIsNone(write_agents_md(self.video, merge(self.settings, {"agents_file": ""})))


class Voice(Case):
    """Голос из текста: теги, EQ, нарезка, дубли и место этапа в конвейере. Без сети."""

    def test_only_allowed_tags(self):
        allowed = ["slightly faster", "thoughtful", "short pause", "emphasized"]
        self.assertEqual(tag_errors("[slightly faster, thoughtful] Текст... дальше.", allowed), [])
        errors = tag_errors("Норм.\n[angry] Текст.\n[thoughtful Текст", allowed)
        self.assertEqual(errors, ["строка 2: тег «angry» не разрешён", "строка 3: незакрытая квадратная скобка"])
        self.assertEqual(plain_text("[short pause] Это  O(1)."), "Это O(1).")

    def test_formulas_must_be_spoken(self):
        cfg = load_settings(user_path=self.root / "none")["voice"]
        cfg = dict(cfg, spoken_forms={"O(1)": "О от единицы"})
        errors = text_errors("[thoughtful] Сложность O(1) и O(n).\nО от единицы.", cfg)
        self.assertEqual(len(errors), 2)
        self.assertIn("«О от единицы»", errors[0])
        self.assertEqual(text_errors("[thoughtful] Сложность О от единицы.", cfg), [])

    def test_directions_leave_speech_and_find_words(self):
        pattern = load_settings(user_path=self.root / "none")["voice"]["direction_pattern"]
        text = "Возьмём массив (массив из пяти ячеек появляется) и число.\nСложность O(1) (знак O(1) крупно).\n(тёмный фон) Начнём."
        spoken, notes = split_directions(text, pattern)
        self.assertEqual(spoken, "Возьмём массив и число.\nСложность O(1).\nНачнём.\n")
        self.assertEqual([n["note"] for n in notes], ["массив из пяти ячеек появляется", "знак O(1) крупно", "тёмный фон"])
        heard = [{"word": w} for w in "Возьмём массив и число. Сложность О от единицы. Начнём.".split()]
        found = [heard[n["timeline_word"]]["word"] for n in direction_words(notes, spoken, heard)]
        self.assertEqual(found, ["массив", "Сложность", "Начнём."])

    def test_eq_filter(self):
        self.assertEqual(eq_filter([{"freq": 2434, "gain": -6.2, "q": 1.4}, {"freq": 3888, "gain": -9.0, "q": 0.9}]),
                         "equalizer=f=2434:t=q:w=1.4:g=-6.2,equalizer=f=3888:t=q:w=0.9:g=-9.0")

    def test_chunks_respect_limit_and_keep_text(self):
        text = "Первая строка.\n\nВторая строка. Ещё предложение. И ещё одно.\nТретья."
        pieces = chunks(text, 30)
        self.assertTrue(all(len(p) <= 30 for p in pieces))
        self.assertEqual(" ".join(" ".join(pieces).split()), " ".join(text.split()))

    def test_previous_take_is_kept(self):
        out = self.write("F1.wav", "first")
        self.assertEqual(keep_take(out).name, "F1.take1.wav")
        self.write("F1.wav", "second")
        self.assertEqual(keep_take(out).name, "F1.take2.wav")
        self.assertIsNone(keep_take(out))

    def test_init_from_text_starts_with_voice(self):
        (self.dir / "F1.wav").unlink()
        f = Fragment(self.video, "F1", settings=self.settings)
        f.create(from_text=True)
        self.assertEqual(f.state["audio"], "F1.wav")
        self.assertEqual(f.next_action()["stage"], "voice")
        f.start("voice")
        self.write("F1.voice.txt", "[angry] Текст.")
        self.write("F1.wav", "audio")
        with self.assertRaises(PipelineError):
            f.done("voice")
        self.write("F1.voice.txt", "[thoughtful] Текст.")
        f.done("voice")
        f.approve("voice")
        self.assertEqual(f.next_action()["stage"], "transcript")

    def test_recorded_audio_skips_voice(self):
        f = self.fragment()
        self.assertTrue(f.state["stages"]["voice"]["skipped"])
        self.assertEqual(f.next_action()["stage"], "transcript")

    def test_audio_from_moves_file(self):
        (self.dir / "F1.wav").unlink()
        source = self.root / "take.WAV"
        source.write_text("audio")
        f = Fragment(self.video, "F1", settings=self.settings)
        f.create(audio_from=source)
        self.assertFalse(source.exists())
        self.assertTrue((self.dir / "F1.wav").is_file())
        self.assertEqual(f.next_action()["stage"], "transcript")

    def test_old_state_without_voice_skips_it(self):
        f = self.fragment()
        del f.state["stages"]["voice"]
        f.save()
        f = Fragment(self.video, "F1", settings=self.settings)
        self.assertEqual(f.status("voice"), "approved")
        self.assertEqual(f.next_action()["stage"], "transcript")


if __name__ == "__main__":
    unittest.main()
