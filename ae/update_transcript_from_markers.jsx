// Writes reviewed layer markers back to <audio>.timeline.json.
// Run: File > Scripts > Run Script File...
// The previous transcript is kept as <audio>.timeline.backup.json.

#include "lib/timeline.jsxinc"

(function () {
    var SCRIPT_NAME = "Update Transcript From Markers";

    function round3(value) {
        return Math.round(value * 1000) / 1000;
    }

    function collectMarkers(layer) {
        var markers = layer.property("ADBE Marker");
        var items = [];
        for (var k = 1; k <= markers.numKeys; k++) {
            var marker = markers.keyValue(k);
            var start = round3(markers.keyTime(k) - layer.startTime);
            if (!marker.comment) {
                throw new Error("Marker at " + start + " s has no text.");
            }
            var i = marker.getParameters().i;
            items.push({
                i: i === undefined ? null : parseInt(i, 10),
                word: marker.comment,
                start: start,
                end: round3(start + marker.duration),
                label: marker.label
            });
        }
        return items;
    }

    function phraseByWord(phrases) {
        var result = {};
        for (var p = 0; p < phrases.length; p++) {
            for (var w = phrases[p].first_word; w <= phrases[p].last_word; w++) {
                result[w] = p;
            }
        }
        return result;
    }

    function rebuildPhrases(words, wordPhrases) {
        var phrases = [];
        var current = null;
        var parts = [];
        var texts = [];
        for (var n = 0; n < words.length; n++) {
            if (n === 0 || wordPhrases[n] !== wordPhrases[n - 1]) {
                current = { i: phrases.length, text: "", start: words[n].start, end: 0, first_word: n, last_word: n };
                phrases.push(current);
                parts = [];
            }
            parts.push(words[n].word);
            current.text = parts.join(" ");
            current.end = words[n].end;
            current.last_word = n;
        }
        for (var p = 0; p < phrases.length; p++) {
            texts.push(phrases[p].text);
        }
        return { phrases: phrases, text: texts.join(" ") };
    }

    function setFrames(word, fps) {
        if (fps) {
            word.start_frame = Math.round(word.start * fps);
            word.end_frame = Math.round(word.end * fps);
        }
    }

    // A dragged marker keeps its duration, so its end can run past the next word.
    function trimOverlaps(words, fps) {
        for (var n = 0; n < words.length - 1; n++) {
            if (words[n].end > words[n + 1].start) {
                words[n].end = words[n + 1].start;
                setFrames(words[n], fps);
            }
        }
    }

    function applyMarkers(data, items, frameDuration) {
        // Markers may sit on the frame grid, so a difference up to half a frame is not an edit.
        var tolerance = frameDuration / 2 + 0.001;
        var fps = data.fps;
        var oldWords = {};
        var phraseOf = phraseByWord(data.phrases);
        var words = [];
        var wordPhrases = [];
        var stats = { changed: 0, added: 0, removed: data.words.length };
        var phrase = 0;
        var n;

        for (n = 0; n < data.words.length; n++) {
            oldWords[data.words[n].i] = data.words[n];
        }

        for (n = 0; n < items.length; n++) {
            var item = items[n];
            var old = item.i === null ? null : oldWords[item.i];
            var word;
            if (old) {
                delete oldWords[item.i];
                stats.removed--;
                phrase = Math.max(phrase, phraseOf[old.i]);
                var moved = Math.abs(old.start - item.start) > tolerance ||
                    Math.abs(old.end - item.end) > tolerance;
                var changed = moved || old.word !== item.word;
                word = old;
                word.word = item.word;
                if (moved) {
                    word.start = item.start;
                    word.end = item.end;
                    setFrames(word, fps);
                }
                if (changed) {
                    word.manual = true;
                    stats.changed++;
                }
                word.needs_review = !changed && item.label === Timeline.REVIEW_LABEL;
            } else {
                word = {
                    word: item.word,
                    start: item.start,
                    end: item.end,
                    confidence: null,
                    needs_review: false,
                    manual: true
                };
                setFrames(word, fps);
                stats.added++;
            }
            word.i = n;
            words.push(word);
            wordPhrases.push(phrase);
        }

        trimOverlaps(words, fps);

        var rebuilt = rebuildPhrases(words, wordPhrases);
        data.words = words;
        data.phrases = rebuilt.phrases;
        data.text = rebuilt.text;
        return stats;
    }

    function hasWordNumbers(items) {
        for (var n = 0; n < items.length; n++) {
            if (items[n].i !== null) {
                return true;
            }
        }
        return false;
    }

    function main() {
        var comp = app.project.activeItem;
        if (!(comp instanceof CompItem)) {
            alert("Open a composition with an audio layer.", SCRIPT_NAME);
            return;
        }

        var layer = Timeline.findAudioLayer(comp);
        if (!layer) {
            alert("Select one audio layer: the target layer is ambiguous.", SCRIPT_NAME);
            return;
        }

        var items = collectMarkers(layer);
        if (!hasWordNumbers(items)) {
            alert("These markers have no word numbers. Run add_word_markers.jsx first, then review.", SCRIPT_NAME);
            return;
        }

        var file = Timeline.findTimelineFile(layer);
        if (!file) {
            return;
        }

        var data = Timeline.readJson(file);
        if (data.status === "approved") {
            alert("This transcript is approved. Reopen it first: approve_transcript.py --reopen", SCRIPT_NAME);
            return;
        }
        var stats = applyMarkers(data, items, comp.frameDuration);

        Timeline.writeJson(file, data, file.fsName.replace(/\.json$/, ".backup.json"));

        alert("Done. Words: " + data.words.length + ", changed: " + stats.changed +
            ", added: " + stats.added + ", removed: " + stats.removed, SCRIPT_NAME);
    }

    try {
        main();
    } catch (e) {
        alert("Error: " + e.toString() + " (line " + e.line + ")", SCRIPT_NAME);
    }
})();
