// Writes where every scene layer sits once its entrance has settled.
// Automation sets $.global.LAYOUT_SPEC to a JSON file: { comp, out, settle, starts }.
// Python checks the result, so an agent reads a short verdict instead of this data.

#include "lib/timeline.jsxinc"

(function () {
    var spec = Timeline.readJson(new File($.global.LAYOUT_SPEC));
    var comp = Timeline.findLatestComp(spec.comp);
    var boxes = [];

    // Children are placed relative to their parent, sometimes a null that moves the group.
    function worldOffset(layer, t) {
        var x = 0;
        var y = 0;
        for (var parent = layer.parent; parent; parent = parent.parent) {
            var position = parent.position.valueAtTime(t, false);
            x += position[0] - parent.anchorPoint.valueAtTime(t, false)[0];
            y += position[1] - parent.anchorPoint.valueAtTime(t, false)[1];
        }
        return [x, y];
    }

    function box(layer, id, t) {
        var rect = layer.sourceRectAtTime(t, false);
        var position = layer.position.valueAtTime(t, false);
        var anchor = layer.anchorPoint.valueAtTime(t, false);
        var scale = layer.scale.valueAtTime(t, false);
        var offset = worldOffset(layer, t);
        var left = offset[0] + position[0] + (rect.left - anchor[0]) * scale[0] / 100;
        var top = offset[1] + position[1] + (rect.top - anchor[1]) * scale[1] / 100;
        return {
            item: id,
            layer: layer.name,
            text: layer instanceof TextLayer,
            time: Math.round(t * 1000) / 1000,
            until: Math.round(layer.outPoint * 1000) / 1000,
            opacity: Math.round(layer.opacity.valueAtTime(t, false)),
            left: Math.round(left),
            top: Math.round(top),
            right: Math.round(left + rect.width * scale[0] / 100),
            bottom: Math.round(top + rect.height * scale[1] / 100)
        };
    }

    for (var i = 1; i <= comp.numLayers; i++) {
        var layer = comp.layer(i);
        var match = /^scene:(.+)$/.exec(layer.comment);
        if (match && layer.hasVideo && spec.starts[match[1]] !== undefined) {
            var t = Math.min(spec.starts[match[1]] + spec.settle, layer.outPoint - comp.frameDuration);
            boxes.push(box(layer, match[1], t));
        }
    }
    Timeline.writeJson(new File(spec.out), { width: comp.width, height: comp.height, boxes: boxes });
})();
