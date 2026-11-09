// Builds a composition from a scene JSON using the blocks of its base.
// Run: File > Scripts > Run Script File... and select a scene file.
// Automation sets $.global.SCENE_PATH and BASES_DIR and gets errors thrown instead of alerts.

#include "../lib/timeline.jsxinc"

(function () {
    var SCRIPT_NAME = "Build Scene";
    var OLD_BUILDS = "old builds";
    // Marks a composition made by this script: "<BUILD_MARK> <comp name> <build time> <signature>".
    var BUILD_MARK = "ae-video build";
    var EDITED = "edited";
    var folder = new File($.fileName).parent.fsName;
    var quiet = !!$.global.SCENE_PATH;

    // A block written for this video lives next to the scene and wins over the base.
    function loadBlock(id) {
        if (!Engine.blocks[id]) {
            var file = new File(Engine.sceneFolder + "/blocks/" + id + "/block.jsxinc");
            if (!file.exists) {
                file = new File(Engine.base + "/blocks/" + id + "/block.jsxinc");
            }
            if (!file.exists) {
                throw new Error("Unknown block: " + id);
            }
            $.evalFile(file);
        }
        return Engine.blocks[id];
    }

    // Paths may be relative to the scene, so a video folder can move between machines.
    function resolvePath(value, folder) {
        var first = value.charAt(0);
        return first === "/" || first === "~" ? value : folder + "/" + value;
    }

    // A base is a path or a name inside the bases folder from the plugin settings.
    function resolveBase(value, folder) {
        if (value.indexOf("/") === -1 && $.global.BASES_DIR) {
            return $.global.BASES_DIR + "/" + value;
        }
        return resolvePath(value, folder);
    }

    function prepare(scenePath) {
        var scene = Timeline.readJson(new File(scenePath));
        Engine.sceneFolder = new File(scenePath).parent.fsName;
        scene.base = resolveBase(scene.base, Engine.sceneFolder);
        scene.audio = resolvePath(scene.audio, Engine.sceneFolder);
        scene.timeline = resolvePath(scene.timeline, Engine.sceneFolder);
        Engine.base = scene.base;
        Engine.tokens = Timeline.readJson(new File(scene.base + "/tokens.json"));
        Engine.tokens = Engine.overlay(Engine.tokens, scene.tokens || {});
        Engine.assets = Timeline.readJson(new File(scene.base + "/assets/assets.json"));
        Engine.timeline = Timeline.readJson(new File(scene.timeline));
        Engine.store = {};
        Engine.starts = {};
        Engine.footage = {};
        Engine.soundLog = [];
        Engine.ends = {};
        Engine.seam = null;
        Engine.missing = {};
        layerLog = {};
        built = [];
        trimmed = [];
        return scene;
    }

    // Settings from ae_run.py; a build started another way takes the plugin defaults.
    function setting(name, section, key) {
        if ($.global[name] !== undefined && $.global[name] !== "") {
            return parseFloat($.global[name]);
        }
        var file = new File(folder + "/../../config/defaults.json");
        if (file.exists) {
            var value = Timeline.readJson(file)[section];
            if (value && value[key] !== undefined) {
                return value[key];
            }
        }
        return undefined;
    }

    // Items built in this run: their video layers and the ids they point at through target or targets.
    var built = [];
    // Per item the AE ids of the layers it made, in the order they were made: the journal keeps them,
    // so an append can find the same objects after manual edits.
    var layerLog = {};
    var replaying = false;
    // Layers cut by trim(): { item, layer, id, from, to } for the journal and the next append.
    var trimmed = [];

    // An item cannot use an earlier one whose layers the author deleted: say which and why.
    function checkTargets(item, id) {
        var targets = Engine.targetsOf(item);
        for (var i = 0; i < targets.length; i++) {
            var lost = Engine.missing[targets[i]];
            if (lost && lost.length) {
                throw new Error(id + ": цель " + targets[i] + " потеряла слои (" + lost.join(", ") +
                    "): их удалили вручную или сборка старая. Верните слои или пересоберите сцену целиком");
            }
        }
    }

    function buildItem(item, index) {
        var id = item.id || item.block + "_" + index;
        if (!replaying) {
            checkTargets(item, id);
        }
        var before = Engine.layerIds(Engine.comp);
        var at = item.always ? 0 : Engine.time(item);
        Engine.starts[id] = at;
        Engine.current = id;
        var tokens = Engine.tokens;
        var layers;
        Engine.tokens = Engine.overlay(tokens, item.tokens || {});
        try {
            layers = loadBlock(item.block)(item.params || {}, at, id, item);
        } finally {
            Engine.tokens = tokens;
        }
        Engine.adjust(layers, item.adjust);
        var end = Engine.endTime(item);
        if (!replaying) {
            built.push({ id: id, targets: Engine.targetsOf(item), layers: layers });
        }
        for (var n = 0; n < layers.length; n++) {
            Engine.tag(layers[n], id);
            if (layers[n].hasVideo) {
                layers[n].threeDLayer = true;
            }
            if (end !== null) {
                if (layers[n].hasVideo) {
                    Engine.fadeOut(layers[n], end, Engine.tokens.motion.exit_duration);
                }
            }
        }
        var fresh = Engine.newLayers(Engine.comp, before);
        if (!replaying) {
            var ids = [];
            for (var f = 0; f < fresh.length; f++) {
                ids.push(fresh[f].id);
            }
            layerLog[id] = ids;
        }
        return fresh;
    }

    // Layers an item made, by the AE ids in the journal, sorted top first.
    function layersOf(id) {
        var ids = layerLog[id] || [];
        var wanted = {};
        var result = [];
        for (var i = 0; i < ids.length; i++) {
            wanted[ids[i]] = true;
        }
        for (var n = 1; n <= Engine.comp.numLayers; n++) {
            if (wanted[Engine.comp.layer(n).id]) {
                result.push(Engine.comp.layer(n));
            }
        }
        return result;
    }

    // adjust.below / adjust.above of the items from this one on, once every item is in the comp.
    function restack(scene, from) {
        for (var i = from; i < scene.items.length; i++) {
            var item = scene.items[i];
            var adjust = item.adjust || {};
            var mode = adjust.below ? "below" : adjust.above ? "above" : "";
            if (mode) {
                Engine.restack(layersOf(item.id || item.block + "_" + i), layersOf(adjust[mode]), mode);
            }
        }
    }

    // Layers that went to opacity 0 for good are cut a little after it (settings ae.trim_hidden, ae.trim_pad).
    // Every item of the scene counts as a reader of its targets, the frozen part of an append too.
    function trim(scene) {
        var on = setting("TRIM_HIDDEN", "ae", "trim_hidden");
        var pad = setting("TRIM_PAD", "ae", "trim_pad");
        if (!(on === true || on === 1) || typeof pad !== "number" || isNaN(pad) || pad < 0) {
            return;
        }
        var links = [];
        for (var i = 0; i < scene.items.length; i++) {
            var item = scene.items[i];
            var id = item.id || item.block + "_" + i;
            links.push({ targets: Engine.targetsOf(item), layers: layersOf(id) });
        }
        trimmed = Engine.trimHidden(Engine.comp, links, pad);
    }

    // Each recorded part of a fragment gets its own voice layer at its place on the timeline.
    function voiceParts(scene) {
        var parts = Engine.timeline.parts;
        if (!parts) {
            return [{ audio: scene.audio, at: 0, first_word: 0 }];
        }
        var result = [];
        var folder = new File(scene.timeline).parent.fsName;
        for (var i = 0; i < parts.length; i++) {
            result.push({
                audio: i === 0 ? scene.audio : folder + "/" + parts[i].audio,
                at: parts[i].at,
                first_word: parts[i].first_word
            });
        }
        return result;
    }

    function addVoice(part, number, lastWord) {
        var voice = Engine.comp.layers.add(Timeline.importOnce(new File(part.audio)));
        voice.name = number === 0 ? "voice" : "voice_" + (number + 1);
        voice.startTime = part.at;
        var words = Engine.timeline.words;
        var markers = voice.property("ADBE Marker");
        for (var i = part.first_word; i < lastWord; i++) {
            var marker = new MarkerValue(words[i].word);
            marker.duration = words[i].end - words[i].start;
            marker.setParameters({ i: String(words[i].i) });
            markers.setValueAtTime(words[i].start, marker);
        }
    }

    function addVoices(scene) {
        var parts = voiceParts(scene);
        for (var i = 0; i < parts.length; i++) {
            var taken = false;
            var name = i === 0 ? "voice" : "voice_" + (i + 1);
            for (var n = 1; n <= Engine.comp.numLayers; n++) {
                taken = taken || Engine.comp.layer(n).name === name;
            }
            if (!taken) {
                addVoice(parts[i], i, i + 1 < parts.length ? parts[i + 1].first_word : Engine.timeline.words.length);
            }
        }
    }

    // A slow push-in gives depth: the 2D background stays, objects drift against it.
    function addCamera() {
        var comp = Engine.comp;
        // A camera block already directs this scene: the default push would fight it.
        for (var n = 1; n <= comp.numLayers; n++) {
            if (comp.layer(n) instanceof CameraLayer) {
                return;
            }
        }
        var camera = comp.layers.addCamera("camera", [comp.width / 2, comp.height / 2]);
        var z = -camera.cameraOption.zoom.value;
        var near = z * Engine.tokens.motion.camera_push;
        camera.autoOrient = AutoOrientType.NO_AUTO_ORIENT;
        camera.position.setValueAtTime(0, [comp.width / 2, comp.height / 2, z]);
        camera.position.setValueAtTime(comp.duration, [comp.width / 2, comp.height / 2, near]);
    }

    // Each build gets its own name so an older comp cannot be mistaken for it.
    function freeName(name) {
        var taken = {};
        for (var i = 1; i <= app.project.numItems; i++) {
            taken[app.project.item(i).name] = true;
        }
        var result = name;
        for (var n = 2; taken[result]; n++) {
            result = name + "_" + n;
        }
        return result;
    }

    // Layers that reached the end keep going, and the camera push stretches to the new end.
    function lengthen(comp, duration) {
        var old = comp.duration;
        var frame = comp.frameDuration;
        comp.duration = duration;
        for (var i = 1; i <= comp.numLayers; i++) {
            var layer = comp.layer(i);
            if ((layer.hasVideo || layer instanceof CameraLayer) && layer.outPoint >= old - frame) {
                layer.outPoint = duration;
            }
            if (layer instanceof CameraLayer && layer.name === "camera" && layer.position.numKeys > 1) {
                var last = layer.position.numKeys;
                var value = layer.position.keyValue(last);
                layer.position.removeKey(last);
                layer.position.setValueAtTime(duration, value);
            }
        }
    }

    // Keeps a finished composition with its manual edits and adds only the new items.
    // Layers of an old item in the real comp, matched to the replayed ones: by the ids in the journal,
    // else (an older build) by tag, name and order. null where nothing matches.
    function realLayersOf(id, fresh, byId) {
        var ids = oldJournal && oldJournal.layers ? oldJournal.layers[id] : null;
        var result = [];
        var i;
        if (ids && ids.length === fresh.length) {
            for (i = 0; i < ids.length; i++) {
                result.push(byId[ids[i]] || null);
            }
            return result;
        }
        var comp = Engine.comp;
        var tagged = [];
        for (var n = 1; n <= comp.numLayers; n++) {
            if (comp.layer(n).comment === "scene:" + id) {
                tagged.push(comp.layer(n));
            }
        }
        tagged.sort(function (a, b) { return a.id - b.id; });
        var used = {};
        for (i = 0; i < fresh.length; i++) {
            var match = null;
            for (var t = 0; !match && fresh[i].comment === "scene:" + id && t < tagged.length; t++) {
                if (!used[tagged[t].id] && tagged[t].name === fresh[i].name) {
                    match = tagged[t];
                    used[match.id] = true;
                }
            }
            result.push(match);
        }
        return result;
    }

    function removeWithSources(comp) {
        var sources = [];
        for (var n = 1; n <= comp.numLayers; n++) {
            var source = comp.layer(n).source;
            if (source && !(source instanceof CompItem)) {
                sources.push(source);
            }
        }
        comp.remove();
        for (var i = 0; i < sources.length; i++) {
            try {
                if (sources[i].usedIn.length === 0) {
                    sources[i].remove();
                }
            } catch (e) {
                // Already removed through another layer of the same source.
            }
        }
    }

    // The old items play again in a scratch comp, so their blocks fill Engine.store, starts and ends as in
    // the first build; then every layer and property in the store points at the real, possibly edited layer.
    function replayOld(scene, from) {
        var real = Engine.comp;
        var temp = app.project.items.addComp("ae-video replay", real.width, real.height, real.pixelAspect,
            real.duration, real.frameRate);
        var log = Engine.soundLog;
        var made = {};
        var order = [];
        var id = "";
        // Sounds of the replay die with the scratch comp: they never reach the journal.
        Engine.soundLog = [];
        Engine.comp = temp;
        replaying = true;
        try {
            for (var n = 0; n < from; n++) {
                var old = scene.items[n];
                id = old.id || old.block + "_" + n;
                made[id] = buildItem(old, n);
                order.push(id);
            }
        } catch (e) {
            Engine.comp = real;
            removeWithSources(temp);
            throw new Error("повтор старого элемента " + id + ": " + e.toString());
        } finally {
            Engine.soundLog = log;
            Engine.comp = real;
            replaying = false;
        }
        var byId = {};
        for (var r = 1; r <= real.numLayers; r++) {
            byId[real.layer(r).id] = real.layer(r);
        }
        var map = {};
        for (var o = 0; o < order.length; o++) {
            var fresh = made[order[o]];
            var matched = realLayersOf(order[o], fresh, byId);
            var ids = [];
            for (var f = 0; f < fresh.length; f++) {
                map[fresh[f].id] = matched[f];
                if (matched[f]) {
                    ids.push(matched[f].id);
                }
            }
            layerLog[order[o]] = ids;
        }
        for (var s = 0; s < order.length; s++) {
            var missing = [];
            if (Engine.store.hasOwnProperty(order[s])) {
                Engine.store[order[s]] = Engine.remap(Engine.store[order[s]], map, temp, real, missing);
            }
            Engine.missing[order[s]] = missing;
        }
        removeWithSources(temp);
    }

    function append(scene, from) {
        var words = Engine.timeline.words;
        Engine.comp = Timeline.findLatestComp(scene.comp.name);
        var comp = Engine.comp;
        // What was there is frozen: its layers are unlocked only while the new part is built.
        var frozen = [];
        for (var n = 1; n <= comp.numLayers; n++) {
            frozen.push(comp.layer(n));
            comp.layer(n).locked = false;
        }
        // What the last build cut comes back first: the new part may use it, and it is cut again below if still hidden.
        Engine.untrim(comp, oldJournal ? oldJournal.trimmed : null);
        lengthen(comp, words[words.length - 1].end + scene.tail);
        addVoices(scene);
        replayOld(scene, from);
        var seam = null;
        for (var t = from; t < scene.items.length; t++) {
            var item = scene.items[t];
            var start = item.always ? 0 : Engine.time(item);
            seam = seam === null ? start : Math.min(seam, start);
        }
        var snapshot = seam === null ? [] : Engine.freezeSnapshot(frozen);
        Engine.seam = seam;
        try {
            for (var i = from; i < scene.items.length; i++) {
                buildItem(scene.items[i], i);
            }
            Engine.endWithTargets(built);
            restack(scene, from);
            freeze = { seam: seam === null ? null : Math.round(seam * 1000) / 1000,
                fixes: seam === null ? [] : Engine.freezeFix(snapshot, seam) };
            trim(scene);
        } finally {
            Engine.seam = null;
            for (var k = 0; k < frozen.length; k++) {
                frozen[k].locked = true;
            }
        }
        // Append is the path after manual edits: the comp is never cleaned up as an unedited build.
        markBuild(Engine.comp, scene.comp.name, true);
        Engine.comp.openInViewer();
    }

    // Earlier builds stay in the project but out of the way.
    function archiveBuilds(name) {
        var folder = null;
        for (var i = 1; i <= app.project.numItems; i++) {
            var item = app.project.item(i);
            if (item instanceof FolderItem && item.name === OLD_BUILDS) {
                folder = item;
            }
        }
        for (var n = app.project.numItems; n >= 1; n--) {
            var old = app.project.item(n);
            if (old instanceof CompItem && (old.name === name || /^_\d+$/.test(old.name.substr(name.length)) && old.name.indexOf(name) === 0)) {
                folder = folder || app.project.items.addFolder(OLD_BUILDS);
                old.parentFolder = folder;
            }
        }
    }

    function round3(value) {
        return Math.round(value * 1000) / 1000;
    }

    function valueText(value) {
        if (value instanceof Array) {
            var out = [];
            for (var i = 0; i < value.length; i++) {
                out.push(round3(value[i]));
            }
            return out.join(",");
        }
        if (typeof value === "number") {
            return String(round3(value));
        }
        return value && value.text !== undefined ? value.text : String(value);
    }

    function propText(prop) {
        if (!prop) {
            return "";
        }
        var text = prop.expressionEnabled ? "e" + prop.expression : "";
        if (prop.numKeys) {
            for (var k = 1; k <= prop.numKeys; k++) {
                text += "@" + round3(prop.keyTime(k)) + "=" + valueText(prop.keyValue(k));
            }
        } else if (prop.propertyValueType !== PropertyValueType.NO_VALUE) {
            text += valueText(prop.value);
        }
        return text;
    }

    // A fingerprint of what an author changes by hand: layers, their names, order, timing, parents,
    // switches, transforms with their keys, text, effects and masks. Same comp, same fingerprint.
    function signature(comp) {
        var text = comp.numLayers + "|" + round3(comp.duration);
        var names = ["ADBE Anchor Point", "ADBE Position", "ADBE Scale", "ADBE Rotate Z", "ADBE Opacity"];
        for (var i = 1; i <= comp.numLayers; i++) {
            var layer = comp.layer(i);
            text += "#" + layer.name + "|" + layer.comment + "|" + round3(layer.inPoint) + "|" + round3(layer.outPoint) +
                "|" + round3(layer.startTime) + "|" + layer.enabled + "|" + (layer.parent ? layer.parent.index : 0);
            var transform = layer.property("ADBE Transform Group");
            for (var n = 0; transform && n < names.length; n++) {
                text += "|" + propText(transform.property(names[n]));
            }
            if (layer instanceof TextLayer) {
                text += "|" + propText(layer.property("ADBE Text Properties").property("ADBE Text Document"));
            }
            var effects = layer.property("ADBE Effect Parade");
            var masks = layer.property("ADBE Mask Parade");
            text += "|" + (effects ? effects.numProperties : 0) + "|" + (masks ? masks.numProperties : 0);
            if (layer.hasAudio && layer.audio) {
                text += "|" + propText(layer.audio.audioLevels);
            }
        }
        var hash = 0;
        for (var c = 0; c < text.length; c++) {
            hash = (hash * 31 + text.charCodeAt(c)) % 2147483647;
        }
        return String(hash) + "-" + text.length;
    }

    function markBuild(comp, name, edited) {
        var stamp = String(new Date().getTime());
        comp.comment = BUILD_MARK + " " + name + " " + stamp + " " + (edited ? EDITED : signature(comp));
    }

    // Older builds beyond keep stay only if a person touched them: renamed, edited (fingerprint differs or
    // they were appended to), used in another comp. Comps without the mark were not made here and stay.
    // Footage, sounds and null solids that only the removed comps used go with them.
    function removeOldBuilds(name, current, keep) {
        if (keep === undefined || keep === null || isNaN(keep) || keep < 0) {
            return;
        }
        var builds = [];
        var i;
        for (i = 1; i <= app.project.numItems; i++) {
            var item = app.project.item(i);
            if (!(item instanceof CompItem) || item === current) {
                continue;
            }
            var mark = String(item.comment).split(" ");
            if (String(item.comment).indexOf(BUILD_MARK + " ") !== 0 || mark[2] !== name) {
                continue;
            }
            builds.push({ comp: item, time: parseFloat(mark[3]), sig: mark[4] });
        }
        builds.sort(function (a, b) { return b.time - a.time; });
        var sources = [];
        for (i = keep; i < builds.length; i++) {
            var old = builds[i].comp;
            var named = old.name === name || (old.name.indexOf(name) === 0 && /^_\d+$/.test(old.name.substr(name.length)));
            if (!named || builds[i].sig === EDITED || old.usedIn.length || signature(old) !== builds[i].sig) {
                continue;
            }
            for (var n = 1; n <= old.numLayers; n++) {
                var source = old.layer(n).source;
                if (source && !(source instanceof CompItem)) {
                    sources.push(source);
                }
            }
            old.remove();
        }
        for (i = 0; i < sources.length; i++) {
            try {
                if (sources[i].usedIn.length === 0) {
                    sources[i].remove();
                }
            } catch (e) {
                // Already removed through another layer of the same source.
            }
        }
    }

    function build(scene) {
        var c = scene.comp;
        archiveBuilds(c.name);
        var words = Engine.timeline.words;
        var duration = words[words.length - 1].end + scene.tail;
        Engine.comp = app.project.items.addComp(freeName(c.name), c.width, c.height, 1, duration, Engine.tokens.motion.fps);
        addVoices(scene);
        for (var i = 0; i < scene.items.length; i++) {
            buildItem(scene.items[i], i);
        }
        addCamera();
        Engine.endWithTargets(built);
        restack(scene, 0);
        trim(scene);
        markBuild(Engine.comp, c.name, false);
        removeOldBuilds(c.name, Engine.comp, setting("KEEP_BUILDS", "ae", "keep_builds"));
        Engine.comp.openInViewer();
    }

    // What was built, for the Python tools: an appended part keeps the sounds of the earlier ones.
    function journalFile(scenePath) {
        return new File(scenePath.replace(/(\.scene)?\.json$/, ".sounds.json"));
    }

    // The journal of the last build when appending: its sounds and layer ids stay.
    var oldJournal = null;
    var oldSounds = null;
    // Append only: the seam time and what was corrected so the frames before it stay as they were.
    var freeze = null;

    function writeJournal(scenePath) {
        var sounds = oldSounds ? oldSounds.concat(Engine.soundLog) : Engine.soundLog;
        var journal = { sounds: sounds, starts: Engine.starts, layers: layerLog, trimmed: trimmed };
        if (freeze) {
            journal.freeze = freeze;
        }
        Timeline.writeJson(journalFile(scenePath), journal);
    }

    function main() {
        var path = $.global.SCENE_PATH;
        if (!path) {
            var picked = File.openDialog("Select a scene file", "*.json");
            if (!picked) {
                return;
            }
            path = picked.fsName;
        }
        $.evalFile(new File(folder + "/engine.jsxinc"));
        var scene = prepare(path);
        var appendFrom = $.global.APPEND_FROM;
        if (appendFrom !== undefined && journalFile(path).exists) {
            oldJournal = Timeline.readJson(journalFile(path));
            oldSounds = oldJournal.sounds;
        }
        app.beginUndoGroup(SCRIPT_NAME);
        try {
            if (appendFrom !== undefined) {
                append(scene, parseInt(appendFrom, 10));
            } else {
                build(scene);
            }
        } finally {
            app.endUndoGroup();
        }
        writeJournal(path);
        if (!quiet) {
            alert("Done: " + Engine.comp.name + ". Items: " + scene.items.length, SCRIPT_NAME);
        }
    }

    try {
        main();
    } catch (e) {
        if (quiet) {
            throw e;
        }
        alert("Error: " + e.toString() + " (line " + e.line + ")", SCRIPT_NAME);
    }
})();
