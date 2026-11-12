// Writes the transforms of every layer of a composition as the author left them: values before expressions,
// keys, order, parent, timing and a fingerprint of the contents (shapes, text, effects, masks).
// Automation sets $.global.TRANSFORMS_SPEC to a JSON file: { comp, latest, out }.
// comp "" takes the active composition; latest picks the newest build with that name (name, name_2...).
// ae_run.py writes it after a build as the snapshot and again for diff; Python compares the two,
// so an agent reads a short summary instead of this data.

#include "lib/timeline.jsxinc"

(function () {
    var spec = Timeline.readJson(new File($.global.TRANSFORMS_SPEC));
    var PROPS = {
        anchor: "ADBE Anchor Point",
        position: "ADBE Position",
        scale: "ADBE Scale",
        rotation: "ADBE Rotate Z",
        opacity: "ADBE Opacity"
    };
    var SEPARATE = ["ADBE Position_0", "ADBE Position_1", "ADBE Position_2"];
    var INNER = ["ADBE Root Vectors Group", "ADBE Text Properties", "ADBE Effect Parade", "ADBE Mask Parade"];

    function pickComp() {
        if (!spec.comp) {
            var active = app.project.activeItem;
            if (!(active instanceof CompItem)) {
                throw new Error("No active composition: open the edited one or pass --comp");
            }
            return active;
        }
        return spec.latest ? Timeline.findLatestComp(spec.comp) : Timeline.findComp(spec.comp);
    }

    function round3(value) {
        return Math.round(value * 1000) / 1000;
    }

    function plain(value) {
        if (value instanceof Array) {
            var out = [];
            for (var i = 0; i < value.length; i++) {
                out.push(round3(value[i]));
            }
            return out;
        }
        return typeof value === "number" ? round3(value) : null;
    }

    function easeList(list) {
        var out = [];
        for (var i = 0; i < list.length; i++) {
            out.push([round3(list[i].speed), round3(list[i].influence)]);
        }
        return out;
    }

    // How the key moves: interpolation in and out, temporal ease per dimension, spatial tangents.
    // The engine replays it when the author's keys go into the scene (adjust.layers).
    function curve(prop, k) {
        var result = {
            i: [prop.keyInInterpolationType(k), prop.keyOutInterpolationType(k)],
            e: [easeList(prop.keyInTemporalEase(k)), easeList(prop.keyOutTemporalEase(k))]
        };
        if (prop.isSpatial) {
            result.s = [plain(prop.keyInSpatialTangent(k)), plain(prop.keyOutSpatialTangent(k))];
        }
        return result;
    }

    // { v } for a still value, { keys: [[time, value]...] } for an animated one, before expressions.
    function readProp(prop) {
        if (!prop) {
            return null;
        }
        var result = {};
        if (prop.numKeys) {
            result.keys = [];
            for (var k = 1; k <= prop.numKeys; k++) {
                result.keys.push([round3(prop.keyTime(k)), plain(prop.keyValue(k)), curve(prop, k)]);
            }
        } else {
            result.v = plain(prop.valueAtTime(0, true));
        }
        if (prop.expressionEnabled && prop.expression) {
            result.expression = true;
        }
        return result;
    }

    function valueText(value) {
        if (value instanceof Array) {
            return plain(value).join(",");
        }
        if (typeof value === "number") {
            return String(round3(value));
        }
        if (value && value.text !== undefined) {
            return value.text + "|" + value.fontSize + "|" + value.font;
        }
        if (value && value.vertices !== undefined) {
            return String(value.vertices) + "|" + String(value.inTangents) + "|" + String(value.outTangents) + "|" + value.closed;
        }
        return String(value);
    }

    function walk(group, parts) {
        for (var i = 1; i <= group.numProperties; i++) {
            var prop = group.property(i);
            if (prop.propertyType === PropertyType.PROPERTY) {
                if (prop.propertyValueType === PropertyValueType.NO_VALUE) {
                    continue;
                }
                try {
                    var text = prop.matchName + "=";
                    if (prop.numKeys) {
                        for (var k = 1; k <= prop.numKeys; k++) {
                            text += "@" + round3(prop.keyTime(k)) + ":" + valueText(prop.keyValue(k));
                        }
                    } else {
                        text += valueText(prop.valueAtTime(0, true));
                    }
                    parts.push(text);
                } catch (e) {
                    // Custom values cannot be read from a script: they stay out of the fingerprint.
                }
            } else {
                parts.push("[" + prop.matchName + ":" + prop.name);
                walk(prop, parts);
                parts.push("]");
            }
        }
    }

    // Changes inside the layer that the transform does not show.
    function inner(layer) {
        var parts = [];
        for (var i = 0; i < INNER.length; i++) {
            var group = layer.property(INNER[i]);
            if (group) {
                parts.push(INNER[i]);
                walk(group, parts);
            }
        }
        var text = parts.join(";");
        var hash = 0;
        for (var c = 0; c < text.length; c++) {
            hash = (hash * 31 + text.charCodeAt(c)) % 2147483647;
        }
        return String(hash) + "-" + text.length;
    }

    function readLayer(layer) {
        var match = /^scene:(.+)$/.exec(layer.comment);
        var data = {
            index: layer.index,
            name: layer.name,
            item: match ? match[1] : "",
            parent: layer.parent ? layer.parent.index : 0,
            video: layer.hasVideo,
            "in": round3(layer.inPoint),
            out: round3(layer.outPoint),
            start: round3(layer.startTime),
            enabled: layer.enabled,
            props: {},
            inner: inner(layer)
        };
        var transform = layer.property("ADBE Transform Group");
        if (!transform) {
            return data;
        }
        var position = transform.property("ADBE Position");
        var separated = position && position.dimensionsSeparated;
        for (var key in PROPS) {
            if (PROPS.hasOwnProperty(key) && !(separated && key === "position")) {
                data.props[key] = readProp(transform.property(PROPS[key]));
            }
        }
        if (separated) {
            var dims = [];
            for (var d = 0; d < SEPARATE.length; d++) {
                var one = transform.property(SEPARATE[d]);
                if (one) {
                    dims.push(readProp(one));
                }
            }
            data.props.position = { dims: dims };
        }
        return data;
    }

    var comp = pickComp();
    var layers = [];
    for (var n = 1; n <= comp.numLayers; n++) {
        layers.push(readLayer(comp.layer(n)));
    }
    Timeline.writeJson(new File(spec.out), { comp: comp.name, layers: layers });
})();
