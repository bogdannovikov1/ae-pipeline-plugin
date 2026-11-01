// Saves frames of the last built composition for the preflight review.
// Automation sets $.global.CAPTURE_SPEC to a JSON file: { comp, folder, times }.

#include "lib/timeline.jsxinc"

(function () {
    function pad(number) {
        var text = String(number);
        while (text.length < 4) {
            text = "0" + text;
        }
        return text;
    }

    var spec = Timeline.readJson(new File($.global.CAPTURE_SPEC));
    var comp = Timeline.findLatestComp(spec.comp);
    for (var n = 0; n < spec.times.length; n++) {
        comp.saveFrameToPng(spec.times[n], new File(spec.folder + "/frame_" + pad(n + 1) + ".png"));
    }
})();
