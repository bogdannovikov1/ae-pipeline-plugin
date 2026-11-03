// Keeps the block lab in its own project. Automation sets $.global.LAB_PROJECT and LAB_MODE.
// open: opens the lab project or creates it. clean: drops archived builds, so iterations do not pile up.

(function () {
    var OLD_BUILDS = "old builds";
    var project = new File($.global.LAB_PROJECT);

    if ($.global.LAB_MODE === "clean") {
        for (var i = app.project.numItems; i >= 1; i--) {
            var item = app.project.item(i);
            if (item instanceof FolderItem && item.name === OLD_BUILDS) {
                item.remove();
            }
        }
        app.project.removeUnusedFootage();
        app.project.save();
        return;
    }

    if (app.project.file && app.project.file.fsName === project.fsName) {
        return;
    }
    if (project.exists) {
        app.open(project);
    } else {
        app.newProject();
        app.project.save(project);
    }
})();
