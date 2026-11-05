// Writes where every scene layer sits once its entrance has settled (boxes) and how its visibility and frame
// change over its life (tracks). Automation sets $.global.LAYOUT_SPEC to a JSON file:
// { comp, out, settle, starts, full_frame }.
// Python checks the result, so an agent reads a short verdict instead of this data.

#include "lib/timeline.jsxinc"

(function () {
    var spec = Timeline.readJson(new File($.global.LAYOUT_SPEC));
    var comp = Timeline.findLatestComp(spec.comp);
    var frame = comp.frameDuration;
    var fullFrame = spec.full_frame === undefined ? 0.9 : spec.full_frame;
    var SAMPLE_STEP = 0.5;
    var TRANSFORM = ["ADBE Anchor Point", "ADBE Position", "ADBE Scale", "ADBE Rotate Z", "ADBE Rotate X",
        "ADBE Rotate Y", "ADBE Orientation", "ADBE Opacity"];
    var boxes = [];
    var tracks = [];

    function round(value, step) {
        return Math.round(value / step) * step;
    }

    // A point of the layer's own space in comp pixels: through its transform and every parent's
    // (position, anchor, scale, rotation in the picture plane; depth and the camera are left out).
    function toComp(layer, point, t) {
        var x = point[0];
        var y = point[1];
        for (var l = layer; l; l = l.parent) {
            var anchor = l.anchorPoint.valueAtTime(t, false);
            var position = l.position.valueAtTime(t, false);
            var scale = l.scale.valueAtTime(t, false);
            var angle = l.rotation.valueAtTime(t, false) * Math.PI / 180;
            var dx = (x - anchor[0]) * scale[0] / 100;
            var dy = (y - anchor[1]) * scale[1] / 100;
            x = position[0] + dx * Math.cos(angle) - dy * Math.sin(angle);
            y = position[1] + dx * Math.sin(angle) + dy * Math.cos(angle);
        }
        return [x, y];
    }

    function frameOf(layer, t) {
        var rect = layer.sourceRectAtTime(t, false);
        var corners = [[rect.left, rect.top], [rect.left + rect.width, rect.top],
            [rect.left, rect.top + rect.height], [rect.left + rect.width, rect.top + rect.height]];
        var box = { left: 1e9, top: 1e9, right: -1e9, bottom: -1e9 };
        for (var i = 0; i < corners.length; i++) {
            var p = toComp(layer, corners[i], t);
            box.left = Math.min(box.left, p[0]);
            box.top = Math.min(box.top, p[1]);
            box.right = Math.max(box.right, p[0]);
            box.bottom = Math.max(box.bottom, p[1]);
        }
        return { left: Math.round(box.left), top: Math.round(box.top), right: Math.round(box.right), bottom: Math.round(box.bottom) };
    }

    function zeroTrim(trim, t) {
        var start = trim.property("ADBE Vector Trim Start").valueAtTime(t, false);
        var end = trim.property("ADBE Vector Trim End").valueAtTime(t, false);
        return Math.abs(end - start) < 0.01;
    }

    // Whether a shape group draws anything: its own opacity, a trim that cut everything, its paths and groups.
    function groupDraws(group, t) {
        var contents = group.property("ADBE Vectors Group") || group;
        var transform = group.property("ADBE Vector Transform Group");
        if (transform && transform.property("ADBE Vector Group Opacity").valueAtTime(t, false) <= 0) {
            return false;
        }
        var drawn = false;
        for (var n = 1; n <= contents.numProperties; n++) {
            var child = contents.property(n);
            if (child.matchName === "ADBE Vector Filter - Trim" && zeroTrim(child, t)) {
                return false;
            }
            if (child.matchName === "ADBE Vector Group") {
                drawn = drawn || groupDraws(child, t);
            } else if (/^ADBE Vector Shape/.test(child.matchName)) {
                drawn = true;
            }
        }
        return drawn;
    }

    // The layer's own opacity, 0 when it is switched off. AE does not pass opacity down to children,
    // so parents do not count.
    function opacityOf(layer, t) {
        return layer.enabled ? Math.round(layer.opacity.valueAtTime(t, false)) : 0;
    }

    // The share of characters a text shows: animators that fade characters through a range selector in
    // percent (the usual reveal) hide what their range covers. Index ranges and expression selectors count as shown.
    function textShown(layer, t) {
        var animators = layer.property("ADBE Text Properties").property("ADBE Text Animators");
        var shown = 1;
        for (var a = 1; animators && a <= animators.numProperties; a++) {
            var animator = animators.property(a);
            var fade = animator.property("ADBE Text Animator Properties").property("ADBE Text Opacity");
            var selector = animator.property("ADBE Text Selectors").property("ADBE Text Selector");
            if (!fade || !selector || !animator.enabled) {
                continue;
            }
            var advanced = selector.property("ADBE Text Range Advanced");
            if (advanced.property("ADBE Text Range Units").value !== 1) {
                continue;
            }
            var offset = selector.property("ADBE Text Percent Offset").valueAtTime(t, false);
            var start = Math.max(0, selector.property("ADBE Text Percent Start").valueAtTime(t, false) + offset);
            var end = Math.min(100, selector.property("ADBE Text Percent End").valueAtTime(t, false) + offset);
            var cover = Math.max(0, end - start) / 100;
            var amount = advanced.property("ADBE Text Selector Max Amount").valueAtTime(t, false) / 100;
            shown *= 1 - cover * amount * (1 - fade.valueAtTime(t, false) / 100);
        }
        return shown;
    }

    // 1 when the layer draws something at t: it has an area and, for a shape, some group is not
    // transparent or trimmed away, for a text some characters are not faded out by its animators.
    // A line drawn on by trim, a scale from zero or a text reveal is 0 before it starts.
    function drawnOf(layer, t, f) {
        if (f.right - f.left < 1 || f.bottom - f.top < 1) {
            return 0;
        }
        if (layer instanceof TextLayer) {
            return textShown(layer, t) < 0.02 ? 0 : 1;
        }
        var root = layer.property("ADBE Root Vectors Group");
        return root && !groupDraws(root, t) ? 0 : 1;
    }

    function box(layer, id, t) {
        var f = frameOf(layer, t);
        return {
            item: id,
            layer: layer.name,
            text: layer instanceof TextLayer,
            time: round(t, 0.001),
            until: round(layer.outPoint, 0.001),
            opacity: opacityOf(layer, t),
            left: f.left,
            top: f.top,
            right: f.right,
            bottom: f.bottom
        };
    }

    function addKeyTimes(prop, times) {
        for (var k = 1; k <= prop.numKeys; k++) {
            times.push(prop.keyTime(k) - frame, prop.keyTime(k), prop.keyTime(k) + frame);
        }
    }

    function trimKeys(group, times) {
        if (!group) {
            return;
        }
        for (var n = 1; n <= group.numProperties; n++) {
            var child = group.property(n);
            if (child.matchName === "ADBE Vector Filter - Trim") {
                for (var p = 1; p <= child.numProperties; p++) {
                    if (child.property(p).propertyType === PropertyType.PROPERTY) {
                        addKeyTimes(child.property(p), times);
                    }
                }
            } else if (child.propertyType !== PropertyType.PROPERTY) {
                trimKeys(child, times);
            }
        }
    }

    // Moments where something changes: every key of the layer and its parents (transform, opacity, trim)
    // with a frame on each side, the first and last frames of its life and every half second in between.
    function sampleTimes(layer) {
        var times = [layer.inPoint, layer.inPoint + frame, layer.outPoint - frame];
        for (var t = layer.inPoint + SAMPLE_STEP; t < layer.outPoint; t += SAMPLE_STEP) {
            times.push(t);
        }
        for (var l = layer; l; l = l.parent) {
            var transform = l.property("ADBE Transform Group");
            for (var n = 0; n < TRANSFORM.length; n++) {
                var prop = transform.property(TRANSFORM[n]);
                if (prop) {
                    addKeyTimes(prop, times);
                }
            }
            trimKeys(l.property("ADBE Root Vectors Group"), times);
        }
        var seen = {};
        var result = [];
        for (var i = 0; i < times.length; i++) {
            var at = round(times[i], 0.001);
            if (at >= layer.inPoint - 1e-4 && at < layer.outPoint - 1e-4 && !seen[at]) {
                seen[at] = true;
                result.push(at);
            }
        }
        result.sort(function (a, b) { return a - b; });
        return result;
    }

    function track(layer, id) {
        var times = sampleTimes(layer);
        var samples = [];
        var big = false;
        var ever = false;
        for (var i = 0; i < times.length; i++) {
            var f = frameOf(layer, times[i]);
            big = big || (f.right - f.left >= comp.width * fullFrame && f.bottom - f.top >= comp.height * fullFrame);
            var drawn = drawnOf(layer, times[i], f);
            ever = ever || drawn === 1;
            samples.push([times[i], opacityOf(layer, times[i]), f.left, f.top, f.right, f.bottom, drawn]);
        }
        // A null only carries its children, an empty placeholder never draws: neither is ever seen.
        if (big || !samples.length || layer.nullLayer || !ever) {
            return null;
        }
        return {
            item: id,
            layer: layer.name,
            text: layer instanceof TextLayer,
            "in": round(layer.inPoint, 0.001),
            out: round(layer.outPoint, 0.001),
            samples: samples
        };
    }

    for (var i = 1; i <= comp.numLayers; i++) {
        var layer = comp.layer(i);
        var match = /^scene:(.+)$/.exec(layer.comment);
        if (match && layer.hasVideo && !(layer instanceof CameraLayer) && !(layer instanceof LightLayer) &&
            spec.starts[match[1]] !== undefined) {
            var t = Math.min(spec.starts[match[1]] + spec.settle, layer.outPoint - frame);
            boxes.push(box(layer, match[1], t));
            var tr = track(layer, match[1]);
            if (tr) {
                tracks.push(tr);
            }
        }
    }
    Timeline.writeJson(new File(spec.out), { width: comp.width, height: comp.height, boxes: boxes, tracks: tracks });
})();
