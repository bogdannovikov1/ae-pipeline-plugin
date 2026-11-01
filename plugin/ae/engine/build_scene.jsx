// Builds a composition from a scene JSON using the blocks of its base.
// Run: File > Scripts > Run Script File... and select a scene file.
// Automation sets $.global.SCENE_PATH and BASES_DIR and gets errors thrown instead of alerts.

#include "../lib/timeline.jsxinc"

(function () {
    var SCRIPT_NAME = "Build Scene";
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
        return scene;
    }

    function buildItem(item, index) {
        var id = item.id || item.block + "_" + index;
        var at = item.always ? 0 : Engine.time(item);
        Engine.starts[id] = at;
        var tokens = Engine.tokens;
        var layers;
        Engine.tokens = Engine.overlay(tokens, item.tokens || {});
        try {
            layers = loadBlock(item.block)(item.params || {}, at, id, item);
        } finally {
            Engine.tokens = tokens;
        }
        var end = Engine.endTime(item);
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
        var voice = Engine.comp.layers.add(app.project.importFile(new ImportOptions(new File(part.audio))));
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

    // Rebuilds get the suffix _2, _3: the newest item with the base name is the current one.
    function findComp(name) {
        var found = null;
        for (var i = 1; i <= app.project.numItems; i++) {
            var item = app.project.item(i);
            if (item instanceof CompItem && (item.name === name || item.name.indexOf(name + "_") === 0)) {
                if (!found || item.id > found.id) {
                    found = item;
                }
            }
        }
        if (!found) {
            throw new Error("Composition to extend not found: " + name);
        }
        return found;
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
            if (layer instanceof CameraLayer && layer.position.numKeys > 1) {
                var last = layer.position.numKeys;
                var value = layer.position.keyValue(last);
                layer.position.removeKey(last);
                layer.position.setValueAtTime(duration, value);
            }
        }
    }

    // Keeps a finished composition with its manual edits and adds only the new items.
    function append(scene, from) {
        var words = Engine.timeline.words;
        Engine.comp = findComp(scene.comp.name);
        lengthen(Engine.comp, words[words.length - 1].end + scene.tail);
        addVoices(scene);
        for (var n = 0; n < from; n++) {
            var old = scene.items[n];
            Engine.starts[old.id || old.block + "_" + n] = old.always ? 0 : Engine.time(old);
        }
        for (var i = from; i < scene.items.length; i++) {
            buildItem(scene.items[i], i);
        }
        Engine.comp.openInViewer();
    }

    function build(scene) {
        var c = scene.comp;
        var words = Engine.timeline.words;
        var duration = words[words.length - 1].end + scene.tail;
        Engine.comp = app.project.items.addComp(freeName(c.name), c.width, c.height, 1, duration, Engine.tokens.motion.fps);
        addVoices(scene);
        for (var i = 0; i < scene.items.length; i++) {
            buildItem(scene.items[i], i);
        }
        addCamera();
        Engine.comp.openInViewer();
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
        app.beginUndoGroup(SCRIPT_NAME);
        try {
            if ($.global.APPEND_FROM !== undefined) {
                append(scene, parseInt($.global.APPEND_FROM, 10));
            } else {
                build(scene);
            }
        } finally {
            app.endUndoGroup();
        }
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
