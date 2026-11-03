// Keeps the block lab in its own project. Automation sets $.global.LAB_PROJECT, LAB_MODE and LAB_COMP.
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
        // The archive held the old name, so the new build got a suffix: give the name back.
        var wanted = $.global.LAB_COMP;
        for (var n = 1; wanted && n <= app.project.numItems; n++) {
            var comp = app.project.item(n);
            if (comp instanceof CompItem && comp.name.indexOf(wanted + "_") === 0 && /^_\d+$/.test(comp.name.substr(wanted.length))) {
                comp.name = wanted;
            }
        }
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
