// Saves frames of the last built composition for the preflight review.
// Automation sets $.global.CAPTURE_SPEC to a JSON file: { comp, folder, times }.

#include "lib/timeline.jsxinc"

(function () {
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
            throw new Error("Composition not found: " + name);
        }
        return found;
    }

    function pad(number) {
        var text = String(number);
        while (text.length < 4) {
            text = "0" + text;
        }
        return text;
    }

    var spec = Timeline.readJson(new File($.global.CAPTURE_SPEC));
    var comp = findComp(spec.comp);
    for (var n = 0; n < spec.times.length; n++) {
        comp.saveFrameToPng(spec.times[n], new File(spec.folder + "/frame_" + pad(n + 1) + ".png"));
    }
})();
