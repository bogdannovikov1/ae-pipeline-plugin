// Adds a marker for every word from <audio>.timeline.json to the audio layer.
// Run: File > Scripts > Run Script File...
// Words with needs_review: true get a red label.
// Each marker keeps the hidden word number i for update_transcript_from_markers.jsx.

#include "lib/timeline.jsxinc"

(function () {
    var SCRIPT_NAME = "Add Word Markers";

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

        var file = Timeline.findTimelineFile(layer);
        if (!file) {
            return;
        }
        var words = Timeline.readJson(file).words;

        var markers = layer.property("ADBE Marker");
        if (markers.numKeys > 0 &&
            !confirm("Markers already on this layer: " + markers.numKeys + ". Replace them?", false, SCRIPT_NAME)) {
            return;
        }

        app.beginUndoGroup(SCRIPT_NAME);
        try {
            Timeline.addWordMarkers(layer, words);
        } finally {
            app.endUndoGroup();
        }

        alert("Done. Markers: " + words.length, SCRIPT_NAME);
    }

    try {
        main();
    } catch (e) {
        alert("Error: " + e.toString() + " (line " + e.line + ")", SCRIPT_NAME);
    }
})();
