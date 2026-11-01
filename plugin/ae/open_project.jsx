// Opens a project unless it is already open. Automation sets $.global.PROJECT_PATH.

(function () {
    var project = new File($.global.PROJECT_PATH);
    if (!project.exists) {
        throw new Error("Project not found: " + project.fsName);
    }
    if (!app.project.file || app.project.file.fsName !== project.fsName) {
        app.open(project);
    }
})();
