// Prepares the transcript review: a project next to the audio, a composition and word markers.
// Automation sets $.global.REVIEW_AUDIO and REVIEW_SIZE ("width,height,fps").
// An existing project is reopened as it is, so manual marker edits survive.

#include "lib/timeline.jsxinc"

(function () {
    var audio = new File($.global.REVIEW_AUDIO);
    var stem = audio.name.replace(/\.[^.]+$/, "");
    var project = new File(audio.parent.fsName + "/" + stem + ".aep");

    if (project.exists) {
        if (!app.project.file || app.project.file.fsName !== project.fsName) {
            app.open(project);
        }
        Timeline.findComp(stem).openInViewer();
        return;
    }

    var data = Timeline.readJson(new File(audio.parent.fsName + "/" + stem + ".timeline.json"));
    var size = $.global.REVIEW_SIZE.split(",");
    app.newProject();
    var footage = app.project.importFile(new ImportOptions(audio));
    var fps = data.fps || parseFloat(size[2]);
    var comp = app.project.items.addComp(stem, parseInt(size[0], 10), parseInt(size[1], 10), 1, footage.duration, fps);
    var layer = comp.layers.add(footage);
    Timeline.addWordMarkers(layer, data.words);
    app.project.save(project);
    comp.openInViewer();
})();
