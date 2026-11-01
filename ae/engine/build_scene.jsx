// Builds a composition from a scene JSON using the blocks of its base.
// Run: File > Scripts > Run Script File... and select a scene file.
// Automation sets $.global.SCENE_PATH and gets errors thrown instead of alerts.

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

    function prepare(scenePath) {
        var scene = Timeline.readJson(new File(scenePath));
        Engine.base = scene.base;
        Engine.sceneFolder = new File(scenePath).parent.fsName;
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

    function addVoice(scene) {
        var voice = Engine.comp.layers.add(app.project.importFile(new ImportOptions(new File(scene.audio))));
        voice.name = "voice";
        var words = Engine.timeline.words;
        var markers = voice.property("ADBE Marker");
        for (var i = 0; i < words.length; i++) {
            var marker = new MarkerValue(words[i].word);
            marker.duration = words[i].end - words[i].start;
            marker.setParameters({ i: String(words[i].i) });
            markers.setValueAtTime(words[i].start, marker);
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

    function build(scene) {
        var c = scene.comp;
        var words = Engine.timeline.words;
        var duration = words[words.length - 1].end + scene.tail;
        Engine.comp = app.project.items.addComp(freeName(c.name), c.width, c.height, 1, duration, Engine.tokens.motion.fps);
        addVoice(scene);
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
            build(scene);
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
